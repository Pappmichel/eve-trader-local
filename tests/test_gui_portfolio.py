"""Smoke tests for the new Portfolio GUI view (`portfolio.
portfolio_overview()`, previously CLI-only) - same "opens without crashing
against an empty throwaway DB" level as the other GUI test modules. Skipped
entirely if PySide6 isn't installed, same guard as the rest."""
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


def test_portfolio_overview_view_opens_with_empty_db(qapp, db):
    from eve_trader_local.gui.views.portfolio_overview import PortfolioOverviewView

    view = PortfolioOverviewView()
    assert view._rows["combined_value"].text() == "-"


def test_portfolio_overview_refresh_end_to_end(qapp, db):
    """Exercises the run_action -> success -> status-label path with a real
    portfolio_overview() call, same style as test_gui_doctrine.py's own
    run_action smoke test. A fresh DB has no realized trades and no stock
    targets - both halves of portfolio_overview() degrade independently to
    zero/None (see portfolio.py's own docstring), so the combined value
    label ends up at "0 ISK" rather than left blank."""
    import time

    from eve_trader_local.gui.views.portfolio_overview import PortfolioOverviewView

    view = PortfolioOverviewView()
    view._refresh()

    deadline = time.monotonic() + 5
    while view._threads and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)

    assert "ISK" in view._rows["combined_value"].text()
    assert view.status_label.text() == "Portfolio overview loaded."


def test_main_window_has_portfolio_menu(qapp, db):
    from eve_trader_local.gui.main_window import MainWindow
    from eve_trader_local.gui.views.portfolio_overview import PortfolioOverviewView

    window = MainWindow()
    menu_labels = [action.text() for action in window.menuBar().actions()]
    assert "Portfolio" in menu_labels
    window._open_view(PortfolioOverviewView)
    assert window.tabs.count() == 1
    assert isinstance(window.tabs.currentWidget(), PortfolioOverviewView)
