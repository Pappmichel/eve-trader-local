"""Phase E.2 — special-order persistence / integrity (core planner frozen)."""
from __future__ import annotations

import pytest

from eve_trader_local import storage
from eve_trader_local.errors import ActionError
from eve_trader_local.production import actions, engine

from test_production_special_orders import FINISHED_A, FINISHED_B, _cfg, _seed

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


def test_create_pools_duplicate_type_ids(order_sde):
    order_id = actions.do_create_special_order([
        {"type_id": FINISHED_A, "quantity": 10.0},
        {"type_id": FINISHED_A, "quantity": 5.0},
        {"type_id": FINISHED_B, "quantity": 2.0},
    ])["order_id"]
    items = actions.do_get_special_order(order_id)["items"]
    by_id = {row["type_id"]: row["quantity"] for row in items}
    assert by_id == {FINISHED_A: 15.0, FINISHED_B: 2.0}
    assert len(storage.list_special_order_items(order_id)) == 2


def test_create_is_one_transaction(order_sde):
    """Unknown type is rejected before any write — no empty header leftover."""
    before = {row.order_id for row in actions.do_list_special_orders()}
    with pytest.raises(ActionError, match="Unknown type_id"):
        actions.do_create_special_order([
            {"type_id": FINISHED_A, "quantity": 1.0},
            {"type_id": 999999, "quantity": 1.0},
        ])
    after = {row.order_id for row in actions.do_list_special_orders()}
    assert after == before


def test_list_special_orders_status_filter(order_sde):
    open_id = actions.do_create_special_order([{"type_id": FINISHED_A, "quantity": 1.0}])["order_id"]
    done_id = actions.do_create_special_order([{"type_id": FINISHED_B, "quantity": 1.0}])["order_id"]
    actions.do_update_special_order(done_id, status="done")
    listed = {row.order_id: row.status for row in actions.do_list_special_orders()}
    assert listed[open_id] == "open"
    assert listed[done_id] == "done"
    assert {row.order_id for row in actions.do_list_special_orders(status="open")} == {open_id}
    assert {row.order_id for row in actions.do_list_special_orders(status="done")} == {done_id}
    with pytest.raises(ActionError, match="Unknown status"):
        actions.do_list_special_orders(status="bogus")


def test_update_net_against_stock_does_not_touch_items_or_planner(order_sde, monkeypatch):
    order_id = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 4.0}], net_against_stock=False)["order_id"]
    real_plan = engine.plan_special_order
    calls = []

    def _wrapped(*a, **k):
        calls.append((a, k))
        return real_plan(*a, **k)

    monkeypatch.setattr(engine, "plan_special_order", _wrapped)
    updated = actions.do_update_special_order(order_id, net_against_stock=True)
    assert updated["order"].net_against_stock is True
    assert updated["items"] == [
        {"type_id": FINISHED_A, "type_name": "Finished Widget A", "quantity": 4.0}]
    assert calls == []
    combined = actions.do_compute_combined_special_orders(
        [order_id], net_against_stock=False, cfg=_cfg())
    assert combined["line_items"][0].quantity == 4.0
    assert storage.get_special_order(order_id)[2] is True
    assert len(calls) == 1


def test_lifecycle_events_are_append_only(order_sde):
    order_id = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 1.0}])["order_id"]
    actions.do_set_special_order_item(order_id, "Finished Widget B", 2.0)
    actions.do_remove_special_order_item(order_id, "Finished Widget B")
    actions.do_update_special_order(order_id, status="done")
    actions.do_remove_special_order(order_id)
    events = [row["event"] for row in actions.do_list_special_order_events(order_id)["rows"]]
    assert events == ["created", "item_set", "item_removed", "updated", "deleted"]
    with pytest.raises(ActionError):
        actions.do_get_special_order(order_id)
    assert actions.do_list_special_order_events(order_id)["rows"][-1]["event"] == "deleted"


def test_audit_reports_empty_order_and_orphan_item(order_sde):
    healthy = actions.do_create_special_order([{"type_id": FINISHED_A, "quantity": 1.0}])["order_id"]
    empty_id = storage.create_special_order("empty", False)
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO special_order_items (order_id, type_id, type_name, quantity) "
            "VALUES (?, ?, ?, ?)",
            ("missing-order", FINISHED_B, "Finished Widget B", 3.0),
        )
    result = actions.do_audit_special_orders()
    assert result["ok"] is False
    kinds = {(issue["kind"], issue["order_id"]) for issue in result["issues"]}
    assert ("empty_order", empty_id) in kinds
    assert ("orphan_item", "missing-order") in kinds
    assert not any(issue["order_id"] == healthy for issue in result["issues"])


def test_audit_and_events_do_not_call_the_planner(order_sde, monkeypatch):
    actions.do_create_special_order([{"type_id": FINISHED_A, "quantity": 1.0}])
    calls = []
    monkeypatch.setattr(engine, "plan_special_order", lambda *a, **k: calls.append(1) or {})
    assert actions.do_audit_special_orders()["ok"] is True
    actions.do_list_special_order_events()
    assert calls == []


def test_cli_list_status_filter_and_audit(order_sde, capsys):
    from eve_trader_local.cli import main

    open_id = actions.do_create_special_order([{"type_id": FINISHED_A, "quantity": 1.0}])["order_id"]
    done_id = actions.do_create_special_order([{"type_id": FINISHED_B, "quantity": 1.0}])["order_id"]
    assert main(["update-special-order", done_id, "--status", "done", "--net-against-stock"]) == 0
    capsys.readouterr()
    assert actions.do_get_special_order(done_id)["order"].net_against_stock is True
    assert main(["list-special-orders", "--status", "open"]) == 0
    out = capsys.readouterr().out
    assert open_id in out
    assert done_id not in out
    assert main(["audit-special-orders"]) == 0
    assert "ok" in capsys.readouterr().out
    assert main(["list-special-order-events", open_id]) == 0
    assert "created" in capsys.readouterr().out
