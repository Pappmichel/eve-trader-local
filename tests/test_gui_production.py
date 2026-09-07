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
    assert view.invention_table.rowCount() == 0


def test_special_orders_invention_needs_preview(qapp, db, monkeypatch):
    """Compute-preview path: a T2 special order fills the Invention Needs tab
    without persisting a second invention workflow."""
    from eve_trader_local.gui.views.production_special_orders import SpecialOrdersView
    from eve_trader_local.production import actions, engine
    from test_production_invention_needs import JITA, T2_MODULE, _cfg, _seed

    _seed()
    monkeypatch.setattr(engine.pricing, "home_prices", lambda type_ids, cfg=None, client=None: {})
    monkeypatch.setattr(engine.pricing, "jita_prices", lambda type_ids, client=None, trading_cfg=None: JITA)

    class _FakeESIClient:
        def __init__(self, *a, **kw):
            pass

        def get_adjusted_prices(self):
            return {}

    import eve_trader_local.esi_client as esi_client_module
    monkeypatch.setattr(esi_client_module, "ESIClient", _FakeESIClient)

    order_id = actions.do_create_special_order(
        [{"type_id": T2_MODULE, "quantity": 100.0}])["order_id"]
    plan = actions.do_compute_special_order(order_id, cfg=_cfg())
    assert plan["invention_list"]

    view = SpecialOrdersView()
    view._on_computed(plan)
    assert view.invention_table.rowCount() == 1
    assert view.invention_table.item(0, 0).text() == "Damage Control II"
    assert view.invention_table.item(0, 1).text() == "Damage Control I Blueprint"


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
