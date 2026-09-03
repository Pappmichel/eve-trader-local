"""Portfolio's Overview tab: `portfolio.portfolio_overview()` - the one
cross-cutting Trading + Production summary (see `portfolio.py`'s own module
docstring: combines Trading's realized P&L with Production's stock value
into one read-only dashboard, plus a daily profit volatility signal).

Not a `TableView` - `portfolio_overview()` returns a single summary dict,
not a list of rows, so this mirrors `cli.py`'s own `cmd_portfolio_overview`
formatting (one line per figure) using plain `QLabel`s instead, matching
the same "no rows to tabulate" reasoning `production_margins_market.py`'s
own `_on_stock_value`/`_on_market_status` handlers use for their own
single-figure results (shown via `show_info`, not a table) - just as a
dedicated view here instead of one line in another view's status bar,
since Portfolio has no other view to attach that line to.

Reached from a standalone "Portfolio" top-level menu (see
`main_window.py`'s own `_TOOL_MENUS`) rather than folded into the "App"
menu: unlike Settings/Characters (one-shot forms, opened as modal
`QDialog`s), this is a live-refreshable dashboard a user might want to
keep open in its own tab, same reasoning every other tool's own top-level
menu already follows."""
from __future__ import annotations

import functools

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QPushButton

from ... import portfolio
from .base import BaseView

_LABEL_FONT = "font-weight: bold;"


class PortfolioOverviewView(BaseView):
    title = "Portfolio - Overview"

    def __init__(self, parent=None):
        super().__init__(parent)

        toolbar = QHBoxLayout()
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._refresh)
        toolbar.addWidget(refresh_btn)
        toolbar.addStretch(1)
        self.root_layout.addLayout(toolbar)

        grid = QGridLayout()
        self._rows: dict[str, QLabel] = {}
        for row_index, key in enumerate(
                ["trading_realized_profit", "trading_average_margin", "trading_daily_profit_volatility",
                 "production_stock_value", "combined_value"]):
            caption = QLabel(_CAPTIONS[key])
            caption.setStyleSheet(_LABEL_FONT)
            value = QLabel("-")
            grid.addWidget(caption, row_index, 0)
            grid.addWidget(value, row_index, 1)
            self._rows[key] = value
        self.root_layout.addLayout(grid)
        self.root_layout.addStretch(1)

        self._finish_status_row()
        self.show_info("Click Refresh to load the current portfolio overview.")

    def _refresh(self) -> None:
        self.run_action(functools.partial(portfolio.portfolio_overview), self._on_result,
                        busy_message="Loading portfolio overview...")

    def _on_result(self, result: dict) -> None:
        self._rows["trading_realized_profit"].setText(
            f"{result['trading_realized_profit']:,.0f} ISK ({result['trading_trade_count']} matched trade(s))")
        self._rows["trading_average_margin"].setText(f"{result['trading_average_margin'] * 100:.1f}%")
        volatility = result["trading_daily_profit_volatility"]
        self._rows["trading_daily_profit_volatility"].setText(
            f"{volatility:,.0f} ISK" if volatility is not None else "- (fewer than 2 days of realized trades)")
        if result["production_stock_targets_configured"]:
            self._rows["production_stock_value"].setText(f"{result['production_stock_value']:,.0f} ISK")
        else:
            self._rows["production_stock_value"].setText("- (no stock targets configured)")
        self._rows["combined_value"].setText(f"{result['combined_value']:,.0f} ISK")
        self.show_info("Portfolio overview loaded.")


_CAPTIONS = {
    "trading_realized_profit": "Trading realized profit:",
    "trading_average_margin": "Trading average margin:",
    "trading_daily_profit_volatility": "Daily profit volatility:",
    "production_stock_value": "Production stock value:",
    "combined_value": "Combined value:",
}
