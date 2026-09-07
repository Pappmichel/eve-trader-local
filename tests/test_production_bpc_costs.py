"""Tests for manual blueprint-copy costs (GitHub issue #40): storage,
production/actions.py do_* wrappers, engine._unit_cost folding, and the
Owned Blueprints GUI section."""
from __future__ import annotations

import sqlite3

import pytest

from eve_trader_local import storage
from eve_trader_local.errors import ActionError
from eve_trader_local.goonmetrics_client import CurrentPrice
from eve_trader_local.production import actions, engine
from eve_trader_local.production.config import ProductionConfig
from eve_trader_local.production.models import ManualBlueprintCopyCostRow

TRITANIUM = 34
SHIP = 90001
SHIP_BP = 91001
MINERAL = 34


def _seed_sde():
    storage.replace_sde_data(
        types=[
            (TRITANIUM, 18, "Tritanium", 0.01, 1, 100, 0, 1, 1),
            (SHIP, 25, "Test Frigate", 27289.0, 1, 400, 0, 1, 1),
            (SHIP_BP, 9, "Test Frigate Blueprint", 0.01, 1, None, 0, None, 1),
        ],
        groups=[(18, 4, "Mineral"), (25, 6, "Frigate"), (9, 9, "Blueprint")],
        market_groups=[(100, None, "Manufacture & Research"), (400, None, "Ships")],
        blueprint_time=[],
        blueprint_materials=[(SHIP_BP, 1, MINERAL, 100)],
        blueprint_products=[(SHIP_BP, 1, SHIP, 1)],
        categories=[(4, "Material"), (6, "Ship"), (9, "Blueprint")],
    )
    storage.set_cached_packaged_volume(SHIP, 2500.0)


def _cfg() -> ProductionConfig:
    return ProductionConfig(jita_buy_broker_fee=0.0, haul_cost_per_m3=0.0,
                            facility_tax_rate=0.0, market_fees=0.0)


# Ship itself has no buy quote, so _unit_cost can only come from building.
HOME = {MINERAL: CurrentPrice(type_id=MINERAL, updated="", buy=4.5, sell=5.0)}
COST_INDICES = {"component": {"manufacturing": 0.10, "reaction": 0.10},
                "manufacturing": {"manufacturing": 0.05, "reaction": 0.05}}
ADJUSTED = {MINERAL: 4.0}


@pytest.fixture
def bpc_sde(db):
    _seed_sde()
    return db


def _unit_cost():
    return engine._unit_cost(SHIP, _cfg(), HOME, {}, {}, {}, {}, COST_INDICES, ADJUSTED)


# ------------------------------------------------------------------- storage


def test_load_manual_blueprint_copy_costs_returns_tuples_not_rows(bpc_sde):
    storage.upsert_manual_blueprint_copy_cost(TRITANIUM, "Tritanium", 1_000_000.0, 10)
    rows = storage.load_manual_blueprint_copy_costs()
    assert rows == [(TRITANIUM, "Tritanium", 1_000_000.0, 10)]
    assert type(rows[0]) is tuple
    assert not isinstance(rows[0], sqlite3.Row)


def test_get_manual_blueprint_copy_cost_per_run_amortizes(bpc_sde):
    storage.upsert_manual_blueprint_copy_cost(TRITANIUM, "Tritanium", 1_000_000.0, 10)
    assert storage.get_manual_blueprint_copy_cost_per_run(TRITANIUM) == pytest.approx(100_000.0)


def test_get_manual_blueprint_copy_cost_per_run_none_when_missing_or_zero_runs(bpc_sde):
    assert storage.get_manual_blueprint_copy_cost_per_run(TRITANIUM) is None
    storage.upsert_manual_blueprint_copy_cost(TRITANIUM, "Tritanium", 1_000_000.0, 0)
    assert storage.get_manual_blueprint_copy_cost_per_run(TRITANIUM) is None


def test_update_manual_blueprint_copy_cost_missing_row_returns_false(bpc_sde):
    assert storage.update_manual_blueprint_copy_cost(TRITANIUM, 2_000_000.0, 5) is False
    assert storage.load_manual_blueprint_copy_costs() == []


# ------------------------------------------------------------------- actions


def test_add_bpc_cost_by_name_and_list(bpc_sde):
    result = actions.do_add_manual_blueprint_copy_cost("Tritanium", 1_000_000.0, 10)
    assert result == {"type_id": TRITANIUM, "type_name": "Tritanium",
                      "purchase_cost": 1_000_000.0, "runs": 10}
    rows = actions.do_list_manual_blueprint_copy_costs()["rows"]
    assert len(rows) == 1
    assert isinstance(rows[0], ManualBlueprintCopyCostRow)
    assert rows[0].type_id == TRITANIUM
    assert rows[0].cost_per_run == pytest.approx(100_000.0)


def test_add_bpc_cost_by_type_id(bpc_sde):
    result = actions.do_add_manual_blueprint_copy_cost(str(TRITANIUM), 500_000.0, 5)
    assert result["type_id"] == TRITANIUM
    assert storage.load_manual_blueprint_copy_costs() == [(TRITANIUM, "Tritanium", 500_000.0, 5)]


def test_add_bpc_cost_is_upsert(bpc_sde):
    actions.do_add_manual_blueprint_copy_cost("Tritanium", 1_000_000.0, 10)
    actions.do_add_manual_blueprint_copy_cost("Tritanium", 2_000_000.0, 5)
    rows = actions.do_list_manual_blueprint_copy_costs()["rows"]
    assert len(rows) == 1
    assert rows[0].purchase_cost == 2_000_000.0
    assert rows[0].runs == 5
    assert rows[0].cost_per_run == pytest.approx(400_000.0)


def test_update_bpc_cost_existing_row(bpc_sde):
    actions.do_add_manual_blueprint_copy_cost("Tritanium", 1_000_000.0, 10)
    result = actions.do_update_manual_blueprint_copy_cost("Tritanium", 3_000_000.0, 15)
    assert result["purchase_cost"] == 3_000_000.0
    assert result["runs"] == 15
    rows = actions.do_list_manual_blueprint_copy_costs()["rows"]
    assert rows[0].purchase_cost == 3_000_000.0
    assert rows[0].runs == 15


def test_update_bpc_cost_missing_row_raises(bpc_sde):
    with pytest.raises(ActionError, match="No registered blueprint copy cost"):
        actions.do_update_manual_blueprint_copy_cost("Tritanium", 1_000_000.0, 10)


def test_remove_bpc_cost(bpc_sde):
    actions.do_add_manual_blueprint_copy_cost("Tritanium", 1_000_000.0, 10)
    result = actions.do_remove_manual_blueprint_copy_cost("Tritanium")
    assert result["removed"] == TRITANIUM
    assert actions.do_list_manual_blueprint_copy_costs()["rows"] == []


def test_add_bpc_cost_rejects_nonpositive_cost_and_runs(bpc_sde):
    with pytest.raises(ActionError, match="Purchase cost"):
        actions.do_add_manual_blueprint_copy_cost("Tritanium", 0, 10)
    with pytest.raises(ActionError, match="Purchase cost"):
        actions.do_add_manual_blueprint_copy_cost("Tritanium", -1, 10)
    with pytest.raises(ActionError, match="Runs"):
        actions.do_add_manual_blueprint_copy_cost("Tritanium", 1_000_000.0, 0)
    with pytest.raises(ActionError, match="Runs"):
        actions.do_add_manual_blueprint_copy_cost("Tritanium", 1_000_000.0, -2)


def test_add_bpc_cost_unknown_name_raises(bpc_sde):
    with pytest.raises(ActionError, match="No type found"):
        actions.do_add_manual_blueprint_copy_cost("Nonexistent Item", 1_000_000.0, 10)


def test_update_bpc_cost_rejects_nonpositive_inputs(bpc_sde):
    actions.do_add_manual_blueprint_copy_cost("Tritanium", 1_000_000.0, 10)
    with pytest.raises(ActionError, match="Purchase cost"):
        actions.do_update_manual_blueprint_copy_cost("Tritanium", 0, 10)
    with pytest.raises(ActionError, match="Runs"):
        actions.do_update_manual_blueprint_copy_cost("Tritanium", 1_000_000.0, 0)


# ------------------------------------------------------------------- engine


def test_unit_cost_unchanged_without_bpc_costs(bpc_sde):
    first = _unit_cost()
    second = _unit_cost()
    assert first is not None
    assert first == pytest.approx(second)


def test_do_add_raises_unit_cost_and_do_remove_restores_baseline(bpc_sde):
    """Ship has no buy quote, so _unit_cost is the modeled build cost. A
    1,000,000 ISK copy good for 10 runs (1 unit/run) adds 100,000 ISK/unit."""
    baseline = _unit_cost()
    assert baseline is not None

    actions.do_add_manual_blueprint_copy_cost(str(SHIP), 1_000_000.0, 10)
    with_bpc = _unit_cost()
    assert with_bpc == pytest.approx(baseline + 100_000.0)

    _, build_cost, buy_price = engine.unit_cost_detail(
        SHIP, _cfg(), HOME, {}, {}, {}, {}, COST_INDICES, ADJUSTED)
    assert buy_price is None
    assert build_cost == pytest.approx(with_bpc)

    actions.do_remove_manual_blueprint_copy_cost(str(SHIP))
    restored = _unit_cost()
    assert restored == pytest.approx(baseline)
