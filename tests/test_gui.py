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

from PySide6.QtCore import Qt, QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def qsettings(qapp, tmp_path):
    """Redirects the bare `QSettings()` constructor `main_window.py` uses to
    a throwaway .ini file under `tmp_path` for the duration of one test, so
    window/tab-state persistence tests never touch the real user's saved
    settings. Sets the org/application name on the (session-scoped) `qapp`
    too - `QSettings()` with no explicit name resolves both from
    `QCoreApplication`, same as the real `main.py` sets them once at
    startup."""
    qapp.setOrganizationName("eve-trader-local-tests")
    qapp.setApplicationName("eve-trader-local-tests")
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    QSettings().clear()
    yield
    # Deliberately left redirected rather than reset to Qt's own default -
    # QSettings.setPath has no getter for "the previous value" to restore,
    # and leaving it pointed at a (by-then-gone) tmp_path is harmless: it
    # only affects other QSettings() calls made later in this same test
    # process, never the real installed app (a separate process entirely).


def test_main_window_has_one_menu_per_tool(qapp, db):
    from eve_trader_local.gui.main_window import MainWindow

    window = MainWindow()
    menu_labels = [action.text() for action in window.menuBar().actions()]
    assert menu_labels == ["App", "Trading", "Production", "Doctrine", "Ore && Minerals", "Station Trading"]


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


def test_station_trading_menu_lists_its_views_not_the_placeholder(qapp, db):
    from eve_trader_local.gui.main_window import _TOOL_MENUS

    labels = [label for label, _view_class in _TOOL_MENUS["Station Trading"]]
    assert labels == ["Shortlist", "Undercut && Skills"]


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


# ------------------------------------------------- column widths/default sort
def test_trading_shortlist_defaults_to_margin_desc(qapp, db):
    """`_DEFAULT_SORT` should already be applied - by the time the last saved
    snapshot loads on open - to Margin desc (see the view module's own
    comment for why), without the user clicking a header first."""
    from eve_trader_local import storage
    from eve_trader_local.shortlist import ShortlistRow
    from eve_trader_local.gui.views.trading_shortlist import TradingShortlistView

    rows = [
        ShortlistRow(item="Low Margin", item_id=1, category="cat", landed_cost=1.0, net_sell=1.1,
                    sell_volume=1, own_orders_remaining=0, profit_per_unit=0.1, margin=0.05,
                    profit_per_m3=0.1, decision="Buy", active=True, volume_m3=1.0, jita_sell=1.0,
                    import_cost=0.0, meta_level=0, avg_daily_volume=1.0),
        ShortlistRow(item="High Margin", item_id=2, category="cat", landed_cost=1.0, net_sell=2.0,
                    sell_volume=1, own_orders_remaining=0, profit_per_unit=1.0, margin=0.5,
                    profit_per_m3=1.0, decision="Buy", active=True, volume_m3=1.0, jita_sell=2.0,
                    import_cost=0.0, meta_level=0, avg_daily_volume=1.0),
    ]
    storage.save_shortlist_snapshot(rows, "2026-01-01T00:00:00")

    view = TradingShortlistView()
    header = view.table.horizontalHeader()
    assert (header.sortIndicatorSection(), header.sortIndicatorOrder()) == (3, Qt.SortOrder.DescendingOrder)
    assert view.table.item(0, 0).text() == "High Margin"
    assert view.table.item(1, 0).text() == "Low Margin"


def test_build_candidates_defaults_to_potential_daily_profit_desc(qapp, db, monkeypatch):
    from eve_trader_local.production import actions as production_actions
    from eve_trader_local.gui.views.production_build_candidates import BuildCandidatesView

    class _Row:
        def __init__(self, type_name, potential_daily_profit):
            self.type_name = type_name
            self.activity = "Manufacturing"
            self.build_cost = 100.0
            self.margin = 0.1
            self.daily_movement = 10.0
            self.potential_daily_profit = potential_daily_profit
            self.meta_level = 0

    rows = [_Row("Cheap Ship", 1_000.0), _Row("Lucrative Ship", 50_000.0)]
    monkeypatch.setattr(production_actions, "do_discover_build_candidates", lambda: {"rows": rows})

    view = BuildCandidatesView()
    view._discover()
    _drain(qapp, view)

    assert view.table.rowCount() == 2
    assert view.table.item(0, 0).text() == "Lucrative Ship"
    assert view.table.item(1, 0).text() == "Cheap Ship"


def test_numeric_columns_sort_by_value_not_text(qapp, db):
    """Regression guard for `_SortableTableWidgetItem`: a plain
    QTableWidgetItem sorts "1,234" before "999" as text - column widths/
    default-sort work is pointless if clicking (or default-applying) a
    numeric column's header doesn't actually order it by value."""
    from eve_trader_local.gui.views.base import BaseView, TableView

    class _NumericView(TableView):
        def __init__(self):
            BaseView.__init__(self)
            self._build_table(["Value"])
            self._finish_status_row()

    view = _NumericView()
    view.populate_table([["999"], ["1,234"], ["50"]])
    view.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
    values = [view.table.item(row, 0).text() for row in range(view.table.rowCount())]
    assert values == ["50", "999", "1,234"]


def _drain(qapp, view) -> None:
    deadline = time.monotonic() + 5
    while view._threads and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)


# ---------------------------------------------------- window/tab-state (QSettings)
def test_window_geometry_round_trips_through_qsettings(qapp, db, qsettings):
    from eve_trader_local.gui.main_window import MainWindow

    # Kept within the offscreen platform's own small virtual screen (800x800)
    # - a size that doesn't fit any available screen gets clipped by Qt on
    # restore, which would make this test flaky rather than a real
    # regression signal.
    window = MainWindow()
    window.resize(650, 500)
    window.close()  # triggers closeEvent -> save

    restored = MainWindow()
    restored.restore_state()
    assert restored.size().width() == 650
    assert restored.size().height() == 500


def test_open_tabs_and_active_tab_round_trip_through_qsettings(qapp, db, qsettings):
    from eve_trader_local.gui.main_window import MainWindow
    from eve_trader_local.gui.views.production_build_candidates import BuildCandidatesView
    from eve_trader_local.gui.views.trading_shortlist import TradingShortlistView

    window = MainWindow()
    window._open_view(TradingShortlistView)
    window._open_view(BuildCandidatesView)
    window.tabs.setCurrentIndex(0)
    window.close()  # triggers closeEvent -> save

    restored = MainWindow()
    restored.restore_state()
    assert restored.tabs.count() == 2
    assert {type(restored.tabs.widget(i)) for i in range(2)} == {TradingShortlistView, BuildCandidatesView}
    assert isinstance(restored.tabs.currentWidget(), TradingShortlistView)


def test_restore_skips_a_tab_whose_location_no_longer_resolves(qapp, db, qsettings, caplog):
    """A saved tab location that doesn't match any current menu entry (e.g.
    after a future rename/removal) must be skipped, not crash the restore."""
    from PySide6.QtCore import QSettings as _QSettings
    from eve_trader_local.gui.main_window import MainWindow

    settings = _QSettings()
    settings.setValue("mainWindow/openTabs", [("Nonexistent Tool", "Nonexistent View")])
    settings.setValue("mainWindow/activeTabIndex", 0)

    window = MainWindow()
    window.restore_state()  # must not raise
    assert window.tabs.count() == 0
