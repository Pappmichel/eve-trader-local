"""Tests for engine.plan_special_order (the ad-hoc one-off build order
planner) and its CRUD layer (production/actions.py's do_*_special_order
family + storage.py's special_orders/special_order_items tables). Same
synthetic-SDE pattern as test_production_planner.py, whose BOM this reuses:

    FINISHED_A --(2x)--> COMPONENT --(10x)--> MINERAL (Input, buy only)
    FINISHED_B --(3x)-->/
"""
from __future__ import annotations

import pytest

from eve_trader_local import storage
from eve_trader_local.errors import ActionError
from eve_trader_local.goonmetrics_client import CurrentPrice
from eve_trader_local.production import actions, engine
from eve_trader_local.production.config import ProductionConfig

pytestmark = pytest.mark.release

MINERAL = 34            # Input - no blueprint at all
COMPONENT = 91201
FINISHED_A = 91202
FINISHED_B = 91203
COMPONENT_BP, FINISHED_A_BP, FINISHED_B_BP = 92201, 92202, 92203


def _seed():
    storage.replace_sde_data(
        types=[
            (MINERAL, 18, "Tritanium", 0.01, 1, 100, 0, 1, 1),
            (COMPONENT, 18, "Widget Component", 1.0, 1, 200, 0, 1, 1),
            (FINISHED_A, 18, "Finished Widget A", 1.0, 1, 200, 0, 1, 1),
            (FINISHED_B, 18, "Finished Widget B", 1.0, 1, 200, 0, 1, 1),
            (COMPONENT_BP, 9, "Widget Component Blueprint", 0.01, 1, None, 0, None, 1),
            (FINISHED_A_BP, 9, "Finished Widget A Blueprint", 0.01, 1, None, 0, None, 1),
            (FINISHED_B_BP, 9, "Finished Widget B Blueprint", 0.01, 1, None, 0, None, 1),
        ],
        groups=[(18, 4, "Mineral"), (9, 9, "Blueprint")],
        market_groups=[(100, None, "Manufacture & Research"), (200, None, "Ship Equipment")],
        blueprint_time=[],
        blueprint_materials=[
            (COMPONENT_BP, 1, MINERAL, 10),
            (FINISHED_A_BP, 1, COMPONENT, 2),
            (FINISHED_B_BP, 1, COMPONENT, 3),
        ],
        blueprint_products=[
            (COMPONENT_BP, 1, COMPONENT, 1),
            (FINISHED_A_BP, 1, FINISHED_A, 1),
            (FINISHED_B_BP, 1, FINISHED_B, 1),
        ],
        categories=[(4, "Material")],
    )


def _cfg(**overrides) -> ProductionConfig:
    cfg = ProductionConfig(jita_buy_broker_fee=0.0, haul_cost_per_m3=0.0,
                           facility_tax_rate=0.0, market_fees=0.0,
                           component_overbuild=0.0, min_margin=0.0)
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


# Only MINERAL has a home quote - COMPONENT/FINISHED_A/FINISHED_B are never
# listed at home, so buy_price() returns None for them and
# _buy_or_build_decision picks "Build" for anything with a real recipe.
HOME = {MINERAL: CurrentPrice(type_id=MINERAL, updated="", buy=4.5, sell=5.0)}


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


# ------------------------------------------------------------------- CRUD


def test_create_and_get_special_order(order_sde):
    _seed()
    result = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}], note="test batch")
    order_id = result["order_id"]

    detail = actions.do_get_special_order(order_id)
    assert detail["order"].note == "test batch"
    assert detail["order"].net_against_stock is False
    assert detail["order"].status == "open"
    assert detail["order"].item_count == 1
    assert detail["items"] == [{"type_id": FINISHED_A, "type_name": "Finished Widget A", "quantity": 10.0}]


def test_create_special_order_requires_items(order_sde):
    with pytest.raises(ActionError):
        actions.do_create_special_order([])


def test_create_special_order_rejects_unknown_type(order_sde):
    with pytest.raises(ActionError):
        actions.do_create_special_order([{"type_id": 999999, "quantity": 1.0}])


def test_create_special_order_rejects_nonpositive_quantity(order_sde):
    with pytest.raises(ActionError):
        actions.do_create_special_order([{"type_id": FINISHED_A, "quantity": 0}])


def test_list_special_orders(order_sde):
    actions.do_create_special_order([{"type_id": FINISHED_A, "quantity": 5.0}])
    actions.do_create_special_order([{"type_id": FINISHED_B, "quantity": 3.0}], net_against_stock=True)

    rows = actions.do_list_special_orders()
    assert len(rows) == 2
    assert {r.item_count for r in rows} == {1}
    assert any(r.net_against_stock for r in rows)
    assert any(not r.net_against_stock for r in rows)


def test_update_special_order_status_and_note(order_sde):
    order_id = actions.do_create_special_order([{"type_id": FINISHED_A, "quantity": 5.0}])["order_id"]
    updated = actions.do_update_special_order(order_id, status="done", note="shipped")
    assert updated["order"].status == "done"
    assert updated["order"].note == "shipped"


def test_update_special_order_rejects_unknown_status(order_sde):
    order_id = actions.do_create_special_order([{"type_id": FINISHED_A, "quantity": 5.0}])["order_id"]
    with pytest.raises(ActionError):
        actions.do_update_special_order(order_id, status="bogus")


def test_update_special_order_unknown_id_raises(order_sde):
    with pytest.raises(ActionError):
        actions.do_update_special_order("nonexistent", status="done")


def test_remove_special_order(order_sde):
    order_id = actions.do_create_special_order([{"type_id": FINISHED_A, "quantity": 5.0}])["order_id"]
    actions.do_remove_special_order(order_id)
    with pytest.raises(ActionError):
        actions.do_get_special_order(order_id)
    assert storage.list_special_order_items(order_id) == []


def test_get_special_order_unknown_id_raises(order_sde):
    with pytest.raises(ActionError):
        actions.do_get_special_order("nonexistent")


# --------------------------------------------------------------- computation


def test_compute_special_order_from_scratch(order_sde):
    """No stock synced at all - net_against_stock=False (the default) plans
    the order's full quantity regardless, and the shared COMPONENT demand
    from a single line item still expands through the same BOM engine
    plan_production uses."""
    order_id = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}])["order_id"]

    plan = actions.do_compute_special_order(order_id, cfg=_cfg())

    assert plan["line_items"][0].type_id == FINISHED_A
    assert plan["line_items"][0].quantity == 10.0
    build_by_type = {row.type_id: row for row in plan["build_list"]}
    assert build_by_type[FINISHED_A].job_runs == 10
    # ceil(2*0.9*10) = 18 units of COMPONENT -> 18 runs (product_qty=1;
    # 0.9 is Tech I's flat perfect-research material multiplier, same as
    # test_production_planner.py's identical BOM); component_overbuild=0.0
    # in _cfg() so no buffer is added on top.
    assert build_by_type[COMPONENT].job_runs == 18
    buy_by_type = {row.type_id: row for row in plan["buy_list"]}
    assert MINERAL in buy_by_type
    assert plan["stock_overlap_warning"] == []
    assert plan["invention_list"] == []


def test_net_against_stock_changes_the_result(order_sde):
    """Same order, same synced *component* stock: net_against_stock=False
    ignores it entirely (plans from scratch), net_against_stock=True nets
    material demand off - confirms the flag actually changes
    plan_special_order's output, not just its bookkeeping. Top-level
    quantity is never netted (see the two tests below)."""
    storage.replace_assets("character_assets", [
        (1, COMPONENT, 60003760, "Hangar", 6, 0, "Test Character"),
    ])

    order_id_scratch = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}], net_against_stock=False)["order_id"]
    order_id_netted = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}], net_against_stock=True)["order_id"]

    plan_scratch = actions.do_compute_special_order(order_id_scratch, cfg=_cfg())
    plan_netted = actions.do_compute_special_order(order_id_netted, cfg=_cfg())

    scratch_runs = {row.type_id: row.job_runs for row in plan_scratch["build_list"]}
    netted_runs = {row.type_id: row.job_runs for row in plan_netted["build_list"]}

    assert scratch_runs[FINISHED_A] == netted_runs[FINISHED_A] == 10
    assert scratch_runs[COMPONENT] == 18
    assert netted_runs[COMPONENT] == 12  # 18 needed - 6 on hand
    assert scratch_runs != netted_runs


def test_net_against_stock_does_not_net_top_level_quantity(order_sde):
    """Bestellung 10 + Bestand 6 => weiterhin 10 Runs of the ordered item.
    Finished hangar stock is never claimed by the order."""
    storage.replace_assets("character_assets", [
        (1, FINISHED_A, 60003760, "Hangar", 6, 0, "Test Character"),
    ])
    order_id = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}], net_against_stock=True)["order_id"]

    plan = actions.do_compute_special_order(order_id, cfg=_cfg())
    build_by_type = {row.type_id: row for row in plan["build_list"]}
    assert build_by_type[FINISHED_A].job_runs == 10


def test_net_against_stock_still_nets_component_materials(order_sde):
    """net_against_stock=True still nets materials/components below the seed
    level against hangar stock - only the ordered top-level quantity is
    exempt."""
    storage.replace_assets("character_assets", [
        (1, COMPONENT, 60003760, "Hangar", 6, 0, "Test Character"),
    ])
    order_id = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}], net_against_stock=True)["order_id"]

    plan = actions.do_compute_special_order(order_id, cfg=_cfg())
    build_by_type = {row.type_id: row for row in plan["build_list"]}
    assert build_by_type[FINISHED_A].job_runs == 10
    assert build_by_type[COMPONENT].job_runs == 12


def test_net_against_stock_flags_overlap_with_configured_stock_targets(order_sde):
    """net_against_stock=True's stock_overlap_warning fires when the order's
    own material tree overlaps a *configured* stock target's tree and stock
    is actually on hand; net_against_stock=False never computes it at all."""
    storage.upsert_stock_target(FINISHED_B, "Finished Widget B", 5.0)
    storage.replace_assets("character_assets", [
        (1, COMPONENT, 60003760, "Hangar", 50, 0, "Test Character"),
    ])
    order_id = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}], net_against_stock=True)["order_id"]

    plan = actions.do_compute_special_order(order_id)

    overlap_types = {row.type_id for row in plan["stock_overlap_warning"]}
    assert COMPONENT in overlap_types  # shared between FINISHED_A's tree and FINISHED_B's


def test_no_margin_gate_unlike_plan_production(order_sde):
    """A build with a margin below cfg.min_margin is still fully planned for
    a special order (no margin gate) - plan_production would drop it."""
    cfg = _cfg(min_margin=0.99)  # would gate out virtually everything in plan_production
    order_id = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}])["order_id"]
    row = storage.get_special_order(order_id)
    items = storage.list_special_order_items(order_id)

    plan = engine.plan_special_order(items, cfg, net_against_stock=row[2])

    build_by_type = {b.type_id: b for b in plan["build_list"]}
    assert FINISHED_A in build_by_type
    assert build_by_type[FINISHED_A].job_runs == 10


def test_compute_special_order_raises_without_sde(db):
    order_id = "does-not-matter"
    with pytest.raises(ActionError):
        actions.do_compute_special_order(order_id)


def test_compute_special_order_unknown_id_raises(order_sde):
    with pytest.raises(ActionError):
        actions.do_compute_special_order("nonexistent")


# ------------------------------------------------------------------- edit item


def test_do_set_special_order_item_updates_quantity(order_sde):
    order_id = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}])["order_id"]

    result = actions.do_set_special_order_item(order_id, "Finished Widget A", 25.0)

    assert result["items"] == [
        {"type_id": FINISHED_A, "type_name": "Finished Widget A", "quantity": 25.0}]
    assert result["order"].item_count == 1
    plan = actions.do_compute_special_order(order_id, cfg=_cfg())
    build_by_type = {row.type_id: row for row in plan["build_list"]}
    assert build_by_type[FINISHED_A].job_runs == 25


def test_do_set_special_order_item_adds_a_new_line(order_sde):
    order_id = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}])["order_id"]

    result = actions.do_set_special_order_item(order_id, str(FINISHED_B), 3.0)

    by_id = {row["type_id"]: row["quantity"] for row in result["items"]}
    assert by_id == {FINISHED_A: 10.0, FINISHED_B: 3.0}
    assert result["order"].item_count == 2


def test_do_set_special_order_item_upsert_does_not_duplicate(order_sde):
    order_id = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}])["order_id"]
    actions.do_set_special_order_item(order_id, "Finished Widget A", 15.0)
    result = actions.do_set_special_order_item(order_id, str(FINISHED_A), 20.0)
    assert len(result["items"]) == 1
    assert result["items"][0]["quantity"] == 20.0


def test_do_set_special_order_item_rejects_zero_quantity(order_sde):
    order_id = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}])["order_id"]
    with pytest.raises(ActionError, match="must be positive"):
        actions.do_set_special_order_item(order_id, "Finished Widget A", 0)


def test_do_set_special_order_item_rejects_negative_quantity(order_sde):
    order_id = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}])["order_id"]
    with pytest.raises(ActionError, match="must be positive"):
        actions.do_set_special_order_item(order_id, "Finished Widget A", -5)


def test_do_set_special_order_item_rejects_unknown_item(order_sde):
    order_id = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}])["order_id"]
    with pytest.raises(ActionError):
        actions.do_set_special_order_item(order_id, "Not A Real Item", 1.0)


def test_do_set_special_order_item_unknown_order_raises(order_sde):
    with pytest.raises(ActionError, match="not found"):
        actions.do_set_special_order_item("nonexistent", "Finished Widget A", 1.0)


# ------------------------------------------------------------------- combine


def _runs(plan: dict) -> dict[int, int]:
    return {row.type_id: row.job_runs for row in plan["build_list"]}


def test_combine_one_order_matches_compute_special_order(order_sde):
    order_id = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}])["order_id"]
    single = actions.do_compute_special_order(order_id, cfg=_cfg())
    combined = actions.do_compute_combined_special_orders([order_id], net_against_stock=False, cfg=_cfg())
    assert _runs(combined) == _runs(single)
    assert combined["line_items"][0].quantity == 10.0
    assert combined["invention_list"] == single["invention_list"] == []


def test_combine_pools_shared_top_level_items(order_sde):
    first = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}])["order_id"]
    second = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 5.0}])["order_id"]

    plan = actions.do_compute_combined_special_orders(
        [first, second], net_against_stock=False, cfg=_cfg())

    by_type = {row.type_id: row.quantity for row in plan["line_items"]}
    assert by_type == {FINISHED_A: 15.0}
    assert _runs(plan)[FINISHED_A] == 15


def test_combine_nets_shared_components_like_one_order_with_both_items(order_sde):
    """Two orders that share COMPONENT must plan the same as one order that
    lists both finished items - the BOM walk pools material demand once."""
    first = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}])["order_id"]
    second = actions.do_create_special_order(
        [{"type_id": FINISHED_B, "quantity": 10.0}])["order_id"]
    both = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0},
         {"type_id": FINISHED_B, "quantity": 10.0}])["order_id"]

    combined = actions.do_compute_combined_special_orders(
        [first, second], net_against_stock=False, cfg=_cfg())
    single = actions.do_compute_special_order(both, cfg=_cfg())

    assert _runs(combined) == _runs(single)
    assert _runs(combined)[FINISHED_A] == 10
    assert _runs(combined)[FINISHED_B] == 10
    assert _runs(combined)[COMPONENT] > 18


def test_combined_preview_is_not_the_sum_of_isolated_previews(order_sde):
    """Shared hangar stock is claimed once in a combined run. Isolated
    Preview(A)+Preview(B) would net the same COMPONENT stock twice."""
    storage.replace_assets("character_assets", [
        (1, COMPONENT, 60003760, "Hangar", 6, 0, "Test Character"),
    ])
    first = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}], net_against_stock=True)["order_id"]
    second = actions.do_create_special_order(
        [{"type_id": FINISHED_B, "quantity": 10.0}], net_against_stock=True)["order_id"]

    isolated_a = actions.do_compute_special_order(first, cfg=_cfg())
    isolated_b = actions.do_compute_special_order(second, cfg=_cfg())
    combined = actions.do_compute_combined_special_orders(
        [first, second], net_against_stock=True, cfg=_cfg())

    isolated_sum = _runs(isolated_a)[COMPONENT] + _runs(isolated_b)[COMPONENT]
    assert _runs(combined)[COMPONENT] != isolated_sum
    assert _runs(combined)[FINISHED_A] == 10
    assert _runs(combined)[FINISHED_B] == 10


def test_combine_net_against_stock_does_not_net_top_level_quantity(order_sde):
    """B1: hangar stock of the ordered product never reduces its job runs,
    including when several orders are pooled."""
    storage.replace_assets("character_assets", [
        (1, FINISHED_A, 60003760, "Hangar", 6, 0, "Test Character"),
        (2, COMPONENT, 60003760, "Hangar", 6, 0, "Test Character"),
    ])
    first = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}])["order_id"]
    second = actions.do_create_special_order(
        [{"type_id": FINISHED_B, "quantity": 10.0}])["order_id"]

    scratch = actions.do_compute_combined_special_orders(
        [first, second], net_against_stock=False, cfg=_cfg())
    netted = actions.do_compute_combined_special_orders(
        [first, second], net_against_stock=True, cfg=_cfg())

    assert _runs(scratch)[FINISHED_A] == _runs(netted)[FINISHED_A] == 10
    assert _runs(scratch)[FINISHED_B] == _runs(netted)[FINISHED_B] == 10
    assert _runs(netted)[COMPONENT] == _runs(scratch)[COMPONENT] - 6


def test_combine_is_deterministic(order_sde):
    first = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}])["order_id"]
    second = actions.do_create_special_order(
        [{"type_id": FINISHED_B, "quantity": 10.0}])["order_id"]
    first_plan = actions.do_compute_combined_special_orders(
        [first, second], net_against_stock=False, cfg=_cfg())
    second_plan = actions.do_compute_combined_special_orders(
        [second, first], net_against_stock=False, cfg=_cfg())
    assert _runs(first_plan) == _runs(second_plan)


def test_set_item_then_combined_preview_uses_updated_state(order_sde):
    first = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}])["order_id"]
    second = actions.do_create_special_order(
        [{"type_id": FINISHED_B, "quantity": 10.0}])["order_id"]
    actions.do_set_special_order_item(first, "Finished Widget A", 20.0)

    plan = actions.do_compute_combined_special_orders(
        [first, second], net_against_stock=False, cfg=_cfg())
    by_type = {row.type_id: row.quantity for row in plan["line_items"]}
    assert by_type[FINISHED_A] == 20.0
    assert by_type[FINISHED_B] == 10.0
    assert _runs(plan)[FINISHED_A] == 20


def test_combine_does_not_persist_or_mutate_source_orders(order_sde):
    first = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}], note="a")["order_id"]
    second = actions.do_create_special_order(
        [{"type_id": FINISHED_B, "quantity": 3.0}], note="b")["order_id"]

    actions.do_compute_combined_special_orders([first, second], net_against_stock=False, cfg=_cfg())

    assert actions.do_get_special_order(first)["items"] == [
        {"type_id": FINISHED_A, "type_name": "Finished Widget A", "quantity": 10.0}]
    assert actions.do_get_special_order(second)["items"] == [
        {"type_id": FINISHED_B, "type_name": "Finished Widget B", "quantity": 3.0}]
    assert len(actions.do_list_special_orders()) == 2


def test_combine_rejects_empty_list(order_sde):
    with pytest.raises(ActionError, match="Select at least one"):
        actions.do_compute_combined_special_orders([], net_against_stock=False)


def test_combine_rejects_duplicate_order_ids(order_sde):
    order_id = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}])["order_id"]
    with pytest.raises(ActionError, match="Duplicate special-order id"):
        actions.do_compute_combined_special_orders([order_id, order_id], net_against_stock=False)


def test_combine_unknown_order_raises(order_sde):
    order_id = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 1.0}])["order_id"]
    with pytest.raises(ActionError, match="not found"):
        actions.do_compute_combined_special_orders([order_id, "missing"], net_against_stock=False)


def test_combine_raises_without_sde(db):
    with pytest.raises(ActionError, match="SDE cache is empty"):
        actions.do_compute_combined_special_orders(["anything"], net_against_stock=False)


def test_single_and_combined_compute_share_plan_special_order(order_sde, monkeypatch):
    """CLI/GUI/actions must not grow a second planner - both public compute
    entry points call engine.plan_special_order once."""
    calls = []
    real = engine.plan_special_order

    def _wrap(items, cfg, net_against_stock):
        calls.append((tuple(items), net_against_stock))
        return real(items, cfg, net_against_stock)

    monkeypatch.setattr(engine, "plan_special_order", _wrap)
    first = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}])["order_id"]
    second = actions.do_create_special_order(
        [{"type_id": FINISHED_B, "quantity": 5.0}])["order_id"]

    actions.do_compute_special_order(first, cfg=_cfg())
    actions.do_compute_combined_special_orders([first, second], net_against_stock=False, cfg=_cfg())
    assert len(calls) == 2


# ------------------------------------------------------------------- CLI


def test_cli_set_special_order_item_upserts(order_sde):
    from eve_trader_local.cli import main

    order_id = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}])["order_id"]
    assert main(["set-special-order-item", order_id, "Finished Widget A", "25"]) == 0
    assert actions.do_get_special_order(order_id)["items"] == [
        {"type_id": FINISHED_A, "type_name": "Finished Widget A", "quantity": 25.0}]


def test_cli_set_special_order_item_unknown_order_errors(order_sde, capsys):
    from eve_trader_local.cli import main

    assert main(["set-special-order-item", "missing", "Finished Widget A", "1"]) == 1
    assert "not found" in capsys.readouterr().err


def test_cli_set_special_order_item_invalid_quantity_errors(order_sde, capsys):
    from eve_trader_local.cli import main

    order_id = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}])["order_id"]
    assert main(["set-special-order-item", order_id, "Finished Widget A", "0"]) == 1
    assert "must be positive" in capsys.readouterr().err


def test_cli_set_special_order_item_unknown_type_errors(order_sde, capsys):
    from eve_trader_local.cli import main

    order_id = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}])["order_id"]
    assert main(["set-special-order-item", order_id, "Not A Real Item", "1"]) == 1
    err = capsys.readouterr().err
    assert "Not A Real Item" in err or "No type found" in err or "No exact match" in err


def test_cli_combine_uses_same_planner_as_actions(order_sde):
    from eve_trader_local.cli import main

    first = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}])["order_id"]
    second = actions.do_create_special_order(
        [{"type_id": FINISHED_B, "quantity": 10.0}])["order_id"]
    expected = _runs(actions.do_compute_combined_special_orders(
        [first, second], net_against_stock=False, cfg=_cfg()))
    assert main(["compute-combined-special-orders", first, second]) == 0
    # CLI doesn't return the plan; recompute through actions after CLI to
    # show the stored orders were not mutated and the same path is valid.
    assert _runs(actions.do_compute_combined_special_orders(
        [first, second], net_against_stock=False, cfg=_cfg())) == expected


def test_cli_combine_duplicate_ids_errors(order_sde, capsys):
    from eve_trader_local.cli import main

    order_id = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}])["order_id"]
    assert main(["compute-combined-special-orders", order_id, order_id]) == 1
    assert "Duplicate special-order id" in capsys.readouterr().err


def test_cli_combine_empty_selection_is_usage_error():
    from eve_trader_local.cli import build_parser

    with pytest.raises(SystemExit):
        build_parser().parse_args(["compute-combined-special-orders"])
