"""Plain dataclasses for the Production tool, mirroring the parent repo's own
production/models.py split (Trading's live in ../models.py). Only the ones a
ported module actually returns exist here so far - see SYNC.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class InventionResult:
    """One (invention source, decryptor) combination priced end to end - see
    invention.estimate."""
    t1_blueprint_type_id: int
    t1_blueprint_name: str
    product_type_id: int
    product_name: str
    decryptor: str
    probability: float
    output_runs: float
    datacore_cost: float
    decryptor_cost: float
    # Cost of t1_blueprint_type_id itself, ONLY when it's a Tech III relic
    # (constants.ANCIENT_RELIC_CATEGORY_ID) - always 0.0 for a genuine T1
    # blueprint, matching that case's deliberate "ignore BPC copy cost"
    # simplification (see invention.py's module docstring for why the two
    # differ: a relic must be bought fresh per attempt, a T1 BPC is usually a
    # near-free reprint of a BPO you already own). Silently leaving this at
    # 0.0 for relics too materially overstated Tech III profitability in the
    # parent repo (reported by a user, 2026-08-30).
    relic_cost: float
    total_attempt_cost: float
    # The three derived figures below are None whenever any input price was
    # unknown - see invention.estimate's all_prices_known.
    expected_cost_per_success: Optional[float]   # total_attempt_cost / probability
    expected_cost_per_run: Optional[float]       # expected_cost_per_success / output_runs
    me: int                                      # resulting BPC's material efficiency
    te: int                                      # resulting BPC's time efficiency
    material_savings_per_run: float              # (me/100) * reducible_material_cost_per_run
    net_cost_per_run: Optional[float]            # expected_cost_per_run - material_savings_per_run


@dataclass
class InventoryRow:
    """Target vs. current stock for one configured stock target - see
    engine.plan_production. current_stock is ESI-derived assets/incoming
    industry jobs (engine._current_stock) - no manual-stock override table
    exists here (see SYNC.md)."""
    type_id: int
    type_name: str
    activity: str        # "Reaction" | "Tech I" | "Tech II" | "Input" (no known blueprint)
    target: float
    current_stock: float
    total_missing: float


@dataclass
class BuyListEntry:
    type_id: int
    type_name: str
    quantity: float      # already net of on-hand stock - see on_hand_pct
    unit_price: Optional[float]
    total_price: Optional[float]
    # % of this item's *total* demand (quantity + whatever's already covered
    # by stock) already covered - 0-100.
    on_hand_pct: float
    buy_from: Optional[str]


@dataclass
class BuildJobEntry:
    type_id: int
    type_name: str
    blueprint_type_id: int
    activity: str         # "Manufacturing" | "Reaction"
    quantity: float
    job_runs: int
    unit_build_cost: Optional[float]
    decryptor: Optional[str]
    job_category: Optional[str]
    margin: Optional[float]  # engine.margin_home - Production sells only at home, never Jita


@dataclass
class SpecialOrder:
    """One row of the Special Orders list - a one-off build order, tracked
    separately from the permanent stock_targets list. See
    engine.plan_special_order for the computed Buy/Build breakdown, kept
    separate from this header row (not persisted - recomputed on demand,
    same "last computed plan" convention as the regular Bauliste)."""
    order_id: str
    note: Optional[str]
    net_against_stock: bool
    status: str  # "open" | "done"
    created_at: Optional[str]
    item_count: int


@dataclass
class SpecialOrderLineItem:
    """One ordered item within a SpecialOrder - what was actually asked for,
    as opposed to BuyListEntry/BuildJobEntry (what it takes to fulfil it)."""
    type_id: int
    type_name: str
    quantity: float


@dataclass
class StockOverlapWarningRow:
    """One item flagged by plan_special_order's net_against_stock=True
    overlap check: reachable from both this order's own material tree and
    the configured stock_targets' own tree, and currently has stock on hand
    right now - see plan_special_order's own docstring for exactly what
    this does/doesn't prove."""
    type_id: int
    type_name: str
    current_stock: float


@dataclass
class InventionNeedRow:
    """One row of plan_production's invention-needs list: how many invention
    runs to queue for a Tech II/III stock target's T1 blueprint (or Tech III
    relic) so the resulting BPCs cover the manufacturing runs the Bauliste
    needs - see engine.plan_production's invention_list."""
    type_id: int             # the manufactured (T2/T3) product's own type_id
    type_name: str
    t1_blueprint_type_id: int
    t1_blueprint_name: str
    decryptor: str
    probability: float
    output_runs: float       # BPC runs produced per successful invention
    runs_needed: int         # total manufacturing runs the Bauliste requires
    bpcs_needed: int          # ceil(max(0, runs_needed - t2_bpc_owned) / output_runs)
    recommended_invention_runs: int  # ceil(bpcs_needed / probability) - expected attempts
    # Total remaining *runs* across every owned copy of the invented T2/T3
    # blueprint (storage.available_blueprint_copies - a run count, not a copy
    # count, matching the parent's own confirmed fix).
    t2_bpc_owned: int = 0
    # t2_bpc_owned as a % of the stock target's own quantity (in manufacturing
    # runs) - deliberately uncapped above 100 (owning more than the target
    # calls for is a real, useful signal, not something to flatten away).
    stockpile_pct: float = 0.0


@dataclass
class AssetPlanJob:
    """One row of the asset-aware Bauliste (engine.plan_asset_optimized): like
    BuildJobEntry, but job_runs is netted against real owned stock at *every*
    level of the recursive bill of materials (not just the top-level stock
    target), and runs_ready_now says how many of those runs can start right
    now vs. are blocked waiting on a scarcer material.

    No recommended_slots field here (unlike the parent's own AssetPlanJob) -
    that needs live character-skill/job-slot ESI data and a per-character
    "excluded from planning" flag, neither of which this repo syncs (see
    SYNC.md); a runs_ready_now/job_runs split is still a real, useful signal
    without it."""
    type_id: int
    type_name: str
    blueprint_type_id: int
    activity: str            # "Manufacturing" | "Reaction"
    quantity: float
    job_runs: int             # runs actually needed after netting stock against pooled demand
    runs_ready_now: int       # of job_runs, how many the *scarcest* direct material covers right now
    unit_build_cost: Optional[float]
    decryptor: Optional[str] = None
    job_category: Optional[str] = None
    margin: Optional[float] = None  # engine.margin_home - Production sells only at home, never Jita
    # How much of this item's own current demand is already covered by owned
    # stock - 0 (nothing on hand) to 1 (fully covered), None if there's
    # nothing meaningful to compare against. See engine.plan_asset_optimized's
    # stock_coverage_by_id docstring for the two denominators this can come
    # from. Deliberately not factored into sort order.
    stock_coverage: Optional[float] = None


@dataclass
class MarketStatusRow:
    """Cheap stock-target readiness read (engine.market_status): target vs.
    current stock only, with no pricing/BOM traversal - unlike
    plan_production's InventoryRow, which is always a side effect of the full
    priced Bauliste pass. Simplified from the parent's three-way backup/
    home-market/Jita-market model - this repo's own stock target is one
    target quantity plus jita_target (see storage.py's stock_targets
    comment), so there is no separate 'how much is actually listed for sale'
    signal to show alongside it."""
    type_id: int
    type_name: str
    target: float
    current_stock: float
    missing: float
    jita_target: bool


@dataclass
class ShipMarginRow:
    """One row of the Margin page's list view (engine.discover_ship_margins):
    every ship with a real recipe, its current home/Jita price, build cost,
    and both margins - a pure information browser, not a candidate filter
    (no min_margin/min_daily_profit gate, no history/movement lookup - unlike
    discover_build_candidates). A ship with no order book on one or both
    sides still appears, with None for whichever price/margin couldn't be
    computed."""
    type_id: int
    type_name: str
    activity: str
    home_price: Optional[float]
    jita_price: Optional[float]
    build_cost: Optional[float]
    margin_home: Optional[float]
    margin_jita: Optional[float]
    meta_level: Optional[int]


@dataclass
class LogisticsRow:
    """One row of engine.invention_logistics: how much of a datacore/
    decryptor/T1-BPC-or-relic is needed for the currently-recommended
    invention attempts vs. what's sitting at cfg.invention_location_id.
    Reuses the parent's own row shape (needed/available/missing at one
    location) - the parent's pull_from_location_id/pull_from_available
    fields are dropped here, since those exist only for its multi-structure
    Logistik tab (see engine.py's module docstring), which this repo doesn't
    have an equivalent of."""
    type_id: int
    type_name: str
    location_id: int
    needed: float
    available: float
    missing: float


@dataclass
class T1BpcInventionNeedRow:
    """The T1-blueprint(-or-relic)-only slice of invention_logistics' demand -
    "how many BPC runs am I short on the thing that actually matters, and can
    I reprint it on site" - see engine.t1_bpc_invention_needs."""
    type_id: int
    name: str
    needed: int       # total BPC copies/runs needed across every stock target inventing from this T1 blueprint
    available: int    # BPC copies currently at cfg.invention_location_id
    missing: int      # max(0, needed - available)
    bpo_present: bool  # an original BPO of this type sits at cfg.invention_location_id too
    stockpile_pct: float = 0.0  # available as a % of needed, deliberately uncapped above 100


@dataclass
class BuildCandidate:
    """One manufacturable SDE item where building clearly beats buying right
    now - see engine.discover_build_candidates. Ranked by
    potential_daily_profit, not margin - see that function's docstring for
    why margin alone isn't a useful ranking."""
    type_id: int
    type_name: str
    activity: str
    build_cost: float
    margin: float
    daily_movement: float
    potential_daily_profit: float
    meta_level: Optional[int]
