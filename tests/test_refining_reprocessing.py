"""Reprocessing-yield math tests (GitHub issue #90, "Ore & Minerals").

Runs against a real, tiny SQLite SDE cache (storage.replace_sde_data), same
pattern as test_production_engine.py - portion-size batching and
get_type_materials are exercised for real, not mocked away.
"""
from __future__ import annotations

from eve_trader_local import storage
from eve_trader_local.refining.config import RefiningConfig
from eve_trader_local.refining.reprocessing import (
    apply_reprocessing_yield,
    ore_ice_base_yield,
    ore_ice_yield,
    scrapmetal_yield,
)

VELDSPAR = 1230
TRITANIUM = 34
DAMAGED_ARMOR_PLATE = 99101  # synthetic scrapmetal-path item
PYERITE = 35
MEGACYTE = 16


def _seed_sde():
    storage.replace_sde_data(
        types=[
            # type_id, group_id, name, volume, published, market_group_id, meta_level, meta_group_id, portion_size
            (VELDSPAR, 18, "Veldspar", 0.1, 1, 100, 0, None, 100),
            (TRITANIUM, 18, "Tritanium", 0.01, 1, 100, 0, None, 1),
            (PYERITE, 18, "Pyerite", 0.01, 1, 100, 0, None, 1),
            (MEGACYTE, 18, "Megacyte", 0.01, 1, 100, 0, None, 1),
            (DAMAGED_ARMOR_PLATE, 60, "Damaged Armor Plate", 5.0, 1, 200, 0, None, 1),
        ],
        groups=[(18, 25, "Veldspar"), (60, 7, "Armor Plates")],
        market_groups=[], blueprint_time=[], blueprint_materials=[], blueprint_products=[],
        type_materials=[
            (VELDSPAR, TRITANIUM, 415.0),
            (DAMAGED_ARMOR_PLATE, TRITANIUM, 100.0),
            (DAMAGED_ARMOR_PLATE, PYERITE, 50.0),
            (DAMAGED_ARMOR_PLATE, MEGACYTE, 3.0),
        ],
    )


def test_ore_ice_base_yield_confirmed_maximum(db):
    """Tatara, T2-Rig, null-sec, max skills, RX-804 -> confirmed 90.63% ceiling
    (constants.py's own module docstring - a real historical-bug-fix figure,
    not an arbitrary test value)."""
    cfg = RefiningConfig(
        structure_type="Tatara (L Refinery)", rig_tier="T2-Rig", security_status=-0.5,
        implant="RX-804", reprocessing_skill_level=5, reprocessing_efficiency_skill_level=5,
        ore_family_skill_levels={"Veldspar": 5},
    )
    pct = ore_ice_yield(cfg, ore_family="Veldspar")
    assert round(pct * 100, 2) == 90.63


def test_ore_ice_base_yield_citadel_no_bonuses_highsec(db):
    """Base case: Citadel, no rig, highsec (sec modifier 0), no bonuses at
    all -> exactly 50%."""
    cfg = RefiningConfig(security_status=1.0)
    assert ore_ice_base_yield(cfg) == 0.5


def test_security_yield_modifier_lowsec_and_nullsec(db):
    cfg_low = RefiningConfig(security_status=0.4)   # rounds to 0.4 -> lowsec bucket
    cfg_null = RefiningConfig(security_status=-0.5)  # nullsec/wormhole bucket
    assert ore_ice_base_yield(cfg_low) > ore_ice_base_yield(RefiningConfig(security_status=1.0))
    assert ore_ice_base_yield(cfg_null) > ore_ice_base_yield(cfg_low)


def test_unknown_ore_family_missing_from_dict_assumes_maxed(db):
    """A family simply absent from ore_family_skill_levels is assumed level 5
    (maxed), not unskilled - see ore_ice_yield's own docstring for why."""
    cfg_missing = RefiningConfig()  # empty dict
    cfg_explicit_max = RefiningConfig(ore_family_skill_levels={"Veldspar": 5})
    assert ore_ice_yield(cfg_missing, "Veldspar") == ore_ice_yield(cfg_explicit_max, "Veldspar")


def test_ore_ice_yield_no_family_gets_no_family_bonus(db):
    cfg = RefiningConfig(ore_family_skill_levels={"Veldspar": 5})
    with_family = ore_ice_yield(cfg, "Veldspar")
    without_family = ore_ice_yield(cfg, None)
    assert without_family < with_family


def test_scrapmetal_yield_confirmed_maximum(db):
    """50% base + 5 x 1%/level -> confirmed 55% ceiling. Also regression-tests
    the parent's confirmed-wrong "2%/level" figure some guides use (would
    wrongly give 60%)."""
    cfg = RefiningConfig(scrapmetal_processing_skill_level=5)
    assert round(scrapmetal_yield(cfg) * 100, 2) == 55.0


def test_scrapmetal_yield_ignores_structure_rig_and_ore_skills(db):
    """Genuine asymmetry vs. the ore/ice path, not a bug - see constants.py's
    module docstring."""
    plain = RefiningConfig(scrapmetal_processing_skill_level=3)
    loaded = RefiningConfig(
        scrapmetal_processing_skill_level=3, structure_type="Tatara (L Refinery)",
        rig_tier="T2-Rig", security_status=-1.0, implant="RX-804",
        reprocessing_skill_level=5, reprocessing_efficiency_skill_level=5,
    )
    assert scrapmetal_yield(plain) == scrapmetal_yield(loaded)


def test_apply_reprocessing_yield_portion_batching_and_per_material_floor(db):
    """250 Veldspar at portion_size=100 -> 2 complete portions (the 50
    leftover units below one portion yield nothing) -> floor(2 * 415 * 0.5)
    = 415 Tritanium."""
    _seed_sde()
    result = apply_reprocessing_yield(VELDSPAR, 250, 0.5)
    assert result == {TRITANIUM: 415}


def test_apply_reprocessing_yield_below_one_portion_yields_nothing(db):
    _seed_sde()
    assert apply_reprocessing_yield(VELDSPAR, 99, 0.9063) == {}


def test_apply_reprocessing_yield_per_material_independent_floor(db):
    """Each material's output is floored independently, not the summed total -
    a real EVE rounding rule, not a continuous approximation."""
    _seed_sde()
    # portion_size 1 (Damaged Armor Plate) -> 7 units = 7 portions.
    # Tritanium: floor(7 * 100 * 0.55) = 385
    # Pyerite:   floor(7 * 50  * 0.55) = 192 (192.5 truncated, not rounded)
    # Megacyte:  floor(7 * 3   * 0.55) = 11  (11.55 truncated)
    result = apply_reprocessing_yield(DAMAGED_ARMOR_PLATE, 7, 0.55)
    assert result == {TRITANIUM: 385, PYERITE: 192, MEGACYTE: 11}


def test_apply_reprocessing_yield_unknown_type_returns_empty(db):
    """No SDE portion_size/material rows at all (not yet refreshed, or
    genuinely not reprocessable - a ship/skillbook/BPO/BPC) -> {}, the
    Reprocessing tab's own "flag non-reprocessable items" signal."""
    _seed_sde()
    assert apply_reprocessing_yield(999999, 100, 0.5) == {}
