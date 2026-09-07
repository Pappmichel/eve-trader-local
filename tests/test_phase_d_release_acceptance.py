"""Phase D release-acceptance workflows for the frozen Special-Order core.

Composed end-to-end paths (create → upsert → compute; combined pooling;
stock-mode isolation; T2 invention). Individual invariants stay in
tests/test_phase_c_adversarial_audit.py and tests/test_production_*.py —
this module does not re-state those attacks.
"""
from __future__ import annotations

import os
import time

import pytest

from eve_trader_local import storage
from eve_trader_local.production import actions, engine

from test_production_invention_needs import (
    JITA, T2_MODULE, T2_MODULE_B, TRITANIUM,
    _cfg as _invention_cfg, _seed as _seed_invention,
)
from test_production_special_orders import (
    COMPONENT, FINISHED_A, FINISHED_B, HOME, _cfg, _seed,
)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytestmark = pytest.mark.release


@pytest.fixture
def order_sde(db, monkeypatch):
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


@pytest.fixture
def invention_sde(db, monkeypatch):
    _seed_invention()
    monkeypatch.setattr(engine.pricing, "home_prices", lambda type_ids, cfg=None, client=None: {})
    monkeypatch.setattr(engine.pricing, "jita_prices", lambda type_ids, client=None, trading_cfg=None: JITA)

    class _FakeESIClient:
        def __init__(self, *a, **kw):
            pass

        def get_adjusted_prices(self):
            return {}

    import eve_trader_local.esi_client as esi_client_module
    monkeypatch.setattr(esi_client_module, "ESIClient", _FakeESIClient)
    return db


@pytest.fixture(scope="session")
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def _wait_for_threads(qapp, view, timeout=5):
    deadline = time.monotonic() + timeout
    while view._threads and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)


def _runs(plan: dict) -> dict[int, int]:
    return {row.type_id: row.job_runs for row in plan["build_list"]}


def _lines(plan: dict) -> dict[int, float]:
    return {row.type_id: row.quantity for row in plan["line_items"]}


def _fingerprint(plan: dict) -> dict:
    return {
        "line": frozenset((r.type_id, r.quantity) for r in plan["line_items"]),
        "build": frozenset((r.type_id, r.job_runs) for r in plan["build_list"]),
        "buy": frozenset((r.type_id, r.quantity) for r in plan["buy_list"]),
        "inv": frozenset((r.type_id, r.runs_needed) for r in plan["invention_list"]),
    }


def _persistent_orders() -> dict:
    orders = storage.list_special_orders()
    return {
        "orders": orders,
        "items": {row[0]: storage.list_special_order_items(row[0]) for row in orders},
    }


# ============================================================== Scenario A


def test_scenario_a_single_order_lifecycle(order_sde, monkeypatch):
    planner_calls = []
    real = engine.plan_special_order

    def _wrap(items, cfg, net_against_stock):
        planner_calls.append(tuple((t, q) for t, _n, q in items))
        return real(items, cfg, net_against_stock)

    monkeypatch.setattr(engine, "plan_special_order", _wrap)

    created = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}], note="lifecycle")
    order_id = created["order_id"]
    assert planner_calls == []

    first = actions.do_set_special_order_item(order_id, "Finished Widget A", 15.0)
    second = actions.do_set_special_order_item(order_id, "Finished Widget A", 15.0)
    assert planner_calls == []
    assert len(first["items"]) == len(second["items"]) == 1
    assert second["items"][0]["quantity"] == 15.0

    plan = actions.do_compute_special_order(order_id, cfg=_cfg())
    assert planner_calls == [((FINISHED_A, 15.0),)]
    assert _lines(plan) == {FINISHED_A: 15.0}
    assert _runs(plan)[FINISHED_A] == 15
    assert plan["invention_list"] == []


# ============================================================== Scenario B


def test_scenario_b_combined_workflow(order_sde, monkeypatch):
    planner_calls = []
    real = engine.plan_special_order

    def _wrap(items, cfg, net_against_stock):
        planner_calls.append(tuple((t, q) for t, _n, q in items))
        return real(items, cfg, net_against_stock)

    monkeypatch.setattr(engine, "plan_special_order", _wrap)

    first = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}])["order_id"]
    second = actions.do_create_special_order(
        [{"type_id": FINISHED_B, "quantity": 10.0}])["order_id"]
    actions.do_set_special_order_item(second, "Finished Widget B", 4.0)
    before = _persistent_orders()

    plan = actions.do_compute_combined_special_orders(
        [first, second], net_against_stock=False, cfg=_cfg())

    assert len(planner_calls) == 1
    assert dict(planner_calls[0]) == {FINISHED_A: 10.0, FINISHED_B: 4.0}
    assert _lines(plan)[FINISHED_A] == 10.0
    assert _lines(plan)[FINISHED_B] == 4.0
    assert _runs(plan)[FINISHED_A] == 10
    assert _runs(plan)[FINISHED_B] == 4
    assert COMPONENT in _runs(plan)
    assert _persistent_orders() == before


# ============================================================== Scenario C


def test_scenario_c_stock_modes_round_trip(order_sde):
    storage.replace_assets("character_assets", [
        (1, FINISHED_A, 60003760, "Hangar", 100, 0, "Test Character"),
        (2, COMPONENT, 60003760, "Hangar", 6, 0, "Test Character"),
    ])
    first = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}], net_against_stock=True)["order_id"]
    second = actions.do_create_special_order(
        [{"type_id": FINISHED_B, "quantity": 10.0}], net_against_stock=False)["order_id"]
    before = _persistent_orders()

    scratch_1 = _fingerprint(actions.do_compute_combined_special_orders(
        [first, second], net_against_stock=False, cfg=_cfg()))
    netted = actions.do_compute_combined_special_orders(
        [first, second], net_against_stock=True, cfg=_cfg())
    scratch_2 = _fingerprint(actions.do_compute_combined_special_orders(
        [first, second], net_against_stock=False, cfg=_cfg()))

    assert scratch_1 == scratch_2
    assert scratch_1 != _fingerprint(netted)
    assert _runs(netted)[FINISHED_A] == 10
    assert _runs(netted)[FINISHED_B] == 10
    assert _persistent_orders() == before
    assert actions.do_get_special_order(first)["order"].net_against_stock is True
    assert actions.do_get_special_order(second)["order"].net_against_stock is False


# ============================================================== Scenario D


def test_scenario_d_invention_aggregation(invention_sde):
    same_a = actions.do_create_special_order(
        [{"type_id": T2_MODULE, "quantity": 40.0}])["order_id"]
    same_b = actions.do_create_special_order(
        [{"type_id": T2_MODULE, "quantity": 60.0}])["order_id"]
    other = actions.do_create_special_order(
        [{"type_id": T2_MODULE_B, "quantity": 10.0}])["order_id"]
    leaf = actions.do_create_special_order(
        [{"type_id": TRITANIUM, "quantity": 100.0}])["order_id"]

    storage.replace_assets("character_assets", [
        (1, T2_MODULE, 60003760, "Hangar", 80, 0, "Test Character"),
    ])
    before = _persistent_orders()

    pooled = actions.do_compute_combined_special_orders(
        [same_a, same_b], net_against_stock=True, cfg=_invention_cfg())
    mixed = actions.do_compute_combined_special_orders(
        [same_a, other, leaf], net_against_stock=True, cfg=_invention_cfg())

    assert len(pooled["invention_list"]) == 1
    assert pooled["invention_list"][0].type_id == T2_MODULE
    assert pooled["invention_list"][0].runs_needed == 100
    by_type = {row.type_id: row.runs_needed for row in mixed["invention_list"]}
    assert by_type == {T2_MODULE: 40, T2_MODULE_B: 10}
    assert TRITANIUM not in by_type
    assert _persistent_orders() == before


# ============================================================== Error / UX


def test_cli_error_paths_use_stderr_and_exit_1(order_sde, capsys):
    from eve_trader_local.cli import main

    order_id = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}])["order_id"]

    assert main(["compute-special-order", "missing"]) == 1
    assert "not found" in capsys.readouterr().err

    assert main(["set-special-order-item", order_id, "Finished Widget A", "-3"]) == 1
    assert "must be positive" in capsys.readouterr().err

    assert main(["compute-combined-special-orders", order_id, "missing"]) == 1
    assert "not found" in capsys.readouterr().err

    assert actions.do_get_special_order(order_id)["items"][0]["quantity"] == 10.0


def test_cli_empty_sde_errors_without_orders(db, capsys):
    from eve_trader_local.cli import main

    assert main(["compute-combined-special-orders", "anything"]) == 1
    assert "SDE cache is empty" in capsys.readouterr().err
    assert storage.list_special_orders() == []


def test_gui_invalid_inputs_do_not_break_tables(order_sde, qapp):
    from eve_trader_local.gui.views.production_special_orders import SpecialOrdersView

    order_id = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}])["order_id"]
    view = SpecialOrdersView()
    assert view.orders_table.rowCount() == 1
    view.combine_ids_input.clear()
    view._compute_combined()
    assert "at least one order id" in view.status_label.text().lower()
    assert view.build_table_widget.rowCount() == 0
    assert view.orders_table.rowCount() == 1

    view.order_id_input.setText(order_id)
    view.edit_item_input.setText("Finished Widget A")
    view.edit_qty_input.setText("0")
    view._set_item()
    _wait_for_threads(qapp, view)
    assert view.orders_table.rowCount() == 1
    assert actions.do_get_special_order(order_id)["items"][0]["quantity"] == 10.0
    assert view.build_table_widget.rowCount() == 0
