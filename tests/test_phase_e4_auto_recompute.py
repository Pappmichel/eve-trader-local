"""Phase E.4 — auto-recompute wrapper. Set/Remove stay planner-pure."""
from __future__ import annotations

import os

import pytest

from eve_trader_local.errors import ActionError
from eve_trader_local.production import actions, engine, preview_refresh

from test_production_special_orders import FINISHED_A, FINISHED_B, _cfg, _seed

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytestmark = pytest.mark.release


@pytest.fixture
def order_sde(db, monkeypatch):
    _seed()
    monkeypatch.setattr(engine.pricing, "home_prices", lambda type_ids, cfg=None, client=None: {})
    monkeypatch.setattr(engine.pricing, "jita_prices", lambda type_ids, client=None, trading_cfg=None: {})

    class _FakeESIClient:
        def __init__(self, *args, **kwargs):
            pass

        def get_adjusted_prices(self):
            return {}

    import eve_trader_local.esi_client as esi_client_module
    monkeypatch.setattr(esi_client_module, "ESIClient", _FakeESIClient)
    return db


def test_set_item_and_preview_runs_compute_after_set(order_sde, monkeypatch):
    order_id = actions.do_create_special_order([{"type_id": FINISHED_A, "quantity": 1.0}])["order_id"]
    calls = []
    real = engine.plan_special_order

    def _wrapped(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(engine, "plan_special_order", _wrapped)
    result = preview_refresh.set_item_and_preview(order_id, "Finished Widget A", 9.0, cfg=_cfg())
    assert result["items"][0]["quantity"] == 9.0
    assert "line_items" in result["plan"]
    assert len(calls) == 1


def test_set_item_without_wrapper_still_does_not_compute(order_sde, monkeypatch):
    order_id = actions.do_create_special_order([{"type_id": FINISHED_A, "quantity": 1.0}])["order_id"]
    calls = []
    monkeypatch.setattr(engine, "plan_special_order", lambda *a, **k: calls.append(1) or {})
    actions.do_set_special_order_item(order_id, "Finished Widget A", 4.0)
    assert calls == []


def test_failed_set_does_not_compute(order_sde, monkeypatch):
    order_id = actions.do_create_special_order([{"type_id": FINISHED_A, "quantity": 1.0}])["order_id"]
    calls = []
    monkeypatch.setattr(engine, "plan_special_order", lambda *a, **k: calls.append(1) or {})
    with pytest.raises(ActionError):
        preview_refresh.set_item_and_preview(order_id, "Finished Widget A", 0, cfg=_cfg())
    assert calls == []


def test_remove_item_and_preview(order_sde):
    order_id = actions.do_create_special_order([
        {"type_id": FINISHED_A, "quantity": 1.0},
        {"type_id": FINISHED_B, "quantity": 2.0},
    ])["order_id"]
    result = preview_refresh.remove_item_and_preview(order_id, "Finished Widget B", cfg=_cfg())
    assert [row["type_id"] for row in result["items"]] == [FINISHED_A]
    assert result["plan"]["line_items"][0].type_id == FINISHED_A


def test_cli_set_item_recompute(order_sde, capsys):
    from eve_trader_local.cli import main

    order_id = actions.do_create_special_order([{"type_id": FINISHED_A, "quantity": 1.0}])["order_id"]
    assert main(["set-special-order-item", order_id, "Finished Widget A", "6", "--recompute"]) == 0
    out = capsys.readouterr().out
    assert "Line Items:" in out
    assert actions.do_get_special_order(order_id)["items"][0]["quantity"] == 6.0


def test_gui_auto_recompute_after_set(qapp, order_sde):
    import time

    pytest.importorskip("PySide6")
    from eve_trader_local.gui.views.production_special_orders import SpecialOrdersView

    view = SpecialOrdersView()
    view.items_input.setText("Finished Widget A:1")
    view._create_order()
    deadline = time.monotonic() + 5
    while view._threads and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    order_id = actions.do_list_special_orders()[0].order_id
    view.order_id_input.setText(order_id)
    view.auto_recompute_checkbox.setChecked(True)
    view.edit_item_input.setText("Finished Widget A")
    view.edit_qty_input.setText("8")
    view._set_item()
    deadline = time.monotonic() + 5
    while view._threads and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert view.line_items_table.rowCount() == 1
    assert view.line_items_table.item(0, 0).text() == "Finished Widget A"
    assert view.line_items_table.item(0, 1).text() == "8"
    assert "line item" in view.status_label.text()
    assert actions.do_get_special_order(order_id)["items"][0]["quantity"] == 8.0


@pytest.fixture(scope="session")
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app
