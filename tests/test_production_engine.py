"""Production classification and buy-vs-build cost tests.

Nothing here touches the network. Everything runs against a real, tiny SQLite
SDE cache written through storage.replace_sde_data (same pattern as
test_candidate_discovery.py), so the two SDE queries classify_activity depends
on - get_blueprint_for_product and
find_invention_recipe_candidates_by_product_type_id - are exercised for real
rather than mocked away. That matters here more than usual: the whole point of
this module is that classification is driven by real SDE columns, never by a
name/metaLevel heuristic. The build-cost half (further down) runs against its
own small multi-level BOM seeded the same way, so ME rounding, the job-fee
formula and the recursive walk are exercised end to end without the network.
"""
from __future__ import annotations

import pytest

from eve_trader_local import storage
from eve_trader_local.goonmetrics_client import CurrentPrice
from eve_trader_local.production import engine
from eve_trader_local.production.config import ProductionConfig
from eve_trader_local.production.constants import ACTIVITY_MODS, SCC_SURCHARGE_RATE
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


# =============================================================== build cost math
# Everything below runs against its own tiny synthetic BOM (a ship built from a
# component built from a mineral, plus one invented Tech II item), seeded the
# same way as above through storage.replace_sde_data - so the recursive walk,
# the ME rounding and the job-fee formula are all exercised against real SQLite
# reads rather than mocked-out blueprint lookups.
MINERAL = 34
SHIP = 90001            # built from 10 components + 20 minerals
COMPONENT = 90002       # group 873 - one of COMPONENT_GROUP_IDS, so it takes the
                        # "component" structure/rig and cost-index profile
T2_WIDGET = 90003       # invented, built from 50 minerals
DATACORE = 90004
AUGMENTATION = 34203    # the one decryptor given a price below
NULLSEC_SYSTEM = 30000001
HIGHSEC_SYSTEM = 30000002

SHIP_BP, COMPONENT_BP, T2_BP, T1_BP = 91001, 91002, 91003, 91004


def _seed_build_tree():
    storage.replace_sde_data(
        types=[
            (MINERAL, 18, "Tritanium", 0.01, 1, 100, 0, 1, 1),
            (SHIP, 25, "Test Frigate", 27289.0, 1, 400, 0, 1, 1),
            (COMPONENT, 873, "Test Capital Component", 100.0, 1, 400, 0, 1, 1),
            (T2_WIDGET, 18, "Test T2 Widget", 5.0, 1, 200, 5, 2, 1),
            (DATACORE, 18, "Test Datacore", 0.1, 1, 200, 0, 1, 1),
            (AUGMENTATION, 18, "Data Sheet - Augmentation", 0.1, 1, 200, 0, 1, 1),
            (SHIP_BP, 9, "Test Frigate Blueprint", 0.01, 1, None, 0, None, 1),
            (COMPONENT_BP, 9, "Test Capital Component Blueprint", 0.01, 1, None, 0, None, 1),
            (T2_BP, 9, "Test T2 Widget Blueprint", 0.01, 1, None, 0, None, 1),
            (T1_BP, 9, "Test T1 Widget Blueprint", 0.01, 1, None, 0, None, 1),
        ],
        groups=[(18, 4, "Mineral"), (25, 6, "Frigate"),
                (873, 4, "Capital Construction Components"), (9, 9, "Blueprint")],
        market_groups=[(100, None, "Manufacture & Research"), (200, None, "Ship Equipment"),
                       (400, None, "Ships")],
        blueprint_time=[],
        blueprint_materials=[
            (SHIP_BP, 1, COMPONENT, 10),
            (SHIP_BP, 1, MINERAL, 20),
            (COMPONENT_BP, 1, MINERAL, 100),
            (T2_BP, 1, MINERAL, 50),
            (T1_BP, 8, DATACORE, 2),   # activity 8's "materials" are the datacores
        ],
        blueprint_products=[
            (SHIP_BP, 1, SHIP, 1),
            (COMPONENT_BP, 1, COMPONENT, 1),
            (T2_BP, 1, T2_WIDGET, 1),
            (T1_BP, 8, T2_BP, 1),      # base_runs 1 on the invented BPC
        ],
        invention_probability=[(T1_BP, T2_BP, 0.3)],
        solar_systems=[(NULLSEC_SYSTEM, "Test Null", -0.1, 10000001),
                       (HIGHSEC_SYSTEM, "Test High", 0.9, 10000002)],
        categories=[(4, "Material"), (6, "Ship"), (9, "Blueprint")],
    )
    # The ship is category 6, so _haul_volume would otherwise ask ESI for its
    # packaged volume - seeding the cache keeps every test here offline.
    storage.set_cached_packaged_volume(SHIP, 2500.0)


@pytest.fixture
def tree(db):
    _seed_build_tree()
    return db


def _cfg(**overrides) -> ProductionConfig:
    """A deliberately fee-free config: broker fee, haul cost and facility tax
    all zero, so each test's expected number isolates the one mechanic it is
    actually about. Tests that care about a fee set it explicitly."""
    cfg = ProductionConfig(jita_buy_broker_fee=0.0, haul_cost_per_m3=0.0,
                           facility_tax_rate=0.0, market_fees=0.0)
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


def _prices(by_type: dict[int, float]) -> dict[int, CurrentPrice]:
    return {tid: CurrentPrice(type_id=tid, updated="", buy=sell * 0.9, sell=sell)
            for tid, sell in by_type.items()}


# Home has a sell order for everything the cost math needs to price; the ship
# itself is deliberately absent, so its own cost can only come from building.
HOME = _prices({MINERAL: 5.0, COMPONENT: 600.0, DATACORE: 100.0,
                AUGMENTATION: 50.0, T2_WIDGET: 900.0})
ADJUSTED = {MINERAL: 4.0, COMPONENT: 500.0, DATACORE: 90.0}
# Two configured systems: the component profile's index is deliberately double
# the manufacturing one, so a test can tell which profile was used.
COST_INDICES = {"component": {"manufacturing": 0.10, "reaction": 0.10},
                "manufacturing": {"manufacturing": 0.05, "reaction": 0.05}}


# ------------------------------------------------------- EVE material rounding
@pytest.mark.parametrize("base_qty, mult, runs, expected", [
    # A base_qty=1 material is never ME-reduced at all: the per-batch floor is
    # `runs` itself, no matter how large the reduction.
    (1, 0.90, 1, 1),
    (1, 0.50, 10, 10),
    # Rounded UP once for the whole batch, not per run: 100 x 0.9 x 3 = 270
    # exactly, while 3 separate runs of ceil(90) would also be 270 - but
    # 7 x 0.9 x 3 = 18.9 rounds to 19 as a batch, vs 3 x ceil(6.3) = 21.
    (100, 0.90, 3, 270),
    (7, 0.90, 3, 19),
])
def test_material_qty_rounds_up_per_batch_and_floors_at_runs(base_qty, mult, runs, expected):
    assert engine._material_qty(base_qty, mult, runs) == expected


# --------------------------------------------------------------- ME/TE stacking
def test_activity_mods_stacks_structure_and_rig_on_the_blueprint_baseline(tree):
    cfg = _cfg(manufacturing_structure_type="Sotiyo (XL Engineering Complex)",
               manufacturing_rig_tier="T2-Rig", manufacturing_system_id=HIGHSEC_SYSTEM)

    material_mult, time_mult, _rate = engine._activity_mods("Tech I", SHIP, cfg, {})

    # Highsec: the rig bonus is not scaled at all (1x), so ME is Tech I's own
    # 0.90 baseline x Sotiyo's 0.99 x the T2 rig's 2.4%.
    assert material_mult == pytest.approx(0.90 * 0.99 * (1 - 0.024))
    assert time_mult == pytest.approx(0.80 * 0.70 * (1 - 0.24))


def test_rig_bonus_scales_with_the_systems_security(tree):
    cfg = _cfg(manufacturing_structure_type="Sotiyo (XL Engineering Complex)",
               manufacturing_rig_tier="T2-Rig", manufacturing_system_id=NULLSEC_SYSTEM)

    material_mult, _time_mult, _rate = engine._activity_mods("Tech I", SHIP, cfg, {})

    # Null-sec multiplies an Engineering Complex rig's bonus by 2.1.
    assert material_mult == pytest.approx(0.90 * 0.99 * (1 - 0.024 * 2.1))


def test_component_groups_use_the_component_structure_not_the_manufacturing_one(tree):
    cfg = _cfg(component_structure_type="Raitaru (M Engineering Complex)",
               component_rig_tier="T1-Rig", component_system_id=HIGHSEC_SYSTEM,
               manufacturing_structure_type="Sotiyo (XL Engineering Complex)",
               manufacturing_rig_tier="T2-Rig", manufacturing_system_id=HIGHSEC_SYSTEM)

    assert engine._structure_profile("Tech I", COMPONENT) == "component"
    assert engine._structure_profile("Tech I", SHIP) == "manufacturing"
    material_mult, _time_mult, _rate = engine._activity_mods("Tech I", COMPONENT, cfg, {})
    assert material_mult == pytest.approx(0.90 * 0.99 * (1 - 0.02))


def test_faction_blueprints_get_no_research_baseline_at_all(tree):
    """Faction/Storyline/Officer/Deadspace blueprints are fixed at ME0/TE0 in
    real EVE - only the structure/rig layer may reduce them."""
    material_mult, time_mult, _rate = engine._activity_mods("Faction", SHIP, _cfg(), {})

    assert (material_mult, time_mult) == (1.0, 1.0)


# ------------------------------------------------------- owned-BPO ME/TE
def _own_bpo(blueprint_type_id: int, me: int, te: int, runs: int = -1, quantity: int = 1):
    """One owned blueprint row, as production/esi_sync.py would have written it.
    runs = -1 is ESI's Original marker; a copy carries a real run count and
    quantity -2 instead."""
    storage.replace_blueprints(
        "character_blueprints",
        [(500 + blueprint_type_id, blueprint_type_id, 60003760, "Hangar", quantity, me, te, runs)],
    )


def test_a_researched_owned_bpo_replaces_the_perfect_research_assumption(tree):
    """Tech I's flat baseline assumes ME10/TE20. Once an ESI sync has recorded
    the BPO you actually own, its real research level wins."""
    _own_bpo(SHIP_BP, me=4, te=8)

    material_mult, time_mult, _rate = engine._activity_mods("Tech I", SHIP, _cfg(), {}, SHIP_BP)

    assert (material_mult, time_mult) == pytest.approx((0.96, 0.92))


def test_without_a_synced_blueprint_the_flat_baseline_still_applies(tree):
    material_mult, time_mult, _rate = engine._activity_mods("Tech I", SHIP, _cfg(), {}, SHIP_BP)

    assert (material_mult, time_mult) == pytest.approx((0.90, 0.80))


def test_owned_bpo_me_is_ignored_without_a_blueprint_id(tree):
    """The blueprint_id argument is what makes the lookup possible at all - a
    call site with none to hand keeps the flat baseline."""
    _own_bpo(SHIP_BP, me=0, te=0)

    material_mult, _time_mult, _rate = engine._activity_mods("Tech I", SHIP, _cfg(), {})

    assert material_mult == pytest.approx(0.90)


def test_a_reaction_never_uses_owned_bpo_research(tree):
    """A real EVE mechanic, not a simplification: reaction formulas have no
    research at all, so an owned row must not change anything."""
    _own_bpo(SHIP_BP, me=0, te=0)

    material_mult, _time_mult, _rate = engine._activity_mods("Reaction", SHIP, _cfg(), {}, SHIP_BP)

    assert material_mult == pytest.approx(ACTIVITY_MODS["Reaction"].material_multiplier)


def test_an_unresearched_owned_bpo_raises_the_real_build_cost(tree):
    """The end-to-end proof that this reaches a real number: the same component
    costs more to build once the ME0 BPO you actually own replaces the assumed
    perfectly-researched one, because 100 minerals are consumed instead of 90."""
    baseline = engine._unit_cost(COMPONENT, _cfg(), HOME, {}, {}, {}, {}, COST_INDICES, ADJUSTED)
    assert baseline == pytest.approx(_component_build_cost())

    _own_bpo(COMPONENT_BP, me=0, te=0)
    owned = engine._unit_cost(COMPONENT, _cfg(), HOME, {}, {}, {}, {}, COST_INDICES, ADJUSTED)

    # Only the material half moves - the job fee is priced off ME-0 quantities
    # either way, so it is unchanged.
    assert owned == pytest.approx(_component_build_cost() + 10 * 5.0)


def test_a_blueprint_copy_never_overrides_the_baseline(tree):
    """A BPC's ME/TE was set by whoever copied it, not by your own research."""
    _own_bpo(COMPONENT_BP, me=0, te=0, runs=30, quantity=-2)

    cost = engine._unit_cost(COMPONENT, _cfg(), HOME, {}, {}, {}, {}, COST_INDICES, ADJUSTED)

    assert cost == pytest.approx(_component_build_cost())


# ------------------------------------------------------------------- job cost
def test_job_cost_rate_adds_tax_and_surcharge_on_top_of_the_scaled_index(tree):
    """EVE's real formula is index x structure_bonus + facility_tax + SCC
    surcharge - the two flat terms are additive, never folded into the index."""
    cfg = _cfg(facility_tax_rate=0.0025,
               manufacturing_structure_type="Sotiyo (XL Engineering Complex)")

    rate = engine._job_cost_rate("Tech I", SHIP, cfg, COST_INDICES)

    assert rate == pytest.approx(0.05 * 0.95 + 0.0025 + SCC_SURCHARGE_RATE)


def test_job_cost_rate_uses_the_component_system_for_component_groups(tree):
    rate = engine._job_cost_rate("Tech I", COMPONENT, _cfg(), COST_INDICES)

    assert rate == pytest.approx(0.10 + SCC_SURCHARGE_RATE)


def test_job_cost_rate_falls_back_to_the_flat_rate_without_a_live_index(tree):
    rate = engine._job_cost_rate("Tech I", SHIP, _cfg(), {})

    assert rate == pytest.approx(ACTIVITY_MODS["Tech I"].job_cost_rate + SCC_SURCHARGE_RATE)


def test_a_manual_cost_index_override_wins_over_the_looked_up_index(tree):
    cfg = _cfg(manufacturing_cost_index_override=0.5)

    rate = engine._job_cost_rate("Tech I", SHIP, cfg, COST_INDICES)

    assert rate == pytest.approx(0.5 + SCC_SURCHARGE_RATE)


# -------------------------------------------------------------- Tech II ME/TE
def test_tech_ii_mods_price_the_best_decryptor_and_use_its_me(tree):
    """Only the Augmentation decryptor has a price here, and its +9 runs make it
    far cheaper per BPC run than inventing with no decryptor at all - so its
    ME0 (a 1.00 multiplier, not the flat Tech II 0.98 fallback) must be what
    the build math then uses."""
    cfg = _cfg()

    material_mult, _time_mult, decryptor, chosen = engine._tech_ii_mods(
        T2_WIDGET, T2_BP, 1, cfg, HOME, {}, {}, {})

    assert decryptor == "Augmentation"
    assert chosen is not None and chosen.output_runs == 10
    assert material_mult == pytest.approx(1.0)


def test_a_manually_selected_decryptor_overrides_the_economic_choice(tree):
    material_mult, _time_mult, decryptor, _chosen = engine._tech_ii_mods(
        T2_WIDGET, T2_BP, 1, _cfg(), HOME, {}, {T2_WIDGET: "Process"}, {})

    assert decryptor == "Process"
    assert material_mult == pytest.approx(1 - 5 / 100)   # Process gives the BPC ME5


def test_tech_ii_mods_are_memoized_per_type(tree):
    memo: dict = {}
    first = engine._tech_ii_mods(T2_WIDGET, T2_BP, 1, _cfg(), HOME, {}, {}, memo)

    assert memo[T2_WIDGET] == first
    # A second call with prices that would now resolve differently still
    # returns the memoized answer - the memo is what makes the recursive walk
    # affordable at all.
    assert engine._tech_ii_mods(T2_WIDGET, T2_BP, 1, _cfg(), {}, {}, {}, memo) == first


# ------------------------------------------------------- recursive build cost
def _component_build_cost() -> float:
    materials = engine._material_qty(100, 0.90, 1) * 5.0    # 90 minerals at 5.0
    job = (100 * ADJUSTED[MINERAL]) * (0.10 + SCC_SURCHARGE_RATE)
    return materials + job


def _ship_build_cost() -> float:
    materials = (engine._material_qty(10, 0.90, 1) * _component_build_cost()
                 + engine._material_qty(20, 0.90, 1) * 5.0)
    eiv = 10 * ADJUSTED[COMPONENT] + 20 * ADJUSTED[MINERAL]
    return materials + eiv * (0.05 + SCC_SURCHARGE_RATE)


def test_unit_cost_expands_the_whole_tree_and_prices_the_job_fee_off_eiv(tree):
    """The ship has no sell order anywhere, so its cost is purely built - from
    components that are themselves cheaper to build (506 vs the 600 they list
    for), from minerals that are only ever bought."""
    cost = engine._unit_cost(SHIP, _cfg(), HOME, {}, {}, {}, {}, COST_INDICES, ADJUSTED)

    assert cost == pytest.approx(_ship_build_cost())


def test_unit_cost_takes_the_cheaper_of_buy_and_build_at_every_level(tree):
    memo: dict = {}
    engine._unit_cost(SHIP, _cfg(), HOME, {}, memo, {}, {}, COST_INDICES, ADJUSTED)

    # The component is cheaper to build than its 600 ISK sell order...
    assert memo[COMPONENT] == pytest.approx(_component_build_cost())
    # ...while the mineral has no blueprint at all and is simply bought.
    assert memo[MINERAL] == pytest.approx(5.0)


def test_a_listed_item_cheaper_than_building_it_is_costed_at_its_buy_price(tree):
    home = dict(HOME)
    home[COMPONENT] = CurrentPrice(type_id=COMPONENT, updated="", buy=90.0, sell=100.0)

    cost = engine._unit_cost(COMPONENT, _cfg(), home, {}, {}, {}, {}, COST_INDICES, ADJUSTED)

    assert cost == pytest.approx(100.0)


def test_an_unpriceable_material_collapses_the_build_rather_than_costing_zero(tree):
    """A mineral with no sell order anywhere must not be silently free - the
    build simply can't be costed, and falls back to the item's own buy price
    (None here, since the ship isn't listed either)."""
    home = {tid: quote for tid, quote in HOME.items() if tid != MINERAL}

    assert engine._unit_cost(SHIP, _cfg(), home, {}, {}, {}, {}, COST_INDICES, ADJUSTED) is None


def test_product_quantity_per_run_divides_the_run_cost(tree):
    """A blueprint producing 5 units per run costs a fifth as much per unit."""
    storage.replace_sde_data(
        types=[(MINERAL, 18, "Tritanium", 0.01, 1, 100, 0, 1, 1),
               (COMPONENT, 873, "Test Capital Component", 100.0, 1, 400, 0, 1, 1),
               (COMPONENT_BP, 9, "Test Capital Component Blueprint", 0.01, 1, None, 0, None, 1)],
        groups=[(18, 4, "Mineral"), (873, 4, "Capital Construction Components"), (9, 9, "Blueprint")],
        market_groups=[(100, None, "Manufacture & Research"), (400, None, "Ships")],
        blueprint_time=[], blueprint_materials=[(COMPONENT_BP, 1, MINERAL, 100)],
        blueprint_products=[(COMPONENT_BP, 1, COMPONENT, 5)],
        categories=[(4, "Material"), (9, "Blueprint")],
    )

    cost = engine._unit_cost(COMPONENT, _cfg(), _prices({MINERAL: 5.0}), {},
                             {}, {}, {}, COST_INDICES, ADJUSTED)

    assert cost == pytest.approx(_component_build_cost() / 5)


def test_unit_cost_detail_reports_buy_and_build_side_by_side(tree):
    best, build_cost, buy = engine.unit_cost_detail(
        COMPONENT, _cfg(), HOME, {}, {}, {}, {}, COST_INDICES, ADJUSTED)

    assert build_cost == pytest.approx(_component_build_cost())
    assert buy == pytest.approx(600.0)
    assert best == pytest.approx(build_cost)


# ---------------------------------------------------------- buy/build decision
def test_buy_or_build_follows_the_modeled_cost(tree):
    cfg = _cfg()
    memo: dict = {}
    engine._unit_cost(COMPONENT, cfg, HOME, {}, memo, {}, {}, COST_INDICES, ADJUSTED)
    _activity, bp = classify_activity(COMPONENT)

    assert engine._buy_or_build_decision(COMPONENT, cfg, HOME, {}, {}, memo, bp) == "Build"
    assert engine._buy_or_build_decision(MINERAL, cfg, HOME, {}, {}, memo, None) == "Buy"


def test_a_manual_override_beats_the_modeled_cost(tree):
    cfg = _cfg()
    memo: dict = {}
    engine._unit_cost(COMPONENT, cfg, HOME, {}, memo, {}, {}, COST_INDICES, ADJUSTED)
    _activity, bp = classify_activity(COMPONENT)

    assert engine._buy_or_build_decision(
        COMPONENT, cfg, HOME, {}, {COMPONENT: "Buy"}, memo, bp) == "Buy"


# ------------------------------------------------------------------- margins
def test_margin_home_is_net_of_market_fees(tree):
    cfg = _cfg(market_fees=0.05)
    home = _prices({SHIP: 8000.0})

    margin = engine.margin_home(SHIP, 5000.0, home, cfg)

    assert margin == pytest.approx((8000.0 * 0.95 - 5000.0) / 5000.0)


def test_margin_jita_also_subtracts_the_export_haul_cost(tree):
    cfg = _cfg(market_fees=0.05, haul_cost_per_m3=2.0)
    jita = _prices({SHIP: 8000.0})

    margin = engine.margin_jita(SHIP, 5000.0, jita, cfg)

    # 2500 m3 packaged (not the ship's 27289 m3 assembled volume) x 2 ISK/m3.
    assert margin == pytest.approx((8000.0 * 0.95 - 2500.0 * 2.0 - 5000.0) / 5000.0)


def test_margin_is_none_without_a_quote_or_a_build_cost(tree):
    assert engine.margin_home(SHIP, 5000.0, {}, _cfg()) is None
    assert engine.margin_home(SHIP, None, _prices({SHIP: 8000.0}), _cfg()) is None
    assert engine.margin_home(SHIP, 0.0, _prices({SHIP: 8000.0}), _cfg()) is None


# ------------------------------------------------------ bill-of-materials walks
def test_material_tree_expands_every_level_with_me_reduced_quantities(tree):
    tree_root = engine.build_material_tree(SHIP, 1, _cfg(), HOME, {}, {}, {})

    assert tree_root["activity"] == "Tech I"
    children = {child["type_id"]: child for child in tree_root["children"]}
    assert children[COMPONENT]["quantity"] == engine._material_qty(10, 0.90, 1)
    assert children[MINERAL]["quantity"] == engine._material_qty(20, 0.90, 1)
    # The component is itself expanded, with its own ME applied to its 100
    # minerals x however many component runs the ship needs.
    component_runs = children[COMPONENT]["quantity"]
    grandchild = children[COMPONENT]["children"][0]
    assert grandchild["type_id"] == MINERAL
    assert grandchild["quantity"] == engine._material_qty(100, 0.90, component_runs)
    # A leaf with no blueprint is a Buy, not a job.
    assert grandchild["children"] == [] and grandchild["activity"] == "Buy"


def test_material_tree_records_the_chosen_decryptor(tree):
    node = engine.build_material_tree(T2_WIDGET, 1, _cfg(), HOME, {}, {}, {})

    assert node["activity"] == "Tech II"
    assert node["decryptor"] == "Augmentation"


def test_structural_material_closure_ignores_economics(tree):
    """The closure exists to bound what needs *pricing*, so it must include a
    node even when buying it would be cheaper than building it."""
    assert engine.structural_material_closure([SHIP]) == {SHIP, COMPONENT, MINERAL}
    assert engine.structural_material_closure([MINERAL]) == {MINERAL}


def test_recursive_walks_stop_at_max_depth(tree, monkeypatch):
    """A blueprint whose materials transitively include its own product would
    otherwise recurse forever - MAX_DEPTH is the guard, and it must hold for
    the cost walk and the tree walk alike."""
    storage.replace_sde_data(
        types=[(MINERAL, 18, "Tritanium", 0.01, 1, 100, 0, 1, 1),
               (COMPONENT, 873, "Test Capital Component", 100.0, 1, 400, 0, 1, 1),
               (COMPONENT_BP, 9, "Test Capital Component Blueprint", 0.01, 1, None, 0, None, 1)],
        groups=[(18, 4, "Mineral"), (873, 4, "Capital Construction Components"), (9, 9, "Blueprint")],
        market_groups=[(100, None, "Manufacture & Research"), (400, None, "Ships")],
        blueprint_time=[],
        # The component is built from itself.
        blueprint_materials=[(COMPONENT_BP, 1, COMPONENT, 2)],
        blueprint_products=[(COMPONENT_BP, 1, COMPONENT, 1)],
        categories=[(4, "Material"), (9, "Blueprint")],
    )

    assert engine.structural_material_closure([COMPONENT]) == {COMPONENT}
    node = engine.build_material_tree(COMPONENT, 1, _cfg(), HOME, {}, {}, {})
    depth = 0
    while node["children"]:
        node = node["children"][0]
        depth += 1
    assert depth == engine.MAX_DEPTH
    # And the cost walk terminates rather than blowing the stack.
    assert engine._unit_cost(COMPONENT, _cfg(), HOME, {}, {}, {}, {},
                             COST_INDICES, ADJUSTED) == pytest.approx(600.0)
