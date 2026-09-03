"""Trading's Unlisted Stock && Undercut Check tab: `do_check_seller_unlisted_stock`
and `do_check_undercut`, grouped into one screen - both are, per `actions.py`'s
own section comment, "live one-shot checks" with no persisted state and no
shared data model, same "small independent read-only reports" grouping
`station_trading_undercut_skills.py`'s `UndercutSkillsView` already uses for
its own pair.

- Unlisted Stock: shortlist stock physically sitting at the structure with no
  sell order on it. Default sort matches `do_check_seller_unlisted_stock`'s
  own documented order ("rows.sort(key=lambda r: r.unlisted_quantity,
  reverse=True)") - Unlisted Qty descending.
- Undercut Check: the seller's own sell orders currently beaten by a cheaper
  competing order. Default sort matches `own_orders.check_undercut_pooled`'s
  own documented order ("results.sort(key=lambda r: r['difference'],
  reverse=True)") - Difference descending.

Neither has a genuinely local read to load on tab-open (both always make a
live ESI call) - same restraint `trading_shortlist.py`/
`station_trading_undercut_skills.py` already document, so this starts empty
until a button is clicked."""
from __future__ import annotations

import functools

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QTabWidget

from ... import actions
from .base import BaseView
from .production_common import build_table, fmt_isk, populate

_UNLISTED_COLUMNS = ["Item", "Asset Qty", "Sell Order Remaining", "Unlisted Qty", "Listed Qty", "Margin"]
_UNLISTED_WIDTHS = [200, None, 150, None, None, None]
# actions.do_check_seller_unlisted_stock: "rows.sort(key=lambda r:
# r.unlisted_quantity, reverse=True)".
_UNLISTED_SORT = (3, Qt.SortOrder.DescendingOrder)

_UNDERCUT_COLUMNS = ["Item", "My Price", "Competitor Price", "Difference"]
_UNDERCUT_WIDTHS = [200, None, None, None]
# own_orders.check_undercut_pooled: "results.sort(key=lambda r:
# r['difference'], reverse=True)".
_UNDERCUT_SORT = (3, Qt.SortOrder.DescendingOrder)


def _fmt_pct(value):
    return f"{value * 100:.1f}%" if value is not None else None


def _unlisted_row(r) -> list:
    return [r.item, f"{r.asset_quantity:,.0f}", f"{r.sell_order_remaining:,.0f}",
            f"{r.unlisted_quantity:,.0f}", r.sell_volume, _fmt_pct(r.margin)]


def _undercut_row(r) -> list:
    return [r.item, fmt_isk(r.my_price), fmt_isk(r.competitor_price), fmt_isk(r.difference)]


class UnlistedUndercutView(BaseView):
    title = "Trading - Unlisted Stock && Undercut Check"

    def __init__(self, parent=None):
        super().__init__(parent)

        toolbar = QHBoxLayout()
        unlisted_btn = QPushButton("Check Unlisted Stock")
        unlisted_btn.clicked.connect(self._check_unlisted)
        toolbar.addWidget(unlisted_btn)
        undercut_btn = QPushButton("Check Undercut")
        undercut_btn.clicked.connect(self._check_undercut)
        toolbar.addWidget(undercut_btn)
        toolbar.addStretch(1)
        self.root_layout.addLayout(toolbar)

        self.tabs = QTabWidget()
        self.unlisted_table = build_table(_UNLISTED_COLUMNS, column_widths=_UNLISTED_WIDTHS)
        self.undercut_table = build_table(_UNDERCUT_COLUMNS, column_widths=_UNDERCUT_WIDTHS)
        self.tabs.addTab(self.unlisted_table, "Unlisted Stock")
        self.tabs.addTab(self.undercut_table, "Undercut Check")
        self.root_layout.addWidget(self.tabs)

        self._finish_status_row()

    def _check_unlisted(self) -> None:
        self.run_action(functools.partial(actions.do_check_seller_unlisted_stock), self._on_unlisted,
                        busy_message="Checking structure stock against open sell orders...")

    def _on_unlisted(self, result: dict) -> None:
        rows = result["rows"]
        populate(self.unlisted_table, [_unlisted_row(r) for r in rows], default_sort=_UNLISTED_SORT)
        self.show_info(f"{len(rows):,} item(s) with unlisted stock." if rows
                       else "No unlisted stock found - everything at the structure is listed.")

    def _check_undercut(self) -> None:
        self.run_action(functools.partial(actions.do_check_undercut), self._on_undercut,
                        busy_message="Checking your own sell orders against competing orders...")

    def _on_undercut(self, result: dict) -> None:
        rows = result["rows"]
        populate(self.undercut_table, [_undercut_row(r) for r in rows], default_sort=_UNDERCUT_SORT)
        self.show_info(f"{len(rows):,} order(s) currently undercut." if rows
                       else "None of your sell orders are currently undercut.")
