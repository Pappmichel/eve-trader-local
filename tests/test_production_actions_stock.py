"""Tests for production/actions.py's stock-target do_* wrappers - name/type_id
resolution and the thin CRUD around storage.stock_targets."""
from __future__ import annotations

import pytest

from eve_trader_local import storage
from eve_trader_local.errors import ActionError
from eve_trader_local.production import actions

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
