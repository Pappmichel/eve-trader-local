"""Realized trade history: what actually happened, not what could.

Pulls wallet transactions for the buyer character(s) (importing in the Jita
region) and the seller character(s) (selling at the structure) over a
lookback window, and matches buys against sells per type_id, FIFO, to
compute realized profit. The sell side's tax deduction uses the real
per-sale amount from the wallet *journal* when available (see
fetch_recent_journal_entries/_ASSUMED_TAX_RATE_IN_DEFAULT_HAIRCUT); the buy
side and the broker's fee stay modeled, because ESI has no per-fill
broker-fee attribution at all (a broker's fee is charged once per *order*,
not per fill, so it cannot be attributed to one specific FIFO-matched sale
the way sales tax can).

Storage: unlike candidate_discovery/history_backtest/shortlist - which are
pure computations whose caller owns every read and write - this module does
touch storage, exactly as the parent's own trade_reconciliation.py does. The
buyer-side location filter needs the SDE's station list
(storage.get_station_ids_in_region), and average_daily_sold_by_type is by
definition a question about persisted rows. The SQL itself still lives in
storage.py, this repo's single persistence module; only the calls are here.
Writing a run's results stays the caller's job (the future do_reconcile_
trades equivalent), same as in the parent.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence

from . import storage
from .config import TRADING_CONFIG, TradingConfig
from .esi_client import ESIClient
from .models import RealizedTrade


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


WALLET_TRANSACTIONS_PAGE_SIZE = 2500  # ESI's fixed per-call cap for this endpoint

# Buys are fetched over a longer window than sells: a sale inside
# cfg.lookback_days can legitimately be funded by inventory bought well before
# that window started (an item sitting at the structure waiting to sell), and
# the matcher's buy_date <= sell_date rule means a too-old buy that was never
# even fetched looks identical to "no cost basis at all" - the sale is dropped
# rather than matched to a fabricated later buy. Safe, but an avoidable
# under-report. A flat multiplier (not its own config field) scales with
# whatever the user already means by "recent" while staying bounded, unlike an
# uncapped buy fetch, which would make reconciliation very slow for a
# character with years of trading history.
_BUY_LOOKBACK_MULTIPLIER = 3

# structure_sell_haircut bundles SCC surcharge + broker's fee + sales tax into
# one multiplier (see config.py: 0.5% + 1.5% + 3.37% = 5.37%). To use the real
# sales tax from the wallet journal without adding a second config field to
# hold the SCC+broker portion separately, this is the tax rate baked into that
# *default* haircut, used only to back it out: (structure_sell_haircut +
# _ASSUMED_TAX_RATE_IN_DEFAULT_HAIRCUT) isolates the SCC+broker-only retention
# ratio, which then multiplies the *real* post-tax proceeds instead of the raw
# unit price. Exact while structure_sell_haircut is its default; a user who has
# customized it (different skills/standings) gets a close-but-not-exact
# SCC+broker estimate - still strictly better on the tax term than the fully
# modeled formula, which is the one thing this is here to improve.
_ASSUMED_TAX_RATE_IN_DEFAULT_HAIRCUT = 0.0337

_MARKET_TRANSACTION_REF_TYPE = "market_transaction"


def fetch_recent_journal_entries(character_id: int, auth_role: str, client: ESIClient,
                                 lookback_days: int) -> dict[int, float]:
    """{journal entry id: amount} for this character's `market_transaction`
    journal entries within `lookback_days` - the lookup reconcile_realized_
    trades uses to find a specific sell's real post-tax proceeds via that
    transaction's own `journal_ref_id`. Best-effort: any ESI failure (missing
    scope, outage, ...) returns {} rather than raising, so a wallet-journal
    problem degrades reconciliation to the fully modeled formula instead of
    blocking it entirely - same stance as _type_info's backfill below."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    try:
        entries = client.character_wallet_journal(character_id, auth_role=auth_role)
    except Exception:  # noqa: BLE001 - best-effort; the modeled fallback is always safe
        return {}
    return {
        entry["id"]: entry["amount"]
        for entry in entries
        if entry.get("ref_type") == _MARKET_TRANSACTION_REF_TYPE
        and _parse_iso(entry["date"]) >= cutoff
    }


def fetch_recent_transactions(character_id: int, auth_role: str, client: ESIClient,
                              lookback_days: int) -> list[dict]:
    """Pages through character_wallet_transactions via `from_id` (cursor
    pagination on the oldest transaction_id of the previous page) until either
    a page's oldest transaction predates the cutoff, or a short page signals
    there is nothing older left. A single un-paginated call only ever sees the
    most recent 2500 transactions, which silently dropped older-but-still-in-
    window trades for any character trading more than that within
    `lookback_days` (confirmed real-world symptom in the parent repo: a
    frequently traded item missing from Realized Trades despite clearly having
    sold inside the window).

    The `len(page) < WALLET_TRANSACTIONS_PAGE_SIZE` stop condition relies on
    ESI's per-call cap staying at 2500 - correct today, but it would silently
    under-page (looking exactly like "no older transactions") if CCP lowered
    it."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    all_txns: list[dict] = []
    from_id: Optional[int] = None
    while True:
        page = client.character_wallet_transactions(character_id, auth_role=auth_role,
                                                    from_id=from_id)
        if not page:
            break
        all_txns.extend(page)
        oldest = min(page, key=lambda t: t["transaction_id"])
        if _parse_iso(oldest["date"]) < cutoff or len(page) < WALLET_TRANSACTIONS_PAGE_SIZE:
            break
        from_id = oldest["transaction_id"]
    return [t for t in all_txns if _parse_iso(t["date"]) >= cutoff]


def reconcile_realized_trades(buyer_characters: Sequence[tuple[int, str]],
                              seller_characters: Sequence[tuple[int, str]],
                              client: ESIClient, item_names: dict[int, str],
                              item_volumes: dict[int, float],
                              cfg: TradingConfig = TRADING_CONFIG) -> list[RealizedTrade]:
    """Matches every buyer character's region buys against every seller
    character's structure sells per type_id, FIFO, within cfg.lookback_days.
    Both arguments are (character_id, auth_role) pairs, and they are *pooled*,
    not paired 1:1 by character: every buyer's buys are matchable against
    every seller's sells, matching how a multi-character setup actually
    operates (one alt buys, another sells)."""
    buys: list[dict] = []
    for character_id, role in buyer_characters:
        buys.extend(fetch_recent_transactions(character_id, role, client,
                                              cfg.lookback_days * _BUY_LOOKBACK_MULTIPLIER))
    sells: list[dict] = []
    for character_id, role in seller_characters:
        sells.extend(fetch_recent_transactions(character_id, role, client, cfg.lookback_days))

    journal_amount_by_ref_id: dict[int, float] = {}
    for character_id, role in seller_characters:
        journal_amount_by_ref_id.update(
            fetch_recent_journal_entries(character_id, role, client, cfg.lookback_days))

    # Both sides must be location-filtered, not just the sell side: any wallet
    # transaction the buyer character made *anywhere* would otherwise enter the
    # FIFO match and get paired against an unrelated structure sale, producing a
    # wrong landed cost/profit/margin for that trade (a confirmed bug in the
    # parent repo, where the buy side had no location filter at all).
    jita_region_stations = storage.get_station_ids_in_region(cfg.jita_region_id)
    buys = [t for t in buys if t.get("is_buy") and t.get("location_id") in jita_region_stations]
    sells = [t for t in sells if not t.get("is_buy") and t.get("location_id") == cfg.structure_id]

    buys_by_type: dict[int, list[dict]] = defaultdict(list)
    for t in buys:
        buys_by_type[t["type_id"]].append(t)
    for lst in buys_by_type.values():
        lst.sort(key=lambda t: t["date"])

    sells_by_type: dict[int, list[dict]] = defaultdict(list)
    for t in sells:
        sells_by_type[t["type_id"]].append(t)
    for lst in sells_by_type.values():
        lst.sort(key=lambda t: t["date"])

    # item_names/item_volumes only cover the *current* shortlist - a type traded
    # historically but since removed (or never added, e.g. incidental moon/ice
    # product income) falls through both. Backfill those from ESI's public
    # /universe/types/ endpoint instead of guessing, once per type_id no matter
    # how many trades match it.
    names = dict(item_names)
    volumes = dict(item_volumes)

    def _type_info(type_id: int) -> None:
        if type_id in volumes:
            return
        try:
            info = client.get_type_info(type_id)
        except Exception:  # noqa: BLE001 - best-effort backfill, never block reconciliation
            volumes[type_id] = 0.0
            return
        volumes[type_id] = info.get("volume") or 0.0
        names.setdefault(type_id, info.get("name", str(type_id)))

    results: list[RealizedTrade] = []
    for type_id, sell_txns in sells_by_type.items():
        buy_queue = [dict(t) for t in buys_by_type.get(type_id, [])]
        buy_idx = 0
        _type_info(type_id)
        for sell in sell_txns:
            remaining_to_match = sell["quantity"]
            while remaining_to_match > 0 and buy_idx < len(buy_queue):
                buy = buy_queue[buy_idx]
                if buy["date"] > sell["date"]:
                    # A sale can't be funded by inventory bought *after* it
                    # sold. FIFO alone only orders buys chronologically - it
                    # never checks that a matched buy actually predates its
                    # sell, and in the parent repo 20% of matched rows (21.8%
                    # of reported profit) had buy_date > sell_date, from sells
                    # whose real cost basis was bought before the window even
                    # started. buy_queue is sorted ascending and buy_idx never
                    # rewinds, so every later buy is at least this late too:
                    # break rather than skip, leaving this buy available to a
                    # genuinely later sell. This sell's unmatched remainder is
                    # simply dropped - the same "no real data yet" honesty as
                    # average_daily_sold_by_type, rather than a fabricated cost
                    # basis. It does not recover the true pre-window basis
                    # (that needs seeding the queue with real pre-window
                    # inventory); it only stops a wrong, later one substituting
                    # for it.
                    break
                matched = min(remaining_to_match, buy["quantity"])
                if matched <= 0:
                    buy_idx += 1
                    continue
                # import_cost_per_m3 is an ISK-per-m3 *rate*, never a flat
                # per-unit fee - freight has to scale with the item's own
                # per-unit volume, or cheap/small/bulk-traded items (ammo, ice
                # products, ...) get a wildly overstated landed cost.
                freight = volumes[type_id] * cfg.import_cost_per_m3
                landed = buy["unit_price"] * (1 + cfg.jita_buy_broker_fee) + freight
                journal_amount = journal_amount_by_ref_id.get(sell.get("journal_ref_id"))
                if journal_amount is not None and sell["quantity"]:
                    net_sell = (journal_amount / sell["quantity"]) * (
                        cfg.structure_sell_haircut + _ASSUMED_TAX_RATE_IN_DEFAULT_HAIRCUT
                    )
                else:
                    net_sell = sell["unit_price"] * cfg.structure_sell_haircut
                profit_per_unit = net_sell - landed
                results.append(RealizedTrade(
                    type_id=type_id,
                    item=names.get(type_id, str(type_id)),
                    buy_date=buy["date"], buy_qty=buy["quantity"],
                    buy_unit_price=buy["unit_price"],
                    sell_date=sell["date"], sell_qty=sell["quantity"],
                    sell_unit_price=sell["unit_price"],
                    matched_qty=matched,
                    realized_profit=profit_per_unit * matched,
                    margin=(profit_per_unit / landed) if landed else 0.0,
                ))
                buy["quantity"] -= matched
                remaining_to_match -= matched
                if buy["quantity"] <= 0:
                    buy_idx += 1
    results.sort(key=lambda r: r.sell_date)
    return results


def summarize_realized(trades: Sequence[RealizedTrade]) -> dict:
    """Total profit, weighted average margin and the three items that made the
    most. The average margin is weighted by cost basis (buy price x matched
    quantity), not a plain mean of per-row margins - one tiny trade with a
    freak margin must not weigh as much as a large one."""
    total_profit = sum(t.realized_profit for t in trades)
    weighted_denom = sum(t.buy_unit_price * t.matched_qty for t in trades)
    avg_margin = (total_profit / weighted_denom) if weighted_denom else 0.0

    by_item: dict[str, float] = defaultdict(float)
    for t in trades:
        by_item[t.item] += t.realized_profit
    top3 = sorted(by_item.items(), key=lambda kv: kv[1], reverse=True)[:3]

    return {
        "total_realized_profit": total_profit,
        "average_margin": avg_margin,
        "top3_items_by_profit": top3,
    }


def average_daily_sold_by_type(cfg: TradingConfig = TRADING_CONFIG) -> dict[int, float]:
    """Real average daily quantity *this trader personally sold*, per type_id,
    from the last reconciliation run's persisted rows. Sums `matched_qty` (the
    actually-matched sale amount, never the whole transaction's buy_qty/
    sell_qty, which can span several matches) and divides by cfg.lookback_days
    - the window save_realized_trades' single stored run covers.

    Deliberately *not* the shortlist's "Profit / Day" volume figure. That is a
    market-wide question ("how much of this trades in a day at all") and is
    answered by shortlist.average_market_daily_volume from region history; the
    parent repo tried this function there first and it left Profit/Day empty
    for every not-yet-sold-by-me candidate, i.e. most of what a shortlist
    exists to evaluate. This one answers the narrower "how much have I moved
    of this" - stock/replenishment planning - and an item never actually sold
    is simply absent here rather than estimated from something else.

    Returns {} if reconciliation has never run (or matched nothing)."""
    trades = storage.latest_realized_trades()
    if not trades or cfg.lookback_days <= 0:
        return {}
    sold: dict[int, float] = defaultdict(float)
    for t in trades:
        sold[t.type_id] += t.matched_qty
    return {type_id: qty / cfg.lookback_days for type_id, qty in sold.items()}
