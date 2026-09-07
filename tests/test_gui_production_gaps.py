"""Smoke tests for the Production GUI views added to close the
`production/actions.py` GUI gaps (Item Lookup, Invention Estimator, Owned
Blueprints, Current Jobs && Slots) - same "opens without crashing against an
empty throwaway DB" level as tests/test_gui_production.py's own coverage.
Skipped entirely if PySide6 isn't installed, same guard as the other GUI
test modules."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _wait_for_threads(qapp, view, timeout=5):
    import time

    deadline = time.monotonic() + timeout
    while view._threads and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)


def test_item_lookup_view_opens_with_empty_db(qapp, db):
    from eve_trader_local.gui.views.production_item_lookup import ItemLookupView

    view = ItemLookupView()
    assert view.margin_table.rowCount() == 0
    assert view.tree_table.rowCount() == 0
    assert view.locations_table.rowCount() == 0


def test_item_lookup_margin_lookup_end_to_end(qapp, db):
    """Exercises the run_action -> failure -> status-label path (an empty
    throwaway DB has no SDE cache, so `do_get_item_margin` raises
    ActionError) - same style as test_gui.py's own run_action smoke tests."""
    from eve_trader_local.gui.views.production_item_lookup import ItemLookupView

    view = ItemLookupView()
    view.item_input.setText("Tritanium")
    view._lookup_margin()
    _wait_for_threads(qapp, view)

    assert view.status_label.text()  # an error message landed in the status label
    assert view.margin_table.rowCount() == 0


def test_invention_estimator_view_opens_with_empty_db(qapp, db):
    from eve_trader_local.gui.views.production_invention_estimator import InventionEstimatorView

    view = InventionEstimatorView()
    assert view.table.rowCount() == 0
    # First entry is the "compare all decryptors" sentinel (data None), the
    # rest are the real DECRYPTORS keys.
    assert view.decryptor_combo.itemData(0) is None
    assert view.decryptor_combo.count() > 1


def test_invention_estimator_end_to_end(qapp, db):
    """Exercises the run_action -> success -> status-label path: an empty
    throwaway DB has no invention recipes, so `do_estimate_invention`
    succeeds with zero results rather than raising."""
    from eve_trader_local.gui.views.production_invention_estimator import InventionEstimatorView

    view = InventionEstimatorView()
    view.product_input.setText("Some Blueprint")
    view._estimate()
    _wait_for_threads(qapp, view)

    assert "No invention recipe" in view.status_label.text()
    assert view.table.rowCount() == 0


def test_owned_blueprints_view_opens_with_empty_db(qapp, db):
    from eve_trader_local.gui.views.production_owned_blueprints import OwnedBlueprintsView

    view = OwnedBlueprintsView()
    assert view.table.rowCount() == 0
    assert view.bpc_cost_table.rowCount() == 0
    assert "No owned blueprints" in view.status_label.text()


def test_owned_blueprints_add_remove_bpc_cost_end_to_end(qapp, db):
    """Exercises the run_action -> success -> table path for Add/Remove Cost
    on OwnedBlueprintsView, same style as the Planner stock-target e2e."""
    from eve_trader_local import storage
    from eve_trader_local.gui.views.production_owned_blueprints import OwnedBlueprintsView

    storage.replace_sde_data(
        types=[(34, 18, "Tritanium", 0.01, 1, 100, 0, 1, 1)],
        groups=[(18, 4, "Mineral")],
        market_groups=[(100, None, "Manufacture & Research")],
        blueprint_time=[], blueprint_materials=[], blueprint_products=[],
        categories=[(4, "Material")],
    )

    view = OwnedBlueprintsView()
    assert view.bpc_cost_table.rowCount() == 0

    view.bpc_item_input.setText("Tritanium")
    view.bpc_cost_input.setText("1000000")
    view.bpc_runs_input.setText("10")
    view._add_bpc_cost()
    _wait_for_threads(qapp, view)

    assert "Blueprint copy cost set" in view.status_label.text()
    assert view.bpc_cost_table.rowCount() == 1
    rows = storage.load_manual_blueprint_copy_costs()
    assert rows == [(34, "Tritanium", 1_000_000.0, 10)]

    view._remove_bpc_cost()
    _wait_for_threads(qapp, view)

    assert "Removed blueprint copy cost" in view.status_label.text()
    assert view.bpc_cost_table.rowCount() == 0
    assert storage.load_manual_blueprint_copy_costs() == []


def test_jobs_slots_view_opens_with_empty_db(qapp, db):
    from eve_trader_local.gui.views.production_jobs_slots import JobsSlotsView

    view = JobsSlotsView()
    assert view.jobs_table.rowCount() == 0
    assert view.slots_table.rowCount() == 0


def test_jobs_slots_refresh_jobs_end_to_end(qapp, db):
    """Exercises the run_action -> success -> status-label path: an empty
    throwaway DB has no industry jobs, so `do_list_current_jobs` succeeds
    with zero rows."""
    from eve_trader_local.gui.views.production_jobs_slots import JobsSlotsView

    view = JobsSlotsView()
    view._refresh_jobs()
    _wait_for_threads(qapp, view)

    assert "No active industry jobs" in view.status_label.text()
    assert view.jobs_table.rowCount() == 0


def test_planner_update_stock_target_end_to_end(qapp, db):
    """Exercises the run_action -> success -> status-label path for
    `do_update_stock_target` (GitHub issue #16's in-place edit, wired into
    `production_planner.py`'s Stock Targets box) - needs a real SDE-resolvable
    item and an existing target first, so seeds a minimal synthetic SDE row
    the same way tests/test_production_gaps.py's own `_seed()` does."""
    from eve_trader_local import storage
    from eve_trader_local.gui.views.production_planner import ProductionPlannerView

    storage.replace_sde_data(
        types=[(34, 18, "Tritanium", 0.01, 1, 100, 0, 1, 1)],
        groups=[(18, 4, "Mineral")],
        market_groups=[(100, None, "Manufacture & Research")],
        blueprint_time=[], blueprint_materials=[], blueprint_products=[],
        categories=[(4, "Material")],
    )

    view = ProductionPlannerView()
    view.item_input.setText("Tritanium")
    view.quantity_input.setText("100")
    view._set_stock_target()
    _wait_for_threads(qapp, view)
    assert "Stock target set" in view.status_label.text()

    view.update_quantity_input.setText("250")
    view.update_jita_combo.setCurrentIndex(view.update_jita_combo.findData(True))
    view._update_stock_target()
    _wait_for_threads(qapp, view)

    assert "Stock target updated" in view.status_label.text()
    assert "250" in view.status_label.text()
    rows = storage.load_stock_targets()
    assert rows == [(34, "Tritanium", 250.0, True)]


def test_planner_set_and_clear_decryptor_override_end_to_end(qapp, db):
    """Exercises Set Override / Clear (auto) on the Planner Decryptor
    Overrides box - same run_action -> table path as the BPC-cost e2e."""
    from eve_trader_local import storage
    from eve_trader_local.gui.views.production_planner import ProductionPlannerView

    storage.replace_sde_data(
        types=[(34, 18, "Tritanium", 0.01, 1, 100, 0, 1, 1)],
        groups=[(18, 4, "Mineral")],
        market_groups=[(100, None, "Manufacture & Research")],
        blueprint_time=[], blueprint_materials=[], blueprint_products=[],
        categories=[(4, "Material")],
    )

    view = ProductionPlannerView()
    assert view.decryptor_override_table.rowCount() == 0
    assert view.decryptor_combo.count() > 1

    view.decryptor_item_input.setText("Tritanium")
    view.decryptor_combo.setCurrentText("Process")
    view._set_decryptor_override()
    _wait_for_threads(qapp, view)

    assert "will use decryptor Process" in view.status_label.text()
    assert view.decryptor_override_table.rowCount() == 1
    assert storage.load_selected_decryptors() == {34: "Process"}

    view.decryptor_combo.setCurrentText("Accelerant")
    view._set_decryptor_override()
    _wait_for_threads(qapp, view)

    assert "will use decryptor Accelerant" in view.status_label.text()
    assert storage.load_selected_decryptors() == {34: "Accelerant"}

    view._clear_decryptor_override()
    _wait_for_threads(qapp, view)

    assert "cleared (auto / Best)" in view.status_label.text()
    assert view.decryptor_override_table.rowCount() == 0
    assert storage.load_selected_decryptors() == {}


def _seed_tritanium():
    from eve_trader_local import storage
    storage.replace_sde_data(
        types=[(34, 18, "Tritanium", 0.01, 1, 100, 0, 1, 1)],
        groups=[(18, 4, "Mineral")],
        market_groups=[(100, None, "Manufacture & Research")],
        blueprint_time=[], blueprint_materials=[], blueprint_products=[],
        categories=[(4, "Material")],
    )


@pytest.mark.release
def test_special_orders_set_item_end_to_end(qapp, db):
    from eve_trader_local import storage
    from eve_trader_local.gui.views.production_special_orders import SpecialOrdersView
    from eve_trader_local.production import actions as production_actions

    _seed_tritanium()
    view = SpecialOrdersView()
    view.items_input.setText("Tritanium:10")
    view._create_order()
    _wait_for_threads(qapp, view)
    assert view.orders_table.rowCount() == 1
    order_id = production_actions.do_list_special_orders()[0].order_id

    view.order_id_input.setText(order_id)
    view.edit_item_input.setText("Tritanium")
    view.edit_qty_input.setText("25")
    view._set_item()
    _wait_for_threads(qapp, view)

    assert "Tritanium x25" in view.status_label.text()
    assert view.line_items_table.rowCount() == 1
    stored = storage.list_special_order_items(order_id)
    assert [(row[0], row[1], row[2]) for row in stored] == [(34, "Tritanium", 25.0)]


@pytest.mark.release
def test_special_orders_remove_item_end_to_end(qapp, db):
    from eve_trader_local import storage
    from eve_trader_local.gui.views.production_special_orders import SpecialOrdersView
    from eve_trader_local.production import actions as production_actions

    storage.replace_sde_data(
        types=[
            (34, 18, "Tritanium", 0.01, 1, 100, 0, 1, 1),
            (35, 18, "Pyerite", 0.01, 1, 100, 0, 1, 1),
        ],
        groups=[(18, 4, "Mineral")],
        market_groups=[(100, None, "Manufacture & Research")],
        blueprint_time=[], blueprint_materials=[], blueprint_products=[],
        categories=[(4, "Material")],
    )
    view = SpecialOrdersView()
    view.items_input.setText("Tritanium:10, Pyerite:5")
    view._create_order()
    _wait_for_threads(qapp, view)
    order_id = production_actions.do_list_special_orders()[0].order_id
    view.order_id_input.setText(order_id)
    view.edit_item_input.setText("Pyerite")
    view._remove_item()
    _wait_for_threads(qapp, view)

    assert "Tritanium x10" in view.status_label.text()
    assert view.line_items_table.rowCount() == 1
    assert view.line_items_table.item(0, 0).text() == "Tritanium"
    stored = storage.list_special_order_items(order_id)
    assert [(row[0], row[1], row[2]) for row in stored] == [(34, "Tritanium", 10.0)]
    assert view.build_table_widget.rowCount() == 0

    view.edit_item_input.setText("Pyerite")
    view._remove_item()
    _wait_for_threads(qapp, view)
    assert "has no item" in view.status_label.text()
    assert view.line_items_table.rowCount() == 1
    assert [(row[0], row[2]) for row in storage.list_special_order_items(order_id)] == [(34, 10.0)]


@pytest.mark.release
def test_special_orders_combine_end_to_end(qapp, db):
    from eve_trader_local.gui.views.production_special_orders import SpecialOrdersView
    from eve_trader_local.production import actions as production_actions

    _seed_tritanium()
    view = SpecialOrdersView()
    view.items_input.setText("Tritanium:10")
    view._create_order()
    _wait_for_threads(qapp, view)
    view.items_input.setText("Tritanium:5")
    view._create_order()
    _wait_for_threads(qapp, view)
    ids = [row.order_id for row in production_actions.do_list_special_orders()]
    assert len(ids) == 2

    view.combine_ids_input.setText(", ".join(ids))
    view._compute_combined()
    _wait_for_threads(qapp, view)

    assert "Combined preview" in view.status_label.text()
    assert view.line_items_table.rowCount() == 1
    assert view.line_items_table.item(0, 1).text() == "15"


def test_main_window_opens_every_production_view_including_new_ones(qapp, db):
    from eve_trader_local.gui.main_window import MainWindow, _TOOL_MENUS

    window = MainWindow()
    labels = [label for label, _view_class in _TOOL_MENUS["Production"]]
    assert "Item Lookup" in labels
    assert "Invention Estimator" in labels
    assert "Owned Blueprints" in labels
    assert "Current Jobs && Slots" in labels
    for index, (_label, view_class) in enumerate(_TOOL_MENUS["Production"]):
        window._open_view(view_class)
        assert window.tabs.count() == index + 1
        assert isinstance(window.tabs.currentWidget(), view_class)
