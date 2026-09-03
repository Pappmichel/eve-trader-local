"""Production's Planner tab: the stock-aware buy/build planner
(`plan-production` / `production_actions.do_plan_production`), plus stock
target management (`set/remove/list-stock-target`) - grouped together
because stock targets are this planner's own only input; there is nothing
to plan without at least one of them, and no other view needs them.

`do_plan_production` re-runs the full priced BOM traversal every time (no
"last plan" table to load cheaply on open, unlike Trading's Shortlist) -
so only the stock targets list itself is loaded on open; the planner output
tables start empty until "Run Planner" is clicked.
"""
from __future__ import annotations

import functools

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QTabWidget, QVBoxLayout)

from ...production import actions as production_actions
from .base import BaseView
from .production_common import build_table, fmt_isk, fmt_pct, populate

_STOCK_COLUMNS = ["Item", "Quantity", "Sells At"]
_STOCK_WIDTHS = [200, None, None]
# storage.load_stock_targets has no ORDER BY of its own - default to
# alphabetical by item name, the most usable order for a manually-maintained
# list with no other inherent ranking.
_STOCK_SORT = (0, Qt.SortOrder.AscendingOrder)

_INVENTORY_COLUMNS = ["Item", "Activity", "Target", "On Hand", "Missing"]
_INVENTORY_WIDTHS = [200, 120, None, None, None]

_BUILD_COLUMNS = ["Item", "Activity", "Runs", "Cost/Unit", "Margin", "Category", "Decryptor"]
_BUILD_WIDTHS = [200, 120, None, None, None, 110, 110]
# engine._build_build_list's own docstring: "sorted by job runs desc".
_BUILD_SORT = (2, Qt.SortOrder.DescendingOrder)

_BUY_COLUMNS = ["Item", "Quantity", "Unit Price", "Total Price", "On Hand %", "Buy From"]
_BUY_WIDTHS = [200, None, None, None, None, 110]
# engine._build_buy_list's own docstring: "sorted by total price desc".
_BUY_SORT = (3, Qt.SortOrder.DescendingOrder)

_INVENTION_COLUMNS = ["Item", "T1 Blueprint", "Decryptor", "Probability", "Attempts",
                      "BPCs Owned", "Stockpile %"]
_INVENTION_WIDTHS = [200, 200, 110, None, None, None, None]
# plan_production's own docstring: invention_list "sorted by recommended
# invention runs desc".
_INVENTION_SORT = (4, Qt.SortOrder.DescendingOrder)


def _stock_target_row(row) -> list:
    type_id, type_name, quantity, jita_target = row
    return [type_name, f"{quantity:,.0f}", "Jita" if jita_target else "home"]


def _inventory_row(row) -> list:
    return [row.type_name, row.activity, f"{row.target:,.0f}", f"{row.current_stock:,.0f}",
            f"{row.total_missing:,.0f}"]


def _build_row(row) -> list:
    return [row.type_name, row.activity, row.job_runs, fmt_isk(row.unit_build_cost),
            fmt_pct(row.margin), row.job_category, row.decryptor]


def _buy_row(row) -> list:
    return [row.type_name, f"{row.quantity:,.0f}", fmt_isk(row.unit_price), fmt_isk(row.total_price),
            f"{row.on_hand_pct:.1f}%", row.buy_from]


def _invention_row(row) -> list:
    return [row.type_name, row.t1_blueprint_name, row.decryptor, fmt_pct(row.probability),
            row.recommended_invention_runs, row.t2_bpc_owned, f"{row.stockpile_pct:.1f}%"]


class ProductionPlannerView(BaseView):
    title = "Production - Planner"

    def __init__(self, parent=None):
        super().__init__(parent)

        toolbar = QHBoxLayout()
        run_btn = QPushButton("Run Planner")
        run_btn.clicked.connect(self._run_planner)
        toolbar.addWidget(run_btn)
        toolbar.addStretch(1)
        self.root_layout.addLayout(toolbar)

        self.root_layout.addWidget(self._build_stock_target_box())

        self.tabs = QTabWidget()
        self.inventory_table = build_table(_INVENTORY_COLUMNS, column_widths=_INVENTORY_WIDTHS)
        self.build_table_widget = build_table(_BUILD_COLUMNS, column_widths=_BUILD_WIDTHS)
        self.buy_table = build_table(_BUY_COLUMNS, column_widths=_BUY_WIDTHS)
        self.invention_table = build_table(_INVENTION_COLUMNS, column_widths=_INVENTION_WIDTHS)
        self.tabs.addTab(self.inventory_table, "Inventory")
        self.tabs.addTab(self.build_table_widget, "Build List")
        self.tabs.addTab(self.buy_table, "Buy List")
        self.tabs.addTab(self.invention_table, "Invention Needs")
        self.root_layout.addWidget(self.tabs)

        self._finish_status_row()
        self._load_stock_targets()

    def _build_stock_target_box(self) -> QGroupBox:
        box = QGroupBox("Stock Targets")
        outer = QVBoxLayout(box)

        form = QHBoxLayout()
        form.addWidget(QLabel("Item:"))
        self.item_input = QLineEdit()
        self.item_input.setPlaceholderText("type_id or exact item name")
        form.addWidget(self.item_input)
        form.addWidget(QLabel("Quantity:"))
        self.quantity_input = QLineEdit()
        self.quantity_input.setPlaceholderText("e.g. 100")
        self.quantity_input.setMaximumWidth(100)
        form.addWidget(self.quantity_input)
        self.jita_checkbox = QCheckBox("Sells at Jita")
        form.addWidget(self.jita_checkbox)
        set_btn = QPushButton("Set Target")
        set_btn.clicked.connect(self._set_stock_target)
        form.addWidget(set_btn)
        remove_btn = QPushButton("Remove Target")
        remove_btn.clicked.connect(self._remove_stock_target)
        form.addWidget(remove_btn)
        refresh_btn = QPushButton("Refresh List")
        refresh_btn.clicked.connect(self._load_stock_targets)
        form.addWidget(refresh_btn)
        form.addStretch(1)
        outer.addLayout(form)

        self.stock_target_table = build_table(_STOCK_COLUMNS, column_widths=_STOCK_WIDTHS)
        self.stock_target_table.setMaximumHeight(160)
        outer.addWidget(self.stock_target_table)
        return box

    def _load_stock_targets(self) -> None:
        # storage.load_stock_targets is a cheap local read (no network) -
        # do_list_stock_targets wraps it 1:1, so it's fine to call inline the
        # way trading_shortlist.py's own _load_last_snapshot does.
        result = production_actions.do_list_stock_targets()
        populate(self.stock_target_table, [_stock_target_row(r) for r in result["rows"]], default_sort=_STOCK_SORT)

    def _set_stock_target(self) -> None:
        item = self.item_input.text().strip()
        if not item:
            self.show_error("Enter an item (type_id or name) first.")
            return
        try:
            quantity = float(self.quantity_input.text().strip())
        except ValueError:
            self.show_error("Quantity must be a number.")
            return
        self.run_action(
            functools.partial(production_actions.do_add_stock_target, item, quantity,
                              jita_target=self.jita_checkbox.isChecked()),
            self._on_stock_target_changed, busy_message="Setting stock target...")

    def _remove_stock_target(self) -> None:
        item = self.item_input.text().strip()
        if not item:
            self.show_error("Enter an item (type_id or name) first.")
            return
        self.run_action(functools.partial(production_actions.do_remove_stock_target, item),
                        self._on_stock_target_changed, busy_message="Removing stock target...")

    def _on_stock_target_changed(self, result: dict) -> None:
        self._load_stock_targets()
        if "quantity" in result:
            self.show_info(f"Stock target set: {result['type_name']} -> {result['quantity']:,.0f} units.")
        else:
            self.show_info(f"Removed stock target: {result['type_name']}")

    def _run_planner(self) -> None:
        self.run_action(functools.partial(production_actions.do_plan_production), self._on_plan,
                        busy_message="Planning production against configured stock targets "
                                     "(this can take a while)...")

    def _on_plan(self, plan: dict) -> None:
        populate(self.inventory_table, [_inventory_row(r) for r in plan["inventory"]])
        populate(self.build_table_widget, [_build_row(r) for r in plan["build_list"]], default_sort=_BUILD_SORT)
        populate(self.buy_table, [_buy_row(r) for r in plan["buy_list"]], default_sort=_BUY_SORT)
        populate(self.invention_table, [_invention_row(r) for r in plan["invention_list"]],
                default_sort=_INVENTION_SORT)
        self.show_info(f"Planned - {len(plan['inventory'])} target(s), {len(plan['build_list'])} build "
                       f"job(s), {len(plan['buy_list'])} buy item(s), {len(plan['invention_list'])} "
                       "invention need(s).")
