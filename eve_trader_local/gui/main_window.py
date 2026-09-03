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
other's independent refreshes)."""
from __future__ import annotations

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMainWindow, QMessageBox, QTabWidget

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
from .views.trading_shortlist import TradingShortlistView

# Declarative menu structure: {tool menu label: [(view menu-item label, view class), ...]}.
# Extend this as more views are ported - see each view module's own docstring
# for which CLI commands/do_* functions it groups together, and this file's
# own docstring for why grouping (not a 1:1 CLI-command mirror) is the point.
_TOOL_MENUS: dict[str, list[tuple[str, type]]] = {
    "Trading": [
        ("Shortlist", TradingShortlistView),
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
