"""Candidate-discovery tests.

Nothing here touches the network. The SDE-backed path (the one that actually
runs on a populated install) is exercised against a real, tiny SQLite SDE
cache written through storage.replace_sde_data, so the storage queries the
module depends on are covered too rather than mocked away. The live-ESI walk
gets a fake client.

`resolve_effective_volume_bulk` is monkeypatched wherever it would otherwise
reach ESI for a Module-category type's packaged volume - one test covers that
substitution explicitly, since it's the fix for a real bug (see the parent
repo's issue #73).
"""
from __future__ import annotations

import pytest

from eve_trader_local import candidate_discovery, storage
from eve_trader_local.candidate_discovery import (
    _market_group_path, build_candidate_universe, build_focused_candidate_universe,
    guess_category, is_wanted_market_path,
)
from eve_trader_local.config import TradingConfig
from eve_trader_local.models import Candidate


@pytest.fixture(autouse=True)
def _no_esi_volume_lookup(monkeypatch):
    """Default: every type keeps its plain SDE volume. Tests that care about
    the packaged-volume substitution override this themselves."""
    monkeypatch.setattr(candidate_discovery, "resolve_effective_volume_bulk",
                        lambda items, **kw: {t: v for t, v, _c in items})


# ------------------------------------------------------ is_wanted_market_path
@pytest.mark.parametrize("path", [
    "Ships > Frigates", "Blueprints > Ship Blueprints", "Apparel > Clothing",
    "Personalization > SKINs", "Pilot's Services > Clone Bay",
    "Structures > Citadels",
])
def test_excluded_top_level_groups_are_rejected(path):
    assert is_wanted_market_path(path) is False


@pytest.mark.parametrize("path", [
    "Ship Equipment > Turrets",     # "Ship Equipment" is not "Ships"
    "Skills > Trade",               # deliberately taken off the exclusion list
    "Implants & Boosters > Attribute Enhancers",
    "Manufacture & Research > Materials",
    "Drones > Combat Drones",
])
def test_everything_else_is_wanted(path):
    assert is_wanted_market_path(path) is True


def test_matching_is_case_insensitive():
    assert is_wanted_market_path("SHIPS > Frigates") is False


def test_exclusions_come_from_the_config():
    cfg = TradingConfig()
    cfg.excluded_path_prefixes = ("drones",)

    assert is_wanted_market_path("Drones > Combat Drones", cfg) is False
    assert is_wanted_market_path("Ships > Frigates", cfg) is True


# ------------------------------------------------------------ guess_category
CATEGORY_NAMES = {4: "Material", 7: "Module", 8: "Charge", 18: "Drone", 20: "Implant"}


def test_uses_the_real_sde_category_name():
    """The whole point of passing category_id/category_names: an implant must
    come back as "Implant", not as the old catch-all "Material" bucket."""
    assert guess_category("Implants & Boosters > X", "Ocular Filter", 1.0,
                          category_id=20, category_names=CATEGORY_NAMES) == "Implant"
    assert guess_category("Drones > X", "Hobgoblin II", 5.0,
                          category_id=18, category_names=CATEGORY_NAMES) == "Drone"


def test_boosters_are_split_out_of_the_implant_category():
    """category_id 20 covers real cyberimplants *and* drugs; only group_id
    tells them apart."""
    assert guess_category("Implants & Boosters > Booster", "Blue Pill", 1.0,
                          category_id=20, category_names=CATEGORY_NAMES,
                          group_id=candidate_discovery.BOOSTER_GROUP_ID) == "Drugs"
    assert guess_category("Implants & Boosters > X", "Ocular Filter", 1.0,
                          category_id=20, category_names=CATEGORY_NAMES,
                          group_id=999) == "Implant"


def test_unknown_category_id_falls_back_to_the_module_vs_material_split():
    assert guess_category("x", "y", 1.0, category_id=7, category_names={}) == "Module/Rig"
    assert guess_category("x", "y", 1.0, category_id=1234, category_names={}) == "Material"


def test_name_heuristic_only_applies_without_a_category_id():
    """The string/volume heuristic is the live-ESI-walk fallback only - a real
    category_id must always win over it, which is what stopped implants from
    being labelled Material."""
    assert guess_category("Implants & Boosters > X", "Ocular Filter", 6.0,
                          category_id=20, category_names=CATEGORY_NAMES) == "Implant"

    assert guess_category("Ship Equipment > Modules", "Large Shield Booster", 0.1) == "Module/Rig"
    assert guess_category("Ship Equipment > Rigs", "Trimark", 0.1) == "Module/Rig"
    assert guess_category("Manufacture > X", "Tritanium", 5.0) == "Module/Rig"  # volume >= 5
    assert guess_category("Manufacture > X", "Tritanium", 0.01) == "Material"


# --------------------------------------------------------- _market_group_path
def test_path_walks_up_to_the_root():
    names = {1: "Ship Equipment", 2: "Turrets", 3: "Hybrid"}
    parents = {1: 0, 2: 1, 3: 2}

    assert _market_group_path(3, names, parents) == "Ship Equipment > Turrets > Hybrid"


def test_a_parent_cycle_cannot_loop_forever():
    assert len(_market_group_path(1, {1: "A", 2: "B"}, {1: 2, 2: 1}).split(" > ")) == 20


# ---------------------------------------------- build_candidate_universe (SDE)
def _seed_sde(db):
    """A tiny but realistic SDE cache: one mineral, one module, one implant,
    one booster, plus a ship that must be excluded by market group."""
    storage.replace_sde_data(
        types=[
            # type_id, group_id, name, volume, published, market_group_id, meta_level, meta_group_id, portion_size
            (34, 18, "Tritanium", 0.01, 1, 100, 0, 1, 1),
            (2048, 60, "Damage Control II", 5.0, 1, 200, 5, 2, 1),
            (9899, 300, "Ocular Filter", 1.0, 1, 300, 0, None, 1),
            (28674, 303, "Blue Pill Booster", 1.0, 1, 300, 0, None, 1),
            (587, 25, "Rifter", 27289.0, 1, 400, 0, 1, 1),
            (999, 18, "Unpublished", 1.0, 0, 100, 0, None, 1),
            (998, 18, "No Market Group", 1.0, 1, None, 0, None, 1),
            (997, 18, "Zero Volume", 0.0, 1, 100, 0, None, 1),
        ],
        groups=[(18, 4, "Mineral"), (60, 7, "Damage Control"), (300, 305, "Cyberimplant"),
                (303, 20, "Booster"), (25, 6, "Frigate")],
        market_groups=[(100, None, "Manufacture & Research"), (200, None, "Ship Equipment"),
                       (300, None, "Implants & Boosters"), (400, None, "Ships")],
        blueprint_time=[], blueprint_materials=[], blueprint_products=[],
        categories=[(4, "Material"), (7, "Module"), (20, "Implant"),
                    (305, "Implant"), (6, "Ship")],
    )


def test_builds_from_the_local_sde_cache(db, monkeypatch):
    _seed_sde(db)

    def _no_esi(*args, **kwargs):
        raise AssertionError("the SDE-backed path must not touch ESI")
    monkeypatch.setattr(candidate_discovery, "_build_candidate_universe_from_esi", _no_esi)

    universe = {c.type_id: c for c in build_candidate_universe()}

    # Rifter: excluded by market group. 999/998/997: unpublished, no market
    # group, zero volume.
    assert set(universe) == {34, 2048, 9899, 28674}
    assert universe[34].item == "Tritanium"
    assert universe[34].market_group_path == "Manufacture & Research"
    assert universe[2048].meta_level == 5


def test_categories_come_from_real_sde_data(db):
    _seed_sde(db)

    universe = {c.type_id: c for c in build_candidate_universe()}

    assert universe[34].category == "Material"
    assert universe[2048].category == "Module"
    assert universe[9899].category == "Implant"
    # Same category_id as a cyberimplant would give ("Implant"); only the
    # group_id lookup separates it.
    assert universe[28674].category == "Drugs"


def test_module_volume_comes_from_the_packaged_lookup(db, monkeypatch):
    """Parent repo issue #73: a capital-sized module's SDE `volume` is its
    assembled volume, far larger than what it actually takes to freight."""
    _seed_sde(db)
    seen = {}

    def _bulk(items, **kwargs):
        seen["items"] = items
        return {t: (1000.0 if t == 2048 else v) for t, v, _c in items}
    monkeypatch.setattr(candidate_discovery, "resolve_effective_volume_bulk", _bulk)

    universe = {c.type_id: c for c in build_candidate_universe()}

    assert universe[2048].volume_m3 == 1000.0
    assert universe[34].volume_m3 == 0.01
    # One bulk call for every row, not one lookup per type inside the loop.
    assert {t for t, _v, _c in seen["items"]} == {34, 2048, 9899, 28674}


def test_category_display_uses_the_raw_sde_volume_not_the_packaged_one(db, monkeypatch):
    """The heuristic branch of guess_category is volume-sensitive; it must see
    the same number it always did, not the freshly substituted packaged one."""
    _seed_sde(db)
    monkeypatch.setattr(candidate_discovery, "resolve_effective_volume_bulk",
                        lambda items, **kw: {t: 0.001 for t, _v, _c in items})
    seen = []
    real = candidate_discovery.guess_category
    monkeypatch.setattr(candidate_discovery, "guess_category",
                        lambda path, name, vol, *a, **kw: seen.append((name, vol)) or real(path, name, vol, *a, **kw))

    build_candidate_universe()

    assert ("Damage Control II", 5.0) in seen


# ---------------------------------------------- build_candidate_universe (ESI)
class FakeESIClient:
    def __init__(self, groups: dict, types: dict):
        self._groups = groups
        self._types = types

    def list_market_group_ids(self):
        return list(self._groups)

    def get_market_group(self, group_id: int):
        return self._groups[group_id]

    def get_type_info(self, type_id: int):
        return self._types[type_id]


def test_falls_back_to_the_live_esi_walk_on_an_empty_sde_cache(db):
    client = FakeESIClient(
        groups={
            1: {"name": "Ship Equipment", "parent_group_id": 0, "types": [2048]},
            2: {"name": "Ships", "parent_group_id": 0, "types": [587]},
            3: {"name": "Frigates", "parent_group_id": 2, "types": [588]},
        },
        types={
            2048: {"name": "Damage Control II", "volume": 5.0, "packaged_volume": 5.0,
                   "dogma_attributes": [{"attribute_id": 633, "value": 5}]},
            587: {"name": "Rifter", "volume": 27289.0},
            588: {"name": "Merlin", "volume": 27289.0},
        },
    )

    universe = build_candidate_universe(client, progress=False)

    # Both the "Ships" group and its child "Ships > Frigates" are excluded.
    assert [(c.item, c.volume_m3, c.meta_level) for c in universe] == \
        [("Damage Control II", 5.0, 5)]
    assert universe[0].category == "Module/Rig"  # heuristic fallback: no category_id on this path


def test_esi_walk_skips_types_without_a_name_or_volume(db):
    client = FakeESIClient(
        groups={1: {"name": "Manufacture", "parent_group_id": 0, "types": [34, 35, 36]}},
        types={34: {"name": "Tritanium", "volume": 0.01},
               35: {"name": "", "volume": 1.0},
               36: {"name": "Zero", "volume": 0}},
    )

    assert [c.type_id for c in build_candidate_universe(client, progress=False)] == [34]


# --------------------------------------------- build_focused_candidate_universe
def test_focused_universe_is_a_pass_through_copy():
    """Deliberately no filtering: nothing is dropped for its category or
    physical size before it has even been price-checked."""
    universe = [Candidate(item="Tritanium", type_id=34, volume_m3=0.01, category="Material",
                          market_group_path="Manufacture", meta_level=0),
                Candidate(item="Rifter Blueprint", type_id=1, volume_m3=99999.0,
                          category="Material", market_group_path="X")]

    focused = build_focused_candidate_universe(universe)

    assert focused == universe
    assert focused is not universe


# ------------------------------------------------------------------- storage
def test_candidate_universe_round_trips_through_storage(db):
    candidates = [Candidate(item="Tritanium", type_id=34, volume_m3=0.01, category="Material",
                            market_group_path="Manufacture & Research", meta_level=0),
                  Candidate(item="Ocular Filter", type_id=9899, volume_m3=1.0,
                            category="Implant", market_group_path="Implants", meta_level=None)]

    storage.save_candidate_universe(candidates, "2026-09-02T10:00:00Z")

    assert storage.load_candidate_universe() == candidates
    assert storage.get_candidate_universe_built_at() == "2026-09-02T10:00:00Z"


def test_a_rebuild_replaces_rather_than_appends(db):
    first = [Candidate(item="A", type_id=1, volume_m3=1.0, category="Material", market_group_path="X")]
    second = [Candidate(item="B", type_id=2, volume_m3=2.0, category="Material", market_group_path="X")]

    storage.save_candidate_universe(first, "2026-09-01T00:00:00Z")
    storage.save_candidate_universe(second, "2026-09-02T00:00:00Z")

    assert storage.load_candidate_universe() == second


def test_the_two_candidate_tables_are_independent(db):
    universe = [Candidate(item="A", type_id=1, volume_m3=1.0, category="Material", market_group_path="X")]
    storage.save_candidate_universe(universe, "2026-09-02T00:00:00Z")
    storage.save_candidate_universe([], "2026-09-02T00:00:00Z", table="focused_candidates")

    assert storage.load_candidate_universe() == universe
    assert storage.load_candidate_universe(table="focused_candidates") == []


def test_an_unknown_table_name_is_rejected(db):
    with pytest.raises(ValueError):
        storage.save_candidate_universe([], "now", table="sde_types; DROP")
    with pytest.raises(ValueError):
        storage.load_candidate_universe(table="sde_types")


def test_an_empty_universe_reads_back_as_empty(db):
    assert storage.load_candidate_universe() == []
    assert storage.get_candidate_universe_built_at() is None
