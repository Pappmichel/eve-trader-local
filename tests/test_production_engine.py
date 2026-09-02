"""Production activity-classification tests.

Nothing here touches the network. Everything runs against a real, tiny SQLite
SDE cache written through storage.replace_sde_data (same pattern as
test_candidate_discovery.py), so the two SDE queries classify_activity depends
on - get_blueprint_for_product and
find_invention_recipe_candidates_by_product_type_id - are exercised for real
rather than mocked away. That matters here more than usual: the whole point of
this module is that classification is driven by real SDE columns, never by a
name/metaLevel heuristic.
"""
from __future__ import annotations

import pytest

from eve_trader_local import storage
from eve_trader_local.production.constants import ACTIVITY_MODS
from eve_trader_local.production.engine import classify_activity


# type_ids/group_ids below are the real ones where a real item is named.
TRITANIUM = 34
RIFTER = 587                # Tech I frigate
DAMAGE_CONTROL_II = 2048    # Tech II module (metaGroupID 2, metaLevel 5)
MACHARIEL = 17738           # Faction battleship - metaLevel 8, metaGroupID 4
NESTOR = 33472              # Faction battleship - metaLevel 8, metaGroupID 4
ZORYAS_LIGHT = 52907        # Officer module (metaGroupID 5)
PURLOINED_ANALYZER = 30371  # Storyline item (metaGroupID 3)
DEADSPACE_MODULE = 99001    # Synthetic: metaGroupID 6 with a blueprint (see below)
LOKI_CORE = 45591           # T3 subsystem - metaLevel 1, but genuinely invented
TUNGSTEN_CARBIDE = 16672    # Reaction output
UNBUILDABLE = 99002         # Synthetic: no blueprint at all


def _seed_sde():
    """A tiny SDE cache covering one item per classification branch.

    metaLevel is deliberately set high (8) on the Faction hulls and on the
    Officer/Storyline/Deadspace items: the parent repo once classified those
    as Tech II from `metaLevel >= 2` alone, which is exactly the regression
    these rows exist to catch. Conversely the Loki subsystem carries
    metaLevel 1 despite being genuinely invented - the other half of why
    metaLevel is not usable for this at all.
    """
    storage.replace_sde_data(
        types=[
            # type_id, group_id, name, volume, published, market_group_id, meta_level, meta_group_id, portion_size
            (TRITANIUM, 18, "Tritanium", 0.01, 1, 100, 0, 1, 1),
            (RIFTER, 25, "Rifter", 27289.0, 1, 400, 0, 1, 1),
            (DAMAGE_CONTROL_II, 60, "Damage Control II", 5.0, 1, 200, 5, 2, 1),
            (MACHARIEL, 27, "Machariel", 486000.0, 1, 400, 8, 4, 1),
            (NESTOR, 27, "Nestor", 486000.0, 1, 400, 8, 4, 1),
            (ZORYAS_LIGHT, 7, "Zorya's Light Entropic Disintegrator", 10.0, 1, 200, 8, 5, 1),
            (PURLOINED_ANALYZER, 7, "Purloined Sansha Data Analyzer", 5.0, 1, 200, 8, 3, 1),
            (DEADSPACE_MODULE, 7, "Deadspace Module", 5.0, 1, 200, 8, 6, 1),
            (LOKI_CORE, 963, "Loki Core - Augmented Nuclear Reactor", 50.0, 1, 400, 1, 2, 1),
            (TUNGSTEN_CARBIDE, 18, "Tungsten Carbide", 0.01, 1, 100, 0, None, 1),
            (UNBUILDABLE, 18, "Drop-only Thing", 1.0, 1, 100, 8, 4, 1),
            # The blueprints/formulae themselves.
            (1000, 9, "Rifter Blueprint", 0.01, 1, None, 0, None, 1),
            (1001, 9, "Damage Control II Blueprint", 0.01, 1, None, 0, None, 1),
            (1002, 9, "Damage Control I Blueprint", 0.01, 1, None, 0, None, 1),
            (1003, 9, "Machariel Blueprint", 0.01, 1, None, 0, None, 1),
            (1004, 9, "Nestor Blueprint", 0.01, 1, None, 0, None, 1),
            (1005, 9, "Zorya's Light Blueprint", 0.01, 1, None, 0, None, 1),
            (1006, 9, "Purloined Analyzer Blueprint", 0.01, 1, None, 0, None, 1),
            (1007, 9, "Deadspace Module Blueprint", 0.01, 1, None, 0, None, 1),
            (1008, 9, "Loki Core Blueprint", 0.01, 1, None, 0, None, 1),
            (1009, 9, "Tungsten Carbide Reaction Formula", 0.01, 1, None, 0, None, 1),
            # Sleeper relic - a pseudo-blueprint row in the SDE, and the
            # invention source for the Loki subsystem.
            (1010, 34, "Intact Armor Nanobot", 10.0, 1, 500, 0, None, 1),
        ],
        groups=[(18, 4, "Mineral"), (60, 7, "Damage Control"), (25, 6, "Frigate"),
                (27, 6, "Battleship"), (7, 7, "Module"), (963, 6, "Strategic Cruiser"),
                (9, 9, "Blueprint"), (34, 34, "Ancient Relics")],
        market_groups=[(100, None, "Manufacture & Research"), (200, None, "Ship Equipment"),
                       (400, None, "Ships"), (500, None, "Ancient Relics")],
        blueprint_time=[],
        blueprint_materials=[],
        blueprint_products=[
            # blueprint_type_id, activity_id, product_type_id, quantity
            (1000, 1, RIFTER, 1),
            (1001, 1, DAMAGE_CONTROL_II, 1),
            (1003, 1, MACHARIEL, 1),
            (1004, 1, NESTOR, 1),
            (1005, 1, ZORYAS_LIGHT, 1),
            (1006, 1, PURLOINED_ANALYZER, 1),
            (1007, 1, DEADSPACE_MODULE, 1),
            (1008, 1, LOKI_CORE, 1),
            (1009, 11, TUNGSTEN_CARBIDE, 100),
            # Invention (activity 8): the T1 blueprint invents the T2
            # blueprint; the relic invents the T3 subsystem's blueprint.
            (1002, 8, 1001, 1),
            (1010, 8, 1008, 1),
        ],
        invention_probability=[(1002, 1001, 0.34), (1010, 1008, 0.26)],
        categories=[(4, "Material"), (7, "Module"), (6, "Ship"),
                    (9, "Blueprint"), (34, "Ancient Relics")],
    )


@pytest.fixture
def sde(db):
    _seed_sde()
    return db


# --------------------------------------------------------------- basic branches
def test_tech_i_item_classifies_as_tech_i(sde):
    activity, bp = classify_activity(RIFTER)

    assert activity == "Tech I"
    assert bp == (1000, 1, 1)


def test_item_without_a_blueprint_is_an_input(sde):
    assert classify_activity(TRITANIUM) == ("Input", None)
    assert classify_activity(UNBUILDABLE) == ("Input", None)


def test_reaction_is_decided_by_activity_id_not_by_meta_group(sde):
    activity, bp = classify_activity(TUNGSTEN_CARBIDE)

    assert activity == "Reaction"
    assert bp == (1009, 11, 100)


# ------------------------------------------------------------------- invention
def test_tech_ii_is_detected_via_the_invention_recipe(sde):
    # metaLevel 5 here, but that is not what decides it - the T1 blueprint's
    # activity_id=8 row pointing at this item's blueprint is.
    activity, bp = classify_activity(DAMAGE_CONTROL_II)

    assert activity == "Tech II"
    assert bp == (1001, 1, 1)


def test_invented_tech_iii_subsystem_is_tech_ii_despite_meta_level_1(sde):
    """A Loki subsystem is metaLevel 1 but genuinely invented (from a Sleeper
    relic, via the same activity_id=8 Invention Tech II uses - CCP removed the
    old relic-based Reverse Engineering mechanic years ago). Any metaLevel-
    based rule would miss this one entirely."""
    activity, _bp = classify_activity(LOKI_CORE)

    assert activity == "Tech II"


def test_invention_lookup_returns_empty_for_a_non_invented_blueprint(sde):
    assert storage.find_invention_recipe_candidates_by_product_type_id(1003) == ()
    assert storage.find_invention_recipe_candidates_by_product_type_id(1001) == (1002,)


# ------------------------------------------------- Machariel/Nestor regression
@pytest.mark.parametrize("type_id, expected", [
    (MACHARIEL, "Faction"),
    (NESTOR, "Faction"),
    (ZORYAS_LIGHT, "Officer"),
    (PURLOINED_ANALYZER, "Storyline"),
    (DEADSPACE_MODULE, "Deadspace"),
])
def test_high_meta_level_non_invented_items_are_never_tech_ii(sde, type_id, expected):
    """The regression this module's whole shape exists for: the parent repo
    once classified anything with `metaLevel >= 2` as Tech II, which swept in
    every Faction/Pirate hull (Machariel and Nestor are both metaLevel 8),
    Officer/Deadspace module and Storyline item - 854 of 4208 scanned items in
    one live scan. None of them are decryptor-invented; each is built from its
    own real blueprint. They must be labelled from their real SDE metaGroupID
    (4/5/3/6) instead."""
    activity, bp = classify_activity(type_id)

    assert activity == expected
    assert bp is not None


def test_the_four_meta_group_labels_share_faction_me_te_treatment():
    """All four are ME0/TE0 and non-researchable in real EVE, unlike Tech I
    (researchable to ME10/TE20) - so the label distinction must never leak
    into different ME/TE handling."""
    faction = ACTIVITY_MODS["Faction"]
    for label in ("Faction", "Storyline", "Officer", "Deadspace"):
        assert ACTIVITY_MODS[label] == faction
    assert (faction.material_multiplier, faction.time_multiplier) == (1.0, 1.0)
    assert ACTIVITY_MODS["Tech I"].material_multiplier == 0.90


# ----------------------------------------------- get_blueprint_for_product
def test_unpublished_blueprints_are_excluded(sde):
    """A leftover unpublished CCP test/legacy blueprint row must not make an
    item look buildable (or worse, be chosen over the real published one)."""
    storage.replace_sde_data(
        types=[
            (777, 18, "Only-Unpublished Product", 1.0, 1, 100, 0, None, 1),
            (778, 9, "Test Reaction Blueprint", 0.01, 0, None, 0, None, 1),
        ],
        groups=[(18, 4, "Mineral"), (9, 9, "Blueprint")],
        market_groups=[(100, None, "Manufacture & Research")],
        blueprint_time=[], blueprint_materials=[],
        blueprint_products=[(778, 11, 777, 100)],
        categories=[(4, "Material"), (9, "Blueprint")],
    )

    assert storage.get_blueprint_for_product(777) is None
    assert classify_activity(777) == ("Input", None)
