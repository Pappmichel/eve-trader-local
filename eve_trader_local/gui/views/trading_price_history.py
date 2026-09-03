"""Trading's Price History tab: `do_shortlist_trends`
(`history_backtest.compute_margin_trends`) - margin momentum per shortlist
item, computed from the price history candidate searches already cached
locally (`storage.read_goonmetrics_history_for_types`). A real, standalone
`do_*` action already existed for this in `actions.py`, but with no CLI
command or GUI view ever built for it - this is that first caller.

Compares the average landed-cost margin over the most recent
RECENT_WINDOW_DAYS against the last BASELINE_WINDOW_DAYS - see
`history_backtest.compute_margin_trends`'s own docstring for the exact
window/rolling-baseline shape. A brand-new shortlist item with too little
paired history, or one whose baseline margin sits too close to zero, simply
has no row here rather than a fabricated trend.

Pure local computation, no network and no login needed - loads on tab-open,
same convention `trading_shortlist.py`'s `_load_last_snapshot` uses."""
from __future__ import annotations

import functools

from PySide6.QtCore import Qt

from ... import actions, storage
from .base import TableView

_COLUMNS = ["Item", "Recent Avg Margin", "Baseline Avg Margin", "Trend"]
_COLUMN_WIDTHS = [220, 150, 150, None]
# No canonical order comes out of compute_margin_trends itself (it returns a
# plain {type_id: {...}} dict) - Trend desc is the most decision-relevant
# default (which items are improving fastest), same "no inherent order ->
# pick the most decision-relevant ranking" reasoning trading_shortlist.py's
# own Margin-desc default documents.
_DEFAULT_SORT = (3, Qt.SortOrder.DescendingOrder)


def _fmt_pct(value):
    return f"{value * 100:.1f}%" if value is not None else None


def _row_to_cells(item_id: int, trend: dict, item_names: dict[int, str]) -> list:
    return [item_names.get(item_id, str(item_id)), _fmt_pct(trend["recent_avg_margin"]),
            _fmt_pct(trend["baseline_avg_margin"]), _fmt_pct(trend["trend_pct"])]


class PriceHistoryView(TableView):
    title = "Trading - Price History"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_toolbar([("Refresh", self._refresh)])
        self._build_table(_COLUMNS, column_widths=_COLUMN_WIDTHS, default_sort=_DEFAULT_SORT)
        self._finish_status_row()
        self._load_trends()

    def _load_trends(self) -> None:
        try:
            trends = actions.do_shortlist_trends()
            item_names = {i.item_id: i.item for i in storage.load_shortlist()}
        except Exception as e:  # noqa: BLE001 - best-effort initial fill, Refresh recovers from any real problem
            self.show_error(f"Could not compute margin trends: {e!r}")
            return
        self.populate_table([_row_to_cells(item_id, trend, item_names) for item_id, trend in trends.items()])
        self.show_info(f"{len(trends):,} item(s) with enough paired history to show a trend."
                       if trends else "No shortlist item has enough paired price history yet.")

    def _refresh(self) -> None:
        self.run_action(functools.partial(actions.do_shortlist_trends), self._on_refreshed,
                        busy_message="Recomputing margin trends from cached price history...")

    def _on_refreshed(self, trends: dict) -> None:
        item_names = {i.item_id: i.item for i in storage.load_shortlist()}
        self.populate_table([_row_to_cells(item_id, trend, item_names) for item_id, trend in trends.items()])
        self.show_info(f"{len(trends):,} item(s) with enough paired history to show a trend."
                       if trends else "No shortlist item has enough paired price history yet.")
