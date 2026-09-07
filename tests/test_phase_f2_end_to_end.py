"""Phase F.2 — realistic operator workflows across E.2–E.5 (core frozen).

These are full sessions, not pairwise feature checks. Persistence is the
SQLite file; a restart is a new SpecialOrdersView / a fresh CLI read.
"""
from __future__ import annotations

import os
import time

import pytest

from eve_trader_local import storage
from eve_trader_local.production import actions, engine, jobs, preview_refresh
from eve_trader_local.production.config import PRODUCTION_CONFIG

from test_production_invention_needs import (
    JITA, T2_MODULE, T2_MODULE_B, TRITANIUM,
    _cfg as _invention_cfg, _seed as _seed_invention,
)
from test_production_special_orders import (
    COMPONENT, FINISHED_A, FINISHED_B, HOME, MINERAL, _cfg, _seed,
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


@pytest.fixture
def unpriced_sde(db, monkeypatch):
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


def _items(order_id: str) -> dict[int, float]:
    return {r["type_id"]: r["quantity"] for r in actions.do_get_special_order(order_id)["items"]}


def _lines(plan: dict) -> dict[int, float]:
    return {row.type_id: row.quantity for row in plan["line_items"]}


def _runs(plan: dict) -> dict[int, int]:
    return {row.type_id: row.job_runs for row in plan["build_list"]}


def _seed_job(installer="Alice"):
    storage.replace_industry_jobs("character_industry_jobs", [
        (1, 1, FINISHED_BP, FINISHED_A, 1, 60003760, "active", "", "", 1, installer),
    ])


# ================================================= S1 + canonical session


def test_canonical_operator_path_survives_restart(order_sde, qapp, capsys):
    from eve_trader_local.cli import main
    from eve_trader_local.gui.views.production_special_orders import SpecialOrdersView

    _seed_job("Alice")
    assert main(["create-special-order", "Finished Widget A:8", "--note", "session"]) == 0
    capsys.readouterr()
    primary = actions.do_list_special_orders()[0].order_id
    other = actions.do_create_special_order(
        [{"type_id": FINISHED_B, "quantity": 20.0}], note="peer")["order_id"]

    assert main(["set-special-order-item", primary, "Finished Widget B", "3"]) == 0
    capsys.readouterr()
    assert _items(primary) == {FINISHED_A: 8.0, FINISHED_B: 3.0}
    assert _event_names(primary) == ["created", "item_set"]

    view = SpecialOrdersView()
    view.order_id_input.setText(primary)
    view.auto_recompute_checkbox.setChecked(True)
    view.edit_item_input.setText("Finished Widget A")
    view.edit_qty_input.setText("12")
    view._set_item()
    _wait_for_threads(qapp, view)
    assert _items(primary)[FINISHED_A] == 12.0
    assert view.build_table_widget.rowCount() >= 1
    assert "line item" in view.status_label.text()

    view.selected_note_input.setText("ready to build")
    view._save_note()
    _wait_for_threads(qapp, view)
    view.order_id_input.setText(primary)
    view._update_status("done")
    _wait_for_threads(qapp, view)

    header = actions.do_get_special_order(primary)["order"]
    assert header.note == "ready to build"
    assert header.status == "done"
    items_before_restart = _items(primary)
    events_before_restart = _event_names(primary)
    assert events_before_restart == ["created", "item_set", "item_set", "updated", "updated"]

    restarted = SpecialOrdersView()
    assert restarted.auto_recompute_checkbox.isChecked() is False
    restarted.status_filter.setCurrentIndex(restarted.status_filter.findData("done"))
    assert restarted.orders_table.rowCount() == 1
    assert restarted.orders_table.item(0, 0).text() == primary
    assert restarted.orders_table.item(0, 4).text() == "ready to build"
    restarted.order_id_input.setText(primary)
    restarted._load_line_items(primary)
    assert _items(primary) == items_before_restart
    assert _event_names(primary) == events_before_restart

    restarted.combine_ids_input.setText(f"{primary}, {other}")
    restarted.combine_net_checkbox.setChecked(False)
    restarted._compute_combined()
    _wait_for_threads(qapp, restarted)
    assert "source orders unchanged" in restarted.status_label.text()
    combined = actions.do_compute_combined_special_orders(
        [primary, other], net_against_stock=False, cfg=_cfg())
    assert _lines(combined) == {FINISHED_A: 12.0, FINISHED_B: 23.0}
    assert actions.do_get_special_order(primary)["order"].status == "done"
    assert actions.do_get_special_order(other)["order"].status == "open"
    assert _items(primary) == items_before_restart

    assert main(["set-cost-index-override", "manufacturing", "0.3"]) == 0
    assert main(["set-character-job-slots", "Alice", "5", "1", "2"]) == 0
    capsys.readouterr()
    assert _items(primary) == items_before_restart
    assert _event_names(primary) == events_before_restart
    cap = actions.do_character_slot_capacity_overview()["rows"]
    assert any(r.character_name == "Alice" and r.total_slots == 5 for r in cap)
    assert len(jobs.character_slot_overview()) == 1

    final = actions.do_compute_special_order(primary, cfg=_cfg())
    assert _lines(final) == items_before_restart
    assert COMPONENT in _runs(final)
    assert actions.do_audit_special_orders()["ok"] is True
    PRODUCTION_CONFIG.manufacturing_cost_index_override = None


def test_s1_small_single_order(order_sde, capsys):
    from eve_trader_local.cli import main

    assert main(["create-special-order", "Finished Widget A:2"]) == 0
    capsys.readouterr()
    order_id = actions.do_list_special_orders()[0].order_id
    assert main(["set-special-order-item", order_id, "Finished Widget A", "5", "--recompute"]) == 0
    out = capsys.readouterr().out
    assert "Line Items:" in out
    plan = actions.do_compute_special_order(order_id, cfg=_cfg())
    assert _lines(plan) == {FINISHED_A: 5.0}
    assert _runs(plan)[FINISHED_A] == 5
    assert _event_names(order_id) == ["created", "item_set"]


# ================================================= S2 shared components


def test_s2_several_orders_share_components(order_sde, monkeypatch):
    storage.replace_assets("character_assets", [
        (1, COMPONENT, 60003760, "Hangar", 20, 0, "Test Character"),
    ])
    planner_calls = []
    real = engine.plan_special_order

    def _wrap(items, cfg, net_against_stock):
        planner_calls.append(tuple((t, q) for t, _n, q in items))
        return real(items, cfg, net_against_stock)

    monkeypatch.setattr(engine, "plan_special_order", _wrap)

    first = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 40.0}], net_against_stock=True)["order_id"]
    second = actions.do_create_special_order(
        [{"type_id": FINISHED_B, "quantity": 30.0}], net_against_stock=True)["order_id"]
    actions.do_set_special_order_item(second, "Finished Widget A", 10.0)
    before = {
        first: _items(first),
        second: _items(second),
    }

    combined = actions.do_compute_combined_special_orders(
        [first, second], net_against_stock=True, cfg=_cfg())
    assert len(planner_calls) == 1
    assert dict(planner_calls[0]) == {FINISHED_A: 50.0, FINISHED_B: 30.0}
    assert _lines(combined) == {FINISHED_A: 50.0, FINISHED_B: 30.0}
    assert COMPONENT in _runs(combined)
    isolated_a = actions.do_compute_special_order(first, cfg=_cfg())
    isolated_b = actions.do_compute_special_order(second, cfg=_cfg())
    # Isolated nets each subtract the same 20 COMPONENT; Combined subtracts
    # them once, so it must schedule more component runs (SF-2).
    assert _runs(combined)[COMPONENT] > _runs(isolated_a)[COMPONENT] + _runs(isolated_b)[COMPONENT]
    assert _items(first) == before[first]
    assert _items(second) == before[second]


# ================================================= S3 T2 / invention


def test_s3_t2_invention_preview_across_orders(invention_sde):
    first = actions.do_create_special_order(
        [{"type_id": T2_MODULE, "quantity": 40.0}])["order_id"]
    second = actions.do_create_special_order(
        [{"type_id": T2_MODULE, "quantity": 60.0}])["order_id"]
    other = actions.do_create_special_order(
        [{"type_id": T2_MODULE_B, "quantity": 10.0}])["order_id"]
    storage.replace_assets("character_assets", [
        (1, T2_MODULE, 60003760, "Hangar", 80, 0, "Test Character"),
    ])
    before = storage.list_special_order_items(first)

    preview_refresh.set_item_and_preview(first, str(T2_MODULE), 40.0, cfg=_invention_cfg())
    pooled = actions.do_compute_combined_special_orders(
        [first, second], net_against_stock=True, cfg=_invention_cfg())
    mixed = actions.do_compute_combined_special_orders(
        [first, other], net_against_stock=True, cfg=_invention_cfg())

    assert len(pooled["invention_list"]) == 1
    assert pooled["invention_list"][0].runs_needed == 100
    by_type = {row.type_id: row.runs_needed for row in mixed["invention_list"]}
    assert by_type == {T2_MODULE: 40, T2_MODULE_B: 10}
    assert TRITANIUM not in by_type
    assert storage.list_special_order_items(first) == before
    assert _event_names(first) == ["created", "item_set"]


# ================================================= S4 hangar


def test_s4_hangar_does_not_net_top_level_and_does_not_stick_to_combined(order_sde):
    storage.replace_assets("character_assets", [
        (1, FINISHED_A, 60003760, "Hangar", 100, 0, "Test Character"),
        (2, COMPONENT, 60003760, "Hangar", 6, 0, "Test Character"),
    ])
    netted_id = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}], net_against_stock=True)["order_id"]
    scratch_id = actions.do_create_special_order(
        [{"type_id": FINISHED_B, "quantity": 10.0}], net_against_stock=False)["order_id"]

    single = actions.do_compute_special_order(netted_id, cfg=_cfg())
    assert _runs(single)[FINISHED_A] == 10
    assert _runs(single)[COMPONENT] < 18

    scratch_combined = _fingerprint(actions.do_compute_combined_special_orders(
        [netted_id, scratch_id], net_against_stock=False, cfg=_cfg()))
    netted_combined = actions.do_compute_combined_special_orders(
        [netted_id, scratch_id], net_against_stock=True, cfg=_cfg())
    again = _fingerprint(actions.do_compute_combined_special_orders(
        [netted_id, scratch_id], net_against_stock=False, cfg=_cfg()))
    assert scratch_combined == again
    assert scratch_combined != _fingerprint(netted_combined)
    assert _runs(netted_combined)[FINISHED_A] == 10
    assert _runs(netted_combined)[FINISHED_B] == 10
    assert actions.do_get_special_order(netted_id)["order"].net_against_stock is True
    assert actions.do_get_special_order(scratch_id)["order"].net_against_stock is False


# ================================================= S5 missing prices


def test_s5_missing_prices_do_not_mutate_or_expand_bom(unpriced_sde):
    order_id = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}])["order_id"]
    world = {
        "items": storage.list_special_order_items(order_id),
        "orders": storage.list_special_orders(),
        "events": storage.list_special_order_events(order_id),
    }
    plan = actions.do_compute_special_order(order_id, cfg=_cfg())
    assert _lines(plan) == {FINISHED_A: 10.0}
    assert plan["build_list"] == []
    assert plan["invention_list"] == []
    assert {r.type_id: r.quantity for r in plan["buy_list"]} == {FINISHED_A: 10.0}
    assert all(row.unit_price is None for row in plan["buy_list"])
    assert MINERAL not in {r.type_id for r in plan["buy_list"]}
    assert storage.list_special_order_items(order_id) == world["items"]
    assert storage.list_special_orders() == world["orders"]
    assert storage.list_special_order_events(order_id) == world["events"]


# ================================================= S6 restart / S7 CLI↔GUI


def test_s6_restart_restores_orders_not_preview_tables(order_sde, qapp):
    from eve_trader_local.gui.views.production_special_orders import SpecialOrdersView

    order_id = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 7.0}], note="keep")["order_id"]
    first = SpecialOrdersView()
    first.order_id_input.setText(order_id)
    first.auto_recompute_checkbox.setChecked(True)
    first.edit_item_input.setText("Finished Widget A")
    first.edit_qty_input.setText("9")
    first._set_item()
    _wait_for_threads(qapp, first)
    assert first.build_table_widget.rowCount() >= 1

    second = SpecialOrdersView()
    assert second.auto_recompute_checkbox.isChecked() is False
    assert second.build_table_widget.rowCount() == 0
    assert second.orders_table.rowCount() == 1
    assert second.orders_table.item(0, 4).text() == "keep"
    assert _items(order_id) == {FINISHED_A: 9.0}


def test_s7_cli_gui_handoff_same_rows(order_sde, qapp, capsys):
    from eve_trader_local.cli import main
    from eve_trader_local.gui.views.production_special_orders import SpecialOrdersView

    assert main(["create-special-order", "Finished Widget A:6", "--note", "cli"]) == 0
    capsys.readouterr()
    order_id = actions.do_list_special_orders()[0].order_id
    view = SpecialOrdersView()
    view.order_id_input.setText(order_id)
    view.edit_item_input.setText("Finished Widget B")
    view.edit_qty_input.setText("2")
    view._set_item()
    _wait_for_threads(qapp, view)
    view.selected_note_input.setText("shared")
    view._save_note()
    _wait_for_threads(qapp, view)

    assert main(["list-special-orders"]) == 0
    listed = capsys.readouterr().out
    assert order_id in listed
    assert "shared" in listed
    assert main(["compute-special-order", order_id]) == 0
    computed = capsys.readouterr().out
    assert "Finished Widget A" in computed
    assert "Finished Widget B" in computed
    detail = actions.do_get_special_order(order_id)
    assert detail["order"].note == "shared"
    assert _items(order_id) == {FINISHED_A: 6.0, FINISHED_B: 2.0}
    assert actions.do_audit_special_orders()["ok"] is True
