"""Production's SDE-driven activity classification.

Only `classify_activity` is ported so far - the buy-vs-build/pricing/invention
machinery around it in the parent repo's `eve_trader/production/engine.py`
needs a `ProductionConfig` and Goonmetrics pricing that don't exist here yet
(see SYNC.md). Classification itself is a pure function of the local SDE
cache: no config, no network, no ESI.
"""
from __future__ import annotations

from typing import Optional

from .. import storage
from .constants import (
    ACTIVITY_REACTION, DEADSPACE_META_GROUP_ID, FACTION_META_GROUP_ID,
    OFFICER_META_GROUP_ID, STORYLINE_META_GROUP_ID,
)


def classify_activity(type_id: int) -> tuple[str, Optional[tuple[int, int, float]]]:
    """Returns (activity_label, blueprint_info). blueprint_info is
    (blueprint_type_id, activity_id, product_qty_per_run), or None if `type_id`
    has no known Manufacturing/Reaction formula (must be bought).

    "Tech II" (meaning: decryptor-invented) is decided *solely* by whether a
    real invention recipe exists for the blueprint (storage.
    find_invention_recipe_candidates_by_product_type_id) - never by metaLevel.
    This used to be "metaLevel>=2 OR has an invention recipe" to catch Tech III
    hulls/subsystems, whose metaLevel doesn't reliably track "genuinely
    invented" (confirmed against real SDE data: a Loki hull is metaLevel 5, but
    its "Loki Core - Augmented Nuclear Reactor" subsystem is metaLevel 1, even
    though it's genuinely invented via a real T1-equivalent blueprint) - but
    that metaLevel>=2 fallback turned out to be both unnecessary
    (find_invention_recipe_candidates_by_product_type_id already correctly
    catches the Loki Core case directly, no metaLevel needed) and actively
    wrong elsewhere: confirmed live (2026-07-16) that metaLevel>=2 also covers
    every Faction/Pirate ship (Machariel, Nestor: metaLevel 8),
    Officer/Deadspace module, and faction booster/drug - none of which are
    actually decryptor-invented (the invention-recipe lookup correctly returns
    an empty tuple for all of them; they're built from their own real,
    directly-researchable blueprint, same as a Tech I item, just not obtainable
    from an NPC seller). The metaLevel branch alone misclassified 854 of 4208
    scanned manufacturable items as "Tech II" in a live scan - each one then
    got priced off the flat Tech II ACTIVITY_MODS ME/TE baseline (and shown as
    "Tech II" in the UI) instead of the correct Tech I-style baseline (owned
    BPO ME/TE if you have it, else the flat "perfect BPO" assumption).

    Tech III uses the exact same activity_id=8 Invention this tool already
    models for Tech II (CCP removed the old relic-based "Reverse Engineering"
    mechanic years ago - confirmed empirically, the current SDE has zero rows
    for activity_id 7) for probability/runs/materials - but its *input* is a
    Sleeper relic (Intact/Malfunctioning/Wrecked, one of 3 grades), never a
    real T1 blueprint, which DOES need its own separate handling elsewhere
    (buy-list/logistics routing, cost pricing, grade optimization - see
    constants.ANCIENT_RELIC_CATEGORY_ID; none of that is ported here yet).

    Non-invented items get a second check against the SDE's real metaGroupID
    (storage.get_sde_type, populated from Fuzzwork's invMetaTypes.csv - see
    sde.py): "Faction" (confirmed with the user, 2026-07-16 - "Faction items
    are de facto T1 items, but add a separate category for them"), extended
    2026-07-17 to "Storyline" and "Officer" the same way (both confirmed to
    have real, blueprint-backed, market-priceable products in the parent app's
    own cached SDE - not just SDE rows that happen to exist but are never
    reachable here) and "Deadspace" for completeness (currently unreachable -
    zero blueprint-backed Deadspace products exist in the SDE, deadspace
    modules are drop-only - but costs nothing to check for a future SDE that
    might add one). Falls back to plain "Tech I" if metaGroupID matches none of
    these. This is a *label* distinction for the user's own tracking/filtering,
    not a purely cosmetic one: all four of these blueprint types are fixed at
    ME0/TE0 and can never be researched or changed at all (Faction confirmed
    with the user 2026-07-16; Storyline/Officer share the same
    non-researchable treatment by the same reasoning - see
    constants.ACTIVITY_MODS) - unlike a genuine Tech I BPO, which CAN be
    researched up to ME10/TE20 - so none of them get Tech I's "assumes perfect
    research, or your real owned BPO's ME/TE if better" treatment."""
    bp = storage.get_blueprint_for_product(type_id)
    if bp is None:
        return "Input", None
    blueprint_id, activity_id, _ = bp
    if activity_id == ACTIVITY_REACTION:
        return "Reaction", bp
    if storage.find_invention_recipe_candidates_by_product_type_id(blueprint_id):
        return "Tech II", bp
    sde_type = storage.get_sde_type(type_id)
    meta_group_id = sde_type[7] if sde_type else None
    meta_group_labels = {
        FACTION_META_GROUP_ID: "Faction",
        STORYLINE_META_GROUP_ID: "Storyline",
        OFFICER_META_GROUP_ID: "Officer",
        DEADSPACE_META_GROUP_ID: "Deadspace",
    }
    return meta_group_labels.get(meta_group_id, "Tech I"), bp
