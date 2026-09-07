"""Phase G.1 — scale baseline. Measure first; assert semantics under load.

Timings are recorded for PHASE_G_SCALE.md. They are not pass/fail gates
except a hang ceiling so a wedged planner cannot hide in CI.
"""
from __future__ import annotations

import os

import pytest

from eve_trader_local import storage
from eve_trader_local.production import actions, engine

from phase_g_scale_harness import fingerprint, measure_scale, timed
from test_production_invention_needs import (
    JITA, T2_MODULE, _cfg as _invention_cfg, _seed as _seed_invention,
)
from test_production_special_orders import (
    COMPONENT, COMPONENT_BP, FINISHED_A, FINISHED_A_BP, FINISHED_B, FINISHED_B_BP,
    HOME, MINERAL, _cfg, _seed,
)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytestmark = pytest.mark.release

# Hang ceiling: this environment's 100-order combined widget plan is well
# under a second. A minute means the planner wedged, not "slow hardware".
_HANG_MS = 60_000

SUB = 91210
SUB_BP = 92210
WIDE_START = 91300
WIDE_BP_START = 92300
WIDE_COUNT = 20


def _seed_deep():
    """FINISHED_A → COMPONENT → SUB → MINERAL (four manufacturing levels)."""
    storage.replace_sde_data(
        types=[
            (MINERAL, 18, "Tritanium", 0.01, 1, 100, 0, 1, 1),
            (SUB, 18, "Subcomponent", 1.0, 1, 200, 0, 1, 1),
            (COMPONENT, 18, "Widget Component", 1.0, 1, 200, 0, 1, 1),
            (FINISHED_A, 18, "Finished Widget A", 1.0, 1, 200, 0, 1, 1),
            (FINISHED_B, 18, "Finished Widget B", 1.0, 1, 200, 0, 1, 1),
            (SUB_BP, 9, "Subcomponent Blueprint", 0.01, 1, None, 0, None, 1),
            (COMPONENT_BP, 9, "Widget Component Blueprint", 0.01, 1, None, 0, None, 1),
            (FINISHED_A_BP, 9, "Finished Widget A Blueprint", 0.01, 1, None, 0, None, 1),
            (FINISHED_B_BP, 9, "Finished Widget B Blueprint", 0.01, 1, None, 0, None, 1),
        ],
        groups=[(18, 4, "Mineral"), (9, 9, "Blueprint")],
        market_groups=[(100, None, "Manufacture & Research"), (200, None, "Ship Equipment")],
        blueprint_time=[],
        blueprint_materials=[
            (SUB_BP, 1, MINERAL, 10),
            (COMPONENT_BP, 1, SUB, 2),
            (FINISHED_A_BP, 1, COMPONENT, 2),
            (FINISHED_B_BP, 1, COMPONENT, 3),
        ],
        blueprint_products=[
            (SUB_BP, 1, SUB, 1),
            (COMPONENT_BP, 1, COMPONENT, 1),
            (FINISHED_A_BP, 1, FINISHED_A, 1),
            (FINISHED_B_BP, 1, FINISHED_B, 1),
        ],
        categories=[(4, "Material")],
    )


def _seed_wide():
    extra_types = [
        (WIDE_START + i, 18, f"Wide Widget {i}", 1.0, 1, 200, 0, 1, 1)
        for i in range(WIDE_COUNT)
    ]
    extra_bps = [
        (WIDE_BP_START + i, 9, f"Wide Widget {i} Blueprint", 0.01, 1, None, 0, None, 1)
        for i in range(WIDE_COUNT)
    ]
    extra_mats = [(WIDE_BP_START + i, 1, COMPONENT, 2) for i in range(WIDE_COUNT)]
    extra_prods = [(WIDE_BP_START + i, 1, WIDE_START + i, 1) for i in range(WIDE_COUNT)]
    storage.replace_sde_data(
        types=[
            (MINERAL, 18, "Tritanium", 0.01, 1, 100, 0, 1, 1),
            (COMPONENT, 18, "Widget Component", 1.0, 1, 200, 0, 1, 1),
            (FINISHED_A, 18, "Finished Widget A", 1.0, 1, 200, 0, 1, 1),
            (FINISHED_B, 18, "Finished Widget B", 1.0, 1, 200, 0, 1, 1),
            (COMPONENT_BP, 9, "Widget Component Blueprint", 0.01, 1, None, 0, None, 1),
            (FINISHED_A_BP, 9, "Finished Widget A Blueprint", 0.01, 1, None, 0, None, 1),
            (FINISHED_B_BP, 9, "Finished Widget B Blueprint", 0.01, 1, None, 0, None, 1),
        ] + extra_types + extra_bps,
        groups=[(18, 4, "Mineral"), (9, 9, "Blueprint")],
        market_groups=[(100, None, "Manufacture & Research"), (200, None, "Ship Equipment")],
        blueprint_time=[],
        blueprint_materials=[
            (COMPONENT_BP, 1, MINERAL, 10),
            (FINISHED_A_BP, 1, COMPONENT, 2),
            (FINISHED_B_BP, 1, COMPONENT, 3),
        ] + extra_mats,
        blueprint_products=[
            (COMPONENT_BP, 1, COMPONENT, 1),
            (FINISHED_A_BP, 1, FINISHED_A, 1),
            (FINISHED_B_BP, 1, FINISHED_B, 1),
        ] + extra_prods,
        categories=[(4, "Material")],
    )


def _patch_prices(monkeypatch, home):
    monkeypatch.setattr(engine.pricing, "home_prices", lambda type_ids, cfg=None, client=None: home)
    monkeypatch.setattr(engine.pricing, "jita_prices", lambda type_ids, client=None, trading_cfg=None: {})

    class _FakeESIClient:
        def __init__(self, *args, **kwargs):
            pass

        def get_adjusted_prices(self):
            return {}

    import eve_trader_local.esi_client as esi_client_module
    monkeypatch.setattr(esi_client_module, "ESIClient", _FakeESIClient)


@pytest.fixture
def order_sde(db, monkeypatch):
    _seed()
    _patch_prices(monkeypatch, HOME)
    return db


@pytest.fixture
def deep_sde(db, monkeypatch):
    _seed_deep()
    _patch_prices(monkeypatch, HOME)
    return db


@pytest.fixture
def wide_sde(db, monkeypatch):
    _seed_wide()
    _patch_prices(monkeypatch, HOME)
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


def _print_row(label: str, row: dict) -> None:
    print(
        f"{label:28} n={row['n']:<4} create={row['create_ms']:8.2f}ms "
        f"list={row['list_ms']:8.2f}ms one={row['one_compute_ms']:8.2f}ms "
        f"combined={row['combined_ms']:8.2f}ms audit={row['audit_ms']:8.2f}ms "
        f"peak={row['peak_kib']:8.1f}KiB"
    )


def test_pooled_order_count_matrix_semantics_and_timings(order_sde, capsys):
    planner_calls = []
    real = engine.plan_special_order

    def _wrap(items, cfg, net_against_stock):
        planner_calls.append(tuple((t, q) for t, _n, q in items))
        return real(items, cfg, net_against_stock)

    engine.plan_special_order = _wrap
    try:
        rows = []
        for n in (1, 10, 50, 100):
            for order_id, *_rest in storage.list_special_orders():
                storage.delete_special_order(order_id)
            planner_calls.clear()
            row = measure_scale(n, FINISHED_A, 2.0, _cfg())
            _print_row(f"pooled qty=2 ×{n}", row)
            for key in ("create_ms", "list_ms", "one_compute_ms", "combined_ms", "audit_ms"):
                assert 0 <= row[key] < _HANG_MS
            plan = row["combined_plan"]
            assert fingerprint(plan)["line"] == frozenset({(FINISHED_A, 2.0 * n)})
            combined_calls = [c for c in planner_calls if dict(c).get(FINISHED_A) == 2.0 * n]
            assert len(combined_calls) >= 1
            assert actions.do_audit_special_orders()["ok"] is True
            before = storage.list_special_orders()
            actions.do_compute_combined_special_orders(
                row["order_ids"], net_against_stock=False, cfg=_cfg())
            assert storage.list_special_orders() == before
            rows.append(row)
    finally:
        engine.plan_special_order = real

    assert len(rows) == 4
    # Listing N headers should not be cheaper than listing 1 if N is larger;
    # we only record the relationship, we do not rewrite list.
    assert rows[-1]["n"] == 100


def test_many_items_on_one_order(wide_sde):
    items = [{"type_id": WIDE_START + i, "quantity": 3.0} for i in range(WIDE_COUNT)]
    order_id = actions.do_create_special_order(items)["order_id"]
    stored = actions.do_get_special_order(order_id)["items"]
    assert len(stored) == WIDE_COUNT
    plan, ms, _kib = timed(lambda: actions.do_compute_special_order(order_id, cfg=_cfg()))
    print(f"one order × {WIDE_COUNT} distinct items compute={ms:.2f}ms")
    assert 0 <= ms < _HANG_MS
    assert len(plan["line_items"]) == WIDE_COUNT
    assert {r.type_id for r in plan["line_items"]} == {WIDE_START + i for i in range(WIDE_COUNT)}
    assert COMPONENT in {r.type_id for r in plan["build_list"]}


def test_combined_distinct_types_one_planner_run(wide_sde, monkeypatch):
    ids = []
    for i in range(WIDE_COUNT):
        ids.append(actions.do_create_special_order(
            [{"type_id": WIDE_START + i, "quantity": 1.0}])["order_id"])
    calls = []
    real = engine.plan_special_order

    def _wrap(items, cfg, net_against_stock):
        calls.append(len(items))
        return real(items, cfg, net_against_stock)

    monkeypatch.setattr(engine, "plan_special_order", _wrap)
    plan, ms, _kib = timed(lambda: actions.do_compute_combined_special_orders(
        ids, net_against_stock=False, cfg=_cfg()))
    print(f"combined {WIDE_COUNT} distinct types={ms:.2f}ms")
    assert calls == [WIDE_COUNT]
    assert len(plan["line_items"]) == WIDE_COUNT
    assert 0 <= ms < _HANG_MS


def test_deep_bom_combined_still_one_run(deep_sde, monkeypatch):
    first = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}])["order_id"]
    second = actions.do_create_special_order(
        [{"type_id": FINISHED_B, "quantity": 10.0}])["order_id"]
    calls = []
    real = engine.plan_special_order

    def _wrap(items, cfg, net_against_stock):
        calls.append(1)
        return real(items, cfg, net_against_stock)

    monkeypatch.setattr(engine, "plan_special_order", _wrap)
    plan, ms, _kib = timed(lambda: actions.do_compute_combined_special_orders(
        [first, second], net_against_stock=False, cfg=_cfg()))
    print(f"deep BOM combined={ms:.2f}ms")
    assert calls == [1]
    assert SUB in {r.type_id for r in plan["build_list"]}
    assert COMPONENT in {r.type_id for r in plan["build_list"]}
    assert 0 <= ms < _HANG_MS
    assert storage.list_special_order_items(first)[0][2] == 10.0


def test_hangar_net_at_scale_does_not_net_top_level(order_sde):
    storage.replace_assets("character_assets", [
        (1, FINISHED_A, 60003760, "Hangar", 10_000, 0, "Test Character"),
        (2, COMPONENT, 60003760, "Hangar", 50, 0, "Test Character"),
    ])
    ids = []
    for _ in range(10):
        ids.append(actions.do_create_special_order(
            [{"type_id": FINISHED_A, "quantity": 10.0}], net_against_stock=True)["order_id"])
    combined = actions.do_compute_combined_special_orders(
        ids, net_against_stock=True, cfg=_cfg())
    assert {r.type_id: r.quantity for r in combined["line_items"]} == {FINISHED_A: 100.0}
    build = {r.type_id: r.job_runs for r in combined["build_list"]}
    assert build[FINISHED_A] == 100
    assert build[COMPONENT] < 180
    for oid in ids:
        assert actions.do_get_special_order(oid)["order"].net_against_stock is True
        assert actions.do_get_special_order(oid)["items"][0]["quantity"] == 10.0


def test_t2_invention_combined_at_scale(invention_sde):
    ids = [
        actions.do_create_special_order([{"type_id": T2_MODULE, "quantity": 10.0}])["order_id"]
        for _ in range(10)
    ]
    plan, ms, _kib = timed(lambda: actions.do_compute_combined_special_orders(
        ids, net_against_stock=False, cfg=_invention_cfg()))
    print(f"T2 combined 10×10={ms:.2f}ms")
    assert len(plan["invention_list"]) == 1
    assert plan["invention_list"][0].runs_needed == 100
    assert 0 <= ms < _HANG_MS
    assert len(storage.list_special_orders()) == 10
