"""Station Trading's Shortlist tab: groups the two shortlist CLI commands
into one view, same "one table, buttons that (re)build it" shape as
trading_shortlist.py/refining_ore_shortlist.py -

- `refresh-station-shortlist` (`do_refresh_shortlist`) - re-runs the
  Goonmetrics-driven Jita-wide spread/volume candidate scan, persists the
  result (newly-discovered rows start active, a previously-deactivated
  type_id stays deactivated), and returns the same live-confirmed rows
  `do_get_shortlist` does.
- `list-station-shortlist` (`do_get_shortlist`) - the persisted shortlist,
  live-confirmed against the real ESI order book on every read.

Unlike trading_shortlist.py/refining_ore_shortlist.py, there is no
genuinely local (no-network) read to load on tab-open here:
`do_get_shortlist` always makes a live ESI order-book call to confirm
prices, even for an already-persisted row (see
`station_trading/actions.py`'s `_build_shortlist_rows` docstring) - so
unlike those two views, this one starts empty and waits for a button click,
same restraint `production_margins_market.py`'s `MarginsMarketView` uses
for its own network-only reports."""
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
            ("Show Shortlist", self._show),
            ("Discover && Refresh", self._refresh),
        ])
        self._build_table(_COLUMNS, column_widths=_COLUMN_WIDTHS, default_sort=_DEFAULT_SORT)
        self._finish_status_row()

    def _show(self) -> None:
        self.run_action(functools.partial(station_trading_actions.do_get_shortlist), self._on_shown,
                        busy_message="Live-confirming the persisted shortlist against the real order book...")

    def _on_shown(self, rows: list[dict]) -> None:
        self.populate_table([_row_to_cells(r) for r in rows])
        self.show_info(f"{len(rows)} item(s)." if rows else
                       "Shortlist is empty - click Discover && Refresh to scan Jita for candidates.")

    def _refresh(self) -> None:
        self.run_action(functools.partial(station_trading_actions.do_refresh_shortlist), self._on_refreshed,
                        busy_message="Scanning the whole Jita market for spread/volume candidates "
                                     "(this can take a while)...")

    def _on_refreshed(self, result: dict) -> None:
        rows = result["rows"]
        self.populate_table([_row_to_cells(r) for r in rows])
        self.show_info(f"Discovered {result['discovered']:,} candidate(s); {len(rows)} in the shortlist.")
