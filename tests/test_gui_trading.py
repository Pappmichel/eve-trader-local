"""Smoke tests for Trading's newly-built GUI views (gui/views/trading_*.py,
apart from the pre-existing trading_shortlist.py, already covered by
test_gui.py) - same level as tests/test_gui_station_trading.py's own
coverage: each view must open without crashing against an empty throwaway
DB. Skipped entirely if PySide6 isn't installed, same guard as
test_gui.py."""
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


def _drain(qapp, view) -> None:
    deadline = time.monotonic() + 5
    while view._threads and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)


def test_candidate_discovery_view_opens_with_empty_db(qapp, db):
    from eve_trader_local.gui.views.trading_candidate_discovery import CandidateDiscoveryView

    view = CandidateDiscoveryView()
    assert view.universe_table.rowCount() == 0
    assert view.focused_table.rowCount() == 0
    assert view.new_candidates_table.rowCount() == 0


def test_realized_transactions_view_opens_with_empty_db(qapp, db):
    from eve_trader_local.gui.views.trading_realized_transactions import RealizedTransactionsView

    view = RealizedTransactionsView()
    assert view.realized_table.rowCount() == 0
    assert view.txn_table.rowCount() == 0
    assert view.character_combo.count() == 0


def test_unlisted_undercut_view_opens_with_empty_db(qapp, db):
    from eve_trader_local.gui.views.trading_unlisted_undercut import UnlistedUndercutView

    view = UnlistedUndercutView()
    assert view.unlisted_table.rowCount() == 0
    assert view.undercut_table.rowCount() == 0


def test_price_history_view_opens_with_empty_db(qapp, db):
    from eve_trader_local.gui.views.trading_price_history import PriceHistoryView

    view = PriceHistoryView()
    assert view.table.rowCount() == 0


def test_main_window_opens_every_trading_view(qapp, db):
    from eve_trader_local.gui.main_window import MainWindow, _TOOL_MENUS

    window = MainWindow()
    for index, (_label, view_class) in enumerate(_TOOL_MENUS["Trading"]):
        window._open_view(view_class)
        assert window.tabs.count() == index + 1
        assert isinstance(window.tabs.currentWidget(), view_class)


def test_unlisted_undercut_check_undercut_end_to_end_with_no_seller_character(qapp, db):
    """No seller character is registered in this throwaway DB, so
    `do_check_undercut` raises ActionError before any live ESI call -
    exercises the run_action -> failure -> status-label path, same style as
    test_gui_station_trading.py's own end-to-end test."""
    from eve_trader_local.gui.views.trading_unlisted_undercut import UnlistedUndercutView

    view = UnlistedUndercutView()
    view._check_undercut()
    _drain(qapp, view)

    assert view.undercut_table.rowCount() == 0
    assert "seller" in view.status_label.text().lower()


def test_candidate_discovery_find_candidates_end_to_end_with_empty_universe(qapp, db):
    """The focused-candidates table is empty in this throwaway DB, so
    `do_find_new_candidates` raises ActionError before any live network call
    - exercises the run_action -> failure -> status-label path without
    depending on network access."""
    from eve_trader_local.gui.views.trading_candidate_discovery import CandidateDiscoveryView

    view = CandidateDiscoveryView()
    view._find_candidates(True)
    _drain(qapp, view)

    assert "focused candidates" in view.status_label.text().lower()


def test_realized_transactions_load_transactions_end_to_end_with_no_character(qapp, db):
    from eve_trader_local.gui.views.trading_realized_transactions import RealizedTransactionsView

    view = RealizedTransactionsView()
    view._load_transactions()

    assert "buyer/seller character" in view.status_label.text().lower()


# ------------------------------------------------- column widths/default sort
def test_unlisted_stock_defaults_to_unlisted_qty_desc(qapp, db, monkeypatch):
    from eve_trader_local import actions
    from eve_trader_local.models import UnlistedStockRow
    from eve_trader_local.gui.views.trading_unlisted_undercut import UnlistedUndercutView

    rows = [
        UnlistedStockRow(type_id=1, item="Low", asset_quantity=5, sell_order_remaining=0,
                         unlisted_quantity=5, sell_volume=10, margin=0.1),
        UnlistedStockRow(type_id=2, item="High", asset_quantity=50, sell_order_remaining=0,
                         unlisted_quantity=50, sell_volume=10, margin=0.1),
    ]
    monkeypatch.setattr(actions, "do_check_seller_unlisted_stock", lambda: {"rows": rows})

    view = UnlistedUndercutView()
    view._check_unlisted()
    _drain(qapp, view)

    assert view.unlisted_table.item(0, 0).text() == "High"
    assert view.unlisted_table.item(1, 0).text() == "Low"


def test_price_history_defaults_to_trend_desc(qapp, db, monkeypatch):
    from eve_trader_local import actions
    from eve_trader_local.models import ShortlistItem
    from eve_trader_local.gui.views.trading_price_history import PriceHistoryView

    monkeypatch.setattr(
        actions, "do_shortlist_trends",
        lambda: {
            1: {"recent_avg_margin": 0.1, "baseline_avg_margin": 0.2, "trend_pct": -0.5},
            2: {"recent_avg_margin": 0.3, "baseline_avg_margin": 0.1, "trend_pct": 2.0},
        })
    import eve_trader_local.storage as storage
    monkeypatch.setattr(
        storage, "load_shortlist",
        lambda: [ShortlistItem(item="Falling", item_id=1, category="cat", volume_m3=1.0),
                ShortlistItem(item="Rising", item_id=2, category="cat", volume_m3=1.0)])

    view = PriceHistoryView()

    assert view.table.item(0, 0).text() == "Rising"
    assert view.table.item(1, 0).text() == "Falling"
