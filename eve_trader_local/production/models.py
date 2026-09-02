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
