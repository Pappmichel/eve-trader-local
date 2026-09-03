"""Ore & Minerals' Reprocessing Quote tab: `quote-reprocessing` on its own -
paste an EVE inventory "Copy As" list (tab-separated Name/Quantity/Group/
Category/Size/Slot/Volume/Meta Level/Tech Level, no header row - see
`refining/paste_parser.py`'s own module docstring for the exact column
shape) and get a per-line sell-as-is-vs-reprocess quote plus totals.

A single-purpose tab (unlike the Ore Shortlist's multi-command grouping)
since there's only one real command here and it doesn't share state with
anything else on screen - matching doctrine_shopping_list.py's own
"one real network call, nothing else fits on this tab" reasoning. Nothing
is loaded on tab-open: a quote only exists for a paste the user provides,
there's no "last quote" saved anywhere to show first.
"""
from __future__ import annotations

import functools

from PySide6.QtWidgets import QPlainTextEdit, QPushButton

from ...refining import actions as refining_actions
from .base import BaseView
from .production_common import build_table, fmt_isk, populate

_COLUMNS = ["Item", "Qty", "Category", "Sell As-Is", "Refined Value", "Mineral Value",
            "Refining Tax", "Decision"]


def _row_to_cells(row) -> list:
    if row.error:
        return [row.name, row.quantity, row.category, "-", "-", "-", "-", f"error: {row.error}"]
    return [row.name, row.quantity, row.category, fmt_isk(row.sell_as_is_value), fmt_isk(row.refined_value),
            fmt_isk(row.mineral_value), fmt_isk(row.refining_tax), row.decision]


class ReprocessingQuoteView(BaseView):
    title = "Ore & Minerals - Reprocessing Quote"

    def __init__(self, parent=None):
        super().__init__(parent)

        self.paste_input = QPlainTextEdit()
        self.paste_input.setPlaceholderText(
            "Paste an Inventory window's list view here (Ctrl+A, Ctrl+C in-game) - "
            "tab-separated Name/Quantity/Group/Category/... columns, no header row.")
        self.paste_input.setMaximumHeight(160)
        self.root_layout.addWidget(self.paste_input)

        quote_btn = QPushButton("Quote")
        quote_btn.clicked.connect(self._quote)
        self.root_layout.addWidget(quote_btn)

        self.table = build_table(_COLUMNS)
        self.root_layout.addWidget(self.table)

        self._finish_status_row()

    def _quote(self) -> None:
        paste_text = self.paste_input.toPlainText()
        if not paste_text.strip():
            self.show_error("Paste an inventory list first.")
            return
        self.run_action(functools.partial(refining_actions.do_quote_reprocessing, paste_text), self._on_quoted,
                        busy_message="Pricing every line (sell-as-is vs. reprocess)...")

    def _on_quoted(self, result: dict) -> None:
        rows = result["rows"]
        populate(self.table, [_row_to_cells(r) for r in rows])
        totals = result["totals"]
        message = (f"{totals['reprocess_count']:,} item(s) worth reprocessing - "
                   f"{totals['total_refined_value']:,.0f} ISK refined vs. "
                   f"{totals['total_sell_as_is_value']:,.0f} ISK sold as-is.")
        if result.get("priced_via_fallback"):
            message += " (prices came from the Goonmetrics fallback, not the real order book)"
        self.show_info(message)
