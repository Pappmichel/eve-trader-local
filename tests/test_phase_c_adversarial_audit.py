"""Phase C — targeted adversarial audit of the special-order planner path.

Tries to break the published invariants (pooling, B1, preview purity,
net_against_stock isolation, determinism, single planner path). Production
code changes belong here only if one of these tests actually fails.
"""
from __future__ import annotations

import ast
import inspect
import os
import time
from pathlib import Path

import pytest

from eve_trader_local import storage
from eve_trader_local.errors import ActionError
from eve_trader_local.production import actions, engine

from test_production_invention_needs import (
    JITA, T2_MODULE, T2_MODULE_B, _cfg as _invention_cfg, _seed as _seed_invention,
)
from test_production_special_orders import (
    COMPONENT, FINISHED_A, FINISHED_B, HOME, MINERAL, _cfg, _seed,
)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = Path(__file__).resolve().parents[1] / "eve_trader_local"

# Tech I BOM with _cfg() (ACTIVITY_MODS ME 0.90, overbuild 0):
# FINISHED_A x N  -> COMPONENT ceil(2 * 0.9 * N) = 1.8N
# FINISHED_B x N  -> COMPONENT ceil(3 * 0.9 * N) = 2.7N
# COMPONENT x R   -> MINERAL   ceil(10 * 0.9 * R)


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


def _buy_qty(plan: dict) -> dict[int, float]:
    return {row.type_id: row.quantity for row in plan["buy_list"]}


def _lines(plan: dict) -> dict[int, float]:
    return {row.type_id: row.quantity for row in plan["line_items"]}


def _inv(plan: dict) -> dict[int, tuple]:
    return {
        row.type_id: (row.runs_needed, row.bpcs_needed, row.recommended_invention_runs, row.decryptor)
        for row in plan["invention_list"]
    }


def _fingerprint(plan: dict) -> dict:
    """Order-independent view of a plan so display sort cannot fake inequality."""
    return {
        "line": frozenset((r.type_id, r.quantity) for r in plan["line_items"]),
        "build": frozenset((r.type_id, r.job_runs, r.quantity) for r in plan["build_list"]),
        "buy": frozenset((r.type_id, r.quantity) for r in plan["buy_list"]),
        "inv": frozenset(
            (r.type_id, r.runs_needed, r.recommended_invention_runs, r.decryptor)
            for r in plan["invention_list"]
        ),
        "overlap": frozenset((r.type_id, r.current_stock) for r in plan["stock_overlap_warning"]),
    }


def _persistent_state() -> dict:
    orders = storage.list_special_orders()
    return {
        "orders": orders,
        "items": {row[0]: storage.list_special_order_items(row[0]) for row in orders},
        "stock_targets": storage.load_stock_targets(),
        "manual_stock": storage.load_manual_stock(),
        "decryptors": storage.load_selected_decryptors(),
        "esi_finished_a": storage.esi_stock_at_location(FINISHED_A, None),
        "esi_component": storage.esi_stock_at_location(COMPONENT, None),
        "esi_t2": storage.esi_stock_at_location(T2_MODULE, None),
    }


# ============================================================== INV-1 / path


def _invention_need_row_call_sites() -> list[tuple[str, int]]:
    hits = []
    for path in REPO.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name) and func.id == "InventionNeedRow":
                hits.append((str(path.relative_to(REPO.parent)), node.lineno))
            elif isinstance(func, ast.Attribute) and func.attr == "InventionNeedRow":
                hits.append((str(path.relative_to(REPO.parent)), node.lineno))
    return hits


def test_inv1_invention_need_row_is_only_constructed_in_helper():
    engine_src = (REPO / "production" / "engine.py").read_text(encoding="utf-8")
    tree = ast.parse(engine_src)
    helper = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_invention_need_row")
    hits = _invention_need_row_call_sites()
    assert hits, "expected InventionNeedRow(...) somewhere in production code"
    for rel, lineno in hits:
        assert rel == "eve_trader_local/production/engine.py", hits
        assert helper.lineno <= lineno <= helper.end_lineno, hits


def test_inv1_cli_and_gui_do_not_call_engine_planner():
    forbidden = ("plan_special_order", "_invention_need_row", "_expand_all", "plan_production")
    for rel in (
        "eve_trader_local/cli.py",
        "eve_trader_local/gui/views/production_special_orders.py",
    ):
        source = (REPO.parent / rel).read_text(encoding="utf-8")
        tree = ast.parse(source)
        called = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id in forbidden:
                    called.append(func.id)
                elif isinstance(func, ast.Attribute) and func.attr in forbidden:
                    called.append(func.attr)
        assert called == [], f"{rel} calls planner internals: {called}"


def test_set_item_does_not_invoke_the_planner(order_sde, monkeypatch):
    calls = []
    monkeypatch.setattr(engine, "plan_special_order", lambda *a, **k: calls.append((a, k)) or {})
    order_id = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}])["order_id"]
    calls.clear()
    actions.do_set_special_order_item(order_id, "Finished Widget A", 12.0)
    assert calls == []
    assert actions.do_get_special_order(order_id)["items"][0]["quantity"] == 12.0


# ============================================================== Schwerpunkt 1


def test_scenario_a_same_product_pooled_once(order_sde):
    first = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}])["order_id"]
    second = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 20.0}])["order_id"]

    plan = actions.do_compute_combined_special_orders(
        [first, second], net_against_stock=False, cfg=_cfg())

    assert _lines(plan) == {FINISHED_A: 30.0}
    assert len(plan["line_items"]) == 1
    assert _runs(plan)[FINISHED_A] == 30
    # 2 * 0.9 * 30 = 54 component runs; mineral 10 * 0.9 * 54 = 486
    assert _runs(plan)[COMPONENT] == 54
    assert _buy_qty(plan)[MINERAL] == pytest.approx(486.0)
    assert plan["invention_list"] == []
    assert len([r for r in plan["build_list"] if r.type_id == FINISHED_A]) == 1
    assert len([r for r in plan["build_list"] if r.type_id == COMPONENT]) == 1


def test_scenario_a_last_write_wins_would_plan_20_not_30(order_sde):
    """If pooling used last-write-wins, the combined plan would silently
    drop the first order's 10 units."""
    first = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}])["order_id"]
    second = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 20.0}])["order_id"]
    plan = actions.do_compute_combined_special_orders(
        [first, second], net_against_stock=False, cfg=_cfg())
    assert _runs(plan)[FINISHED_A] != 20
    assert _runs(plan)[FINISHED_A] != 10
    assert _runs(plan)[FINISHED_A] == 30


def test_scenario_b_shared_component_stock_claimed_once(order_sde):
    storage.replace_assets("character_assets", [
        (1, COMPONENT, 60003760, "Hangar", 6, 0, "Test Character"),
    ])
    first = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}], net_against_stock=True)["order_id"]
    second = actions.do_create_special_order(
        [{"type_id": FINISHED_B, "quantity": 10.0}], net_against_stock=True)["order_id"]

    combined = actions.do_compute_combined_special_orders(
        [first, second], net_against_stock=True, cfg=_cfg())
    isolated_a = actions.do_compute_special_order(first, cfg=_cfg())
    isolated_b = actions.do_compute_special_order(second, cfg=_cfg())
    scratch = actions.do_compute_combined_special_orders(
        [first, second], net_against_stock=False, cfg=_cfg())

    # Isolated each nets the same 6 stock; combined nets it once.
    # A: 18-6=12; B: 27-6=21; isolated sum 33. Combined scratch 45; netted 39.
    assert _runs(isolated_a)[COMPONENT] == 12
    assert _runs(isolated_b)[COMPONENT] == 21
    assert _runs(scratch)[COMPONENT] == 45
    assert _runs(combined)[COMPONENT] == 39
    assert _runs(combined)[COMPONENT] != _runs(isolated_a)[COMPONENT] + _runs(isolated_b)[COMPONENT]
    assert _runs(combined)[FINISHED_A] == 10
    assert _runs(combined)[FINISHED_B] == 10


def test_scenario_c_same_product_plus_component_stock(order_sde):
    storage.replace_assets("character_assets", [
        (1, COMPONENT, 60003760, "Hangar", 10, 0, "Test Character"),
    ])
    first = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}])["order_id"]
    second = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 20.0}])["order_id"]

    plan = actions.do_compute_combined_special_orders(
        [first, second], net_against_stock=True, cfg=_cfg())
    scratch = actions.do_compute_combined_special_orders(
        [first, second], net_against_stock=False, cfg=_cfg())

    assert _lines(plan) == {FINISHED_A: 30.0}
    assert _runs(plan)[FINISHED_A] == 30  # B1: product hangar is empty here anyway
    assert _runs(scratch)[COMPONENT] == 54
    assert _runs(plan)[COMPONENT] == 44  # 54 - 10 on hand, claimed once


def test_component_as_top_level_is_not_netted(order_sde):
    """B1 still applies when a shared component is itself an ordered item."""
    storage.replace_assets("character_assets", [
        (1, COMPONENT, 60003760, "Hangar", 6, 0, "Test Character"),
    ])
    product = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}])["order_id"]
    component = actions.do_create_special_order(
        [{"type_id": COMPONENT, "quantity": 10.0}])["order_id"]
    scratch = actions.do_compute_combined_special_orders(
        [product, component], net_against_stock=False, cfg=_cfg())
    netted = actions.do_compute_combined_special_orders(
        [product, component], net_against_stock=True, cfg=_cfg())
    # Seed COMPONENT 10 is never netted; only the 18 downstream of FINISHED_A are.
    assert _runs(scratch)[COMPONENT] == 28
    assert _runs(netted)[COMPONENT] == 22
    assert _runs(netted)[FINISHED_A] == 10
    assert _lines(netted)[COMPONENT] == 10.0
    assert _lines(netted)[FINISHED_A] == 10.0


# ============================================================== Schwerpunkt 2 B1


@pytest.mark.parametrize("hangar,ordered", [(100.0, 100.0), (50.0, 100.0), (200.0, 100.0)])
def test_b1_top_level_never_netted_single_order(order_sde, hangar, ordered):
    storage.replace_assets("character_assets", [
        (1, FINISHED_A, 60003760, "Hangar", hangar, 0, "Test Character"),
    ])
    order_id = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": ordered}], net_against_stock=True)["order_id"]
    plan = actions.do_compute_special_order(order_id, cfg=_cfg())
    assert _runs(plan)[FINISHED_A] == ordered


def test_b1_combined_same_product_with_full_hangar_cover(order_sde):
    storage.replace_assets("character_assets", [
        (1, FINISHED_A, 60003760, "Hangar", 100, 0, "Test Character"),
    ])
    first = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 50.0}])["order_id"]
    second = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 50.0}])["order_id"]
    plan = actions.do_compute_combined_special_orders(
        [first, second], net_against_stock=True, cfg=_cfg())
    assert _lines(plan) == {FINISHED_A: 100.0}
    assert _runs(plan)[FINISHED_A] == 100


def test_b1_holds_after_engine_level_type_id_pooling(order_sde):
    storage.replace_assets("character_assets", [
        (1, FINISHED_A, 60003760, "Hangar", 40, 0, "Test Character"),
    ])
    plan = engine.plan_special_order(
        [(FINISHED_A, "Finished Widget A", 10.0),
         (FINISHED_A, "Finished Widget A", 20.0)],
        _cfg(), net_against_stock=True,
    )
    assert _runs(plan)[FINISHED_A] == 30


# ============================================================== Schwerpunkt 3/4/7


def test_net_against_stock_preview_is_isolated_and_repeatable(order_sde):
    storage.replace_assets("character_assets", [
        (1, COMPONENT, 60003760, "Hangar", 6, 0, "Test Character"),
    ])
    first = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}], net_against_stock=True)["order_id"]
    second = actions.do_create_special_order(
        [{"type_id": FINISHED_B, "quantity": 10.0}], net_against_stock=False)["order_id"]
    before = _persistent_state()

    scratch_1 = _fingerprint(actions.do_compute_combined_special_orders(
        [first, second], net_against_stock=False, cfg=_cfg()))
    netted = _fingerprint(actions.do_compute_combined_special_orders(
        [first, second], net_against_stock=True, cfg=_cfg()))
    scratch_2 = _fingerprint(actions.do_compute_combined_special_orders(
        [first, second], net_against_stock=False, cfg=_cfg()))

    assert scratch_1 == scratch_2
    assert scratch_1 != netted
    assert _persistent_state() == before
    assert actions.do_get_special_order(first)["order"].net_against_stock is True
    assert actions.do_get_special_order(second)["order"].net_against_stock is False


def test_preview_purity_does_not_mutate_orders_or_stock(order_sde):
    storage.replace_assets("character_assets", [
        (1, COMPONENT, 60003760, "Hangar", 6, 0, "Test Character"),
        (2, FINISHED_A, 60003760, "Hangar", 4, 0, "Test Character"),
    ])
    first = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}], note="keep")["order_id"]
    second = actions.do_create_special_order(
        [{"type_id": FINISHED_B, "quantity": 7.0}])["order_id"]
    before = _persistent_state()
    actions.do_compute_combined_special_orders([first, second], net_against_stock=True, cfg=_cfg())
    actions.do_compute_special_order(first, cfg=_cfg())
    assert _persistent_state() == before


def test_repeated_compute_and_combined_are_stable(order_sde):
    first = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}])["order_id"]
    second = actions.do_create_special_order(
        [{"type_id": FINISHED_B, "quantity": 10.0}])["order_id"]

    singles = [_fingerprint(actions.do_compute_special_order(first, cfg=_cfg())) for _ in range(3)]
    assert singles[0] == singles[1] == singles[2]

    combined = [
        _fingerprint(actions.do_compute_combined_special_orders(
            [first, second], net_against_stock=False, cfg=_cfg()))
        for _ in range(3)
    ]
    assert combined[0] == combined[1] == combined[2]
    assert len(_inv(actions.do_compute_combined_special_orders(
        [first, second], net_against_stock=False, cfg=_cfg()))) == 0


def test_repeated_combined_does_not_accumulate_hangar_consumption(order_sde):
    storage.replace_assets("character_assets", [
        (1, COMPONENT, 60003760, "Hangar", 6, 0, "Test Character"),
    ])
    first = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}])["order_id"]
    second = actions.do_create_special_order(
        [{"type_id": FINISHED_B, "quantity": 10.0}])["order_id"]
    runs = [
        _runs(actions.do_compute_combined_special_orders(
            [first, second], net_against_stock=True, cfg=_cfg()))[COMPONENT]
        for _ in range(3)
    ]
    assert runs[0] == runs[1] == runs[2]


# ============================================================== Schwerpunkt 5


def test_three_order_permutation_is_semantically_identical(order_sde):
    a = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}])["order_id"]
    b = actions.do_create_special_order(
        [{"type_id": FINISHED_B, "quantity": 4.0}])["order_id"]
    c = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 6.0}])["order_id"]
    fingerprints = [
        _fingerprint(actions.do_compute_combined_special_orders(order, net_against_stock=False, cfg=_cfg()))
        for order in ([a, b, c], [b, a, c], [c, b, a])
    ]
    assert fingerprints[0] == fingerprints[1] == fingerprints[2]
    plan = actions.do_compute_combined_special_orders([c, b, a], net_against_stock=False, cfg=_cfg())
    assert _lines(plan)[FINISHED_A] == 16.0
    assert _lines(plan)[FINISHED_B] == 4.0


# ============================================================== Schwerpunkt 6 / INV-8


def test_cli_calls_the_same_action_and_emits_the_same_plan(order_sde, capsys, monkeypatch):
    from eve_trader_local import cli
    from eve_trader_local.cli import main

    first = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}])["order_id"]
    second = actions.do_create_special_order(
        [{"type_id": FINISHED_B, "quantity": 10.0}])["order_id"]
    # CLI uses PRODUCTION_CONFIG (not _cfg()); compare against the same default.
    expected = _fingerprint(actions.do_compute_combined_special_orders(
        [first, second], net_against_stock=False))

    captured = []
    real = actions.do_compute_combined_special_orders

    def _wrap(order_ids, net_against_stock, cfg=None):
        if cfg is None:
            result = real(order_ids, net_against_stock)
        else:
            result = real(order_ids, net_against_stock, cfg)
        captured.append(_fingerprint(result))
        return result

    monkeypatch.setattr(cli.production_actions, "do_compute_combined_special_orders", _wrap)

    assert main(["compute-combined-special-orders", first, second]) == 0
    out = capsys.readouterr().out
    assert "Combined preview of 2 order(s)" in out
    assert "Finished Widget A" in out
    assert "Finished Widget B" in out
    assert captured == [expected]
    assert _persistent_state()["items"][first][0][2] == 10.0


def test_gui_populate_renders_the_action_plan(order_sde, qapp):
    from eve_trader_local.gui.views.production_special_orders import SpecialOrdersView

    first = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}])["order_id"]
    second = actions.do_create_special_order(
        [{"type_id": FINISHED_B, "quantity": 5.0}])["order_id"]
    plan = actions.do_compute_combined_special_orders(
        [first, second], net_against_stock=False, cfg=_cfg())

    view = SpecialOrdersView()
    view._on_combined(plan)
    assert view.line_items_table.rowCount() == len(plan["line_items"])
    names = {view.line_items_table.item(row, 0).text() for row in range(view.line_items_table.rowCount())}
    assert names == {r.type_name for r in plan["line_items"]}
    assert view.build_table_widget.rowCount() == len(plan["build_list"])
    assert view.invention_table.rowCount() == len(plan["invention_list"])


def test_gui_compute_combined_only_calls_the_action(order_sde, qapp, monkeypatch):
    from eve_trader_local.gui.views.production_special_orders import SpecialOrdersView
    import eve_trader_local.gui.views.production_special_orders as gui_mod

    first = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}])["order_id"]
    calls = []
    real = actions.do_compute_combined_special_orders

    def _wrap(order_ids, net_against_stock, cfg=None):
        calls.append((tuple(order_ids), net_against_stock))
        if cfg is None:
            return real(order_ids, net_against_stock)
        return real(order_ids, net_against_stock, cfg)

    monkeypatch.setattr(gui_mod.production_actions, "do_compute_combined_special_orders", _wrap)

    view = SpecialOrdersView()
    view.combine_ids_input.setText(first)
    view._compute_combined()
    _wait_for_threads(qapp, view)
    assert calls == [((first,), False)]


def test_combined_preview_is_a_single_planner_run(order_sde, monkeypatch):
    calls = []
    real = engine.plan_special_order

    def _wrap(items, cfg, net_against_stock):
        calls.append(tuple((t, q) for t, _n, q in items))
        return real(items, cfg, net_against_stock)

    monkeypatch.setattr(engine, "plan_special_order", _wrap)
    ids = [
        actions.do_create_special_order([{"type_id": FINISHED_A, "quantity": float(n)}])["order_id"]
        for n in (1, 2, 3, 4, 5)
    ]
    actions.do_compute_combined_special_orders(ids, net_against_stock=False, cfg=_cfg())
    assert len(calls) == 1
    assert calls[0] == ((FINISHED_A, 15.0),)


def test_cli_and_gui_source_do_not_import_engine_planner():
    from eve_trader_local import cli
    from eve_trader_local.gui.views import production_special_orders as gui

    cli_src = (
        inspect.getsource(cli.cmd_compute_special_order)
        + inspect.getsource(cli.cmd_compute_combined_special_orders)
    )
    gui_src = (
        inspect.getsource(gui.SpecialOrdersView._compute_order)
        + inspect.getsource(gui.SpecialOrdersView._compute_combined)
    )
    assert "plan_special_order" not in cli_src
    assert "plan_special_order" not in gui_src
    assert "_invention_need_row" not in cli_src
    assert "_invention_need_row" not in gui_src
    assert "do_compute_special_order" in inspect.getsource(cli.cmd_compute_special_order)
    assert "do_compute_combined_special_orders" in inspect.getsource(cli.cmd_compute_combined_special_orders)
    assert "do_compute_special_order" in inspect.getsource(gui.SpecialOrdersView._compute_order)
    assert "do_compute_combined_special_orders" in inspect.getsource(gui.SpecialOrdersView._compute_combined)


# ============================================================== Schwerpunkt 8


def test_invalid_inputs_do_not_leave_half_written_state(order_sde):
    order_id = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}])["order_id"]
    before = _persistent_state()

    with pytest.raises(ActionError):
        actions.do_set_special_order_item(order_id, "Finished Widget A", 0)
    with pytest.raises(ActionError):
        actions.do_set_special_order_item(order_id, "Finished Widget A", -1)
    with pytest.raises(ActionError):
        actions.do_set_special_order_item(order_id, "Not A Real Item", 1.0)
    with pytest.raises(ActionError):
        actions.do_set_special_order_item("missing", "Finished Widget A", 1.0)
    with pytest.raises(ActionError):
        actions.do_compute_combined_special_orders([], net_against_stock=False)
    with pytest.raises(ActionError):
        actions.do_compute_combined_special_orders([order_id, order_id], net_against_stock=False)
    with pytest.raises(ActionError):
        actions.do_compute_combined_special_orders([order_id, "missing"], net_against_stock=False)

    assert _persistent_state() == before
    assert actions.do_get_special_order(order_id)["items"][0]["quantity"] == 10.0


def test_repeated_upsert_does_not_create_silent_duplicates(order_sde):
    order_id = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}])["order_id"]
    for qty in (11.0, 12.0, 13.0, 13.0):
        result = actions.do_set_special_order_item(order_id, "Finished Widget A", qty)
        assert len(result["items"]) == 1
        assert result["items"][0]["quantity"] == qty
    plan = actions.do_compute_special_order(order_id, cfg=_cfg())
    assert _runs(plan)[FINISHED_A] == 13


def test_extremely_large_quantity_still_plans_once(order_sde):
    qty = 1_000_000.0
    order_id = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": qty}])["order_id"]
    plan = actions.do_compute_special_order(order_id, cfg=_cfg())
    assert _runs(plan)[FINISHED_A] == qty
    assert _runs(plan)[COMPONENT] == 1_800_000


# ============================================================== Schwerpunkt 9


def test_missing_sde_errors_without_creating_state(db):
    before = _persistent_state()
    with pytest.raises(ActionError, match="SDE cache is empty"):
        actions.do_compute_special_order("anything")
    with pytest.raises(ActionError, match="SDE cache is empty"):
        actions.do_compute_combined_special_orders(["anything"], net_against_stock=False)
    assert _persistent_state() == before


def test_input_without_blueprint_is_bought_not_invented(order_sde):
    order_id = actions.do_create_special_order(
        [{"type_id": MINERAL, "quantity": 100.0}])["order_id"]
    plan = actions.do_compute_special_order(order_id, cfg=_cfg())
    assert plan["build_list"] == []
    assert plan["invention_list"] == []
    assert _buy_qty(plan)[MINERAL] == pytest.approx(100.0)


def test_unknown_type_on_create_does_not_persist_an_order(order_sde):
    before = _persistent_state()
    with pytest.raises(ActionError):
        actions.do_create_special_order([{"type_id": 999999, "quantity": 1.0}])
    assert _persistent_state() == before


def test_partial_tree_still_buys_the_mineral_leaf(order_sde):
    """COMPONENT is buildable; MINERAL has no blueprint — defined buy fallback."""
    order_id = actions.do_create_special_order(
        [{"type_id": COMPONENT, "quantity": 5.0}])["order_id"]
    plan = actions.do_compute_special_order(order_id, cfg=_cfg())
    assert _runs(plan)[COMPONENT] == 5
    assert MINERAL in _buy_qty(plan)
    assert FINISHED_A not in _runs(plan)


def test_missing_market_prices_still_plan(order_sde, monkeypatch):
    monkeypatch.setattr(engine.pricing, "home_prices", lambda type_ids, cfg=None, client=None: {})
    monkeypatch.setattr(engine.pricing, "jita_prices", lambda type_ids, client=None, trading_cfg=None: {})
    order_id = actions.do_create_special_order(
        [{"type_id": FINISHED_A, "quantity": 10.0}])["order_id"]
    plan = actions.do_compute_special_order(order_id, cfg=_cfg())
    assert _runs(plan)[FINISHED_A] == 10
    assert _runs(plan)[COMPONENT] == 18
    assert all(row.unit_price is None for row in plan["buy_list"])


# ============================================================== Invention combined


def test_combined_t2_invention_is_one_row_and_not_isolated_sum(invention_sde):
    first = actions.do_create_special_order(
        [{"type_id": T2_MODULE, "quantity": 40.0}])["order_id"]
    second = actions.do_create_special_order(
        [{"type_id": T2_MODULE, "quantity": 60.0}])["order_id"]
    isolated_a = actions.do_compute_special_order(first, cfg=_invention_cfg())
    isolated_b = actions.do_compute_special_order(second, cfg=_invention_cfg())
    combined = actions.do_compute_combined_special_orders(
        [first, second], net_against_stock=False, cfg=_invention_cfg())

    assert len(combined["invention_list"]) == 1
    assert combined["invention_list"][0].runs_needed == 100
    isolated_attempts = (
        isolated_a["invention_list"][0].recommended_invention_runs
        + isolated_b["invention_list"][0].recommended_invention_runs
    )
    assert combined["invention_list"][0].recommended_invention_runs <= isolated_attempts
    fps = [
        _fingerprint(actions.do_compute_combined_special_orders(
            [first, second], net_against_stock=False, cfg=_invention_cfg()))
        for _ in range(3)
    ]
    assert fps[0] == fps[1] == fps[2]


def test_combined_t2_b1_hangar_does_not_reduce_invention_runs(invention_sde):
    storage.replace_assets("character_assets", [
        (1, T2_MODULE, 60003760, "Hangar", 80, 0, "Test Character"),
    ])
    first = actions.do_create_special_order(
        [{"type_id": T2_MODULE, "quantity": 40.0}])["order_id"]
    second = actions.do_create_special_order(
        [{"type_id": T2_MODULE, "quantity": 60.0}])["order_id"]
    plan = actions.do_compute_combined_special_orders(
        [first, second], net_against_stock=True, cfg=_invention_cfg())
    assert plan["invention_list"][0].runs_needed == 100


def test_combined_two_different_t2_products_keep_separate_rows(invention_sde):
    first = actions.do_create_special_order(
        [{"type_id": T2_MODULE, "quantity": 40.0}])["order_id"]
    second = actions.do_create_special_order(
        [{"type_id": T2_MODULE_B, "quantity": 10.0}])["order_id"]
    plan = actions.do_compute_combined_special_orders(
        [first, second], net_against_stock=False, cfg=_invention_cfg())
    by_type = _inv(plan)
    assert set(by_type) == {T2_MODULE, T2_MODULE_B}
    assert by_type[T2_MODULE][0] == 40
    assert by_type[T2_MODULE_B][0] == 10


def test_combined_t2_and_buy_leaf_does_not_invent_the_leaf(invention_sde):
    from test_production_invention_needs import TRITANIUM

    first = actions.do_create_special_order(
        [{"type_id": T2_MODULE, "quantity": 40.0}])["order_id"]
    second = actions.do_create_special_order(
        [{"type_id": TRITANIUM, "quantity": 100.0}])["order_id"]
    plan = actions.do_compute_combined_special_orders(
        [first, second], net_against_stock=False, cfg=_invention_cfg())
    assert {row.type_id for row in plan["invention_list"]} == {T2_MODULE}
    assert TRITANIUM in _buy_qty(plan) or TRITANIUM in _runs(plan)


# ============================================================== Schwerpunkt 10


def test_combine_does_not_reschedule_planner_per_order(order_sde, monkeypatch):
    calls = []
    real = engine.plan_special_order

    def _wrap(items, cfg, net_against_stock):
        calls.append(len(items))
        return real(items, cfg, net_against_stock)

    monkeypatch.setattr(engine, "plan_special_order", _wrap)
    ids = [
        actions.do_create_special_order(
            [{"type_id": FINISHED_A if i % 2 == 0 else FINISHED_B, "quantity": 2.0}]
        )["order_id"]
        for i in range(12)
    ]
    t0 = time.monotonic()
    actions.do_compute_combined_special_orders(ids, net_against_stock=False, cfg=_cfg())
    elapsed = time.monotonic() - t0
    assert len(calls) == 1
    assert calls[0] == 2  # 12 orders pooled to two type_ids, not 12 planner inputs
    assert elapsed < 2.0
