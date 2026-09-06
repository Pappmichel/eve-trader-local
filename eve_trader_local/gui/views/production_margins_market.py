"""Production's Margins & Market Status tab: `discover-ship-margins`,
`market-status`, `stock-value`, and `system-cost-indices` - four
independent, cheap-ish read-only reports, small enough on their own to not
deserve separate tabs (unlike the planner-backed views above, none of
these share a data model with each other or need input widgets), so
they're grouped into one screen with one button each. System Cost Indices
is display-only ("what's a sane value here" for Settings' manual cost
override fields, see `do_get_system_cost_indices`'s own docstring) and has
no view of its own elsewhere in the app, so it lands here rather than as a
fourth standalone tab.

All four price/cost-index data from storage.order_book_cache/result_cache
now (see esi_update.py's own docstring) - the live ESI fetch only happens
inside App > Update Data...'s Production scope; each button here just
(re)computes its report against whatever that last run cached."""
from __future__ import annotations

import functools

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QTabWidget

from ...production import actions as production_actions
from .base import BaseView
from .production_common import build_table, fmt_isk, fmt_pct, populate

_MARGIN_COLUMNS = ["Item", "Activity", "Home Price", "Jita Price", "Build Cost",
                   "Margin (Home)", "Margin (Jita)", "Meta Level"]
_MARGIN_WIDTHS = [200, 120, None, None, None, None, None, None]
# discover_ship_margins's own docstring: sorted by margin_home desc.
_MARGIN_SORT = (5, Qt.SortOrder.DescendingOrder)

_MARKET_STATUS_COLUMNS = ["Item", "Target", "On Hand", "Missing", "Sells At"]
_MARKET_STATUS_WIDTHS = [200, None, None, None, 90]

_COST_INDEX_COLUMNS = ["Profile", "Activity", "Cost Index"]
_COST_INDEX_WIDTHS = [140, 160, None]


def _margin_row(row) -> list:
    return [row.type_name, row.activity, fmt_isk(row.home_price), fmt_isk(row.jita_price),
            fmt_isk(row.build_cost), fmt_pct(row.margin_home), fmt_pct(row.margin_jita), row.meta_level]


def _market_status_row(row) -> list:
    return [row.type_name, f"{row.target:,.0f}", f"{row.current_stock:,.0f}", f"{row.missing:,.0f}",
            "Jita" if row.jita_target else "home"]


def _cost_index_rows(indices: dict) -> list:
    rows = []
    for profile in ("manufacturing", "component"):
        values = indices.get(profile)
        if not values:
            continue
        for activity, rate in values.items():
            rows.append([profile, activity, fmt_pct(rate)])
    return rows


class MarginsMarketView(BaseView):
    title = "Production - Margins && Market Status"

    def __init__(self, parent=None):
        super().__init__(parent)

        toolbar = QHBoxLayout()
        margins_btn = QPushButton("Show Ship Margins")
        margins_btn.clicked.connect(self._refresh_margins)
        toolbar.addWidget(margins_btn)
        market_btn = QPushButton("Show Market Status")
        market_btn.clicked.connect(self._refresh_market_status)
        toolbar.addWidget(market_btn)
        value_btn = QPushButton("Show Stock Value")
        value_btn.clicked.connect(self._refresh_stock_value)
        toolbar.addWidget(value_btn)
        indices_btn = QPushButton("Show System Cost Indices")
        indices_btn.clicked.connect(self._refresh_cost_indices)
        toolbar.addWidget(indices_btn)
        toolbar.addStretch(1)
        self.root_layout.addLayout(toolbar)

        self.tabs = QTabWidget()
        self.margins_table = build_table(_MARGIN_COLUMNS, column_widths=_MARGIN_WIDTHS)
        self.market_status_table = build_table(_MARKET_STATUS_COLUMNS, column_widths=_MARKET_STATUS_WIDTHS)
        self.cost_index_table = build_table(_COST_INDEX_COLUMNS, column_widths=_COST_INDEX_WIDTHS)
        self.tabs.addTab(self.margins_table, "Ship Margins")
        self.tabs.addTab(self.market_status_table, "Market Status")
        self.tabs.addTab(self.cost_index_table, "System Cost Indices")
        self.root_layout.addWidget(self.tabs)

        self._add_staleness_label(["market_prices", "cost_indices"])
        self._finish_status_row()

    def _refresh_margins(self) -> None:
        self.run_action(functools.partial(production_actions.do_get_ship_margins), self._on_margins,
                        busy_message="Scanning ships for build cost/margin (this can take a while)...")

    def _on_margins(self, result: dict) -> None:
        rows = result["rows"]
        populate(self.margins_table, [_margin_row(r) for r in rows], default_sort=_MARGIN_SORT)
        self._refresh_staleness_label()
        self.show_info(f"{len(rows)} ship(s)." if rows else "No manufacturable ships found - refresh SDE first?")

    def _refresh_market_status(self) -> None:
        self.run_action(functools.partial(production_actions.do_market_status), self._on_market_status,
                        busy_message="Checking stock target readiness...")

    def _on_market_status(self, result: dict) -> None:
        rows = result["rows"]
        populate(self.market_status_table, [_market_status_row(r) for r in rows])
        self._refresh_staleness_label()
        self.show_info(f"{len(rows)} stock target(s)." if rows else "No stock targets configured.")

    def _refresh_stock_value(self) -> None:
        self.run_action(functools.partial(production_actions.do_stock_value), self._on_stock_value,
                        busy_message="Pricing current stock...")

    def _on_stock_value(self, result: dict) -> None:
        self._refresh_staleness_label()
        self.show_info(f"Total stock value: {result['total_value']:,.0f} ISK "
                       f"({result['priced_items']} priced, {result['unpriced_items']} unpriced)")

    def _refresh_cost_indices(self) -> None:
        self.run_action(functools.partial(production_actions.do_get_system_cost_indices), self._on_cost_indices,
                        busy_message="Loading cached system cost indices...")

    def _on_cost_indices(self, indices: dict) -> None:
        rows = _cost_index_rows(indices)
        populate(self.cost_index_table, rows)
        self._refresh_staleness_label()
        self.show_info(f"{len(rows)} cost index value(s)." if rows else
                       "No cost index data cached yet - run Update Data for Production first.")
