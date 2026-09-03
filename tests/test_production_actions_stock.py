"""Tests for production/actions.py's stock-target do_* wrappers - name/type_id
resolution and the thin CRUD around storage.stock_targets."""
from __future__ import annotations

import pytest

from eve_trader_local import storage
from eve_trader_local.errors import ActionError
from eve_trader_local.production import actions
from eve_trader_local.production.config import ProductionConfig

TRITANIUM = 34


def _seed_sde():
    storage.replace_sde_data(
        types=[(TRITANIUM, 18, "Tritanium", 0.01, 1, 100, 0, 1, 1)],
        groups=[(18, 4, "Mineral")],
        market_groups=[(100, None, "Manufacture & Research")],
        blueprint_time=[], blueprint_materials=[], blueprint_products=[],
        categories=[(4, "Material")],
    )


def test_add_stock_target_by_name(db):
    _seed_sde()
    result = actions.do_add_stock_target("Tritanium", 1000.0)
    assert result == {"type_id": TRITANIUM, "type_name": "Tritanium", "quantity": 1000.0,
                      "jita_target": False}
    assert storage.load_stock_targets() == [(TRITANIUM, "Tritanium", 1000.0, False)]


def test_add_stock_target_by_type_id(db):
    _seed_sde()
    result = actions.do_add_stock_target(str(TRITANIUM), 500.0, jita_target=True)
    assert result["type_id"] == TRITANIUM
    assert storage.load_stock_targets() == [(TRITANIUM, "Tritanium", 500.0, True)]


def test_add_stock_target_unknown_name_raises(db):
    _seed_sde()
    with pytest.raises(ActionError):
        actions.do_add_stock_target("Nonexistent Item", 100.0)


def test_add_stock_target_negative_quantity_raises(db):
    _seed_sde()
    with pytest.raises(ActionError):
        actions.do_add_stock_target("Tritanium", -1.0)


def test_remove_stock_target(db):
    _seed_sde()
    actions.do_add_stock_target("Tritanium", 1000.0)
    result = actions.do_remove_stock_target("Tritanium")
    assert result == {"removed": TRITANIUM, "type_name": "Tritanium"}
    assert storage.load_stock_targets() == []


def test_list_stock_targets(db):
    _seed_sde()
    actions.do_add_stock_target("Tritanium", 1000.0)
    assert actions.do_list_stock_targets() == {"rows": [(TRITANIUM, "Tritanium", 1000.0, False)]}


# --------------------------------------------------------------- manual stock
def test_set_manual_stock_by_name(db):
    _seed_sde()
    result = actions.do_set_manual_stock("Tritanium", 500.0)
    assert result == {"type_id": TRITANIUM, "type_name": "Tritanium", "count": 500.0}
    assert storage.load_manual_stock() == {TRITANIUM: 500.0}


def test_set_manual_stock_negative_count_raises(db):
    _seed_sde()
    with pytest.raises(ActionError):
        actions.do_set_manual_stock("Tritanium", -1.0)


def test_remove_manual_stock(db):
    _seed_sde()
    actions.do_set_manual_stock("Tritanium", 500.0)
    result = actions.do_remove_manual_stock("Tritanium")
    assert result == {"removed": TRITANIUM, "type_name": "Tritanium"}
    assert storage.load_manual_stock() == {}


def test_list_manual_stock(db):
    _seed_sde()
    actions.do_set_manual_stock("Tritanium", 500.0)
    assert actions.do_list_manual_stock() == {"rows": [(TRITANIUM, "Tritanium", 500.0)]}


# ------------------------------------------------------- planner preconditions
def test_do_plan_asset_optimized_requires_sde(db):
    with pytest.raises(ActionError):
        actions.do_plan_asset_optimized()


def test_do_market_status_requires_stock_targets(db):
    _seed_sde()
    with pytest.raises(ActionError):
        actions.do_market_status()


def test_do_stock_value_requires_stock_targets(db):
    _seed_sde()
    with pytest.raises(ActionError):
        actions.do_stock_value()


def test_do_invention_logistics_requires_invention_location(db):
    _seed_sde()
    from eve_trader_local.production.config import ProductionConfig
    with pytest.raises(ActionError):
        actions.do_invention_logistics(ProductionConfig(invention_location_id=None))


def test_do_t1_bpc_invention_needs_requires_invention_location(db):
    _seed_sde()
    from eve_trader_local.production.config import ProductionConfig
    with pytest.raises(ActionError):
        actions.do_t1_bpc_invention_needs(ProductionConfig(invention_location_id=None))


def test_do_get_logistics_status_requires_stock_targets(db):
    _seed_sde()
    with pytest.raises(ActionError):
        actions.do_get_logistics_status()


def test_do_get_distribution_recommendations_requires_stock_targets(db):
    _seed_sde()
    with pytest.raises(ActionError):
        actions.do_get_distribution_recommendations()


# ------------------------------------------------- category locations (issue #4)
def test_set_category_location(db):
    result = actions.do_set_category_location("Reactions", 1000000000001)
    assert result == {"category": "Reactions", "location_id": 1000000000001}
    assert storage.load_category_locations() == {"Reactions": 1000000000001}
    # setting a category active also remembers it as a quick-switch option
    assert storage.load_category_location_options() == {"Reactions": [1000000000001]}


def test_set_category_location_unknown_category_raises(db):
    with pytest.raises(ActionError):
        actions.do_set_category_location("Not A Real Category", 1000000000001)


def test_clear_category_location(db):
    actions.do_set_category_location("Reactions", 1000000000001)
    result = actions.do_clear_category_location("Reactions")
    assert result == {"category": "Reactions"}
    assert storage.load_category_locations() == {}
    # the quick-switch option itself is untouched by clearing the active assignment
    assert storage.load_category_location_options() == {"Reactions": [1000000000001]}


def test_add_and_remove_category_location_option(db):
    result = actions.do_add_category_location_option("Reactions", 1000000000001)
    assert result == {"category": "Reactions", "location_id": 1000000000001}
    assert storage.load_category_location_options() == {"Reactions": [1000000000001]}

    result = actions.do_remove_category_location_option("Reactions", 1000000000001)
    assert result == {"category": "Reactions", "location_id": 1000000000001}
    assert storage.load_category_location_options() == {}


def test_add_category_location_option_unknown_category_raises(db):
    with pytest.raises(ActionError):
        actions.do_add_category_location_option("Not A Real Category", 1000000000001)


def test_list_category_locations(db):
    actions.do_set_category_location("Reactions", 1000000000001)
    actions.do_add_category_location_option("Reactions", 1000000000002)

    result = actions.do_list_category_locations()
    assert result == {
        "assigned": {"Reactions": 1000000000001},
        "options": {"Reactions": [1000000000001, 1000000000002]},
    }


# ---------------------------------------------------------------- settings
def test_update_settings_persists_and_applies(db):
    cfg = ProductionConfig()
    result = actions.do_update_settings({"haul_cost_per_m3": 1200.0}, cfg)
    assert result == {"updated": ["haul_cost_per_m3"]}
    assert cfg.haul_cost_per_m3 == 1200.0
    assert storage.load_settings("production")["haul_cost_per_m3"] == 1200.0


def test_update_settings_rejects_a_bad_value_without_persisting(db):
    cfg = ProductionConfig()
    with pytest.raises(ActionError):
        actions.do_update_settings({"haul_cost_per_m3": -5.0}, cfg)
    assert storage.load_settings("production") == {}
    assert cfg.haul_cost_per_m3 == ProductionConfig().haul_cost_per_m3


def test_update_settings_rejects_unknown_structure_type(db):
    cfg = ProductionConfig()
    with pytest.raises(ActionError):
        actions.do_update_settings({"reaction_structure_type": "Not A Real Structure"}, cfg)
    assert storage.load_settings("production") == {}
