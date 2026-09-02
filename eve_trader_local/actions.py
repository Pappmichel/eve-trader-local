"""The orchestration layer: every `do_*` function the CLI (and, later, the
native GUI) actually calls.

This is the one module that wires the pure computation modules
(candidate_discovery, history_backtest, shortlist, own_orders,
trade_reconciliation) to the two things they deliberately don't touch
themselves: the network clients and persistence. Those modules were ported
storage-free on the understanding that *their caller* owns saving results -
this is that caller.

Conventions, same as the parent eve-trader repo's actions.py:
  - UI-agnostic: no printing, no argparse, plain dicts/dataclasses out.
  - Every expected failure (nobody logged in, an empty table, a bad setting)
    raises ActionError, which the CLI catches and prints as a one-line
    message. Anything else escaping is a bug, not a supported failure mode.
  - Thin: the real logic lives in the modules above. Where a function here is
    long (do_refresh_and_prune_candidates, do_pipeline) it is because
    sequencing and failure isolation *are* the work, not because business
    logic leaked in.

Deliberately absent, unlike the parent's version: backup actions (its
create/list wrap a Postgres `pg_dump` inside Docker - meaningless here) and
anything tenant/admin-shaped. `do_auth` is absent too: cli.py's `auth`
command calls TokenManager.login directly, and a wrapper adding nothing but
a dict conversion would be paperwork.
"""
from __future__ import annotations

import datetime as dt
import logging
import math
from typing import Optional

from . import candidate_discovery, config, history_backtest, own_orders, storage
from .auth import TokenManager
from .config import OAUTH_CONFIG, TRADING_CONFIG, OAuthConfig, TradingConfig
from .errors import ActionError
from .esi_client import ESIClient, ESIError
from .goonmetrics_client import GoonmetricsClient
from .models import ShortlistItem, ShortlistRow, UndercutRow, UnlistedStockRow
from .shortlist import (NO_MARKET_DATA_DECISION, SKIP_DECISION, _decision, audit_shortlist,
                        average_market_daily_volume, evaluate_shortlist, summary_counts,
                        top_imports_by_daily_profit)
from .trade_reconciliation import fetch_recent_transactions, reconcile_realized_trades, summarize_realized

log = logging.getLogger("eve_trader_local.actions")


def now_ts() -> str:
    return dt.datetime.utcnow().isoformat(timespec="seconds")


def _group_id(type_id: int) -> Optional[int]:
    """SDE group_id for `type_id`, falling back to a live ESI lookup when the
    local SDE cache doesn't have this type yet. Without the fallback an
    incomplete cache silently mislabels items whose category alone can't tell
    them apart (a Booster reads as an Implant - both are category 20, only
    the group distinguishes them). Not persisted: a rare, self-healing gap
    that the next `refresh-sde` closes for good."""
    row = storage.get_sde_type(type_id)
    if row is not None:
        return row[1]
    try:
        return ESIClient().get_type_info(type_id).get("group_id")
    except Exception:  # noqa: BLE001 - best-effort; None just leaves guess_category's existing behavior
        return None


# ----------------------------------------------------------- characters
def _list_role_characters(tm: TokenManager, prefix: str) -> list[tuple[str, int, str]]:
    """[(role_key, character_id, character_name)] for every character
    registered under this role prefix. Simpler than the parent's version,
    which additionally has to cope with tokens stored under a pre-multi-
    character fixed key - this repo's TokenManager.login has only ever
    stored f"{prefix}:{character_id}"."""
    return [(r.role, r.character_id, r.character_name) for r in tm.list_records(prefix)]


def do_list_buyer_characters(oauth_cfg: OAuthConfig = OAUTH_CONFIG) -> list[tuple[str, int, str]]:
    return _list_role_characters(TokenManager(oauth_cfg), "buyer")


def do_list_seller_characters(oauth_cfg: OAuthConfig = OAUTH_CONFIG) -> list[tuple[str, int, str]]:
    return _list_role_characters(TokenManager(oauth_cfg), "seller")


def do_remove_trading_character(role_key: str, oauth_cfg: OAuthConfig = OAUTH_CONFIG) -> dict:
    TokenManager(oauth_cfg).remove_token(role_key)
    return {"removed": role_key}


# --------------------------------------------------------------- wallet
def do_wallet_balance(role_key: str, cfg: TradingConfig = TRADING_CONFIG,
                      oauth_cfg: OAuthConfig = OAUTH_CONFIG) -> dict:
    """Live ISK balance - never cached/persisted, same as the transaction
    history below."""
    tm = TokenManager(oauth_cfg)
    record = tm.get_record(role_key)
    if record is None:
        raise ActionError(f"Character '{role_key}' is not logged in.")
    balance = ESIClient(cfg, tm).character_wallet_balance(record.character_id, role_key)
    return {"role_key": role_key, "balance": balance}


def do_wallet_transactions(role_key: str, lookback_days: Optional[int] = None,
                           cfg: TradingConfig = TRADING_CONFIG,
                           oauth_cfg: OAuthConfig = OAUTH_CONFIG) -> list[dict]:
    """One character's recent wallet transactions, newest first, labelled with
    real item names from the SDE cache. Location ids are passed through as-is:
    the parent resolves them against a persisted structure-name table this
    repo has no equivalent of yet."""
    tm = TokenManager(oauth_cfg)
    record = tm.get_record(role_key)
    if record is None:
        raise ActionError(f"Character '{role_key}' is not logged in.")
    client = ESIClient(cfg, tm)
    txns = fetch_recent_transactions(record.character_id, role_key, client,
                                     lookback_days or cfg.lookback_days)
    item_names = storage.sde_type_names({t["type_id"] for t in txns})
    rows = [{
        "transaction_id": t["transaction_id"],
        "date": t["date"],
        "type_id": t["type_id"],
        "item": item_names.get(t["type_id"], str(t["type_id"])),
        "is_buy": t["is_buy"],
        "quantity": t["quantity"],
        "unit_price": t["unit_price"],
        "total": t["quantity"] * t["unit_price"],
        "location_id": t["location_id"],
    } for t in txns]
    return sorted(rows, key=lambda r: r["date"], reverse=True)


# ------------------------------------------------------------- settings
def do_update_settings(updates: dict, cfg: TradingConfig = TRADING_CONFIG) -> dict:
    """Validates, persists and applies config changes in that order (see
    config.save_config_overrides). No ConfigError translation here, unlike the
    parent: ConfigError already *is* an ActionError in this repo (errors.py)."""
    config.save_config_overrides(updates, cfg)
    return updates


# --------------------------------------------------- candidate universe
def do_build_universe(cfg: TradingConfig = TRADING_CONFIG) -> dict:
    """Rebuilds the whole candidate universe from the SDE cache (or, on a
    fresh install without one, a live ESI market-group walk) and persists it -
    candidate_discovery never writes anything itself."""
    candidates = candidate_discovery.build_candidate_universe(ESIClient(cfg), cfg)
    storage.save_candidate_universe(candidates, now_ts(), table="candidate_universe")
    return {"count": len(candidates)}


def do_build_focused(cfg: TradingConfig = TRADING_CONFIG) -> dict:
    universe = storage.load_candidate_universe("candidate_universe")
    if not universe:
        raise ActionError("Candidate universe is empty - run 'build-universe' first.")
    focused = candidate_discovery.build_focused_candidate_universe(universe, cfg)
    storage.save_candidate_universe(focused, now_ts(), table="focused_candidates")
    return {"count": len(focused)}


def do_find_new_candidates(safe: bool = True, cfg: TradingConfig = TRADING_CONFIG) -> dict:
    """Backtests focused candidates that aren't already on the shortlist and
    records the results. This is where history_backtest's two sinks get wired
    to storage: every internal batch is saved as soon as it's scored, so an
    interrupted full run (minutes, thousands of requests) keeps what it
    already computed instead of losing all of it."""
    candidates = storage.load_candidate_universe("focused_candidates")
    if not candidates:
        raise ActionError("Focused candidates is empty - run 'build-universe' first.")
    existing_ids = {i.item_id for i in storage.load_shortlist() if i.active}
    gm = GoonmetricsClient(cfg)
    run_ts = now_ts()

    def results_sink(batch):
        storage.save_new_candidates(batch, run_ts)

    if safe:
        # Safe mode tests a rotating window per run (see
        # history_backtest.select_candidate_window) - persisting the cursor is
        # what turns "some candidates, maybe" into "every candidate,
        # eventually".
        offset = storage.get_candidate_search_offset()
        results, next_offset = history_backtest.find_new_import_candidates_safe(
            candidates, existing_ids, gm, cfg, offset=offset,
            history_sink=storage.save_goonmetrics_history, results_sink=results_sink)
        storage.set_candidate_search_offset(next_offset)
    else:
        results, _ = history_backtest.find_new_import_candidates(
            candidates, existing_ids, gm, cfg,
            history_sink=storage.save_goonmetrics_history, results_sink=results_sink)
    return {"evaluated": len(results), "recommended": sum(1 for r in results if r.add)}


def do_add_to_shortlist() -> dict:
    """Promotes the last search run's recommended candidates onto the
    shortlist. Category is re-derived from the SDE here rather than taken from
    the stored candidate row: that value was computed whenever the universe
    was last rebuilt, which can predate a categorization change and would
    silently re-introduce stale labels (441 of 1305 shortlist items had
    drifted this way in the parent repo)."""
    if not storage.latest_new_candidates():
        raise ActionError("No candidate search results yet - run 'find-candidates' first.")
    # A run that recommended nothing is a normal outcome (added: 0), not an
    # error - only a complete absence of any search run is.
    recommended = storage.latest_new_candidates(only_recommended=True)
    if not recommended:
        return {"added": 0}
    category_names = storage.load_sde_category_names()
    items = [ShortlistItem(
        item=r.item, item_id=r.type_id,
        category=candidate_discovery.guess_category(
            "", r.item, r.volume_m3, storage.get_type_category(r.type_id), category_names,
            _group_id(r.type_id)),
        volume_m3=r.volume_m3, active=True, meta_level=r.meta_level) for r in recommended]
    storage.upsert_shortlist(items)
    return {"added": len(items)}


# -------------------------------------------------------------- shortlist
def _backfill_meta_levels(items: list[ShortlistItem], client: ESIClient) -> dict:
    """Fills in meta_level for items that don't have one cached yet and
    persists it, so later runs don't re-fetch it. Best-effort: one failed
    lookup must not take the whole refresh down with it."""
    missing = [i for i in items if i.item_id and i.meta_level is None]
    if not missing:
        return {"checked": 0, "fetched": 0, "no_attribute": 0, "failed": 0}
    fetched: dict[int, int] = {}
    failed = 0
    for item in missing:
        try:
            level = client.get_meta_level(item.item_id)
        except Exception as e:  # noqa: BLE001
            log.warning("Could not fetch meta level for %s (%s): %s", item.item, item.item_id, e)
            failed += 1
            continue
        if level is not None:
            fetched[item.item_id] = level
            item.meta_level = level
    if fetched:
        storage.update_shortlist_meta_levels(fetched)
    return {"checked": len(missing), "fetched": len(fetched),
            "no_attribute": len(missing) - len(fetched) - failed, "failed": failed}


def _refresh_shortlist_rows(cfg: TradingConfig = TRADING_CONFIG,
                            oauth_cfg: OAuthConfig = OAUTH_CONFIG
                            ) -> tuple[list[ShortlistItem], list[ShortlistRow], dict]:
    """Re-fetches live market data for the shortlist and recomputes every
    row's decision. Deliberately does NOT save a snapshot - both callers do
    that themselves once they're done mutating the rows (the pruning one
    deactivates items *before* the snapshot is written, so the snapshot
    reflects the same run's prunes)."""
    items = storage.load_shortlist()
    if not items:
        raise ActionError("Shortlist is empty - run 'add-to-shortlist' first.")
    tm = TokenManager(oauth_cfg)
    seller_characters = _list_role_characters(tm, "seller")
    client = ESIClient(cfg, tm)

    meta_backfill = _backfill_meta_levels(items, client)

    # Own listings pooled across every seller character - they share the
    # structure's order slots, so "how much of this do I already have listed"
    # is a total across all of them. There is no unauthenticated equivalent
    # for "my own orders", so with no seller logged in this simply stays
    # empty rather than blocking the refresh; pricing below has its own
    # independent fallback.
    own_remaining: dict[int, float] = {}
    for seller_role, seller_character_id, _name in seller_characters:
        for item_id, remaining in own_orders.fetch_own_sell_orders(
                seller_character_id, seller_role, client, cfg).items():
            own_remaining[item_id] = own_remaining.get(item_id, 0.0) + remaining

    covered: set[int] = set()
    for buyer_role, buyer_character_id, _name in _list_role_characters(tm, "buyer"):
        try:
            covered |= own_orders.fetch_buyer_already_covered(buyer_character_id, buyer_role, client, cfg)
        except ESIError as e:
            # Needs esi-assets.read_assets.v1 - a character authorized before
            # that scope existed won't have it. Skip just that buyer.
            log.warning("Could not read buyer %s's Jita orders/assets (%s) - log that character in again?",
                        buyer_role, e)
    buyer_already_covered_ids = frozenset(covered)

    # Every item with an id, not just the active ones: an inactive item still
    # shows real margin/profit numbers (only its *decision* short-circuits),
    # and _items_to_reactivate needs those numbers to find items whose
    # economics have recovered.
    priced_item_ids = [i.item_id for i in items if i.item_id]
    try:
        # One full order-book download covers every item (the structure
        # endpoint has no type_id filter, so a per-item call would re-download
        # the whole book each time). Any one seller with docking access is
        # enough; the Goonmetrics snapshot covers "nobody logged in / call
        # failed" at reduced precision.
        structure_stats_by_item, priced_via_fallback = client.structure_order_stats_bulk_or_goonmetrics(
            cfg.structure_id, priced_item_ids,
            auth_role=seller_characters[0][0] if seller_characters else None,
            goonmetrics_market_slug=cfg.structure_market_slug)
    except ESIError as e:
        raise ActionError(f"Could not fetch the structure's order book ({e}). "
                          "Does the seller character still have docking access, or set "
                          "structure_market_slug for the Goonmetrics fallback.") from e
    jita_stats_by_item = client.region_order_stats_bulk(cfg.jita_region_id, priced_item_ids)

    try:
        history_points = GoonmetricsClient(cfg).price_history_chunked(cfg.reference_region_id, priced_item_ids)
        avg_daily_volume_by_item = average_market_daily_volume(history_points)
    except Exception:  # noqa: BLE001 - best-effort; a history outage shouldn't block the whole refresh
        log.exception("Could not fetch region history for Profit/Day - leaving it empty this run.")
        avg_daily_volume_by_item = {}

    rows = evaluate_shortlist(items, own_remaining, jita_stats_by_item, structure_stats_by_item, cfg=cfg,
                              buyer_already_covered_ids=buyer_already_covered_ids,
                              avg_daily_volume_by_item=avg_daily_volume_by_item)
    extra = {
        "own_sell_orders_found": sum(1 for v in own_remaining.values() if v > 0),
        "buyer_already_covered_found": len(buyer_already_covered_ids),
        "meta_level_backfill": meta_backfill,
        "priced_via_fallback": priced_via_fallback,
    }
    return items, rows, extra


def do_refresh_shortlist(cfg: TradingConfig = TRADING_CONFIG,
                         oauth_cfg: OAuthConfig = OAUTH_CONFIG) -> dict:
    items, rows, extra = _refresh_shortlist_rows(cfg, oauth_cfg)
    run_ts = now_ts()
    storage.save_shortlist_snapshot(rows, run_ts)
    storage.set_esi_sync_time("trading", run_ts)
    return {**extra, "summary": summary_counts(rows),
            "top_imports": top_imports_by_daily_profit(rows), "audit": audit_shortlist(items)}


def do_shortlist_trends(cfg: TradingConfig = TRADING_CONFIG) -> dict:
    """Margin momentum per shortlist item, computed from the price history
    already cached by candidate searches - a pure local computation, no
    network and no login needed."""
    items = storage.load_shortlist()
    volumes = {i.item_id: i.volume_m3 for i in items if i.item_id and i.volume_m3}
    history = storage.read_goonmetrics_history_for_types(list(volumes.keys()))
    return history_backtest.compute_margin_trends(history, volumes, cfg)


# ------------------------------------------------- live one-shot checks
def do_check_seller_unlisted_stock(cfg: TradingConfig = TRADING_CONFIG,
                                   oauth_cfg: OAuthConfig = OAUTH_CONFIG) -> dict:
    """Shortlist stock physically sitting at the structure with no sell order
    on it at all. Deliberately covers inactive shortlist items too: "not
    tracked for re-pricing anymore" is not the same as "don't bother selling
    what you already hold". Always a fresh live read, never cached."""
    tm = TokenManager(oauth_cfg)
    seller_characters = _list_role_characters(tm, "seller")
    if not seller_characters:
        raise ActionError("No seller character is logged in yet (eve-trader-local auth --role seller).")
    client = ESIClient(cfg, tm)
    shortlist_item_ids = {i.item_id for i in storage.load_shortlist() if i.item_id}

    try:
        unlisted = own_orders.fetch_seller_stock_without_order_pooled(
            [(cid, role) for role, cid, _name in seller_characters], client, shortlist_item_ids, cfg)
    except ESIError as e:
        # Most likely cause: a seller authorized before esi-assets.read_assets.v1
        # was part of the requested scope set.
        raise ActionError(
            f"ESI access failed ({e}). If that seller was logged in before "
            "esi-assets.read_assets.v1 was requested: run "
            "`eve-trader-local auth --role seller` once more."
        ) from e

    unlisted_type_ids = [entry["type_id"] for entry in unlisted]
    structure_stats_by_item: dict = {}
    jita_stats_by_item: dict = {}
    if unlisted_type_ids:
        try:
            structure_stats_by_item = client.structure_order_stats_bulk(
                cfg.structure_id, unlisted_type_ids, auth_role=seller_characters[0][0])
        except ESIError as e:
            raise ActionError(f"Could not fetch the structure's order book ({e}). "
                              "Does the seller character still have docking access?") from e
        jita_stats_by_item = client.region_order_stats_bulk(cfg.jita_region_id, unlisted_type_ids)

    rows = []
    for entry in unlisted:
        type_id = entry["type_id"]
        sde_type = storage.get_sde_type(type_id)
        name = sde_type[2] if sde_type else str(type_id)
        volume_m3 = sde_type[3] if sde_type and sde_type[3] else None

        jita_stats = jita_stats_by_item.get(type_id)
        structure_stats = structure_stats_by_item.get(type_id)
        jita_sell = jita_stats.sell_percentile if jita_stats else None
        net_sell = (structure_stats.sell_percentile * cfg.structure_sell_haircut) \
            if structure_stats and structure_stats.sell_percentile is not None else None
        sell_volume = structure_stats.sell_volume if structure_stats else None
        margin = None
        # Same landed-cost/margin formula the shortlist itself uses - a bare
        # quantity alone doesn't say whether listing it is worth the trip.
        if jita_sell is not None and volume_m3 is not None and net_sell is not None:
            landed_cost = jita_sell * (1 + cfg.jita_buy_broker_fee) + volume_m3 * cfg.import_cost_per_m3
            if landed_cost:
                margin = (net_sell - landed_cost) / landed_cost

        rows.append(UnlistedStockRow(
            type_id=type_id, item=name, asset_quantity=entry["asset_quantity"],
            sell_order_remaining=entry["sell_order_remaining"],
            unlisted_quantity=entry["unlisted_quantity"], sell_volume=sell_volume, margin=margin))
    rows.sort(key=lambda r: r.unlisted_quantity, reverse=True)
    return {"rows": rows}


def do_check_undercut(cfg: TradingConfig = TRADING_CONFIG,
                      oauth_cfg: OAuthConfig = OAUTH_CONFIG) -> dict:
    """Which of the seller's own sell orders a competitor is currently
    beating. ESI has no "you were undercut" notification, so this is only ever
    a fresh live check."""
    tm = TokenManager(oauth_cfg)
    seller_characters = _list_role_characters(tm, "seller")
    if not seller_characters:
        raise ActionError("No seller character is logged in yet (eve-trader-local auth --role seller).")
    client = ESIClient(cfg, tm)
    try:
        undercut = own_orders.check_undercut_pooled(
            [(cid, role) for role, cid, _name in seller_characters], client, cfg)
    except ESIError as e:
        raise ActionError(f"ESI access failed ({e}).") from e

    rows = []
    for entry in undercut:
        sde_type = storage.get_sde_type(entry["type_id"])
        rows.append(UndercutRow(
            type_id=entry["type_id"], item=sde_type[2] if sde_type else str(entry["type_id"]),
            my_price=entry["my_price"], competitor_price=entry["competitor_price"],
            difference=entry["difference"]))
    return {"rows": rows}


# --------------------------------------------------- pruning / reactivation
# "No market data" (never priced) and "Skip" (priced but unprofitable) stay
# two distinct labels for the user, but count identically towards the
# deactivation streak - a different grace period per label would be a
# separate decision nobody has made.
SKIP_STREAK_DECISIONS = (NO_MARKET_DATA_DECISION, SKIP_DECISION)


def _items_past_skip_grace_period(rows: list[ShortlistRow], skip_since: dict[int, str],
                                  grace_period_days: int, now: dt.datetime) -> list[tuple[int, str]]:
    """Which items are due for deactivation: Skip-like *this* run and on an
    unbroken streak that started at least `grace_period_days` ago.
    `skip_since` must be read before this run's streak updates are applied -
    an item whose streak started this run isn't due yet."""
    grace_period = dt.timedelta(days=grace_period_days)
    due = []
    for r in rows:
        if r.decision not in SKIP_STREAK_DECISIONS or not r.item_id:
            continue
        since_str = skip_since.get(r.item_id)
        if since_str is None:
            continue
        if now - dt.datetime.fromisoformat(since_str) >= grace_period:
            due.append((r.item_id, r.item))
    return due


def _items_beyond_rank(rows: list[ShortlistRow], max_active_items: int) -> list[tuple[int, str]]:
    """Everything past the cap when the still-active rows are ranked by max
    daily profit (margin x liquidity, not margin alone - a huge margin on an
    item nobody buys isn't worth a shortlist slot). Rows with no computable
    figure sort last: never prioritized over rows that do have data, but not
    pushed out ahead of them either."""
    def _daily_profit(r):
        if r.profit_per_unit is None or r.sell_volume is None:
            return float("-inf")
        return r.profit_per_unit * r.sell_volume

    ranked = sorted(rows, key=_daily_profit, reverse=True)
    return [(r.item_id, r.item) for r in ranked[max_active_items:] if r.item_id]


def _items_to_reactivate(rows: list[ShortlistRow], cfg: TradingConfig) -> list[tuple[int, str]]:
    """The deactivation gate mirrored in reverse. Without this an item that
    was deactivated once could never come back: shortlist._decision
    short-circuits to "Inactive" without ever re-checking the real numbers,
    which are computed for every row regardless of active state."""
    due = []
    for r in rows:
        if r.active or not r.item_id:
            continue
        if (r.sell_volume is not None and r.sell_volume > 0
                and r.profit_per_unit is not None and r.profit_per_unit > cfg.min_profit_threshold
                and r.margin is not None and r.margin >= cfg.min_margin_threshold):
            due.append((r.item_id, r.item))
    return due


def do_refresh_and_prune_candidates(safe: bool = True, cfg: TradingConfig = TRADING_CONFIG,
                                    oauth_cfg: OAuthConfig = OAUTH_CONFIG) -> dict:
    """One-button shortlist maintenance: find new candidates, add the
    recommended ones, then re-evaluate everything and deactivate

      1. items continuously Skip-like for cfg.skip_grace_period_days, and
      2. if cfg.enforce_shortlist_cap is on, whatever *remains* active beyond
         cfg.max_active_shortlist_items by daily profit (applied on top of 1,
         not instead of it),

    while reactivating any inactive item whose numbers now clear the same bar
    again. Deactivation is active=False, never a delete - snapshot history
    survives and the item can come back.

    Assumes the candidate universe already exists (build-universe is an
    occasional setup step, not a daily one)."""
    find_result = do_find_new_candidates(safe=safe, cfg=cfg)
    try:
        add_result = do_add_to_shortlist()
    except ActionError:
        # Only reachable when the search above scored nothing at all (every
        # candidate already on the shortlist, or a fully failed batch) - that
        # must not abort the refresh/prune half of this action.
        add_result = {"added": 0}

    items, rows, extra = _refresh_shortlist_rows(cfg, oauth_cfg)
    run_ts = now_ts()

    to_reactivate = _items_to_reactivate(rows, cfg)
    if to_reactivate:
        reactivated_ids = [item_id for item_id, _ in to_reactivate]
        storage.activate_shortlist_items(reactivated_ids)
        reactivated_id_set = set(reactivated_ids)
        for r in rows:
            if r.item_id in reactivated_id_set:
                r.active = True
                # buyer_already_covered isn't available out here; worst case a
                # row reads "Import" for one cycle where "Already ordered"
                # would be more precise, and self-corrects next refresh.
                r.decision = _decision(True, r.item_id, r.sell_volume, r.profit_per_unit, r.margin,
                                       r.own_orders_remaining, buyer_already_covered=False, cfg=cfg)

    skip_since = storage.get_shortlist_skip_since()
    skip_deactivate = _items_past_skip_grace_period(rows, skip_since, cfg.skip_grace_period_days,
                                                    dt.datetime.fromisoformat(run_ts))

    storage.clear_shortlist_skip_streak(
        [r.item_id for r in rows if r.decision not in SKIP_STREAK_DECISIONS and r.item_id])
    storage.start_shortlist_skip_streak(
        [r.item_id for r in rows if r.decision in SKIP_STREAK_DECISIONS and r.item_id], run_ts)

    cap_deactivate: list[tuple[int, str]] = []
    if cfg.enforce_shortlist_cap:
        skip_deactivated_ids = {item_id for item_id, _ in skip_deactivate}
        still_active = [r for r in rows if r.active and r.item_id and r.item_id not in skip_deactivated_ids]
        cap_deactivate = _items_beyond_rank(still_active, cfg.max_active_shortlist_items)

    to_deactivate = skip_deactivate + cap_deactivate
    if to_deactivate:
        deactivated_ids = [item_id for item_id, _ in to_deactivate]
        storage.deactivate_shortlist_items(deactivated_ids)
        storage.clear_shortlist_skip_streak(deactivated_ids)
        # Applied to `rows` before the snapshot is written, so the saved
        # snapshot reflects this run's own prunes instead of showing them as
        # still-active for one more cycle.
        deactivated_id_set = set(deactivated_ids)
        for r in rows:
            if r.item_id in deactivated_id_set:
                r.active = False
                r.decision = "Inactive"

    storage.save_shortlist_snapshot(rows, run_ts)
    storage.set_esi_sync_time("trading", run_ts)

    return {
        "new_candidates_evaluated": find_result["evaluated"],
        "new_candidates_added": add_result["added"],
        **extra,
        "summary": summary_counts(rows),
        "top_imports": top_imports_by_daily_profit(rows),
        "audit": audit_shortlist(items),
        "deactivated_count": len(to_deactivate),
        "deactivated_items": [item for _, item in to_deactivate],
        "cap_deactivated_count": len(cap_deactivate),
        "reactivated_count": len(to_reactivate),
        "reactivated_items": [item for _, item in to_reactivate],
    }


def shortlist_skip_deactivation_days(cfg: TradingConfig = TRADING_CONFIG) -> dict[int, int]:
    """{item_id: days left before auto-deactivation} for every item currently
    on an unbroken Skip streak. An item absent from the result isn't on one."""
    now = dt.datetime.utcnow()
    return {
        item_id: max(0, math.ceil(cfg.skip_grace_period_days
                                  - (now - dt.datetime.fromisoformat(since)).total_seconds() / 86400))
        for item_id, since in storage.get_shortlist_skip_since().items()
    }


# ------------------------------------------------------- realized trades
def do_reconcile_trades(cfg: TradingConfig = TRADING_CONFIG,
                        oauth_cfg: OAuthConfig = OAUTH_CONFIG) -> dict:
    tm = TokenManager(oauth_cfg)
    buyer_characters = _list_role_characters(tm, "buyer")
    seller_characters = _list_role_characters(tm, "seller")
    if not (buyer_characters and seller_characters):
        raise ActionError("At least one buyer and one seller character need to be logged in.")
    client = ESIClient(cfg, tm)

    items = storage.load_shortlist()
    trades = reconcile_realized_trades(
        [(cid, role) for role, cid, _name in buyer_characters],
        [(cid, role) for role, cid, _name in seller_characters],
        client, {i.item_id: i.item for i in items}, {i.item_id: i.volume_m3 for i in items}, cfg)
    storage.save_realized_trades(trades, now_ts())
    return {"matched_trades": len(trades), **summarize_realized(trades)}


# ---------------------------------------------------------------- pipeline
def do_pipeline(safe: bool = True, rebuild_universe: bool = False,
                cfg: TradingConfig = TRADING_CONFIG,
                oauth_cfg: OAuthConfig = OAUTH_CONFIG) -> dict:
    """The daily workflow. Each step is isolated: a missing login or a network
    hiccup in one must not stop the others from running, so a failure is
    recorded as that step's result instead of raising.

    Uses do_refresh_and_prune_candidates rather than a separate find + refresh
    pair, so one run actually finishes the job - newly found candidates get
    added and stale ones pruned in the same run (in the parent repo this
    called find + refresh only, and so never added or pruned anything).

    `rebuild_universe` re-derives the entire candidate universe and is off by
    default: that's an occasional setup step, not a daily one."""
    results: dict = {}

    if rebuild_universe:
        try:
            results["build_universe"] = do_build_universe(cfg)
            results["build_focused"] = do_build_focused(cfg)
        except ActionError as e:  # ESIError is an ActionError here (see errors.py)
            results["build_universe"] = {"error": str(e)}

    try:
        results["refresh_and_prune_candidates"] = do_refresh_and_prune_candidates(
            safe=safe, cfg=cfg, oauth_cfg=oauth_cfg)
    except ActionError as e:
        results["refresh_and_prune_candidates"] = {"error": str(e)}

    try:
        results["reconcile_trades"] = do_reconcile_trades(cfg, oauth_cfg)
    except ActionError as e:
        results["reconcile_trades"] = {"error": str(e)}

    return results
