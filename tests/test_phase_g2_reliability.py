"""Phase G.2 — edit → auto-recompute → reload → compute → combined, repeated.

Determinism and persistence only. The planner is not modified.
"""
from __future__ import annotations

import os
import time

import pytest

from eve_trader_local import storage
from eve_trader_local.production import actions, engine, preview_refresh

from phase_g_scale_harness import fingerprint
from test_production_special_orders import (
    COMPONENT, FINISHED_A, FINISHED_B, HOME, _cfg, _seed,
)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytestmark = pytest.mark.release

CYCLES = 20


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


def _events(order_id: str) -> list[str]:
    return [row["event"] for row in actions.do_list_special_order_events(order_id)["rows"]]


def _qty(order_id: str) -> float:
    return actions.do_get_special_order(order_id)["items"][0]["quantity"]


def test_twenty_edit_recompute_reload_compute_combined_cycles(order_sde):
    storage.replace_assets("character_assets", [
        (1, COMPONENT, 60003760, "Hangar", 5, 0, "Test Character"),
        (2, FINISHED_A, 60003760, "Hangar", 500, 0, "Test Character"),
    ])
    primary = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}], net_against_stock=True)["order_id"]
    peer = actions.do_create_special_order(
        [{"type_id": FINISHED_B, "quantity": 5.0}], net_against_stock=False)["order_id"]

    hangar_before = (
        storage.esi_stock_at_location(COMPONENT, None),
        storage.esi_stock_at_location(FINISHED_A, None),
    )
    combined_net_fingerprints = []
    component_runs = []

    for i in range(CYCLES):
        qty = 10.0 + i
        wrapped = preview_refresh.set_item_and_preview(
            primary, "Finished Widget A", qty, cfg=_cfg())
        reloaded = actions.do_get_special_order(primary)
        assert reloaded["items"][0]["quantity"] == qty
        computed = actions.do_compute_special_order(primary, cfg=_cfg())
        assert fingerprint(wrapped["plan"]) == fingerprint(computed)
        assert computed["line_items"][0].quantity == qty
        build = {r.type_id: r.job_runs for r in computed["build_list"]}
        assert build[FINISHED_A] == int(qty)
        component_runs.append(build[COMPONENT])

        combined = actions.do_compute_combined_special_orders(
            [primary, peer], net_against_stock=False, cfg=_cfg())
        combined_net_fingerprints.append(fingerprint(combined))
        assert combined["line_items"]
        by_type = {r.type_id: r.quantity for r in combined["line_items"]}
        assert by_type[FINISHED_A] == qty
        assert by_type[FINISHED_B] == 5.0
        assert actions.do_get_special_order(peer)["items"][0]["quantity"] == 5.0

    assert _events(primary) == ["created"] + ["item_set"] * CYCLES
    assert _events(peer) == ["created"]
    assert (
        storage.esi_stock_at_location(COMPONENT, None),
        storage.esi_stock_at_location(FINISHED_A, None),
    ) == hangar_before
    # Same qty always yields the same COMPONENT runs (no cumulative netting).
    again = preview_refresh.set_item_and_preview(
        primary, "Finished Widget A", 10.0, cfg=_cfg())
    first_cycle_at_10 = component_runs[0]
    assert {r.type_id: r.job_runs for r in again["plan"]["build_list"]}[COMPONENT] == first_cycle_at_10
    # Combined from-scratch fingerprints differ only because qty changed, not
    # because hangar was drained: repeating the last qty matches the last fingerprint.
    last_qty = 10.0 + (CYCLES - 1)
    preview_refresh.set_item_and_preview(primary, "Finished Widget A", last_qty, cfg=_cfg())
    repeat_last = fingerprint(actions.do_compute_combined_special_orders(
        [primary, peer], net_against_stock=False, cfg=_cfg()))
    assert repeat_last == combined_net_fingerprints[-1]
    assert actions.do_audit_special_orders()["ok"] is True


def test_cli_recompute_matches_wrapper_each_cycle(order_sde, capsys):
    from eve_trader_local.cli import main

    order_id = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 1.0}])["order_id"]
    for qty in (4.0, 7.0, 4.0):
        wrapped = preview_refresh.set_item_and_preview(
            order_id, "Finished Widget A", qty, cfg=_cfg())
        assert main(["set-special-order-item", order_id, "Finished Widget A", str(int(qty)),
                     "--recompute"]) == 0
        capsys.readouterr()
        cli_plan = actions.do_compute_special_order(order_id, cfg=_cfg())
        assert fingerprint(wrapped["plan"]) == fingerprint(cli_plan)
        assert _qty(order_id) == qty
    # Three wrapper sets + three CLI sets after create.
    assert _events(order_id) == ["created"] + ["item_set"] * 6


def test_gui_cycles_do_not_diverge_from_storage(order_sde, qapp):
    from eve_trader_local.gui.views.production_special_orders import SpecialOrdersView

    order_id = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 2.0}])["order_id"]
    view = SpecialOrdersView()
    view.auto_recompute_checkbox.setChecked(True)
    view.order_id_input.setText(order_id)
    for qty in (3, 8, 3):
        view.edit_item_input.setText("Finished Widget A")
        view.edit_qty_input.setText(str(qty))
        view._set_item()
        _wait_for_threads(qapp, view)
        assert _qty(order_id) == float(qty)
        recomputed = actions.do_compute_special_order(order_id, cfg=_cfg())
        assert recomputed["line_items"][0].quantity == float(qty)

    restarted = SpecialOrdersView()
    assert restarted.build_table_widget.rowCount() == 0
    restarted.order_id_input.setText(order_id)
    restarted._load_line_items(order_id)
    assert _qty(order_id) == 3.0
    assert restarted.auto_recompute_checkbox.isChecked() is False
    assert _events(order_id) == ["created"] + ["item_set"] * 3
