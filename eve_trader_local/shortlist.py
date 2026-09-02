"""Per-shortlist-row margin calculation:

    Landed Cost   = Jita sell percentile x (1 + jita_buy_broker_fee)
                    + volume_m3 x import_cost_per_m3
    Net Sell      = Structure sell percentile x structure_sell_haircut
    Profit / Unit = Net Sell - Landed Cost
    Margin        = Profit / Landed Cost
    Profit / m3   = Profit / volume_m3
    Profit / Day  = Profit / Unit x avg_daily_volume
    Decision      = Inactive | Missing ID | No market data | Skip |
                    Already ordered | Import

Pure computation: nothing here makes a network call, opens a database
connection, or reads global state. Order-book stats and price history are
fetched once by the caller and handed in - in the parent repo this matters
for a concrete reason, since the structure order-book endpoint has no
type_id filter, so pricing items one-by-one re-downloaded the entire order
book per item. Shortlist *membership* is persisted (storage.load_shortlist /
upsert_shortlist), but the persistence lives in storage.py, same as
everywhere else in this repo.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Optional

from .config import TRADING_CONFIG, TradingConfig
from .esi_client import OrderStats
from .goonmetrics_client import HistoryPoint
from .models import ShortlistItem, ShortlistRow

# "No market data" and "Skip" are deliberately two labels, not one merged
# one: a brand-new candidate with no structure-side price data at all (never
# even listed there) and a fully priced item that simply isn't profitable
# enough look identical otherwise, even though the right next action differs
# - seed the market vs. accept it isn't viable.
NO_MARKET_DATA_DECISION = "No market data"
SKIP_DECISION = "Skip"
ALL_DECISIONS = ["Inactive", "Missing ID", NO_MARKET_DATA_DECISION, SKIP_DECISION,
                 "Already ordered", "Import"]


def _decision(active: bool, item_id: Optional[int], sell_volume: Optional[float],
              profit: Optional[float], margin: Optional[float],
              own_orders_remaining: float, buyer_already_covered: bool,
              cfg: TradingConfig) -> str:
    """Precedence, most restrictive first: an inactive item is always
    "Inactive" regardless of everything else; an item without an item_id
    can't be priced at all, so it comes next; only then does a genuine data
    gap ("No market data" - sell_volume/profit/margin never came back)
    get checked, ahead of the actual buy decision. "Already ordered" is a
    refinement of "Import" ("you'd import this, but it's already covered"),
    not an independent state, so it's only reachable once the profit/margin
    bar has been cleared."""
    if not active:
        return "Inactive"
    if not item_id:
        return "Missing ID"
    have_data = sell_volume is not None and profit is not None and margin is not None
    if not have_data:
        return NO_MARKET_DATA_DECISION
    if sell_volume > 0 and profit > cfg.min_profit_threshold and margin >= cfg.min_margin_threshold:
        return "Already ordered" if (own_orders_remaining > 0 or buyer_already_covered) else "Import"
    return SKIP_DECISION


def evaluate_shortlist_item(item: ShortlistItem, own_orders_remaining: float,
                            jita_stats: Optional[OrderStats],
                            structure_stats: Optional[OrderStats],
                            cfg: TradingConfig = TRADING_CONFIG,
                            buyer_already_covered: bool = False,
                            avg_daily_volume: Optional[float] = None) -> ShortlistRow:
    """Computes landed cost, net sell, margin and decision for one item.
    `jita_stats`/`structure_stats` are pre-fetched by the caller - this makes
    no network calls. `buyer_already_covered`: the buyer already holds an
    open buy order or inventory for this item, which counts as "Already
    ordered" just like the seller already having it listed.
    `avg_daily_volume` (see average_market_daily_volume) is the item's real
    market-wide average daily traded quantity, never this trader's own
    sales; None means Goonmetrics has no history for it.

    An inactive item is still fully priced here - only a genuinely
    unpriceable one (no item_id) short-circuits. `_decision` independently
    returns "Inactive" first regardless of these pricing fields, so the
    displayed status is unaffected either way, but the margin/profit numbers
    stay visible instead of going blank the moment an item is deactivated.
    """
    if not item.item_id:
        return ShortlistRow(
            item=item.item, category=item.category, landed_cost=None, net_sell=None,
            sell_volume=None, own_orders_remaining=own_orders_remaining,
            profit_per_unit=None, margin=None, profit_per_m3=None,
            decision=_decision(item.active, item.item_id, None, None, None,
                               own_orders_remaining, buyer_already_covered, cfg),
            active=item.active, item_id=item.item_id, volume_m3=item.volume_m3,
            jita_sell=None, import_cost=None, meta_level=item.meta_level,
            avg_daily_volume=avg_daily_volume,
        )

    # Deliberately sell_percentile (the ask - an instant-buy fill), not
    # buy_percentile, even though real purchases go through a standing buy
    # order (which is why jita_buy_broker_fee still applies: EVE only charges
    # broker's fee on standing orders). Pricing off the ask keeps the margin
    # estimate conservative - a lowball bid at buy_percentile might never
    # fill before the opportunity is gone.
    jita_sell = jita_stats.sell_percentile if jita_stats else None
    import_cost = item.volume_m3 * cfg.import_cost_per_m3
    landed_cost = (jita_sell * (1 + cfg.jita_buy_broker_fee) + import_cost) \
        if jita_sell is not None else None
    net_sell = (structure_stats.sell_percentile * cfg.structure_sell_haircut) \
        if structure_stats and structure_stats.sell_percentile is not None else None
    # Order-book depth right now, carried through only so it can be shown as
    # "Listed Qty" and used as a "is anything listed at all" gate in
    # _decision - never as the multiplier for Profit / Day (see
    # top_imports_by_daily_profit).
    sell_volume = structure_stats.sell_volume if structure_stats else None

    profit = (net_sell - landed_cost) if (net_sell is not None and landed_cost is not None) else None
    margin = (profit / landed_cost) if (profit is not None and landed_cost not in (None, 0)) else None
    profit_m3 = (profit / item.volume_m3) if (profit is not None and item.volume_m3 > 0) else None

    return ShortlistRow(
        item=item.item, category=item.category, landed_cost=landed_cost, net_sell=net_sell,
        sell_volume=sell_volume, own_orders_remaining=own_orders_remaining,
        profit_per_unit=profit, margin=margin, profit_per_m3=profit_m3,
        decision=_decision(item.active, item.item_id, sell_volume, profit, margin,
                           own_orders_remaining, buyer_already_covered, cfg),
        active=item.active, item_id=item.item_id, volume_m3=item.volume_m3,
        jita_sell=jita_sell, import_cost=import_cost, meta_level=item.meta_level,
        avg_daily_volume=avg_daily_volume,
    )


def evaluate_shortlist(items: Iterable[ShortlistItem], own_orders_by_item: dict[int, float],
                       jita_stats_by_item: dict[int, OrderStats],
                       structure_stats_by_item: dict[int, OrderStats],
                       cfg: TradingConfig = TRADING_CONFIG,
                       buyer_already_covered_ids: frozenset[int] = frozenset(),
                       avg_daily_volume_by_item: Optional[dict[int, float]] = None,
                       ) -> list[ShortlistRow]:
    """Recomputes every shortlist row in one pass over pre-fetched data (see
    ESIClient.region_order_stats/structure_order_stats_bulk_or_goonmetrics
    for the stats dicts, average_market_daily_volume for the volume one)."""
    avg_daily_volume_by_item = avg_daily_volume_by_item or {}
    rows = []
    for item in items:
        remaining = own_orders_by_item.get(item.item_id, 0.0)
        jita_stats = jita_stats_by_item.get(item.item_id) if item.item_id else None
        structure_stats = structure_stats_by_item.get(item.item_id) if item.item_id else None
        buyer_covered = item.item_id in buyer_already_covered_ids
        avg_daily_volume = avg_daily_volume_by_item.get(item.item_id) if item.item_id else None
        rows.append(evaluate_shortlist_item(item, remaining, jita_stats, structure_stats, cfg,
                                            buyer_covered, avg_daily_volume))
    return rows


def average_market_daily_volume(history_points: Iterable[HistoryPoint]) -> dict[int, float]:
    """Real average daily *market-wide* traded quantity per type_id, from
    Goonmetrics region history. The caller fetches `history_points` for
    cfg.reference_region_id - the real region the destination structure's
    own solar system sits in; there is no ESI or Goonmetrics history
    endpoint for a player structure's own market at all, so region-wide is
    the closest real signal that exists. Averages `movement` (a genuine unit
    count, see goonmetrics_client.HistoryPoint) over every day Goonmetrics
    returned (its price_history endpoint serves a fixed ~28-day window), not
    a recent slice - the same "average over every day available" approach
    history_backtest.py takes against the same reference region.

    This is what ShortlistRow.avg_daily_volume, and therefore Profit / Day,
    is computed from. It is deliberately neither of the two things it has
    been in the parent repo's history:

    - NOT sell_volume/order-book depth (the parent's GitHub issue #51): that
      is "how much is listed for sale right now", so a single seller parking
      a large batch of a never-actually-sold item inflated Profit / Day
      purely from the listed quantity.
    - NOT the trader's own realized sales (the parent's GitHub issue #100):
      #51's first fix scoped this to matched sales from the trader's own
      reconciliation, which overcorrected - a prospective import candidate
      has by definition never been sold by this trader, so Profit / Day went
      empty for exactly the rows a shortlist exists to evaluate.

    A type Goonmetrics returned no history for is simply absent from the
    result (the caller then leaves avg_daily_volume None) - never estimated
    from something else.
    """
    by_type: dict[int, list[float]] = defaultdict(list)
    for point in history_points:
        by_type[point.type_id].append(point.movement)
    return {type_id: sum(movements) / len(movements)
            for type_id, movements in by_type.items() if movements}


def summary_counts(rows: Iterable[ShortlistRow]) -> dict[str, Optional[float]]:
    """Headline counts for one evaluation run. `avg_margin` averages only
    positive margins - it's a "what do the worthwhile items look like"
    figure, and including deeply negative ones would drown that out."""
    rows = list(rows)
    margins = [r.margin for r in rows if r.margin is not None and r.margin > 0]
    return {
        "import_candidates": sum(1 for r in rows if r.decision == "Import"),
        "already_ordered": sum(1 for r in rows if r.decision == "Already ordered"),
        "skipped": sum(1 for r in rows if "Skip" in r.decision),
        "positive_margin": len(margins),
        "avg_margin": sum(margins) / len(margins) if margins else None,
    }


def top_imports_by_daily_profit(rows: Iterable[ShortlistRow], top_n: int = 10) -> list[dict]:
    """Best items by Profit / Day = profit_per_unit x avg_daily_volume -
    real market-wide traded quantity from Goonmetrics region history (see
    average_market_daily_volume), NOT sell_volume/order-book depth and NOT
    the trader's own realized sales.

    This is a theoretical ceiling: "what a whole day of market turnover in
    this item is worth", not a claim about what one seller could personally
    capture. A tiny-volume, huge-per-unit item showing an enormous number is
    mathematically correct for that question - don't cap or filter the
    multiplication itself.

    An item Goonmetrics has no history for yet (avg_daily_volume is None) is
    excluded here rather than estimated from something else.
    """
    out = []
    for r in rows:
        if r.profit_per_unit is None or r.avg_daily_volume is None or r.margin is None:
            continue
        out.append({
            "item": r.item, "profit_per_unit": r.profit_per_unit, "margin": r.margin,
            "avg_daily_volume": r.avg_daily_volume,
            "max_profit_per_day": r.profit_per_unit * r.avg_daily_volume,
            "decision": r.decision,
        })
    out.sort(key=lambda x: x["max_profit_per_day"], reverse=True)
    return out[:top_n]


def audit_shortlist(items: Iterable[ShortlistItem]) -> dict[str, int]:
    """Data-quality check over the membership list: duplicate or missing
    type_ids and non-positive volumes, each of which silently breaks pricing
    (a volume of 0 makes profit_per_m3 uncomputable, a missing id makes the
    whole row unpriceable)."""
    items = list(items)
    ids = [i.item_id for i in items if i.item_id]
    return {
        "duplicate_type_ids": len(ids) - len(set(ids)),
        "missing_type_ids": sum(1 for i in items if not i.item_id),
        "invalid_volume": sum(1 for i in items if i.volume_m3 is None or i.volume_m3 <= 0),
    }
