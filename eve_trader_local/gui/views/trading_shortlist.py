"""Trading's Shortlist tab: the live import/sell decision table plus the two
actions that (re)compute it. Groups three CLI commands into one view, since
they all operate on the same on-screen table:

- `refresh-shortlist` (`do_refresh_shortlist`) - re-fetches live prices and
  recomputes every row's decision, no candidate search.
- `refresh-and-prune` (`do_refresh_and_prune_candidates`) - the daily-driver
  one-button action: finds new candidates, adds recommended ones, refreshes,
  then deactivates/reactivates per the grace-period and cap rules.
- On open, loads the last saved snapshot from storage directly (no network
  call) via `storage.latest_shortlist_snapshot` - the CLI's own
  `list-shortlist` equivalent - so the view shows *something* immediately
  rather than an empty table until the user clicks Refresh.

Candidate-universe building (`build-universe`/`build-focused`, an occasional
setup step, not a daily one - see `actions.do_pipeline`'s own docstring) is
deliberately not on this tab; it belongs with Trading's Settings/setup view
once that exists, matching the CLI's own framing of it as setup rather than
routine shortlist work.
"""
from __future__ import annotations

import functools

from PySide6.QtCore import Qt

from ... import actions, storage
from .base import TableView

_COLUMNS = ["Item", "Category", "Decision", "Margin", "Profit/Unit",
            "Listed Qty", "Own Orders", "Net Sell", "Landed Cost", "Active"]
# Item/Category/Decision hold text, so they get more room than the numeric
# ISK/percentage/quantity columns; the rest are left at Qt's default.
_COLUMN_WIDTHS = [180, 120, 90, None, None, None, None, None, None, None]
# No single canonical order comes out of evaluate_shortlist/storage itself
# (see shortlist.py's own docstring) - Margin desc is the most decision-
# relevant ranking to default to, same reasoning shortlist.py's own
# top_imports_by_daily_profit uses for its own "what matters most" ordering.
_DEFAULT_SORT = (3, Qt.SortOrder.DescendingOrder)


def _fmt_pct(value):
    return f"{value * 100:.1f}%" if value is not None else None


def _fmt_isk(value):
    return f"{value:,.2f}" if value is not None else None


def _row_to_cells(row) -> list:
    return [row.item, row.category, row.decision, _fmt_pct(row.margin), _fmt_isk(row.profit_per_unit),
            row.sell_volume, row.own_orders_remaining, _fmt_isk(row.net_sell), _fmt_isk(row.landed_cost),
            "yes" if row.active else "no"]


class TradingShortlistView(TableView):
    title = "Trading - Shortlist"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_toolbar([
            ("Refresh", self._refresh),
            ("Refresh && Prune", self._refresh_and_prune),
        ])
        self._build_table(_COLUMNS, column_widths=_COLUMN_WIDTHS, default_sort=_DEFAULT_SORT)
        self._finish_status_row()
        self._load_last_snapshot()

    def _load_last_snapshot(self) -> None:
        try:
            rows = storage.latest_shortlist_snapshot()
        except Exception as e:  # noqa: BLE001 - best-effort initial fill, Refresh recovers from any real problem
            self.show_error(f"Could not load the last saved snapshot: {e!r}")
            return
        self.populate_table([_row_to_cells(r) for r in rows])
        if rows:
            self.show_info(f"Showing the last saved snapshot ({len(rows)} items). Click Refresh for live data.")

    def _refresh(self) -> None:
        self.run_action(functools.partial(actions.do_refresh_shortlist), self._on_refreshed,
                        busy_message="Fetching live prices and recomputing decisions...")

    def _refresh_and_prune(self) -> None:
        self.run_action(functools.partial(actions.do_refresh_and_prune_candidates), self._on_refreshed,
                        busy_message="Searching new candidates, refreshing, and pruning the shortlist...")

    def _on_refreshed(self, result: dict) -> None:
        rows = storage.latest_shortlist_snapshot()
        self.populate_table([_row_to_cells(r) for r in rows])
        summary = result.get("summary", {})
        parts = [f"{k}: {v}" for k, v in summary.items()]
        deactivated = result.get("deactivated_count")
        reactivated = result.get("reactivated_count")
        if deactivated is not None:
            parts.append(f"deactivated: {deactivated}")
        if reactivated is not None:
            parts.append(f"reactivated: {reactivated}")
        self.show_info("Refreshed - " + ", ".join(parts) if parts else "Refreshed.")
