"""Tests for the 12 backend/CLI gaps closed against the parent repo's
Production tool (see SYNC.md's own row for each) - one file since most of
these are small, independent surfaces rather than a cohesive subsystem of
their own. Reuses the same synthetic-SDE/no-network pattern as
test_production_engine.py/test_production_planner.py throughout.
"""
from __future__ import annotations

import pytest

from eve_trader_local import storage
from eve_trader_local.errors import ActionError
from eve_trader_local.goonmetrics_client import CurrentPrice
from eve_trader_local.production import actions as production_actions
from eve_trader_local.production import engine, jobs
from eve_trader_local.production.config import ProductionConfig

MINERAL = 34
COMPONENT = 91301
FINISHED = 91302
COMPONENT_BP, FINISHED_BP = 92301, 92302


def _seed():
    storage.replace_sde_data(
        types=[
            (MINERAL, 18, "Tritanium", 0.01, 1, 100, 0, 1, 1),
            (COMPONENT, 18, "Widget Component", 1.0, 1, 200, 0, 1, 1),
            (FINISHED, 18, "Finished Widget", 1.0, 1, 200, 0, 1, 1),
            (COMPONENT_BP, 9, "Widget Component Blueprint", 0.01, 1, None, 0, None, 1),
            (FINISHED_BP, 9, "Finished Widget Blueprint", 0.01, 1, None, 0, None, 1),
        ],
        groups=[(18, 4, "Mineral"), (9, 9, "Blueprint")],
        market_groups=[(100, None, "Manufacture & Research"), (200, None, "Ship Equipment")],
        blueprint_time=[],
        blueprint_materials=[
            (COMPONENT_BP, 1, MINERAL, 10),
            (FINISHED_BP, 1, COMPONENT, 2),
        ],
        blueprint_products=[
            (COMPONENT_BP, 1, COMPONENT, 1),
            (FINISHED_BP, 1, FINISHED, 1),
        ],
        categories=[(4, "Material")],
    )


def _cfg(**overrides) -> ProductionConfig:
    cfg = ProductionConfig(jita_buy_broker_fee=0.0, haul_cost_per_m3=0.0,
                           facility_tax_rate=0.0, market_fees=0.0,
                           component_overbuild=0.0, min_margin=0.0)
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


HOME = {MINERAL: CurrentPrice(type_id=MINERAL, updated="", buy=4.5, sell=5.0)}


@pytest.fixture
def gaps_sde(db, monkeypatch):
    _seed()
    monkeypatch.setattr(engine.pricing, "home_prices", lambda type_ids, cfg=None, client=None: HOME)
    monkeypatch.setattr(engine.pricing, "jita_prices", lambda type_ids, client=None, trading_cfg=None: {})

    class _FakeESIClient:
        def __init__(self, *args, **kwargs):
            pass

        def get_adjusted_prices(self):
            return {}

    import eve_trader_local.esi_client as esi_client_module
    monkeypatch.setattr(esi_client_module, "ESIClient", _FakeESIClient)
    return db


# --------------------------------------------------------------- (1) item margin
def test_item_margin_detail_matches_ship_margin_shape(gaps_sde):
    row = engine.item_margin_detail(FINISHED, "Finished Widget", _cfg())
    assert row["type_id"] == FINISHED
    assert row["activity"] == "Tech I"
    assert row["build_cost"] is not None
    assert "meta_level" in row


def test_do_get_item_margin_resolves_by_name(gaps_sde):
    row = production_actions.do_get_item_margin("Finished Widget", _cfg())
    assert row.type_id == FINISHED
    assert row.type_name == "Finished Widget"


def test_do_get_item_margin_unknown_item_raises(gaps_sde):
    with pytest.raises(ActionError):
        production_actions.do_get_item_margin("Not A Real Item", _cfg())


# --------------------------------------------------------------- (3) material tree
def test_do_build_material_tree_recurses_full_bom(gaps_sde):
    tree = production_actions.do_build_material_tree("Finished Widget", quantity=5, cfg=_cfg())
    assert tree["type_id"] == FINISHED
    assert tree["quantity"] == 5
    component_node = tree["children"][0]
    assert component_node["type_id"] == COMPONENT
    # 5 runs x 2/run x the default perfect-BPO-research ME10 multiplier
    # (0.9), rounded up: ceil(9.0) = 9.
    assert component_node["quantity"] == 9
    mineral_node = component_node["children"][0]
    assert mineral_node["type_id"] == MINERAL


def test_do_build_material_tree_rejects_non_positive_quantity(gaps_sde):
    with pytest.raises(ActionError):
        production_actions.do_build_material_tree("Finished Widget", quantity=0, cfg=_cfg())


# --------------------------------------------------------------- (4) item locations
def test_search_item_stock_locations_groups_by_location_and_owner(gaps_sde):
    storage.replace_assets("character_assets", [
        (1, FINISHED, 60003760, "Hangar", 3, 0, "Alice"),
        (2, FINISHED, 60003760, "Hangar", 2, 0, "Bob"),
    ])
    rows = storage.search_item_stock_locations(FINISHED)
    by_owner = {owner: qty for _loc, _name, owner, qty in rows}
    assert by_owner == {"Alice": 3.0, "Bob": 2.0}


def test_search_item_stock_locations_prefers_structure_name_cache(gaps_sde):
    storage.replace_assets("character_assets", [(1, FINISHED, 1000000000123, "Hangar", 1, 0, "Alice")])
    storage.set_cached_structure_name(1000000000123, "C-J Keepstar")
    rows = storage.search_item_stock_locations(FINISHED)
    assert rows[0][1] == "C-J Keepstar"


def test_do_search_item_locations_action(gaps_sde):
    storage.replace_assets("character_assets", [(1, FINISHED, 60003760, "Hangar", 4, 0, "Alice")])
    result = production_actions.do_search_item_locations("Finished Widget")
    assert result["type_id"] == FINISHED
    assert result["locations"][0].quantity == 4


# --------------------------------------------------------------- (5) cost indices
def test_do_get_system_cost_indices_normalizes_empty_to_none(gaps_sde, monkeypatch):
    monkeypatch.setattr(engine.pricing, "system_cost_indices_for", lambda client, system_id: {})
    result = production_actions.do_get_system_cost_indices(_cfg())
    assert result == {"component": None, "manufacturing": None}


def test_do_get_system_cost_indices_passes_through_real_data(gaps_sde, monkeypatch):
    monkeypatch.setattr(
        production_actions.pricing, "system_cost_indices_for",
        lambda client, system_id: {"manufacturing": 0.02} if system_id else {})
    result = production_actions.do_get_system_cost_indices(
        _cfg(manufacturing_system_id=30000142, component_system_id=None))
    assert result["manufacturing"] == {"manufacturing": 0.02}
    assert result["component"] is None


# --------------------------------------------------------------- (7) structure names
def test_structure_name_cache_round_trip(db):
    was_cached, name = storage.get_cached_structure_name(1000000000123)
    assert was_cached is False
    assert name is None

    storage.set_cached_structure_name(1000000000123, "C-J Keepstar", solar_system_id=30000142)
    was_cached, name = storage.get_cached_structure_name(1000000000123)
    assert was_cached is True
    assert name == "C-J Keepstar"


def test_structure_name_cache_records_failed_resolution(db):
    """A resolution attempt that fails (no producer character could see the
    structure) still caches was_cached=True with name=None, distinct from
    "never attempted" - so a caller knows not to keep retrying every page
    load."""
    storage.set_cached_structure_name(1000000000123, None)
    was_cached, name = storage.get_cached_structure_name(1000000000123)
    assert was_cached is True
    assert name is None


def test_set_cached_structure_name_keeps_earlier_solar_system_id(db):
    storage.set_cached_structure_name(1000000000123, "C-J Keepstar", solar_system_id=30000142)
    storage.set_cached_structure_name(1000000000123, "C-J Keepstar (renamed)", solar_system_id=None)
    with storage.connect() as conn:
        row = conn.execute(
            "SELECT solar_system_id FROM structure_names WHERE location_id = ?", (1000000000123,)
        ).fetchone()
    assert row[0] == 30000142


def test_do_resolve_structure_name_uses_cache_unless_forced(db):
    storage.set_cached_structure_name(1000000000123, "C-J Keepstar")
    result = production_actions.do_resolve_structure_name(1000000000123)
    assert result == {"location_id": 1000000000123, "name": "C-J Keepstar", "cached": True}


def test_do_resolve_structure_name_raises_without_producer_character(db):
    with pytest.raises(ActionError):
        production_actions.do_resolve_structure_name(1000000000123, force=True)


# ----------------------------------------------------------- (8) manual build/buy
def test_manual_build_buy_storage_round_trip(db):
    assert storage.load_manual_build_buy() == {}
    storage.upsert_manual_build_buy(COMPONENT, "Buy")
    assert storage.load_manual_build_buy() == {COMPONENT: "Buy"}
    storage.delete_manual_build_buy(COMPONENT)
    assert storage.load_manual_build_buy() == {}


def test_do_set_manual_build_buy_rejects_unknown_decision(gaps_sde):
    with pytest.raises(ActionError):
        production_actions.do_set_manual_build_buy("Widget Component", "Maybe")


def test_do_set_and_clear_manual_build_buy(gaps_sde):
    result = production_actions.do_set_manual_build_buy("Widget Component", "Buy")
    assert result == {"type_id": COMPONENT, "type_name": "Widget Component", "decision": "Buy"}
    assert production_actions.do_list_manual_build_buy()["rows"] == [
        (COMPONENT, "Widget Component", "Buy")]

    cleared = production_actions.do_clear_manual_build_buy("Widget Component")
    assert cleared["decision"] == "Auto"
    assert production_actions.do_list_manual_build_buy()["rows"] == []


def test_plan_production_respects_manual_build_override(gaps_sde):
    """COMPONENT has no home/Jita quote, so buy_price() is None and the
    cost-based decision would always be "Build" - a manual "Buy" override
    must still win, forcing it into the buy list instead of the build list
    (engine._buy_or_build_decision consults manual_overrides before any cost
    comparison)."""
    storage.upsert_stock_target(FINISHED, "Finished Widget", 5.0)
    storage.upsert_manual_build_buy(COMPONENT, "Buy")

    plan = engine.plan_production(_cfg())

    build_type_ids = {row.type_id for row in plan["build_list"]}
    buy_type_ids = {row.type_id for row in plan["buy_list"]}
    assert COMPONENT not in build_type_ids
    assert COMPONENT in buy_type_ids


def test_plan_production_manual_build_override_bypasses_margin_gate(gaps_sde):
    """A manual "Build" override skips the min_margin gate that would
    otherwise drop this stock target's demand entirely (engine.
    plan_production: `if bp is not None and type_id not in manual_overrides`)."""
    storage.upsert_stock_target(FINISHED, "Finished Widget", 5.0)
    storage.upsert_manual_build_buy(FINISHED, "Build")

    # An impossibly high margin floor would normally drop FINISHED's demand
    # entirely - the override must still seed it.
    plan = engine.plan_production(_cfg(min_margin=100.0))

    build_type_ids = {row.type_id for row in plan["build_list"]}
    assert FINISHED in build_type_ids


# --------------------------------------------------------------- (9) stock target update
def test_do_update_stock_target_partial_update_preserves_other_field(gaps_sde):
    production_actions.do_add_stock_target("Finished Widget", 10, jita_target=True)
    result = production_actions.do_update_stock_target("Finished Widget", quantity=25)
    assert result["quantity"] == 25
    assert result["jita_target"] is True  # untouched


def test_do_update_stock_target_raises_if_not_configured(gaps_sde):
    with pytest.raises(ActionError):
        production_actions.do_update_stock_target("Finished Widget", quantity=25)


def test_do_update_stock_target_rejects_negative_quantity(gaps_sde):
    production_actions.do_add_stock_target("Finished Widget", 10)
    with pytest.raises(ActionError):
        production_actions.do_update_stock_target("Finished Widget", quantity=-1)


# --------------------------------------------------------- (10)/(11) jobs & slots
def test_list_industry_jobs_filters_to_in_progress_statuses(db):
    storage.replace_industry_jobs("character_industry_jobs", [
        (1, 1, FINISHED_BP, FINISHED, 2, 60003760, "active", "2030-01-01T00:00:00Z", "", 1, "Alice"),
        (2, 1, FINISHED_BP, FINISHED, 1, 60003760, "delivered", "2020-01-01T00:00:00Z", "", 1, "Alice"),
    ])
    rows = storage.list_industry_jobs()
    assert len(rows) == 1
    assert rows[0][0] == 1


def test_jobs_list_current_jobs_prices_output_and_sorts_by_remaining(gaps_sde):
    storage.replace_sde_data(
        types=[
            (MINERAL, 18, "Tritanium", 0.01, 1, 100, 0, 1, 1),
            (COMPONENT, 18, "Widget Component", 1.0, 1, 200, 0, 1, 1),
            (FINISHED, 18, "Finished Widget", 1.0, 1, 200, 0, 1, 1),
            (COMPONENT_BP, 9, "Widget Component Blueprint", 0.01, 1, None, 0, None, 1),
            (FINISHED_BP, 9, "Finished Widget Blueprint", 0.01, 1, None, 0, None, 1),
        ],
        groups=[(18, 4, "Mineral"), (9, 9, "Blueprint")],
        market_groups=[(100, None, "Manufacture & Research"), (200, None, "Ship Equipment")],
        blueprint_time=[], blueprint_materials=[(COMPONENT_BP, 1, MINERAL, 10), (FINISHED_BP, 1, COMPONENT, 2)],
        blueprint_products=[(COMPONENT_BP, 1, COMPONENT, 1), (FINISHED_BP, 1, FINISHED, 1)],
        categories=[(4, "Material")],
    )
    storage.replace_industry_jobs("character_industry_jobs", [
        (1, 1, FINISHED_BP, FINISHED, 2, 60003760, "active", "2099-01-01T00:00:00+00:00", "", 1, "Alice"),
    ])
    rows = jobs.list_current_jobs(_cfg())
    assert len(rows) == 1
    assert rows[0].type_name == "Finished Widget"
    assert rows[0].runs == 2
    assert rows[0].activity == "Manufacturing"


def test_character_slot_overview_counts_active_jobs_per_category(db):
    storage.replace_industry_jobs("character_industry_jobs", [
        (1, 1, FINISHED_BP, FINISHED, 1, 60003760, "active", "", "", 1, "Alice"),
        (2, 1, FINISHED_BP, FINISHED, 1, 60003760, "active", "", "", 1, "Alice"),
        (3, 8, COMPONENT_BP, FINISHED, 1, 60003760, "active", "", "", 2, "Bob"),
        (4, 1, FINISHED_BP, FINISHED, 1, 60003760, "delivered", "", "", 1, "Alice"),
    ])
    rows = jobs.character_slot_overview()
    by_key = {(r.character_name, r.job_type): r.used_slots for r in rows}
    assert by_key[("Alice", "Manufacturing")] == 2
    assert by_key[("Bob", "Science")] == 1
    assert ("Alice", "Science") not in by_key  # never had any - not shown as a zero row


def test_do_list_current_jobs_and_character_slot_overview_actions(gaps_sde):
    storage.replace_industry_jobs("character_industry_jobs", [
        (1, 1, FINISHED_BP, FINISHED, 1, 60003760, "active", "", "", 1, "Alice"),
    ])
    assert len(production_actions.do_list_current_jobs(_cfg())["rows"]) == 1
    assert len(production_actions.do_character_slot_overview()["rows"]) == 1


# --------------------------------------------------------------- (12) owned blueprints
def test_load_owned_blueprints_reads_character_and_corp_tables(db):
    storage.replace_assets("character_assets", [])
    storage.replace_blueprints("character_blueprints", [
        (1, COMPONENT_BP, 60003760, "Hangar", -1, 10, 20, -1),  # BPO, ME10/TE20
    ])
    storage.replace_blueprints("corp_blueprints", [
        (2, FINISHED_BP, 60003760, "Hangar", -2, 0, 0, 5),  # BPC, 5 runs left
    ])
    rows = storage.load_owned_blueprints()
    assert (COMPONENT_BP, -1, 10, 20, -1) in rows
    assert (FINISHED_BP, -2, 0, 0, 5) in rows


def test_engine_list_owned_blueprints_aggregates_identical_groups(db):
    storage.replace_assets("character_assets", [])
    storage.replace_sde_data(
        types=[(COMPONENT_BP, 9, "Widget Component Blueprint", 0.01, 1, None, 0, None, 1)],
        groups=[(9, 9, "Blueprint")], market_groups=[], blueprint_time=[],
        blueprint_materials=[], blueprint_products=[], categories=[(9, "Blueprint")],
    )
    storage.replace_blueprints("character_blueprints", [
        (1, COMPONENT_BP, 60003760, "Hangar", -1, 10, 20, -1),
        (2, COMPONENT_BP, 60003760, "Hangar", -1, 10, 20, -1),  # identical BPO, second character
    ])
    rows = engine.list_owned_blueprints()
    assert len(rows) == 1
    row = rows[0]
    assert row.type_id == COMPONENT_BP
    assert row.is_original is True
    assert row.quantity == 2
    assert row.material_efficiency == 10
    assert row.time_efficiency == 20
    assert row.runs is None


def test_do_list_owned_blueprints_action(db):
    storage.replace_assets("character_assets", [])
    storage.replace_blueprints("character_blueprints", [
        (1, COMPONENT_BP, 60003760, "Hangar", -1, 10, 20, -1),
    ])
    rows = production_actions.do_list_owned_blueprints()["rows"]
    assert len(rows) == 1
    assert rows[0].type_id == COMPONENT_BP


# --------------------------------------------------- (2) invention estimate action
def test_find_invention_recipe_by_product_name_case_insensitive(db):
    storage.replace_sde_data(
        types=[
            (COMPONENT_BP, 9, "Widget Component Blueprint", 0.01, 1, None, 0, None, 1),
            (FINISHED_BP, 9, "Finished Widget Blueprint", 0.01, 1, None, 0, None, 1),
        ],
        groups=[(9, 9, "Blueprint")], market_groups=[], blueprint_time=[],
        blueprint_materials=[],
        blueprint_products=[(COMPONENT_BP, 8, FINISHED_BP, 10)],
        categories=[(9, "Blueprint")],
    )
    result = storage.find_invention_recipe_by_product_name("finished widget blueprint")
    assert result == (COMPONENT_BP, FINISHED_BP)
    assert storage.find_invention_recipe_by_product_name("no such item") is None


def test_do_estimate_invention_unknown_product_raises(gaps_sde):
    with pytest.raises(ActionError):
        production_actions.do_estimate_invention("No Such Blueprint")
