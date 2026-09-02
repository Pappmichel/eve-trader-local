"""Plain result dataclasses shared by the Trading business logic.

No storage/config imports here on purpose - these are the values passed
between candidate discovery, backtesting and the shortlist, and they have to
stay free of any dependency on where those values came from.

`Candidate` and `NewCandidateResult` exist so far: the parent eve-trader's
models.py also carries ShortlistItem/ShortlistRow/RealizedTrade/... which
belong to shortlist.py/trade_reconciliation.py, neither of which is ported
here yet (see SYNC.md) - each arrives with its own module.
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
