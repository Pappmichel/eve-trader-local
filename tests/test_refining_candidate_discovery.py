"""Tests for eve_trader_local/refining/candidate_discovery.py - the Ore
Shortlist's fixed, SDE-derived candidate universe (mirrors the parent repo's
GitHub issue #91). Against a synthetic SDE cache seeded via
storage.replace_sde_data (this repo's established pattern) - no real
network."""
from eve_trader_local import storage
from eve_trader_local.refining.candidate_discovery import (
    _family_and_is_ice,
    build_ore_candidate_universe,
)

# sde_types: (type_id, group_id, type_name, volume, published, market_group_id,
#             meta_level, meta_group_id, portion_size)
# sde_groups: (group_id, category_id, group_name)
ORE_CATEGORY_ID = 25

_TYPES = [
    # Compressed ore - one group per family (Veldspar).
    (17471, 1884, "Compressed Veldspar", 0.01, 1, 100, None, None, 100),
    # Compressed ice - shared "Compressed Ice" group, family from type name.
    (28434, 4029, "Compressed Blue Ice", 0.05, 1, 101, None, None, 1000),
    # Unpublished compressed ore - must be excluded.
    (99991, 1884, "Compressed Unpublished Ore", 0.01, 0, 100, None, None, 100),
    # Raw (uncompressed) ore in the same category/group - not "Compressed%", excluded.
    (1230, 1884, "Veldspar", 0.1, 1, 102, None, None, 100),
    # A completely unrelated type (e.g. a module) - must never appear.
    (11184, 300, "Damage Control II", 5.0, 1, 200, 5, 2, 1),
]
_GROUPS = [
    (1884, ORE_CATEGORY_ID, "Veldspar"),
    (4029, ORE_CATEGORY_ID, "Ice"),
    (300, 7, "Electronic Systems"),
]


def _seed():
    storage.replace_sde_data(
        types=_TYPES, groups=_GROUPS, market_groups=[], blueprint_time=[],
        blueprint_materials=[], blueprint_products=[],
    )


def test_family_and_is_ice_ore_uses_group_name():
    family, is_ice = _family_and_is_ice("Compressed Veldspar", "Veldspar")
    assert family == "Veldspar"
    assert is_ice is False


def test_family_and_is_ice_ice_uses_type_name():
    family, is_ice = _family_and_is_ice("Compressed Blue Ice", "Ice")
    assert family == "Blue Ice"
    assert is_ice is True


def test_build_ore_candidate_universe_finds_ore_and_ice(db):
    _seed()
    candidates = build_ore_candidate_universe()

    by_item = {c.item: c for c in candidates}
    assert "Compressed Veldspar" in by_item
    assert by_item["Compressed Veldspar"].family == "Veldspar"
    assert by_item["Compressed Veldspar"].is_ice is False
    assert by_item["Compressed Veldspar"].type_id == 17471
    assert by_item["Compressed Veldspar"].volume_m3 == 0.01

    assert "Compressed Blue Ice" in by_item
    assert by_item["Compressed Blue Ice"].family == "Blue Ice"
    assert by_item["Compressed Blue Ice"].is_ice is True


def test_build_ore_candidate_universe_excludes_non_ore_ice(db):
    _seed()
    candidates = build_ore_candidate_universe()

    items = {c.item for c in candidates}
    assert "Compressed Unpublished Ore" not in items  # unpublished
    assert "Veldspar" not in items                    # raw, uncompressed
    assert "Damage Control II" not in items            # not ore/ice at all
    assert len(candidates) == 2


def test_build_ore_candidate_universe_empty_sde_returns_empty(db):
    assert build_ore_candidate_universe() == []
