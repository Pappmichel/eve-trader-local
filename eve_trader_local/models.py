"""Plain result dataclasses shared by the Trading business logic.

No storage/config imports here on purpose - these are the values passed
between candidate discovery, backtesting and the shortlist, and they have to
stay free of any dependency on where those values came from.

Each dataclass arrives with the module it belongs to (see SYNC.md); the
parent eve-trader's models.py carries further ones, for tools this repo
hasn't ported.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class Candidate:
    """One row of 'Candidate Universe' / 'Focused Candidates'."""
    item: str
    type_id: int
    volume_m3: float
    category: str            # real SDE category name (e.g. "Implant", "Drone", "Material" - see candidate_discovery.guess_category), "Module/Rig"/"Material" only as a fallback
    market_group_path: str
    meta_level: Optional[int] = None    # EVE "metaLevel" dogma attribute (0=Tech I, 5=Tech II, ...)


@dataclass
class NewCandidateResult:
    """One backtested candidate - the output of history_backtest's scoring
    pass over a candidate's paired (Jita, reference-region) price history."""
    item: str
    category: str
    type_id: int
    volume_m3: float
    paired_days: int         # days with history in *both* regions
    profitable_days: int     # of those, how many cleared min_margin_threshold
    hit_rate: float
    latest_margin: float     # margin on the most recent paired day only
    best_margin: float
    avg_profit_m3: float
    avg_sell_movement: float
    score: float
    recommendation: str
    add: bool
    meta_level: Optional[int] = None


@dataclass
class ShortlistItem:
    """One shortlist entry: a candidate item actively tracked for import.
    The persisted membership list (storage.load_shortlist/upsert_shortlist),
    as opposed to ShortlistRow below, which is what one evaluation pass
    computes from it."""
    item: str
    item_id: int
    category: str
    volume_m3: float
    active: bool = True
    meta_level: Optional[int] = None


@dataclass
class ShortlistRow:
    """One fully evaluated shortlist item - see shortlist.py's module
    docstring for the formula behind each field."""
    item: str
    category: str
    landed_cost: Optional[float]
    net_sell: Optional[float]
    # Currently-listed sell-order quantity at the structure: order-book
    # *depth* right now, NOT actual daily traded volume (see
    # esi_client.OrderStats). Shown to the user as "Listed Qty" and nothing
    # more - it must never be multiplied into a "Profit / Day" figure, which
    # is exactly the bug GitHub issue #51 fixed in the parent repo: a single
    # seller parking a large batch of a never-actually-sold item inflated
    # that figure purely from the listed quantity.
    sell_volume: Optional[float]
    own_orders_remaining: float
    profit_per_unit: Optional[float]
    margin: Optional[float]
    profit_per_m3: Optional[float]
    decision: str
    active: bool
    item_id: int
    volume_m3: float
    jita_sell: Optional[float]
    import_cost: Optional[float]
    meta_level: Optional[int] = None
    # Real average daily *market-wide* traded quantity, from Goonmetrics
    # region history for cfg.reference_region_id (see
    # shortlist.average_market_daily_volume). Deliberately neither
    # sell_volume/order-book depth (issue #51) nor this trader's own
    # realized sales (issue #100 - #51's own realized-sales version left
    # this empty for every not-yet-sold-by-me candidate, which is most of
    # what a shortlist exists to evaluate). None means Goonmetrics has no
    # history for this item in that region - never estimated from something
    # else.
    avg_daily_volume: Optional[float] = None


@dataclass
class RealizedTrade:
    """One FIFO-matched buy (Jita) / sell (structure) pair for the same
    type_id - see trade_reconciliation.py. buy_qty/sell_qty are the *whole*
    transactions either side of the match; `matched_qty` is how much of them
    this particular pairing consumed, and the only quantity any figure
    derived from this row may use (one transaction can span several rows)."""
    type_id: int
    item: str
    buy_date: str
    buy_qty: int
    buy_unit_price: float
    sell_date: str
    sell_qty: int
    sell_unit_price: float
    matched_qty: int
    realized_profit: float
    margin: float


@dataclass
class UnlistedStockRow:
    """Stock physically sitting at the structure that isn't covered by an
    open sell order - see own_orders.fetch_seller_stock_without_order, whose
    dicts the display layer turns into these. sell_volume/margin mirror
    ShortlistRow's own fields (same shortlist.evaluate_shortlist_item
    formula) - None when the item has no Jita/structure order-book data at
    all (e.g. never priced through the shortlist)."""
    type_id: int
    item: str
    asset_quantity: float
    sell_order_remaining: float
    unlisted_quantity: float
    sell_volume: Optional[float] = None
    margin: Optional[float] = None


@dataclass
class UndercutRow:
    """One of the seller's own sell orders currently beaten by a cheaper
    competing order at the same structure - see own_orders.check_undercut."""
    type_id: int
    item: str
    my_price: float
    competitor_price: float
    difference: float
