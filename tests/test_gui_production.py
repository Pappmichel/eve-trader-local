"""Smoke tests for Production's GUI views (gui/views/production_*.py) - same
level as tests/test_gui.py's own Trading coverage: each view must open
without crashing against an empty throwaway DB. Skipped entirely if PySide6
isn't installed, same guard as test_gui.py."""
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


def test_build_candidates_view_opens_with_empty_db(qapp, db):
    from eve_trader_local.gui.views.production_build_candidates import BuildCandidatesView

    view = BuildCandidatesView()
    assert view.table.rowCount() == 0


def test_production_planner_view_opens_with_empty_db(qapp, db):
    from eve_trader_local.gui.views.production_planner import ProductionPlannerView

    view = ProductionPlannerView()
    assert view.stock_target_table.rowCount() == 0
    assert view.manual_stock_table.rowCount() == 0
    assert view.decryptor_override_table.rowCount() == 0
    assert view.inventory_table.rowCount() == 0
    assert view.build_table_widget.rowCount() == 0
    assert view.buy_table.rowCount() == 0
    assert view.invention_table.rowCount() == 0


def test_asset_optimized_planner_view_opens_with_empty_db(qapp, db):
    from eve_trader_local.gui.views.production_asset_optimized import AssetOptimizedPlannerView

    view = AssetOptimizedPlannerView()
    assert view.table.rowCount() == 0


def test_logistics_view_opens_with_empty_db(qapp, db):
    from eve_trader_local.gui.views.production_logistics import LogisticsView
    from eve_trader_local.production.constants import JOB_CATEGORIES

    view = LogisticsView()
    # Every job category gets a row, even with nothing configured yet.
    assert view.category_location_table.rowCount() == len(JOB_CATEGORIES)
    assert view.manual_bb_table.rowCount() == 0
    assert view.logistics_table.rowCount() == 0
    assert view.distribution_table.rowCount() == 0
    assert view.invention_logistics_table.rowCount() == 0
    assert view.t1_bpc_table.rowCount() == 0


def test_special_orders_view_opens_with_empty_db(qapp, db):
    from eve_trader_local.gui.views.production_special_orders import SpecialOrdersView

    view = SpecialOrdersView()
    assert view.orders_table.rowCount() == 0
    assert view.line_items_table.rowCount() == 0


def test_margins_market_view_opens_with_empty_db(qapp, db):
    from eve_trader_local.gui.views.production_margins_market import MarginsMarketView

    view = MarginsMarketView()
    assert view.margins_table.rowCount() == 0
    assert view.market_status_table.rowCount() == 0
    assert view.cost_index_table.rowCount() == 0


def test_main_window_opens_every_production_view(qapp, db):
    from eve_trader_local.gui.main_window import MainWindow, _TOOL_MENUS

    window = MainWindow()
    for index, (_label, view_class) in enumerate(_TOOL_MENUS["Production"]):
        window._open_view(view_class)
        assert window.tabs.count() == index + 1
        assert isinstance(window.tabs.currentWidget(), view_class)
