"""Read-only views over the ESI-synced industry job cache (see esi_sync.py):
a flat list of currently active jobs, and a per-character job-slot *usage*
overview - ported from the parent's production/jobs.py, with
character_slot_overview deliberately reduced (see its own docstring for why:
this repo's esi_sync.py never requests the character-skills ESI scope the
parent's total-slots/free-slots/excluded-from-planning half depends on)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from .. import storage
from . import pricing
from .config import PRODUCTION_CONFIG, ProductionConfig
from .constants import ACTIVITY_JOB_LABELS, ACTIVITY_SLOT_CATEGORY, SLOT_CATEGORY_LABELS
from .models import CharacterSlotRow, IndustryJobRow


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def list_current_jobs(cfg: ProductionConfig = PRODUCTION_CONFIG) -> list[IndustryJobRow]:
    """Every active/paused/ready character + corp industry job, one row per
    job (not aggregated by item), sorted by soonest-completing first.

    output_value is quantity x unit price, priced the same way stock_value
    prices owned stock (home sell quote, falling back to Jita sell quote) -
    None if the job has no product (research/copying jobs - quantity is
    already None for those) or if neither market has a sell quote for it, so
    a temporary data gap shows as "no value" rather than silently as 0."""
    jobs = storage.list_industry_jobs()
    # Only the distinct products these jobs actually output need pricing -
    # see pricing.home_prices/jita_prices' own docstrings for why callers
    # must scope type_ids explicitly now.
    product_type_ids = list({j[3] for j in jobs if j[3] is not None})
    home = pricing.home_prices(product_type_ids, cfg)
    jita = pricing.jita_prices(product_type_ids)

    now = datetime.now(timezone.utc)
    rows = []
    for (job_id, activity_id, blueprint_type_id, product_type_id, type_name, runs,
         _output_location_id, status, end_date, start_date, installer_name) in jobs:
        quantity = None
        if product_type_id is not None:
            qty_per_run = storage.get_product_quantity(blueprint_type_id, activity_id, product_type_id)
            if qty_per_run is not None:
                quantity = qty_per_run * runs
        output_value = None
        if quantity is not None and product_type_id is not None:
            home_quote = home.get(product_type_id)
            jita_quote = jita.get(product_type_id)
            if home_quote and home_quote.sell > 0:
                output_value = quantity * home_quote.sell
            elif jita_quote and jita_quote.sell > 0:
                output_value = quantity * jita_quote.sell
        end_dt = _parse_iso(end_date)
        remaining = (end_dt - now).total_seconds() if end_dt else None
        rows.append(IndustryJobRow(
            job_id=job_id,
            type_name=type_name or (str(product_type_id) if product_type_id else "?"),
            activity=ACTIVITY_JOB_LABELS.get(activity_id, str(activity_id)),
            runs=runs,
            quantity=quantity,
            output_value=output_value,
            status=status,
            start_date=start_date,
            end_date=end_date,
            remaining_seconds=remaining,
            installer_name=installer_name or "?",
        ))
    rows.sort(key=lambda r: r.remaining_seconds if r.remaining_seconds is not None else float("inf"))
    return rows


def character_slot_overview() -> list[CharacterSlotRow]:
    """Per-character, per-slot-category count of currently active/paused/
    ready industry jobs installed by that character (corp jobs still draw on
    the installing character's own slots, same as the parent) - a
    deliberate reduction of the parent's total/free/excluded_from_planning
    version (see CharacterSlotRow's own docstring for why: no ESI
    character-skills pull exists here to derive a real total slot count
    from, and nothing in this repo's planner reads an
    excluded_from_planning-style flag either - plan_asset_optimized's slot-
    splitting isn't ported here, see SYNC.md). Still genuinely useful on its
    own: "how many jobs does each character have running right now, by
    category" doesn't need a total to answer.

    No storage.character_slots table exists locally (nothing to read a total
    from) - this derives entirely from storage.list_industry_jobs(), unlike
    the parent's version which merges that against a synced total-slots
    table."""
    used: dict[tuple[str, str], int] = {}
    characters: set[str] = set()
    for (_job_id, activity_id, _bp, _product, _name, _runs, _loc,
         status, _end, _start, installer_name) in storage.list_industry_jobs():
        if status not in ("active", "paused", "ready"):
            continue
        category = ACTIVITY_SLOT_CATEGORY.get(activity_id)
        if category is None or not installer_name:
            continue
        characters.add(installer_name)
        key = (installer_name, category)
        used[key] = used.get(key, 0) + 1

    rows = []
    for character_name in sorted(characters):
        for category in ("manufacturing", "reaction", "science"):
            used_count = used.get((character_name, category), 0)
            if used_count == 0:
                continue
            rows.append(CharacterSlotRow(
                character_name=character_name,
                job_type=SLOT_CATEGORY_LABELS[category],
                used_slots=used_count,
            ))
    return rows
