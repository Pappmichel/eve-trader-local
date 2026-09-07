"""Phase E.3 — additive Special-Order UX (create-by-name, filters, preview totals)."""
from __future__ import annotations

import os

import pytest

from eve_trader_local.errors import ActionError
from eve_trader_local.production import actions, engine
from eve_trader_local.production.models import BuyListEntry, SpecialOrderLineItem

from test_production_special_orders import FINISHED_A, FINISHED_B, _seed

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytestmark = pytest.mark.release


@pytest.fixture(scope="session")
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


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


def test_create_accepts_type_id_or_name(order_sde):
    order_id = actions.do_create_special_order([
        {"type_id_or_name": "Finished Widget A", "quantity": 3.0},
        {"name": "Finished Widget B", "quantity": 2.0},
    ])["order_id"]
    items = {(row["type_id"], row["quantity"]) for row in actions.do_get_special_order(order_id)["items"]}
    assert items == {(FINISHED_A, 3.0), (FINISHED_B, 2.0)}


def test_create_still_accepts_type_id(order_sde):
    order_id = actions.do_create_special_order([{"type_id": FINISHED_A, "quantity": 1.0}])["order_id"]
    assert actions.do_get_special_order(order_id)["items"][0]["type_id"] == FINISHED_A


def test_create_by_name_unknown_item_errors(order_sde):
    with pytest.raises(ActionError, match="No type found|No exact match"):
        actions.do_create_special_order([{"type_id_or_name": "Not A Real Item", "quantity": 1.0}])


def test_cli_create_by_name(order_sde):
    from eve_trader_local.cli import main

    assert main(["create-special-order", "Finished Widget A:7"]) == 0
    rows = actions.do_list_special_orders()
    assert len(rows) == 1
    assert actions.do_get_special_order(rows[0].order_id)["items"][0]["quantity"] == 7.0


def test_gui_status_filter_and_note(qapp, db):
    import os
    import time

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from eve_trader_local.gui.views.production_special_orders import SpecialOrdersView

    _seed()
    open_id = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 1.0}], note="keep")["order_id"]
    done_id = actions.do_create_special_order([{"type_id": FINISHED_B, "quantity": 1.0}])["order_id"]
    actions.do_update_special_order(done_id, status="done")

    view = SpecialOrdersView()
    assert view.orders_table.rowCount() == 2
    view.status_filter.setCurrentIndex(view.status_filter.findData("open"))
    assert view.orders_table.rowCount() == 1
    assert view.orders_table.item(0, 0).text() == open_id

    view.order_id_input.setText(open_id)
    view.selected_note_input.setText("updated-note")
    view._save_note()
    deadline = time.monotonic() + 5
    while view._threads and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert actions.do_get_special_order(open_id)["order"].note == "updated-note"
    assert "updated" in view.status_label.text()


def test_gui_buy_total_is_display_only():
    from eve_trader_local.gui.views.production_special_orders import SpecialOrdersView

    plan = {
        "line_items": [SpecialOrderLineItem(FINISHED_A, "Finished Widget A", 1.0)],
        "build_list": [],
        "buy_list": [
            BuyListEntry(FINISHED_A, "Finished Widget A", 1.0, 10.0, 10.0, 0.0, "Jita"),
            BuyListEntry(FINISHED_B, "Finished Widget B", 2.0, None, None, 0.0, "Jita"),
        ],
        "invention_list": [],
        "stock_overlap_warning": [],
    }
    view = SpecialOrdersView.__new__(SpecialOrdersView)
    assert view._buy_total_isk(plan) == 10.0
    assert "buy total" in view._plan_summary(plan, "Computed - ")
    assert plan["buy_list"][0].total_price == 10.0
