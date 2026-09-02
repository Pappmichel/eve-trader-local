"""Plain result dataclasses shared by the Trading business logic.

No storage/config imports here on purpose - these are the values passed
between candidate discovery, backtesting and the shortlist, and they have to
stay free of any dependency on where those values came from.

Only `Candidate` exists so far: the parent eve-trader's models.py also
carries ShortlistItem/ShortlistRow/NewCandidateResult/RealizedTrade/... which
belong to shortlist.py/history_backtest.py/trade_reconciliation.py, none of
which are ported here yet (see SYNC.md) - each arrives with its own module.
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
