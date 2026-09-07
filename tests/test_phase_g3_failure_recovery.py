"""Phase G.3 — failed mutations must not leave half-written special orders."""
from __future__ import annotations

import pytest

from eve_trader_local import storage
from eve_trader_local.errors import ActionError
from eve_trader_local.production import actions, engine, preview_refresh

from test_production_special_orders import FINISHED_A, FINISHED_B, HOME, _cfg, _seed

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


def _events(order_id: str) -> list[str]:
    return [row["event"] for row in actions.do_list_special_order_events(order_id)["rows"]]


def _all_event_ids() -> set[int]:
    return {row["event_id"] for row in actions.do_list_special_order_events()["rows"]}


def test_invalid_create_payloads_leave_no_rows(order_sde):
    before_orders = storage.list_special_orders()
    before_events = storage.list_special_order_events()
    with pytest.raises(ActionError):
        actions.do_create_special_order([])
    with pytest.raises(ActionError, match="must be positive"):
        actions.do_create_special_order([{"type_id": FINISHED_A, "quantity": 0}])
    with pytest.raises(ActionError, match="Unknown type_id"):
        actions.do_create_special_order([{"type_id": 999999, "quantity": 1.0}])
    with pytest.raises(ActionError, match="Unknown type_id"):
        actions.do_create_special_order([
            {"type_id": FINISHED_A, "quantity": 1.0},
            {"type_id": 999999, "quantity": 1.0},
        ])
    assert storage.list_special_orders() == before_orders
    assert storage.list_special_order_events() == before_events
    assert actions.do_audit_special_orders()["ok"] is True


def test_failed_set_and_remove_do_not_write_events(order_sde):
    order_id = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 2.0}])["order_id"]
    before = actions.do_get_special_order(order_id)
    event_ids = _all_event_ids()
    with pytest.raises(ActionError, match="must be positive"):
        actions.do_set_special_order_item(order_id, "Finished Widget A", 0)
    with pytest.raises(ActionError, match="not found"):
        actions.do_set_special_order_item("missing", "Finished Widget A", 1)
    with pytest.raises(ActionError, match="at least one item"):
        actions.do_remove_special_order_item(order_id, "Finished Widget A")
    with pytest.raises(ActionError, match="has no item"):
        actions.do_remove_special_order_item(order_id, "Finished Widget B")
    with pytest.raises(ActionError):
        preview_refresh.set_item_and_preview(order_id, "Finished Widget A", -1, cfg=_cfg())
    assert actions.do_get_special_order(order_id)["items"] == before["items"]
    assert _events(order_id) == ["created"]
    assert _all_event_ids() == event_ids
    assert actions.do_audit_special_orders()["ok"] is True


def test_injected_failure_after_header_rolls_back(order_sde, monkeypatch):
    snapshot_orders = storage.list_special_orders()
    snapshot_items = storage.list_all_special_order_item_rows()
    snapshot_events = storage.list_special_order_events()

    def exploding(note, net_against_stock, items, path=None):
        order_id = "injected-partial"
        with storage.connect(path) as conn:
            conn.execute(
                "INSERT INTO special_orders (order_id, note, net_against_stock) VALUES (?, ?, ?)",
                (order_id, note, int(net_against_stock)),
            )
            raise RuntimeError("injected failure after header")
        return order_id

    monkeypatch.setattr(storage, "create_special_order_with_items", exploding)
    with pytest.raises(RuntimeError, match="injected failure"):
        actions.do_create_special_order([{"type_id": FINISHED_A, "quantity": 1.0}])
    assert storage.list_special_orders() == snapshot_orders
    assert storage.list_all_special_order_item_rows() == snapshot_items
    assert storage.list_special_order_events() == snapshot_events
    assert storage.get_special_order("injected-partial") is None
    assert actions.do_audit_special_orders()["ok"] is True


def test_reload_and_audit_after_failures(order_sde):
    healthy = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 4.0}])["order_id"]
    with pytest.raises(ActionError):
        actions.do_create_special_order([{"type_id": FINISHED_B, "quantity": -2}])
    reloaded = actions.do_get_special_order(healthy)
    assert reloaded["items"][0]["quantity"] == 4.0
    empty_id = storage.create_special_order("broken", False)
    result = actions.do_audit_special_orders()
    assert result["ok"] is False
    kinds = {(issue["kind"], issue["order_id"]) for issue in result["issues"]}
    assert ("empty_order", empty_id) in kinds
    assert not any(issue["order_id"] == healthy for issue in result["issues"])
    actions.do_remove_special_order(healthy)
    with pytest.raises(ActionError, match="not found"):
        actions.do_get_special_order(healthy)
    assert _events(healthy)[-1] == "deleted"
    leftover = actions.do_audit_special_orders()
    assert any(issue["order_id"] == empty_id for issue in leftover["issues"])
    assert not any(issue["order_id"] == healthy for issue in leftover["issues"])
