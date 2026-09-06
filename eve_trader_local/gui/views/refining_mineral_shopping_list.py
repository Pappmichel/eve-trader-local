"""Ore & Minerals' Mineral Shopping List tab: groups the saved-requirement
management commands (`set-mineral-requirement`/`remove-mineral-requirement`/
`list-mineral-requirements`) with the LP solve itself
(`solve-mineral-shopping-list`) - "the input list and the thing that
consumes it share a screen", same reasoning as doctrine_fittings.py's own
doctrine+fitting grouping.

`do_load_mineral_requirements` is a pure local read (SQLite only) so the
requirements table loads on tab-open, same convention as every other
view's cheap-local-read table. `do_set_mineral_requirement`/
`do_remove_mineral_requirement` are local writes too but still go through
`run_action`, matching doctrine_fittings.py's own `_create_doctrine`/
`_remove_fitting` (every mutation goes through the worker thread regardless
of whether it happens to touch the network - see refining_ore_shortlist.py's
own docstring for the same point). `do_optimize_mineral_shopping_list` reads
Jita/C-J prices from storage.order_book_cache now - the last App > Update
Data...'s Ore & Minerals run, never a live call of its own (see
esi_update.py's own docstring) - and always solves against the saved
requirement list here (there's no ad-hoc-list UI; the do_* function's own
`requirements=None` default already handles that), so Solve always re-reads
whatever's currently saved/shown in the table above it.
"""
from __future__ import annotations

import functools

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QTabWidget, QVBoxLayout)

from ...refining import actions as refining_actions
from .. import icons
from .base import BaseView
from .production_common import build_table, fmt_isk, populate

_REQUIREMENT_COLUMNS = ["Type ID", "Name", "Required Qty"]
_REQUIREMENT_WIDTHS = [90, 200, None]
# storage.load_mineral_requirements: "ORDER BY mineral_name".
_REQUIREMENT_SORT = (1, Qt.SortOrder.AscendingOrder)

_ORE_COLUMNS = ["Item", "Family", "Ice?", "Portions", "Units", "Volume/m3", "Landed Cost/Unit", "Total Cost"]
_ORE_WIDTHS = [180, 120, None, None, None, None, None, None]
# optimizer.py: "ore_purchases.sort(key=lambda p: -p.total_cost)".
_ORE_SORT = (7, Qt.SortOrder.DescendingOrder)

_DIRECT_COLUMNS = ["Name", "Quantity", "Landed Cost/Unit", "Source", "Total Cost"]
_DIRECT_WIDTHS = [200, None, None, 110, None]
# optimizer.py: "direct_purchases.sort(key=lambda p: -p.total_cost)".
_DIRECT_SORT = (4, Qt.SortOrder.DescendingOrder)

_COVERAGE_COLUMNS = ["Name", "Required", "From Ore", "From Direct", "Delivered", "Surplus"]
_COVERAGE_WIDTHS = [200, None, None, None, None, None]
# optimizer.py: "coverage.sort(key=lambda c: c.name)".
_COVERAGE_SORT = (0, Qt.SortOrder.AscendingOrder)


def _requirement_row(row: dict) -> list:
    return [row["type_id"], row["name"], f"{row['required_qty']:,.0f}"]


def _ore_purchase_row(p) -> list:
    return [p.item, p.family, "yes" if p.is_ice else "no", f"{p.portions:,}", f"{p.units:,.0f}",
            f"{p.volume_m3:,.1f}", fmt_isk(p.landed_cost_per_unit), fmt_isk(p.total_cost)]


def _direct_purchase_row(p) -> list:
    return [p.name, f"{p.quantity:,.0f}", fmt_isk(p.landed_cost_per_unit), p.source or "-", fmt_isk(p.total_cost)]


def _coverage_row(c) -> list:
    return [c.name, f"{c.required:,.0f}", f"{c.from_ore:,}", f"{c.from_direct:,}", f"{c.delivered:,}",
            f"{c.surplus:,.1f}"]


class MineralShoppingListView(BaseView):
    title = "Ore & Minerals - Mineral Shopping List"

    def __init__(self, parent=None):
        super().__init__(parent)

        self.root_layout.addWidget(self._build_requirement_box())

        solve_btn = QPushButton(icons.icon("calculate", color="#06222b"), "Solve Shopping List")
        solve_btn.setProperty("cssClass", "primary")
        solve_btn.clicked.connect(self._solve)
        self.root_layout.addWidget(solve_btn)

        self.result_tabs = QTabWidget()
        self.ore_table = build_table(_ORE_COLUMNS, column_widths=_ORE_WIDTHS)
        self.direct_table = build_table(_DIRECT_COLUMNS, column_widths=_DIRECT_WIDTHS)
        self.coverage_table = build_table(_COVERAGE_COLUMNS, column_widths=_COVERAGE_WIDTHS)
        self.result_tabs.addTab(self.ore_table, "Buy Ore && Refine")
        self.result_tabs.addTab(self.direct_table, "Buy Direct")
        self.result_tabs.addTab(self.coverage_table, "Coverage")
        self.root_layout.addWidget(self.result_tabs)

        self._add_staleness_label("market_prices")
        self._finish_status_row()
        self._load_requirements()

    def _build_requirement_box(self) -> QGroupBox:
        box = QGroupBox("Required Minerals")
        outer = QVBoxLayout(box)
        form = QHBoxLayout()
        form.addWidget(QLabel("Item (name or type ID):"))
        self.item_input = QLineEdit()
        form.addWidget(self.item_input)
        form.addWidget(QLabel("Required qty:"))
        self.qty_input = QLineEdit()
        self.qty_input.setMaximumWidth(120)
        form.addWidget(self.qty_input)
        add_btn = QPushButton(icons.icon("target"), "Set/Update")
        add_btn.clicked.connect(self._set_requirement)
        form.addWidget(add_btn)
        remove_btn = QPushButton(icons.icon("remove"), "Remove")
        remove_btn.clicked.connect(self._remove_requirement)
        form.addWidget(remove_btn)
        outer.addLayout(form)

        self.requirements_table = build_table(_REQUIREMENT_COLUMNS, column_widths=_REQUIREMENT_WIDTHS)
        self.requirements_table.setMaximumHeight(180)
        self.requirements_table.itemSelectionChanged.connect(self._on_requirement_selected)
        outer.addWidget(self.requirements_table)
        return box

    def _load_requirements(self) -> None:
        rows = refining_actions.do_load_mineral_requirements()
        populate(self.requirements_table, [_requirement_row(r) for r in rows], default_sort=_REQUIREMENT_SORT)

    def _set_requirement(self) -> None:
        item = self.item_input.text().strip()
        if not item:
            self.show_error("Enter an item name or type ID first.")
            return
        try:
            qty = float(self.qty_input.text().strip())
        except ValueError:
            self.show_error("Required qty must be a number.")
            return
        self.run_action(functools.partial(refining_actions.do_set_mineral_requirement, item, qty),
                        self._on_requirement_set, busy_message="Resolving item and saving requirement...")

    def _on_requirement_set(self, result: dict) -> None:
        self._load_requirements()
        self.item_input.clear()
        self.qty_input.clear()
        self.show_info(f"Requirement set: {result['type_name']} -> {result['required_qty']:,.0f} units.")

    def _on_requirement_selected(self) -> None:
        rows = self.requirements_table.selectionModel().selectedRows()
        if not rows:
            return
        name_item = self.requirements_table.item(rows[0].row(), 1)
        if name_item is not None:
            self.item_input.setText(name_item.text())

    def _remove_requirement(self) -> None:
        item = self.item_input.text().strip()
        if not item:
            self.show_error("Select a row, or enter an item name/type ID, first.")
            return
        self.run_action(functools.partial(refining_actions.do_remove_mineral_requirement, item),
                        self._on_requirement_removed, busy_message="Removing requirement...")

    def _on_requirement_removed(self, result: dict) -> None:
        self._load_requirements()
        self.item_input.clear()
        self.show_info(f"Removed requirement: {result['type_name']}")

    def _solve(self) -> None:
        self.run_action(functools.partial(refining_actions.do_optimize_mineral_shopping_list), self._on_solved,
                        busy_message="Solving the cheapest ore-buy-and-refine-vs-buy-direct mix "
                                     "(this can take a moment)...")

    def _on_solved(self, plan: dict) -> None:
        populate(self.ore_table, [_ore_purchase_row(p) for p in plan["ore_purchases"]], default_sort=_ORE_SORT)
        populate(self.direct_table, [_direct_purchase_row(p) for p in plan["direct_purchases"]],
                default_sort=_DIRECT_SORT)
        populate(self.coverage_table, [_coverage_row(c) for c in plan["coverage"]], default_sort=_COVERAGE_SORT)
        self._refresh_staleness_label()
        message = (f"Total cost: {plan['total_cost']:,.0f} ISK "
                   f"(ore {plan['ore_cost']:,.0f} + direct {plan['direct_cost']:,.0f})")
        if plan.get("savings_vs_all_direct") is not None:
            message += f". Savings vs. buying everything outright: {plan['savings_vs_all_direct']:,.0f} ISK"
        self.show_info(message)
