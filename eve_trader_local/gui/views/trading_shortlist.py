"""Trading's Shortlist tab: the import/sell decision table. `do_refresh_
shortlist` is a pure local recompute now (no network - see esi_update.py's
own docstring): it reads whatever the Market Orders/Assets/Market Prices
scopes last cached and re-evaluates every row, so "Reload" here is cheap and
always safe to click, unlike the live ESI/Goonmetrics fetch that only ever
happens via App > Update Data... On open, the last saved snapshot loads
directly from storage first (`storage.latest_shortlist_snapshot`, the CLI's
own `list-shortlist` equivalent) so the tab shows something immediately,
then a Reload recomputes it.
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
            ("Reload", self._reload, "reload"),
        ])
        self._build_table(_COLUMNS, column_widths=_COLUMN_WIDTHS, default_sort=_DEFAULT_SORT)
        self._add_staleness_label(["market_orders", "assets", "market_prices"])
        self._finish_status_row()
        self._load_last_snapshot()

    def _load_last_snapshot(self) -> None:
        try:
            rows = storage.latest_shortlist_snapshot()
        except Exception as e:  # noqa: BLE001 - best-effort initial fill, Reload recovers from any real problem
            self.show_error(f"Could not load the last saved snapshot: {e!r}")
            return
        self.populate_table([_row_to_cells(r) for r in rows])
        self._refresh_staleness_label()
        if rows:
            self.show_info(f"Showing the last saved snapshot ({len(rows)} items). "
                           "Click Reload to recompute it, or App > Update Data... to refresh the prices first.")

    def _reload(self) -> None:
        self.run_action(functools.partial(actions.do_refresh_shortlist), self._on_reloaded,
                        busy_message="Recomputing the shortlist from cached prices/orders...")

    def _on_reloaded(self, result: dict) -> None:
        rows = storage.latest_shortlist_snapshot()
        self.populate_table([_row_to_cells(r) for r in rows])
        self._refresh_staleness_label()
        summary = result.get("summary", {})
        parts = [f"{k}: {v}" for k, v in summary.items()]
        self.show_info("Recomputed - " + ", ".join(parts) if parts else "Recomputed.")
