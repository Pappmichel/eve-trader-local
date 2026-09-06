"""Doctrine's Shopping List tab: `shopping-list` on its own - every
stockpile shortfall (across every doctrine, optionally filtered to one)
priced Build vs. Buy-C-J vs. Buy-Jita. Kept separate from Stockpile Status
even though it's derived from the same underlying shortfalls, because
`do_get_shopping_list` (home/Jita pricing plus Production's own build-cost
engine, see doctrine/engine.py's `shopping_list_rows`) is a noticeably
heavier computation than the Stockpile Status tab's purely-local ampel/
shortfall pass - keeping it on its own tab means clicking Refresh here never
blocks on re-running that other, unrelated computation too.

Prices themselves are cache-only now (see esi_update.py's own docstring):
the live ESI order-book fetch only happens inside App > Update Data...'s
Doctrine scope; "Refresh" here just re-runs the local build-cost/shortfall
math against whatever that last sync cached."""
from __future__ import annotations

import functools

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit

from ...doctrine import actions as doctrine_actions
from .base import TableView
from .production_common import fmt_isk

_COLUMNS = ["Item", "Shortfall", "Build Cost", "C-J Price", "Jita Landed", "Recommended", "Total Cost"]
_COLUMN_WIDTHS = [200, None, None, None, None, 130, None]
# doctrine/engine.py's aggregate_stockpile_rows: "aggregated.sort(key=lambda
# r: r.shortfall, reverse=True)" - the biggest shortfall first.
_DEFAULT_SORT = (1, Qt.SortOrder.DescendingOrder)


def _row_to_cells(row: dict) -> list:
    return [row["type_name"], f"{row['shortfall']:,.0f}", fmt_isk(row["build_cost"]), fmt_isk(row["cj_price"]),
            fmt_isk(row["jita_landed_price"]), row["recommended_source"] or "-", fmt_isk(row["total_cost"])]


class ShoppingListView(TableView):
    title = "Doctrine - Shopping List"

    def __init__(self, parent=None):
        super().__init__(parent)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Doctrine ID (blank = all):"))
        self.doctrine_filter_input = QLineEdit()
        filter_row.addWidget(self.doctrine_filter_input)
        filter_row.addStretch(1)
        self.root_layout.addLayout(filter_row)

        self._build_toolbar([("Refresh", self._refresh, "refresh")])
        self._build_table(_COLUMNS, column_widths=_COLUMN_WIDTHS, default_sort=_DEFAULT_SORT)
        self._add_staleness_label(["assets", "market_prices"])
        self._finish_status_row()

    def _refresh(self) -> None:
        doctrine_id = self.doctrine_filter_input.text().strip() or None
        self.run_action(functools.partial(doctrine_actions.do_get_shopping_list, doctrine_id), self._on_refreshed,
                        busy_message="Pricing every stockpile shortfall (Build vs. Buy-C-J vs. Buy-Jita)...")

    def _on_refreshed(self, result: dict) -> None:
        rows = result["rows"]
        self.populate_table([_row_to_cells(r) for r in rows])
        self._refresh_staleness_label()
        self.show_info(f"{len(rows)} shortfall row(s)." if rows else "Nothing short of target.")
