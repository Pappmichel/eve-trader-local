"""Smoke tests for Doctrine's GUI views (gui/views/doctrine_*.py) - same
level as tests/test_gui_production.py's own Production coverage: each view
must open without crashing against an empty throwaway DB. Skipped entirely
if PySide6 isn't installed, same guard as test_gui.py."""
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


def test_fittings_view_opens_with_empty_db(qapp, db):
    from eve_trader_local.gui.views.doctrine_fittings import FittingsView

    view = FittingsView()
    assert view.doctrine_table.rowCount() == 0
    assert view.fittings_table.rowCount() == 0


def test_stockpile_status_view_opens_with_empty_db(qapp, db):
    from eve_trader_local.gui.views.doctrine_stockpile import StockpileStatusView

    view = StockpileStatusView()
    assert view.fitting_status_table.rowCount() == 0
    assert view.stockpile_table.rowCount() == 0
    assert view.contracts_table.rowCount() == 0


def test_shopping_list_view_opens_with_empty_db(qapp, db):
    from eve_trader_local.gui.views.doctrine_shopping_list import ShoppingListView

    view = ShoppingListView()
    assert view.table.rowCount() == 0


def test_contract_history_view_opens_with_empty_db(qapp, db):
    from eve_trader_local.gui.views.doctrine_contract_history import ContractHistoryView

    view = ContractHistoryView()
    assert view.table.rowCount() == 0


def test_main_window_opens_every_doctrine_view(qapp, db):
    from eve_trader_local.gui.main_window import MainWindow, _TOOL_MENUS

    window = MainWindow()
    for index, (_label, view_class) in enumerate(_TOOL_MENUS["Doctrine"]):
        window._open_view(view_class)
        assert window.tabs.count() == index + 1
        assert isinstance(window.tabs.currentWidget(), view_class)


def test_fittings_view_create_doctrine_end_to_end(qapp, db):
    """Exercises the run_action -> success -> status-label path with a real
    do_* call, same style as test_gui.py's own run_action smoke tests but
    through an actual view/form rather than a bare lambda."""
    import time

    from eve_trader_local.gui.views.doctrine_fittings import FittingsView

    view = FittingsView()
    view.doctrine_name_input.setText("Test Doctrine")
    view._create_doctrine()

    deadline = time.monotonic() + 5
    while view._threads and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)

    assert "Created doctrine 'Test Doctrine'" in view.status_label.text()
    assert view.doctrine_table.rowCount() == 1
