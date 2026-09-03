"""Smoke tests for Station Trading's GUI views (gui/views/station_trading_*.py)
- same level as tests/test_gui_refining.py's own coverage: each view must
open without crashing against an empty throwaway DB. Skipped entirely if
PySide6 isn't installed, same guard as test_gui.py."""
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


def test_station_shortlist_view_opens_with_empty_db(qapp, db):
    from eve_trader_local.gui.views.station_trading_shortlist import StationShortlistView

    view = StationShortlistView()
    assert view.table.rowCount() == 0


def test_undercut_skills_view_opens_with_empty_db(qapp, db):
    from eve_trader_local.gui.views.station_trading_undercut_skills import UndercutSkillsView

    view = UndercutSkillsView()
    assert view.sell_table.rowCount() == 0
    assert view.buy_table.rowCount() == 0
    assert view.skills_table.rowCount() == 0


def test_main_window_opens_every_station_trading_view(qapp, db):
    from eve_trader_local.gui.main_window import MainWindow, _TOOL_MENUS

    window = MainWindow()
    for index, (_label, view_class) in enumerate(_TOOL_MENUS["Station Trading"]):
        window._open_view(view_class)
        assert window.tabs.count() == index + 1
        assert isinstance(window.tabs.currentWidget(), view_class)


def test_station_shortlist_show_end_to_end_with_empty_db(qapp, db):
    """Exercises the run_action -> success -> status-label path with a real
    do_* call - `do_get_shortlist` on an empty persisted shortlist makes no
    live ESI call at all (confirm_live short-circuits on an empty type_id
    list), so this is safe to run with no network/character in this
    sandbox, same style as test_gui_refining.py's own end-to-end test."""
    from eve_trader_local.gui.views.station_trading_shortlist import StationShortlistView

    view = StationShortlistView()
    view._show()

    deadline = time.monotonic() + 5
    while view._threads and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)

    assert view.table.rowCount() == 0
    assert "empty" in view.status_label.text().lower()


def test_undercut_skills_refresh_skills_end_to_end_with_no_trader_characters(qapp, db):
    """No trader characters are registered in this throwaway DB, so
    `do_get_skill_summary` returns an empty list without needing a live ESI
    call/character login - exercises the same run_action round trip as
    above without depending on network access."""
    from eve_trader_local.gui.views.station_trading_undercut_skills import UndercutSkillsView

    view = UndercutSkillsView()
    view._refresh_skills()

    deadline = time.monotonic() + 5
    while view._threads and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)

    assert view.skills_table.rowCount() == 0
    assert "no trader characters registered" in view.status_label.text().lower()
