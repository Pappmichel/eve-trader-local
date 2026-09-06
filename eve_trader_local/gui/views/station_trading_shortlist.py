"""Station Trading's Shortlist tab: the persisted shortlist
(`do_get_shortlist`), live-price-confirmed at cache-write time now, not on
every read - see esi_update.py's own docstring. The Goonmetrics-driven
Jita-wide spread/volume candidate scan that used to sit behind a "Discover &&
Refresh" button here (`do_refresh_shortlist`) only runs from App > Update
Data...'s Station Trading scope now; this view just displays whatever that
last run cached, same "one table, a Reload button" shape as
trading_shortlist.py/refining_ore_shortlist.py."""
from __future__ import annotations

import functools

from PySide6.QtCore import Qt

from ...station_trading import actions as station_trading_actions
from .base import TableView
from .production_common import fmt_isk, fmt_pct

_COLUMNS = ["Item", "Category", "Spread %", "Avg Daily Vol", "Live Buy", "Live Sell",
            "Profit/Unit", "Margin", "Profit/Day", "Active"]
_COLUMN_WIDTHS = [170, 120, None, None, None, None, None, None, None, None]
# storage.load_station_trading_shortlist has no ORDER BY of its own - Profit/
# Day is this tool's own "theoretical ceiling" ranking (see CLAUDE.md), the
# same decision-relevance reasoning trading_shortlist.py's own Margin default
# uses for its unordered shortlist.
_DEFAULT_SORT = (8, Qt.SortOrder.DescendingOrder)


def _row_to_cells(row: dict) -> list:
    return [row["name"], row["category"], fmt_pct(row["spread_pct"]), row["avg_daily_volume"],
            fmt_isk(row["live_buy"]), fmt_isk(row["live_sell"]), fmt_isk(row["profit_per_unit"]),
            fmt_pct(row["margin"]), fmt_isk(row["profit_per_day"]), "yes" if row["active"] else "no"]


class StationShortlistView(TableView):
    title = "Station Trading - Shortlist"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_toolbar([
            ("Reload", self._show, "reload"),
        ])
        self._build_table(_COLUMNS, column_widths=_COLUMN_WIDTHS, default_sort=_DEFAULT_SORT)
        self._add_staleness_label("market_prices")
        self._finish_status_row()
        self._show()

    def _show(self) -> None:
        self.run_action(functools.partial(station_trading_actions.do_get_shortlist), self._on_shown,
                        busy_message="Loading the last cached shortlist...")

    def _on_shown(self, rows: list[dict]) -> None:
        self.populate_table([_row_to_cells(r) for r in rows])
        self._refresh_staleness_label()
        self.show_info(f"{len(rows)} item(s)." if rows else
                       "Shortlist is empty - use App > Update Data... to discover candidates.")
