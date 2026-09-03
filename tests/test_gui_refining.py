"""Smoke tests for Ore & Minerals' GUI views (gui/views/refining_*.py) - same
level as tests/test_gui_doctrine.py's own coverage: each view must open
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


def test_ore_shortlist_view_opens_with_empty_db(qapp, db):
    from eve_trader_local.gui.views.refining_ore_shortlist import OreShortlistView

    view = OreShortlistView()
    assert view.table.rowCount() == 0


def test_reprocessing_quote_view_opens_with_empty_db(qapp, db):
    from eve_trader_local.gui.views.refining_reprocessing_quote import ReprocessingQuoteView

    view = ReprocessingQuoteView()
    assert view.table.rowCount() == 0


def test_mineral_shopping_list_view_opens_with_empty_db(qapp, db):
    from eve_trader_local.gui.views.refining_mineral_shopping_list import MineralShoppingListView

    view = MineralShoppingListView()
    assert view.requirements_table.rowCount() == 0
    assert view.ore_table.rowCount() == 0
    assert view.direct_table.rowCount() == 0
    assert view.coverage_table.rowCount() == 0


def test_main_window_opens_every_ore_and_minerals_view(qapp, db):
    from eve_trader_local.gui.main_window import MainWindow, _TOOL_MENUS

    window = MainWindow()
    for index, (_label, view_class) in enumerate(_TOOL_MENUS["Ore && Minerals"]):
        window._open_view(view_class)
        assert window.tabs.count() == index + 1
        assert isinstance(window.tabs.currentWidget(), view_class)


def test_mineral_shopping_list_set_requirement_end_to_end(qapp, db, monkeypatch):
    """Exercises the run_action -> success -> status-label path with a real
    do_* call, same style as test_gui_doctrine.py's own
    test_fittings_view_create_doctrine_end_to_end."""
    import time

    from eve_trader_local import storage
    from eve_trader_local.gui.views.refining_mineral_shopping_list import MineralShoppingListView

    # do_set_mineral_requirement resolves the item against the SDE cache via
    # storage.search_sde_types - stub it so the resolve succeeds without a
    # real SDE refresh, same pattern tests/test_refining_quote.py uses.
    monkeypatch.setattr(storage, "search_sde_types", lambda name, limit=2: [(34, "Tritanium")])

    view = MineralShoppingListView()
    view.item_input.setText("Tritanium")
    view.qty_input.setText("1000")
    view._set_requirement()

    deadline = time.monotonic() + 5
    while view._threads and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)

    assert "Requirement set: Tritanium" in view.status_label.text()
    assert view.requirements_table.rowCount() == 1
