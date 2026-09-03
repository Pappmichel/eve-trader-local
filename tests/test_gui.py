"""Smoke tests for the native GUI skeleton (gui/). Skipped entirely if
PySide6 isn't installed - it's an optional `[gui]` extra (see
pyproject.toml), not a hard dependency of the CLI/backend the rest of this
test suite covers.

These deliberately stay at the "does the machinery work at all" level
(menu structure, tab open/re-focus/close bookkeeping, the worker-thread
round trip) rather than testing real do_* wiring per view - that's each
view's own, much larger job once more of them exist; see
gui/views/trading_shortlist.py for the one real view built so far."""
from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_main_window_has_one_menu_per_tool(qapp, db):
    from eve_trader_local.gui.main_window import MainWindow

    window = MainWindow()
    menu_labels = [action.text() for action in window.menuBar().actions()]
    assert menu_labels == ["Trading", "Production", "Doctrine", "Ore && Minerals", "Station Trading"]


def test_production_menu_lists_its_views_not_the_placeholder(qapp, db):
    from eve_trader_local.gui.main_window import _TOOL_MENUS

    labels = [label for label, _view_class in _TOOL_MENUS["Production"]]
    assert labels == [
        "Build Candidates", "Planner", "Asset-Optimized Planner", "Logistics",
        "Special Orders", "Ship Margins && Market Status",
    ]


def test_doctrine_menu_lists_its_views_not_the_placeholder(qapp, db):
    from eve_trader_local.gui.main_window import _TOOL_MENUS

    labels = [label for label, _view_class in _TOOL_MENUS["Doctrine"]]
    assert labels == ["Fittings", "Stockpile Status", "Shopping List", "Contract History"]


def test_ore_and_minerals_menu_lists_its_views_not_the_placeholder(qapp, db):
    from eve_trader_local.gui.main_window import _TOOL_MENUS

    labels = [label for label, _view_class in _TOOL_MENUS["Ore && Minerals"]]
    assert labels == ["Ore Shortlist", "Reprocessing Quote", "Mineral Shopping List"]


def test_opening_same_view_twice_refocuses_not_duplicates(qapp, db):
    from eve_trader_local.gui.main_window import MainWindow
    from eve_trader_local.gui.views.trading_shortlist import TradingShortlistView

    window = MainWindow()
    window._open_view(TradingShortlistView)
    assert window.tabs.count() == 1
    window._open_view(TradingShortlistView)
    assert window.tabs.count() == 1


def test_closing_a_tab_removes_its_tracking_entry(qapp, db):
    from eve_trader_local.gui.main_window import MainWindow
    from eve_trader_local.gui.views.trading_shortlist import TradingShortlistView

    window = MainWindow()
    window._open_view(TradingShortlistView)
    assert window._open_views
    window._close_tab(0)
    assert window.tabs.count() == 0
    assert not window._open_views


def test_run_action_delivers_result_on_the_ui_thread(qapp):
    from eve_trader_local.gui.views.base import BaseView

    view = BaseView()
    results = []
    view.run_action(lambda: 42, results.append)

    deadline = time.monotonic() + 5
    while not results and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)

    assert results == [42]
    assert view._threads == []


def test_run_action_surfaces_action_error_as_status_text(qapp):
    from eve_trader_local.errors import ActionError
    from eve_trader_local.gui.views.base import BaseView

    view = BaseView()

    def _fail():
        raise ActionError("nope")

    view.run_action(_fail, lambda _r: None)

    deadline = time.monotonic() + 5
    while view._threads and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)

    assert view.status_label.text() == "nope"


def test_trading_shortlist_view_opens_with_empty_db(qapp, db):
    from eve_trader_local.gui.views.trading_shortlist import TradingShortlistView

    view = TradingShortlistView()
    assert view.table.rowCount() == 0
