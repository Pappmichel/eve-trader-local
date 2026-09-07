"""Manual industry-job-slot totals joined with used counts from ESI jobs.

Phase E.5. Does not request character-skills, does not change
jobs.character_slot_overview or plan_production.
"""
from __future__ import annotations

from .. import storage
from .constants import ACTIVITY_SLOT_CATEGORY, SLOT_CATEGORY_LABELS
from .models import JobSlotCapacityRow

_CATEGORIES = ("manufacturing", "reaction", "science")


def used_slot_counts() -> dict[tuple[str, str], int]:
    used: dict[tuple[str, str], int] = {}
    for (_job_id, activity_id, _bp, _product, _name, _runs, _loc,
         status, _end, _start, installer_name) in storage.list_industry_jobs():
        if status not in ("active", "paused", "ready"):
            continue
        category = ACTIVITY_SLOT_CATEGORY.get(activity_id)
        if category is None or not installer_name:
            continue
        key = (installer_name, category)
        used[key] = used.get(key, 0) + 1
    return used


def overview() -> list[JobSlotCapacityRow]:
    used = used_slot_counts()
    totals = storage.load_character_job_slot_totals()
    characters = {name for name, _cat in used} | set(totals)
    rows: list[JobSlotCapacityRow] = []
    for character_name in sorted(characters):
        stored = totals.get(character_name)
        for category in _CATEGORIES:
            used_count = used.get((character_name, category), 0)
            total = stored[category] if stored is not None else None
            if total is None and used_count == 0:
                continue
            free = max(0, total - used_count) if total is not None else None
            rows.append(JobSlotCapacityRow(
                character_name=character_name,
                job_type=SLOT_CATEGORY_LABELS[category],
                used_slots=used_count,
                total_slots=total,
                free_slots=free,
            ))
    return rows
