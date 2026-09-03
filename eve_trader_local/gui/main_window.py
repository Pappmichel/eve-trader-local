"""The main window: a menu bar with one menu per tool, each listing that
tool's views as menu items, and a shared workspace of closable tabs - see
the `gui` package docstring for the full navigation-model rationale
(jEveAssets-style, not the parent web frontend's fixed-sidebar-per-page
model).

Each tool's menu is built from a declarative list of (label, view_class)
pairs (`_TOOL_MENUS` below) rather than one hand-written `_open_x` method per
view - adding a new view later is a one-line addition to that list, not a
new method plus a new `QAction` wiring block. Opening the same view twice
re-focuses its existing tab instead of duplicating it (matching jEveAssets'
own behavior, and avoiding two tabs silently drifting out of sync with each
other's independent refreshes).

Window/tab state persistence (`closeEvent`/`restore_state` below) uses
`QSettings` (org/app name set once in `main.py`) - the standard Qt mechanism
for exactly this, no custom storage needed:

- Window geometry: `saveGeometry()`/`restoreGeometry()` (bytes, handles
  multi-monitor/maximized state correctly, unlike hand-tracking x/y/w/h).
  No `saveState()`/`restoreState()` alongside it - this window has no
  toolbars/docks yet for that to matter.
- Which tabs were open, and which was active: each open tab is identified by
  `(tool menu label, view menu-item label)` - a reverse lookup
  (`_VIEW_MENU_LOCATION`, built once from `_TOOL_MENUS`) maps a view class
  back to that pair so it can round-trip through `QSettings` as plain
  strings and be reopened via the exact same `_open_view` mechanism a menu
  click uses. A tab whose view class no longer resolves to a menu entry (a
  future code change) is skipped on restore, not a hard failure - see
  `restore_state`'s own docstring.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QSettings
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import QMainWindow, QMessageBox, QTabWidget

logger = logging.getLogger(__name__)

from .dialogs.characters_dialog import CharactersDialog
from .dialogs.settings_dialog import SettingsDialog
from .views.doctrine_contract_history import ContractHistoryView
from .views.doctrine_fittings import FittingsView
from .views.doctrine_shopping_list import ShoppingListView
from .views.doctrine_stockpile import StockpileStatusView
from .views.production_asset_optimized import AssetOptimizedPlannerView
from .views.production_build_candidates import BuildCandidatesView
from .views.production_logistics import LogisticsView
from .views.production_margins_market import MarginsMarketView
from .views.production_planner import ProductionPlannerView
from .views.production_special_orders import SpecialOrdersView
from .views.refining_mineral_shopping_list import MineralShoppingListView
from .views.refining_ore_shortlist import OreShortlistView
from .views.refining_reprocessing_quote import ReprocessingQuoteView
from .views.station_trading_shortlist import StationShortlistView
from .views.station_trading_undercut_skills import UndercutSkillsView
from .views.trading_candidate_discovery import CandidateDiscoveryView
from .views.trading_price_history import PriceHistoryView
from .views.trading_realized_transactions import RealizedTransactionsView
from .views.trading_shortlist import TradingShortlistView
from .views.trading_unlisted_undercut import UnlistedUndercutView

# Declarative menu structure: {tool menu label: [(view menu-item label, view class), ...]}.
# Extend this as more views are ported - see each view module's own docstring
# for which CLI commands/do_* functions it groups together, and this file's
# own docstring for why grouping (not a 1:1 CLI-command mirror) is the point.
_TOOL_MENUS: dict[str, list[tuple[str, type]]] = {
    "Trading": [
        ("Shortlist", TradingShortlistView),
        ("Candidate Discovery", CandidateDiscoveryView),
        ("Realized Trades && Transactions", RealizedTransactionsView),
        ("Unlisted Stock && Undercut Check", UnlistedUndercutView),
        ("Price History", PriceHistoryView),
    ],
    "Production": [
        ("Build Candidates", BuildCandidatesView),
        ("Planner", ProductionPlannerView),
        ("Asset-Optimized Planner", AssetOptimizedPlannerView),
        ("Logistics", LogisticsView),
        ("Special Orders", SpecialOrdersView),
        ("Ship Margins && Market Status", MarginsMarketView),
    ],
    "Doctrine": [
        ("Fittings", FittingsView),
        ("Stockpile Status", StockpileStatusView),
        ("Shopping List", ShoppingListView),
        ("Contract History", ContractHistoryView),
    ],
    "Ore && Minerals": [
        ("Ore Shortlist", OreShortlistView),
        ("Reprocessing Quote", ReprocessingQuoteView),
        ("Mineral Shopping List", MineralShoppingListView),
    ],
    "Station Trading": [
        ("Shortlist", StationShortlistView),
        ("Undercut && Skills", UndercutSkillsView),
    ],
}

# Reverse lookup built once from _TOOL_MENUS: view class -> (tool menu label,
# view menu-item label) - what closeEvent/restore_state serialize an open tab
# as, and what _open_view's own action-click handler effectively does the
# forward direction of already.
_VIEW_MENU_LOCATION: dict[type, tuple[str, str]] = {
    view_class: (tool_label, view_label)
    for tool_label, views in _TOOL_MENUS.items()
    for view_label, view_class in views
}
_VIEW_CLASS_BY_LOCATION: dict[tuple[str, str], type] = {
    location: view_class for view_class, location in _VIEW_MENU_LOCATION.items()
}


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("eve-trader-local")
        self.resize(1200, 800)

        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.setCentralWidget(self.tabs)

        self._open_views: dict[type, int] = {}  # view class -> its tab index, for re-focus-not-duplicate
        self._build_menus()

    def _build_menus(self) -> None:
        self._build_app_menu()
        for tool_label, views in _TOOL_MENUS.items():
            menu = self.menuBar().addMenu(tool_label)
            if not views:
                placeholder = QAction("(not built yet)", self)
                placeholder.setEnabled(False)
                menu.addAction(placeholder)
                continue
            for view_label, view_class in views:
                action = QAction(view_label, self)
                action.triggered.connect(lambda checked=False, vc=view_class: self._open_view(vc))
                menu.addAction(action)

    def _build_app_menu(self) -> None:
        """Cross-tool, app-level actions (Settings, Characters) - deliberately
        the one menu that isn't one of `_TOOL_MENUS`' per-tool entries, since
        neither dialog belongs to just one tool. Both are opened as modal
        `QDialog`s (`exec()`), not workspace tabs - see `dialogs/__init__.py`
        for why they can't be `views.base.BaseView` subclasses."""
        menu = self.menuBar().addMenu("App")

        settings_action = QAction("Settings...", self)
        settings_action.triggered.connect(self._open_settings)
        menu.addAction(settings_action)

        characters_action = QAction("Characters...", self)
        characters_action.triggered.connect(self._open_characters)
        menu.addAction(characters_action)

    def _open_settings(self) -> None:
        SettingsDialog(self).exec()

    def _open_characters(self) -> None:
        CharactersDialog(self).exec()

    def _open_view(self, view_class: type) -> None:
        existing_index = self._open_views.get(view_class)
        if existing_index is not None and self._tab_still_at(existing_index, view_class):
            self.tabs.setCurrentIndex(existing_index)
            return
        try:
            widget = view_class(self)
        except Exception as e:  # noqa: BLE001 - a broken view must not take the whole window down
            QMessageBox.critical(self, "Could not open view", f"{view_class.__name__} failed to open:\n{e!r}")
            return
        index = self.tabs.addTab(widget, getattr(widget, "title", view_class.__name__))
        self.tabs.setCurrentIndex(index)
        self._open_views[view_class] = index

    def _tab_still_at(self, index: int, view_class: type) -> bool:
        """Guards against a stale index after other tabs were closed and Qt
        shifted everything's position - cheap enough to just check the
        widget's own type still matches what we expect at that slot."""
        widget = self.tabs.widget(index)
        return widget is not None and isinstance(widget, view_class)

    def _close_tab(self, index: int) -> None:
        widget = self.tabs.widget(index)
        self.tabs.removeTab(index)
        if widget is not None:
            widget.deleteLater()
        # Indices below `index` are untouched by Qt's removal; anything above
        # it shifts down by one - keep the tracking map consistent with that.
        for view_class, tracked_index in list(self._open_views.items()):
            if tracked_index == index:
                del self._open_views[view_class]
            elif tracked_index > index:
                self._open_views[view_class] = tracked_index - 1

    # ------------------------------------------------------- window/tab state
    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt's own method name
        """Saves geometry plus which tabs were open/active - see this
        module's own docstring for the QSettings shape. Best-effort: a
        failure here (e.g. a genuinely unwritable settings location) must
        never block the window from actually closing."""
        try:
            settings = QSettings()
            settings.setValue("mainWindow/geometry", self.saveGeometry())

            locations = []
            for index in range(self.tabs.count()):
                widget = self.tabs.widget(index)
                location = _VIEW_MENU_LOCATION.get(type(widget))
                if location is not None:
                    locations.append(location)
            settings.setValue("mainWindow/openTabs", locations)
            settings.setValue("mainWindow/activeTabIndex", self.tabs.currentIndex())
        except Exception:  # noqa: BLE001 - saving state must never block a real close
            logger.warning("Could not save window/tab state on close", exc_info=True)
        super().closeEvent(event)

    def restore_state(self) -> None:
        """Call once after construction, before `show()` (see `main.py`) -
        not from `__init__` itself, so a broken restore can't interfere with
        the window's own construction. Restores geometry and reopens
        whichever tabs were open (and which was active) at the last close.
        Fails soft, tab by tab: a location that no longer maps to a real
        view class (e.g. a view renamed/removed in a later code change) is
        skipped with a logged warning rather than aborting the whole
        restore, and any unexpected error restoring geometry/tabs is caught
        the same way - a broken saved state must never prevent the window
        from opening at all."""
        try:
            settings = QSettings()
            geometry = settings.value("mainWindow/geometry")
            if geometry is not None:
                self.restoreGeometry(geometry)

            locations = settings.value("mainWindow/openTabs") or []
            for location in locations:
                try:
                    tool_label, view_label = location
                except (TypeError, ValueError):
                    logger.warning("Skipping malformed saved tab entry: %r", location)
                    continue
                view_class = _VIEW_CLASS_BY_LOCATION.get((tool_label, view_label))
                if view_class is None:
                    logger.warning("Skipping saved tab for a view that no longer exists: %s / %s",
                                   tool_label, view_label)
                    continue
                try:
                    self._open_view(view_class)
                except Exception:  # noqa: BLE001 - one broken saved tab must not abort the rest
                    logger.warning("Could not reopen saved tab %s / %s", tool_label, view_label, exc_info=True)

            active_index = settings.value("mainWindow/activeTabIndex")
            if active_index is not None:
                try:
                    active_index = int(active_index)
                except (TypeError, ValueError):
                    active_index = None
                if active_index is not None and 0 <= active_index < self.tabs.count():
                    self.tabs.setCurrentIndex(active_index)
        except Exception:  # noqa: BLE001 - a broken saved state must never block startup
            logger.warning("Could not restore window/tab state", exc_info=True)
