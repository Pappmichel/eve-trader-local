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
