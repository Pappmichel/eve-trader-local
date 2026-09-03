"""Production's Margins & Market Status tab: `discover-ship-margins`,
`market-status`, and `stock-value` - three independent, cheap-ish read-only
reports, small enough on their own to not deserve separate tabs (unlike the
planner-backed views above, none of these three share a data model with
each other or need input widgets), so they're grouped into one screen with
one button each."""
from __future__ import annotations

import functools

from PySide6.QtWidgets import QHBoxLayout, QPushButton, QTabWidget

from ...production import actions as production_actions
from .base import BaseView
from .production_common import build_table, fmt_isk, fmt_pct, populate

_MARGIN_COLUMNS = ["Item", "Activity", "Home Price", "Jita Price", "Build Cost",
                   "Margin (Home)", "Margin (Jita)", "Meta Level"]
_MARKET_STATUS_COLUMNS = ["Item", "Target", "On Hand", "Missing", "Sells At"]


def _margin_row(row) -> list:
    return [row.type_name, row.activity, fmt_isk(row.home_price), fmt_isk(row.jita_price),
            fmt_isk(row.build_cost), fmt_pct(row.margin_home), fmt_pct(row.margin_jita), row.meta_level]


def _market_status_row(row) -> list:
    return [row.type_name, f"{row.target:,.0f}", f"{row.current_stock:,.0f}", f"{row.missing:,.0f}",
            "Jita" if row.jita_target else "home"]


class MarginsMarketView(BaseView):
    title = "Production - Margins && Market Status"

    def __init__(self, parent=None):
        super().__init__(parent)

        toolbar = QHBoxLayout()
        margins_btn = QPushButton("Refresh Ship Margins")
        margins_btn.clicked.connect(self._refresh_margins)
        toolbar.addWidget(margins_btn)
        market_btn = QPushButton("Refresh Market Status")
        market_btn.clicked.connect(self._refresh_market_status)
        toolbar.addWidget(market_btn)
        value_btn = QPushButton("Refresh Stock Value")
        value_btn.clicked.connect(self._refresh_stock_value)
        toolbar.addWidget(value_btn)
        toolbar.addStretch(1)
        self.root_layout.addLayout(toolbar)

        self.tabs = QTabWidget()
        self.margins_table = build_table(_MARGIN_COLUMNS)
        self.market_status_table = build_table(_MARKET_STATUS_COLUMNS)
        self.tabs.addTab(self.margins_table, "Ship Margins")
        self.tabs.addTab(self.market_status_table, "Market Status")
        self.root_layout.addWidget(self.tabs)

        self._finish_status_row()

    def _refresh_margins(self) -> None:
        self.run_action(functools.partial(production_actions.do_get_ship_margins), self._on_margins,
                        busy_message="Scanning ships for build cost/margin (this can take a while)...")

    def _on_margins(self, result: dict) -> None:
        rows = result["rows"]
        populate(self.margins_table, [_margin_row(r) for r in rows])
        self.show_info(f"{len(rows)} ship(s)." if rows else "No manufacturable ships found - refresh SDE first?")

    def _refresh_market_status(self) -> None:
        self.run_action(functools.partial(production_actions.do_market_status), self._on_market_status,
                        busy_message="Checking stock target readiness...")

    def _on_market_status(self, result: dict) -> None:
        rows = result["rows"]
        populate(self.market_status_table, [_market_status_row(r) for r in rows])
        self.show_info(f"{len(rows)} stock target(s)." if rows else "No stock targets configured.")

    def _refresh_stock_value(self) -> None:
        self.run_action(functools.partial(production_actions.do_stock_value), self._on_stock_value,
                        busy_message="Pricing current stock...")

    def _on_stock_value(self, result: dict) -> None:
        self.show_info(f"Total stock value: {result['total_value']:,.0f} ISK "
                       f"({result['priced_items']} priced, {result['unpriced_items']} unpriced)")
