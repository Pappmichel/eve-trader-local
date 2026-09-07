"""Phase F.1 — cross-feature integration of E.2–E.5 (core planner frozen).

Each test is a combination that a single Phase E file never runs. Invariants
stay in PRODUCTION_SEMANTICS.md / Phase C; this module only asks whether the
extensions interfere.
"""
from __future__ import annotations

import os
import time

import pytest

from eve_trader_local import storage
from eve_trader_local.errors import ActionError
from eve_trader_local.production import actions, engine, jobs, preview_refresh
from eve_trader_local.production.config import PRODUCTION_CONFIG

from test_production_special_orders import (
    COMPONENT, FINISHED_A, FINISHED_B, HOME, _cfg, _seed,
)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytestmark = pytest.mark.release

FINISHED_BP = 35


@pytest.fixture(autouse=True)
def _reset_cost_index_overrides():
    PRODUCTION_CONFIG.reaction_cost_index_override = None
    PRODUCTION_CONFIG.component_cost_index_override = None
    PRODUCTION_CONFIG.manufacturing_cost_index_override = None
    yield
    PRODUCTION_CONFIG.reaction_cost_index_override = None
    PRODUCTION_CONFIG.component_cost_index_override = None
    PRODUCTION_CONFIG.manufacturing_cost_index_override = None


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


def _event_names(order_id: str) -> list[str]:
    return [row["event"] for row in actions.do_list_special_order_events(order_id)["rows"]]


def _fingerprint(plan: dict) -> dict:
    return {
        "line": frozenset((r.type_id, r.quantity) for r in plan["line_items"]),
        "build": frozenset((r.type_id, r.job_runs) for r in plan["build_list"]),
        "buy": frozenset((r.type_id, r.quantity) for r in plan["buy_list"]),
        "inv": frozenset((r.type_id, r.runs_needed) for r in plan["invention_list"]),
    }


def _order_snapshot(order_id: str) -> dict:
    detail = actions.do_get_special_order(order_id)
    order = detail["order"]
    return {
        "items": [(r["type_id"], r["quantity"]) for r in detail["items"]],
        "note": order.note,
        "status": order.status,
        "net_against_stock": order.net_against_stock,
        "item_count": order.item_count,
    }


def _persistent_world() -> dict:
    orders = storage.list_special_orders()
    return {
        "orders": orders,
        "items": {row[0]: storage.list_special_order_items(row[0]) for row in orders},
        "events": storage.list_special_order_events(),
        "slot_totals": storage.load_character_job_slot_totals(),
    }


def _seed_job(installer="Alice"):
    storage.replace_industry_jobs("character_industry_jobs", [
        (1, 1, FINISHED_BP, FINISHED_A, 1, 60003760, "active", "", "", 1, installer),
    ])


# ================================================= Persistenz × Auto-Recompute


def test_persist_set_autorecompute_reload_compute(order_sde, monkeypatch):
    planner_calls = []
    real = engine.plan_special_order

    def _wrap(items, cfg, net_against_stock):
        planner_calls.append(tuple((t, q) for t, _n, q in items))
        return real(items, cfg, net_against_stock)

    monkeypatch.setattr(engine, "plan_special_order", _wrap)

    order_id = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 4.0}], note="persist-recompute")["order_id"]
    assert planner_calls == []
    assert _event_names(order_id) == ["created"]

    wrapped = preview_refresh.set_item_and_preview(
        order_id, "Finished Widget A", 9.0, cfg=_cfg())
    assert wrapped["items"][0]["quantity"] == 9.0
    assert _event_names(order_id) == ["created", "item_set"]
    assert planner_calls == [((FINISHED_A, 9.0),)]

    reloaded = actions.do_get_special_order(order_id)
    assert reloaded["items"][0]["quantity"] == 9.0
    assert reloaded["order"].note == "persist-recompute"

    after_reload = actions.do_compute_special_order(order_id, cfg=_cfg())
    assert planner_calls == [((FINISHED_A, 9.0),), ((FINISHED_A, 9.0),)]
    assert _fingerprint(wrapped["plan"]) == _fingerprint(after_reload)
    assert _event_names(order_id) == ["created", "item_set"]


def test_wrapper_preview_is_not_a_persisted_plan(order_sde):
    order_id = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 1.0}])["order_id"]
    preview_refresh.set_item_and_preview(order_id, "Finished Widget A", 6.0, cfg=_cfg())
    actions.do_set_special_order_item(order_id, "Finished Widget A", 3.0)
    plan = actions.do_compute_special_order(order_id, cfg=_cfg())
    assert plan["line_items"][0].quantity == 3.0
    assert _event_names(order_id) == ["created", "item_set", "item_set"]


# ================================================= Remove × Events × Reload


def test_remove_events_survive_delete_and_audit_stays_clean(order_sde):
    order_id = actions.do_create_special_order([
        {"type_id": FINISHED_A, "quantity": 1.0},
        {"type_id": FINISHED_B, "quantity": 2.0},
    ])["order_id"]
    actions.do_set_special_order_item(order_id, "Finished Widget B", 5.0)
    actions.do_remove_special_order_item(order_id, "Finished Widget B")
    assert _event_names(order_id) == ["created", "item_set", "item_removed"]
    assert actions.do_audit_special_orders()["ok"] is True

    actions.do_remove_special_order(order_id)
    assert _event_names(order_id) == ["created", "item_set", "item_removed", "deleted"]
    with pytest.raises(ActionError, match="not found"):
        actions.do_get_special_order(order_id)
    audit = actions.do_audit_special_orders()
    assert audit["ok"] is True
    assert not any(issue["order_id"] == order_id for issue in audit["issues"])
    assert storage.list_special_order_items(order_id) == []


def test_failed_remove_does_not_duplicate_events(order_sde):
    order_id = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 1.0}])["order_id"]
    with pytest.raises(ActionError, match="at least one item"):
        actions.do_remove_special_order_item(order_id, "Finished Widget A")
    assert _event_names(order_id) == ["created"]
    assert actions.do_get_special_order(order_id)["items"][0]["type_id"] == FINISHED_A


# ================================================= GUI × CLI × Persistence


def test_cli_create_gui_note_cli_list_compute(order_sde, qapp, capsys):
    from eve_trader_local.cli import main
    from eve_trader_local.gui.views.production_special_orders import SpecialOrdersView

    assert main(["create-special-order", "Finished Widget A:10", "--note", "from-cli"]) == 0
    capsys.readouterr()
    order_id = actions.do_list_special_orders()[0].order_id
    assert actions.do_get_special_order(order_id)["order"].note == "from-cli"

    view = SpecialOrdersView()
    assert view.orders_table.rowCount() == 1
    view.order_id_input.setText(order_id)
    view.selected_note_input.setText("gui-note")
    view._save_note()
    _wait_for_threads(qapp, view)
    assert actions.do_get_special_order(order_id)["order"].note == "gui-note"

    view.edit_item_input.setText("Finished Widget B")
    view.edit_qty_input.setText("4")
    view._set_item()
    _wait_for_threads(qapp, view)

    assert main(["list-special-orders"]) == 0
    listed = capsys.readouterr().out
    assert order_id in listed
    assert "gui-note" in listed
    stored = _order_snapshot(order_id)
    assert stored["note"] == "gui-note"
    assert dict(stored["items"]) == {FINISHED_A: 10.0, FINISHED_B: 4.0}

    assert main(["compute-special-order", order_id]) == 0
    out = capsys.readouterr().out
    assert "Finished Widget A" in out
    assert "Finished Widget B" in out
    plan = actions.do_compute_special_order(order_id, cfg=_cfg())
    assert {r.type_id: r.quantity for r in plan["line_items"]} == {
        FINISHED_A: 10.0, FINISHED_B: 4.0,
    }


def test_cli_create_pooled_duplicates_match_stored_count(order_sde, capsys):
    from eve_trader_local.cli import main

    assert main(["create-special-order", "Finished Widget A:10", "Finished Widget A:5"]) == 0
    out = capsys.readouterr().out
    order_id = actions.do_list_special_orders()[0].order_id
    items = actions.do_get_special_order(order_id)["items"]
    assert len(items) == 1
    assert items[0]["quantity"] == 15.0
    assert "1 item" in out


# ================================================= net_against_stock × Combined


def test_combined_preview_ignores_stored_flags_across_reload(order_sde):
    storage.replace_assets("character_assets", [
        (1, FINISHED_A, 60003760, "Hangar", 100, 0, "Test Character"),
        (2, COMPONENT, 60003760, "Hangar", 6, 0, "Test Character"),
    ])
    first = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}], net_against_stock=True)["order_id"]
    second = actions.do_create_special_order(
        [{"type_id": FINISHED_B, "quantity": 10.0}], net_against_stock=False)["order_id"]
    before_flags = (
        actions.do_get_special_order(first)["order"].net_against_stock,
        actions.do_get_special_order(second)["order"].net_against_stock,
    )
    world_before = _persistent_world()

    scratch_1 = _fingerprint(actions.do_compute_combined_special_orders(
        [first, second], net_against_stock=False, cfg=_cfg()))
    netted = _fingerprint(actions.do_compute_combined_special_orders(
        [first, second], net_against_stock=True, cfg=_cfg()))
    reloaded_first = actions.do_get_special_order(first)["order"].net_against_stock
    reloaded_second = actions.do_get_special_order(second)["order"].net_against_stock
    scratch_2 = _fingerprint(actions.do_compute_combined_special_orders(
        [first, second], net_against_stock=False, cfg=_cfg()))

    assert before_flags == (True, False)
    assert (reloaded_first, reloaded_second) == (True, False)
    assert scratch_1 == scratch_2
    assert scratch_1 != netted
    assert _persistent_world()["orders"] == world_before["orders"]
    assert _persistent_world()["items"] == world_before["items"]


def test_gui_selected_net_does_not_drive_combined(order_sde, qapp):
    from eve_trader_local.gui.views.production_special_orders import SpecialOrdersView

    storage.replace_assets("character_assets", [
        (1, COMPONENT, 60003760, "Hangar", 6, 0, "Test Character"),
    ])
    first = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}], net_against_stock=True)["order_id"]
    second = actions.do_create_special_order(
        [{"type_id": FINISHED_B, "quantity": 10.0}])["order_id"]

    view = SpecialOrdersView()
    view.order_id_input.setText(first)
    view.selected_net_checkbox.setChecked(True)
    view._save_net_against_stock()
    _wait_for_threads(qapp, view)
    assert actions.do_get_special_order(first)["order"].net_against_stock is True

    view.combine_ids_input.setText(f"{first}, {second}")
    view.combine_net_checkbox.setChecked(False)
    view._compute_combined()
    _wait_for_threads(qapp, view)
    assert "source orders unchanged" in view.status_label.text()
    assert "(from scratch)" in view.status_label.text()
    assert actions.do_get_special_order(first)["order"].net_against_stock is True
    assert actions.do_get_special_order(second)["order"].net_against_stock is False

    expected = _fingerprint(actions.do_compute_combined_special_orders(
        [first, second], net_against_stock=False, cfg=_cfg()))
    assert _fingerprint(actions.do_compute_combined_special_orders(
        [first, second], net_against_stock=False, cfg=_cfg())) == expected


# ================================================= Cost indices × Job slots × Production


def test_slot_totals_and_cost_override_do_not_leak_into_orders(order_sde, monkeypatch):
    _seed_job("Alice")
    order_id = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}])["order_id"]
    before = _persistent_world()
    baseline = _fingerprint(actions.do_compute_special_order(order_id, cfg=_cfg()))

    planner_calls = []
    real = engine.plan_special_order

    def _wrap(*a, **k):
        planner_calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(engine, "plan_special_order", _wrap)

    actions.do_set_character_job_slot_totals("Alice", manufacturing=5, reaction=2, science=3)
    actions.do_set_cost_index_override("manufacturing", 0.4)
    cap = actions.do_character_slot_capacity_overview()["rows"]
    used_only = jobs.character_slot_overview()

    assert planner_calls == []
    assert _persistent_world()["orders"] == before["orders"]
    assert _persistent_world()["items"] == before["items"]
    assert _event_names(order_id) == ["created"]
    assert actions.do_list_cost_index_overrides()["manufacturing"] == 0.4
    by_type = {r.job_type: r for r in cap}
    assert by_type["Manufacturing"].total_slots == 5
    assert by_type["Manufacturing"].free_slots == 4
    assert len(used_only) == 1
    assert not hasattr(used_only[0], "total_slots") or not getattr(used_only[0], "total_slots", None)

    after_slots = _fingerprint(actions.do_compute_special_order(order_id, cfg=_cfg()))
    assert after_slots == baseline
    assert "slots" not in after_slots
    assert storage.load_character_job_slot_totals()["Alice"]["manufacturing"] == 5


def test_cli_capacity_and_cost_index_leave_special_orders_alone(order_sde, capsys):
    from eve_trader_local.cli import main

    _seed_job("Alice")
    order_id = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 2.0}])["order_id"]
    assert main(["set-character-job-slots", "Alice", "6", "1", "2"]) == 0
    assert main(["character-slot-capacity"]) == 0
    cap_out = capsys.readouterr().out
    assert "Alice" in cap_out
    assert main(["set-cost-index-override", "reaction", "0.12"]) == 0
    capsys.readouterr()
    assert actions.do_get_special_order(order_id)["items"][0]["quantity"] == 2.0
    assert _event_names(order_id) == ["created"]
    assert actions.do_audit_special_orders()["ok"] is True
    PRODUCTION_CONFIG.reaction_cost_index_override = None
