"""Station Trading's Undercut Check && Trader Skills tab: `check-station-
undercut` and `station-trading-skills` grouped into one screen, same
"small, independent, read-only reports with no shared data model" reasoning
`production_margins_market.py`'s `MarginsMarketView` already uses for its
own three-way grouping.

- `do_check_undercut` - bidirectional: flags any of the registered trader
  characters' own Jita orders (sell *or* buy) that a genuinely different
  market participant now beats. Shown as two tables (sell/buy), since a row
  is either one or the other, never both.
- `do_get_skill_summary` - trade-skill levels per registered trader
  character plus the derived order-slot count. One row per character; a
  character whose skill pull failed shows an error note instead of levels
  rather than being silently dropped.

Both are cache-only reads now (see esi_update.py's own docstring) - the live
ESI fetch only happens inside App > Update Data...'s Station Trading scope.
Neither loads on tab-open (nothing may have been cached yet), so this starts
empty until a button is clicked."""
from __future__ import annotations

import functools

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QTabWidget

from ...station_trading import actions as station_trading_actions
from ...station_trading.constants import SKILL_LABELS
from .. import icons
from .base import BaseView
from .production_common import build_table, fmt_isk, populate

_UNDERCUT_COLUMNS = ["Item", "My Price", "Competitor Price", "Difference"]
_UNDERCUT_WIDTHS = [200, None, None, None]
# undercut.py: "results.sort(key=lambda r: r['difference'], reverse=True)",
# shared by both the sell and buy undercut checks.
_UNDERCUT_SORT = (3, Qt.SortOrder.DescendingOrder)

_SKILL_COLUMNS = ["Character", "Order Slots", *SKILL_LABELS.values(), "Note"]
_SKILL_WIDTHS = [160, None, *([None] * len(SKILL_LABELS)), 160]
# One row per registered trader character, in registration order - no
# inherent ranking to default-sort by.


def _undercut_row(row: dict) -> list:
    return [row["name"], fmt_isk(row["my_price"]), fmt_isk(row["competitor_price"]), fmt_isk(row["difference"])]


def _skill_row(summary: dict) -> list:
    if "error" in summary:
        return [summary["character_name"], None, *([None] * len(SKILL_LABELS)), summary["error"]]
    levels = summary["levels"]
    return [summary["character_name"], summary["order_slots"], *(levels[label] for label in SKILL_LABELS.values()),
            ""]


class UndercutSkillsView(BaseView):
    title = "Station Trading - Undercut && Skills"

    def __init__(self, parent=None):
        super().__init__(parent)

        toolbar = QHBoxLayout()
        undercut_btn = QPushButton(icons.icon("show"), "Show Undercuts")
        undercut_btn.clicked.connect(self._check_undercut)
        toolbar.addWidget(undercut_btn)
        skills_btn = QPushButton(icons.icon("show"), "Show Trader Skills")
        skills_btn.clicked.connect(self._refresh_skills)
        toolbar.addWidget(skills_btn)
        toolbar.addStretch(1)
        self.root_layout.addLayout(toolbar)

        self.tabs = QTabWidget()
        self.sell_table = build_table(_UNDERCUT_COLUMNS, column_widths=_UNDERCUT_WIDTHS)
        self.buy_table = build_table(_UNDERCUT_COLUMNS, column_widths=_UNDERCUT_WIDTHS)
        self.skills_table = build_table(_SKILL_COLUMNS, column_widths=_SKILL_WIDTHS)
        self.tabs.addTab(self.sell_table, "Sell Orders Undercut")
        self.tabs.addTab(self.buy_table, "Buy Orders Outbid")
        self.tabs.addTab(self.skills_table, "Trader Skills")
        self.root_layout.addWidget(self.tabs)

        self._add_staleness_label(["market_orders", "skills"])
        self._finish_status_row()

    def _check_undercut(self) -> None:
        self.run_action(functools.partial(station_trading_actions.do_check_undercut), self._on_undercut,
                        busy_message="Loading the last cached undercut check...")

    def _on_undercut(self, result: dict) -> None:
        populate(self.sell_table, [_undercut_row(r) for r in result["sell"]], default_sort=_UNDERCUT_SORT)
        populate(self.buy_table, [_undercut_row(r) for r in result["buy"]], default_sort=_UNDERCUT_SORT)
        total = len(result["sell"]) + len(result["buy"])
        self._refresh_staleness_label()
        self.show_info(f"{len(result['sell'])} sell order(s) undercut, {len(result['buy'])} buy order(s) outbid."
                       if total else "None of your Jita orders are currently undercut/outbid.")

    def _refresh_skills(self) -> None:
        self.run_action(functools.partial(station_trading_actions.do_get_skill_summary), self._on_skills,
                        busy_message="Loading the last cached trade-skill summary...")

    def _on_skills(self, summaries: list[dict]) -> None:
        populate(self.skills_table, [_skill_row(s) for s in summaries])
        self._refresh_staleness_label()
        self.show_info(f"{len(summaries)} trader character(s)." if summaries else
                       "No trader characters registered yet. Run: eve-trader-local auth --role trader")
