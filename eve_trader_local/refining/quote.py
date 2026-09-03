"""Reprocessing-tab quote calculation - GitHub issue #92 in the parent repo,
ported here from `eve_trader/refining/reprocessing.py`.

**File-naming note (three refining-related files now exist across the two
repos - read this before renaming anything)**: the parent's own
`refining/reprocessing.py` and `refining/engine.py` are two different files
with two different jobs. This repo's own `refining/reprocessing.py` already
ports the parent's `engine.py` (the pure ore/ice + scrapmetal yield-%
formulas - see that module's own file-mapping note). Naming *this* module
`reprocessing.py` too would collide with that file and with the parent's own
naming, so it's called `quote.py` instead - it's the live-pricing wrapper
around that yield math (paste a stack of items, get a sell-as-is-vs-refine
recommendation for each), matching the parent's `refining/reprocessing.py`
one-for-one in behavior, just not in filename.

    Sell-as-is value = quantity x C-J sell percentile x structure_sell_haircut
    Refined value     = mineral yield (scrapmetal_yield, portion-size-rounded)
                         x mineral C-J sell percentile x structure_sell_haircut - Refining Tax
    Recommendation    = "Reprocess" if Refined value > Sell-as-is value, else "Sell instead"
                         (both numbers always shown - nothing is auto-decided/excluded, #92's
                         own "informational, not auto-decided" requirement)

Both options end with a C-J sell order, so both incur the same
structure_sell_haircut (broker fee + sales tax + SCC surcharge) - the
parent's own confirmed fix (2026-08-29): sell-as-is used to be a bare gross
quantity x price with no haircut at all, while the refined side already
netted it out on the mineral side, systematically biasing borderline items
toward "Sell instead" even when reprocessing was actually the better outcome.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .. import storage
from ..config import TRADING_CONFIG, TradingConfig
from ..esi_client import OrderStats
from .config import REFINING_CONFIG, RefiningConfig
from .paste_parser import ParsedPasteLine
from .reprocessing import apply_reprocessing_yield, scrapmetal_yield

REPROCESS_DECISION = "Reprocess"
SELL_DECISION = "Sell instead"
NOT_REPROCESSABLE_DECISION = "Not reprocessable"
UNRESOLVED_DECISION = "Unknown item"
NO_MARKET_DATA_DECISION = "No market data"


@dataclass
class ReprocessingQuoteRow:
    name: str
    quantity: int
    type_id: Optional[int]
    category: str
    sell_as_is_value: Optional[float]
    refined_value: Optional[float]
    mineral_value: Optional[float]
    refining_tax: Optional[float]
    decision: str
    error: Optional[str] = None


def resolve_type_id(name: str) -> Optional[int]:
    """Exact (case-insensitive) match against the SDE - a paste line's own
    name field should already be a real EVE item name, so a fuzzy/substring
    match (like search_sde_types' own type-ahead ordering) risks silently
    resolving to the wrong item; better to report "unknown" and let the user
    fix a typo than guess."""
    candidates = storage.search_sde_types(name, limit=5)
    for type_id, type_name in candidates:
        if type_name.strip().lower() == name.strip().lower():
            return type_id
    return None


def mineral_type_ids_for_lines(type_ids: list[int]) -> list[int]:
    ids: set[int] = set()
    for type_id in type_ids:
        for material_type_id, _qty in storage.get_type_materials(type_id):
            ids.add(material_type_id)
    return sorted(ids)


def evaluate_reprocessing_line(line: ParsedPasteLine, item_stats: Optional[OrderStats],
                                mineral_stats_by_id: dict[int, OrderStats],
                                trading_cfg: TradingConfig = TRADING_CONFIG,
                                refining_cfg: RefiningConfig = REFINING_CONFIG) -> ReprocessingQuoteRow:
    """Pure - `item_stats`/`mineral_stats_by_id` are pre-fetched by the caller
    (the future do_quote_reprocessing, once actions.py exists), no network
    calls here."""
    if line.error:
        return ReprocessingQuoteRow(name=line.name, quantity=line.quantity, type_id=None, category=line.category,
                                     sell_as_is_value=None, refined_value=None, mineral_value=None,
                                     refining_tax=None, decision=UNRESOLVED_DECISION, error=line.error)

    type_id = resolve_type_id(line.name)
    if type_id is None:
        return ReprocessingQuoteRow(name=line.name, quantity=line.quantity, type_id=None, category=line.category,
                                     sell_as_is_value=None, refined_value=None, mineral_value=None,
                                     refining_tax=None, decision=UNRESOLVED_DECISION,
                                     error="No exact match in the SDE for this item name.")

    portion_size = storage.get_portion_size(type_id)
    materials = storage.get_type_materials(type_id)
    sell_as_is_value = (
        item_stats.sell_percentile * line.quantity * trading_cfg.structure_sell_haircut
        if item_stats and item_stats.sell_percentile is not None else None
    )

    if not portion_size or not materials:
        return ReprocessingQuoteRow(name=line.name, quantity=line.quantity, type_id=type_id, category=line.category,
                                     sell_as_is_value=sell_as_is_value, refined_value=None, mineral_value=None,
                                     refining_tax=None, decision=NOT_REPROCESSABLE_DECISION)

    yield_pct = scrapmetal_yield(refining_cfg)
    minerals = apply_reprocessing_yield(type_id, line.quantity, yield_pct)

    mineral_value = 0.0
    have_full_mineral_data = bool(minerals)
    for material_type_id, qty in minerals.items():
        stats = mineral_stats_by_id.get(material_type_id)
        if stats is None or stats.sell_percentile is None:
            have_full_mineral_data = False
            continue
        mineral_value += qty * stats.sell_percentile * trading_cfg.structure_sell_haircut

    if sell_as_is_value is None or not have_full_mineral_data:
        return ReprocessingQuoteRow(name=line.name, quantity=line.quantity, type_id=type_id, category=line.category,
                                     sell_as_is_value=sell_as_is_value, refined_value=None,
                                     mineral_value=mineral_value if have_full_mineral_data else None,
                                     refining_tax=None, decision=NO_MARKET_DATA_DECISION)

    refining_tax = mineral_value * refining_cfg.refining_tax_rate
    refined_value = mineral_value - refining_tax
    decision = REPROCESS_DECISION if refined_value > sell_as_is_value else SELL_DECISION

    return ReprocessingQuoteRow(name=line.name, quantity=line.quantity, type_id=type_id, category=line.category,
                                 sell_as_is_value=sell_as_is_value, refined_value=refined_value,
                                 mineral_value=mineral_value, refining_tax=refining_tax, decision=decision)
