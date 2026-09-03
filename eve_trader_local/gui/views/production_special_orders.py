"""Production's Special Orders tab: one-off build orders, tracked separately
from the permanent stock_targets list - `create/list/update/remove/compute-
special-order`, all grouped into one tab since they all operate on the same
underlying list (matching trading_shortlist.py's own "several CLI commands,
one screen" precedent).

The orders list itself is a cheap local read (`storage.list_special_orders`,
no network) so it's loaded on open; `compute-special-order` re-runs the full
priced planner (`engine.plan_special_order`) so that one stays a Refresh/
Compute-button action, same as every other planner-backed view here."""
from __future__ import annotations

import functools

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QTabWidget, QVBoxLayout)

from ...production import actions as production_actions
from .base import BaseView
from .production_common import build_table, fmt_isk, populate

_ORDER_COLUMNS = ["Order ID", "Status", "Items", "Net Against Stock", "Note", "Created At"]
_ORDER_WIDTHS = [220, 90, None, None, 200, 160]
# storage.list_special_orders: "ORDER BY created_at DESC" - newest first.
_ORDER_SORT = (5, Qt.SortOrder.DescendingOrder)

_LINE_ITEM_COLUMNS = ["Item", "Quantity"]
_LINE_ITEM_WIDTHS = [220, None]

_BUILD_COLUMNS = ["Item", "Activity", "Runs", "Cost/Unit", "Category"]
_BUILD_WIDTHS = [220, 120, None, None, 110]
# plan_special_order reuses engine._build_build_list, sorted by job runs desc.
_BUILD_SORT = (2, Qt.SortOrder.DescendingOrder)

_BUY_COLUMNS = ["Item", "Quantity", "Unit Price", "Total Price", "Buy From"]
_BUY_WIDTHS = [220, None, None, None, 110]
# plan_special_order reuses engine._build_buy_list, sorted by total price desc.
_BUY_SORT = (3, Qt.SortOrder.DescendingOrder)

_OVERLAP_COLUMNS = ["Item", "Current Stock"]
_OVERLAP_WIDTHS = [220, None]


def _order_row(order) -> list:
    return [order.order_id, order.status, order.item_count,
            "yes" if order.net_against_stock else "no", order.note, order.created_at]


def _line_item_row(row) -> list:
    return [row.type_name, f"{row.quantity:,.0f}"]


def _build_row(row) -> list:
    return [row.type_name, row.activity, row.job_runs, fmt_isk(row.unit_build_cost), row.job_category]


def _buy_row(row) -> list:
    return [row.type_name, f"{row.quantity:,.0f}", fmt_isk(row.unit_price), fmt_isk(row.total_price),
            row.buy_from]


def _overlap_row(row) -> list:
    return [row.type_name, f"{row.current_stock:,.0f}"]


class SpecialOrdersView(BaseView):
    title = "Production - Special Orders"

    def __init__(self, parent=None):
        super().__init__(parent)

        self.root_layout.addWidget(self._build_create_box())

        toolbar = QHBoxLayout()
        refresh_btn = QPushButton("Refresh List")
        refresh_btn.clicked.connect(self._load_orders)
        toolbar.addWidget(refresh_btn)
        toolbar.addStretch(1)
        self.root_layout.addLayout(toolbar)

        self.orders_table = build_table(_ORDER_COLUMNS, column_widths=_ORDER_WIDTHS)
        self.orders_table.setMaximumHeight(200)
        self.orders_table.itemSelectionChanged.connect(self._on_selection_changed)
        self.root_layout.addWidget(self.orders_table)

        self.root_layout.addWidget(self._build_action_box())

        self.tabs = QTabWidget()
        self.line_items_table = build_table(_LINE_ITEM_COLUMNS, column_widths=_LINE_ITEM_WIDTHS)
        self.build_table_widget = build_table(_BUILD_COLUMNS, column_widths=_BUILD_WIDTHS)
        self.buy_table = build_table(_BUY_COLUMNS, column_widths=_BUY_WIDTHS)
        self.overlap_table = build_table(_OVERLAP_COLUMNS, column_widths=_OVERLAP_WIDTHS)
        self.tabs.addTab(self.line_items_table, "Line Items")
        self.tabs.addTab(self.build_table_widget, "Build List")
        self.tabs.addTab(self.buy_table, "Buy List")
        self.tabs.addTab(self.overlap_table, "Stock Overlap Warning")
        self.root_layout.addWidget(self.tabs)

        self._finish_status_row()
        self._load_orders()

    def _build_create_box(self) -> QGroupBox:
        box = QGroupBox("Create Special Order")
        outer = QVBoxLayout(box)
        form = QHBoxLayout()
        form.addWidget(QLabel("Items:"))
        self.items_input = QLineEdit()
        self.items_input.setPlaceholderText("NAME_OR_TYPE_ID:QUANTITY, comma-separated, e.g. Tritanium:1000, 34:500")
        form.addWidget(self.items_input)
        form.addWidget(QLabel("Note:"))
        self.note_input = QLineEdit()
        self.note_input.setMaximumWidth(160)
        form.addWidget(self.note_input)
        self.net_against_stock_checkbox = QCheckBox("Net against stock")
        form.addWidget(self.net_against_stock_checkbox)
        create_btn = QPushButton("Create")
        create_btn.clicked.connect(self._create_order)
        form.addWidget(create_btn)
        outer.addLayout(form)
        return box

    def _build_action_box(self) -> QGroupBox:
        box = QGroupBox("Selected Order")
        outer = QHBoxLayout(box)
        outer.addWidget(QLabel("Order ID:"))
        self.order_id_input = QLineEdit()
        self.order_id_input.setPlaceholderText("select a row above, or paste an order id")
        outer.addWidget(self.order_id_input)
        done_btn = QPushButton("Mark Done")
        done_btn.clicked.connect(lambda: self._update_status("done"))
        outer.addWidget(done_btn)
        reopen_btn = QPushButton("Reopen")
        reopen_btn.clicked.connect(lambda: self._update_status("open"))
        outer.addWidget(reopen_btn)
        remove_btn = QPushButton("Remove")
        remove_btn.clicked.connect(self._remove_order)
        outer.addWidget(remove_btn)
        compute_btn = QPushButton("Compute")
        compute_btn.clicked.connect(self._compute_order)
        outer.addWidget(compute_btn)
        outer.addStretch(1)
        return box

    def _on_selection_changed(self) -> None:
        rows = self.orders_table.selectionModel().selectedRows()
        if not rows:
            return
        order_id_item = self.orders_table.item(rows[0].row(), 0)
        if order_id_item is not None:
            self.order_id_input.setText(order_id_item.text())

    def _load_orders(self) -> None:
        # storage.list_special_orders is a cheap local read (no network).
        orders = production_actions.do_list_special_orders()
        populate(self.orders_table, [_order_row(o) for o in orders], default_sort=_ORDER_SORT)

    def _parse_items(self) -> list[dict] | None:
        specs = [s.strip() for s in self.items_input.text().split(",") if s.strip()]
        if not specs:
            self.show_error("Enter at least one NAME_OR_TYPE_ID:QUANTITY item.")
            return None
        items = []
        for spec in specs:
            if ":" not in spec:
                self.show_error(f"Invalid item '{spec}' - expected NAME_OR_TYPE_ID:QUANTITY.")
                return None
            name_or_id, qty_str = spec.rsplit(":", 1)
            try:
                quantity = float(qty_str.strip())
            except ValueError:
                self.show_error(f"Invalid quantity in '{spec}'.")
                return None
            items.append({"name_or_id": name_or_id.strip(), "quantity": quantity})
        return items

    def _create_order(self) -> None:
        parsed = self._parse_items()
        if parsed is None:
            return
        # do_create_special_order needs resolved type_ids up front (unlike
        # do_add_stock_target, which resolves name-or-id itself via its
        # module-private _resolve_type) - resolve each item the same way
        # cli.py's own cmd_create_special_order does, via storage's public
        # SDE lookup.
        from ... import storage
        resolved = []
        for entry in parsed:
            name_or_id = entry["name_or_id"]
            if name_or_id.isdigit():
                type_id = int(name_or_id)
            else:
                matches = storage.search_sde_types(name_or_id, limit=2)
                exact = [m for m in matches if m[1].lower() == name_or_id.lower()]
                if not exact:
                    hint = f" Did you mean: {matches[0][1]}?" if matches else ""
                    self.show_error(f"No exact match for '{name_or_id}'.{hint}")
                    return
                type_id = exact[0][0]
            resolved.append({"type_id": type_id, "quantity": entry["quantity"]})
        note = self.note_input.text().strip() or None
        self.run_action(
            functools.partial(production_actions.do_create_special_order, resolved, note=note,
                              net_against_stock=self.net_against_stock_checkbox.isChecked()),
            self._on_order_created, busy_message="Creating special order...")

    def _on_order_created(self, result: dict) -> None:
        self._load_orders()
        self.items_input.clear()
        self.note_input.clear()
        self.show_info(f"Created special order {result['order_id']}.")

    def _update_status(self, status: str) -> None:
        order_id = self.order_id_input.text().strip()
        if not order_id:
            self.show_error("Select or enter an order id first.")
            return
        self.run_action(
            functools.partial(production_actions.do_update_special_order, order_id, status=status),
            self._on_order_updated, busy_message="Updating special order...")

    def _on_order_updated(self, result: dict) -> None:
        self._load_orders()
        self.show_info(f"Special order {result['order'].order_id} updated - status: {result['order'].status}.")

    def _remove_order(self) -> None:
        order_id = self.order_id_input.text().strip()
        if not order_id:
            self.show_error("Select or enter an order id first.")
            return
        self.run_action(functools.partial(production_actions.do_remove_special_order, order_id),
                        self._on_order_removed, busy_message="Removing special order...")

    def _on_order_removed(self, result: dict) -> None:
        self._load_orders()
        self.order_id_input.clear()
        self.show_info(f"Removed special order {result['removed']}.")

    def _compute_order(self) -> None:
        order_id = self.order_id_input.text().strip()
        if not order_id:
            self.show_error("Select or enter an order id first.")
            return
        self.run_action(functools.partial(production_actions.do_compute_special_order, order_id),
                        self._on_computed, busy_message="Computing buy/build plan for this order...")

    def _on_computed(self, plan: dict) -> None:
        populate(self.line_items_table, [_line_item_row(r) for r in plan["line_items"]])
        populate(self.build_table_widget, [_build_row(r) for r in plan["build_list"]], default_sort=_BUILD_SORT)
        populate(self.buy_table, [_buy_row(r) for r in plan["buy_list"]], default_sort=_BUY_SORT)
        populate(self.overlap_table, [_overlap_row(r) for r in plan["stock_overlap_warning"]])
        self.show_info(f"Computed - {len(plan['build_list'])} build job(s), {len(plan['buy_list'])} "
                       f"buy item(s), {len(plan['stock_overlap_warning'])} stock overlap warning(s).")
