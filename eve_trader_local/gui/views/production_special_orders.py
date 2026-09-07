"""Production's Special Orders tab: one-off build orders, tracked separately
from the permanent stock_targets list - `create/list/update/remove/set-item/
remove-item/compute/compute-combined-special-order`, all grouped into one tab since they
all operate on the same underlying list (matching trading_shortlist.py's own
"several CLI commands, one screen" precedent).

The orders list itself is a cheap local read (`storage.list_special_orders`,
no network) so it's loaded on open; compute / compute-combined re-run the
full priced planner via production_actions (engine.plan_special_order /
_invention_need_row) so those stay button actions, same as every other
planner-backed view here. Combine is preview-only: selected orders are
pooled (shared type_ids summed) and never modified.
"""
from __future__ import annotations

import functools

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QTableWidget, QTabWidget, QVBoxLayout)

from ...errors import ActionError
from ...production import actions as production_actions
from ...production import preview_refresh
from .. import icons
from .base import BaseView
from .production_common import build_table, fmt_isk, fmt_pct, populate

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

# Same columns as the Planner Invention Needs tab - plan_special_order
# returns the same InventionNeedRow shape, sorted by recommended runs desc.
_INVENTION_COLUMNS = ["Item", "T1 Blueprint", "Decryptor", "Probability", "Attempts",
                      "BPCs Owned", "Stockpile %"]
_INVENTION_WIDTHS = [200, 200, 110, None, None, None, None]
_INVENTION_SORT = (4, Qt.SortOrder.DescendingOrder)


def _order_row(order) -> list:
    return [order.order_id, order.status, order.item_count,
            "yes" if order.net_against_stock else "no", order.note, order.created_at]


def _line_item_row(row) -> list:
    return [row.type_name, f"{row.quantity:,.0f}"]


def _line_item_row_from_dict(row: dict) -> list:
    return [row["type_name"], f"{row['quantity']:,.0f}"]


def _build_row(row) -> list:
    return [row.type_name, row.activity, row.job_runs, fmt_isk(row.unit_build_cost), row.job_category]


def _buy_row(row) -> list:
    return [row.type_name, f"{row.quantity:,.0f}", fmt_isk(row.unit_price), fmt_isk(row.total_price),
            row.buy_from]


def _overlap_row(row) -> list:
    return [row.type_name, f"{row.current_stock:,.0f}"]


def _invention_row(row) -> list:
    return [row.type_name, row.t1_blueprint_name, row.decryptor, fmt_pct(row.probability),
            row.recommended_invention_runs, row.t2_bpc_owned, f"{row.stockpile_pct:.1f}%"]


class SpecialOrdersView(BaseView):
    title = "Production - Special Orders"

    def __init__(self, parent=None):
        super().__init__(parent)

        self.root_layout.addWidget(self._build_create_box())

        toolbar = QHBoxLayout()
        refresh_btn = QPushButton(icons.icon("refresh"), "Refresh List")
        refresh_btn.clicked.connect(self._load_orders)
        toolbar.addWidget(refresh_btn)
        toolbar.addWidget(QLabel("Status:"))
        self.status_filter = QComboBox()
        self.status_filter.addItem("All", None)
        self.status_filter.addItem("Open", "open")
        self.status_filter.addItem("Done", "done")
        self.status_filter.currentIndexChanged.connect(self._load_orders)
        toolbar.addWidget(self.status_filter)
        toolbar.addStretch(1)
        self.root_layout.addLayout(toolbar)

        self.orders_table = build_table(_ORDER_COLUMNS, column_widths=_ORDER_WIDTHS)
        self.orders_table.setMaximumHeight(200)
        self.orders_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.orders_table.itemSelectionChanged.connect(self._on_selection_changed)
        self.root_layout.addWidget(self.orders_table)

        self.root_layout.addWidget(self._build_action_box())
        self.root_layout.addWidget(self._build_combine_box())

        self.tabs = QTabWidget()
        self.line_items_table = build_table(_LINE_ITEM_COLUMNS, column_widths=_LINE_ITEM_WIDTHS)
        self.build_table_widget = build_table(_BUILD_COLUMNS, column_widths=_BUILD_WIDTHS)
        self.buy_table = build_table(_BUY_COLUMNS, column_widths=_BUY_WIDTHS)
        self.invention_table = build_table(_INVENTION_COLUMNS, column_widths=_INVENTION_WIDTHS)
        self.overlap_table = build_table(_OVERLAP_COLUMNS, column_widths=_OVERLAP_WIDTHS)
        self.tabs.addTab(self.line_items_table, "Line Items")
        self.tabs.addTab(self.build_table_widget, "Build List")
        self.tabs.addTab(self.buy_table, "Buy List")
        self.tabs.addTab(self.invention_table, "Invention Needs")
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
        create_btn = QPushButton(icons.icon("add"), "Create")
        create_btn.clicked.connect(self._create_order)
        form.addWidget(create_btn)
        outer.addLayout(form)
        return box

    def _build_action_box(self) -> QGroupBox:
        box = QGroupBox("Selected Order")
        outer = QVBoxLayout(box)

        row = QHBoxLayout()
        row.addWidget(QLabel("Order ID:"))
        self.order_id_input = QLineEdit()
        self.order_id_input.setPlaceholderText("select a row above, or paste an order id")
        row.addWidget(self.order_id_input)
        done_btn = QPushButton(icons.icon("check"), "Mark Done")
        done_btn.clicked.connect(lambda: self._update_status("done"))
        row.addWidget(done_btn)
        reopen_btn = QPushButton(icons.icon("undo"), "Reopen")
        reopen_btn.clicked.connect(lambda: self._update_status("open"))
        row.addWidget(reopen_btn)
        remove_btn = QPushButton(icons.icon("remove"), "Remove")
        remove_btn.clicked.connect(self._remove_order)
        row.addWidget(remove_btn)
        compute_btn = QPushButton(icons.icon("calculate"), "Compute")
        compute_btn.clicked.connect(self._compute_order)
        row.addWidget(compute_btn)
        row.addStretch(1)
        outer.addLayout(row)

        meta = QHBoxLayout()
        meta.addWidget(QLabel("Note:"))
        self.selected_note_input = QLineEdit()
        self.selected_note_input.setPlaceholderText("optional note")
        meta.addWidget(self.selected_note_input)
        save_note_btn = QPushButton(icons.icon("save"), "Save Note")
        save_note_btn.clicked.connect(self._save_note)
        meta.addWidget(save_note_btn)
        self.selected_net_checkbox = QCheckBox("Net against stock")
        self.selected_net_checkbox.clicked.connect(self._save_net_against_stock)
        meta.addWidget(self.selected_net_checkbox)
        self.auto_recompute_checkbox = QCheckBox("Auto-recompute after edit")
        meta.addWidget(self.auto_recompute_checkbox)
        meta.addStretch(1)
        outer.addLayout(meta)

        edit = QHBoxLayout()
        edit.addWidget(QLabel("Item:"))
        self.edit_item_input = QLineEdit()
        self.edit_item_input.setPlaceholderText("type_id or exact item name")
        edit.addWidget(self.edit_item_input)
        edit.addWidget(QLabel("Quantity:"))
        self.edit_qty_input = QLineEdit()
        self.edit_qty_input.setPlaceholderText("e.g. 10")
        self.edit_qty_input.setMaximumWidth(100)
        edit.addWidget(self.edit_qty_input)
        set_item_btn = QPushButton(icons.icon("save"), "Set Item")
        set_item_btn.clicked.connect(self._set_item)
        edit.addWidget(set_item_btn)
        remove_item_btn = QPushButton(icons.icon("remove"), "Remove Item")
        remove_item_btn.clicked.connect(self._remove_item)
        edit.addWidget(remove_item_btn)
        edit.addStretch(1)
        outer.addLayout(edit)
        return box

    def _build_combine_box(self) -> QGroupBox:
        box = QGroupBox("Combine Orders (preview only)")
        outer = QHBoxLayout(box)
        outer.addWidget(QLabel("Order IDs:"))
        self.combine_ids_input = QLineEdit()
        self.combine_ids_input.setPlaceholderText("comma-separated, or select several rows above")
        outer.addWidget(self.combine_ids_input)
        self.combine_net_checkbox = QCheckBox("Net against stock")
        outer.addWidget(self.combine_net_checkbox)
        combine_btn = QPushButton(icons.icon("calculate"), "Compute Combined")
        combine_btn.clicked.connect(self._compute_combined)
        outer.addWidget(combine_btn)
        outer.addStretch(1)
        return box

    def _selected_order_ids(self) -> list[str]:
        ids = []
        for index in self.orders_table.selectionModel().selectedRows():
            item = self.orders_table.item(index.row(), 0)
            if item is not None:
                ids.append(item.text())
        return ids

    def _on_selection_changed(self) -> None:
        ids = self._selected_order_ids()
        if not ids:
            return
        self.order_id_input.setText(ids[0])
        self.combine_ids_input.setText(", ".join(ids))
        self._load_line_items(ids[0])
        try:
            detail = production_actions.do_get_special_order(ids[0])
        except ActionError:
            return
        order = detail["order"]
        self.selected_note_input.setText(order.note or "")
        self.selected_net_checkbox.blockSignals(True)
        self.selected_net_checkbox.setChecked(order.net_against_stock)
        self.selected_net_checkbox.blockSignals(False)

    def _load_line_items(self, order_id: str) -> None:
        try:
            detail = production_actions.do_get_special_order(order_id)
        except ActionError:
            return
        populate(self.line_items_table, [_line_item_row_from_dict(r) for r in detail["items"]])

    def _load_orders(self) -> None:
        # storage.list_special_orders is a cheap local read (no network).
        status = self.status_filter.currentData()
        orders = production_actions.do_list_special_orders(status=status)
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
        resolved = [{"type_id_or_name": entry["name_or_id"], "quantity": entry["quantity"]}
                    for entry in parsed]
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

    def _save_note(self) -> None:
        order_id = self.order_id_input.text().strip()
        if not order_id:
            self.show_error("Select or enter an order id first.")
            return
        note = self.selected_note_input.text().strip() or None
        self.run_action(
            functools.partial(production_actions.do_update_special_order, order_id, note=note),
            self._on_order_updated, busy_message="Saving note...")

    def _save_net_against_stock(self) -> None:
        order_id = self.order_id_input.text().strip()
        if not order_id:
            self.show_error("Select or enter an order id first.")
            return
        self.run_action(
            functools.partial(production_actions.do_update_special_order, order_id,
                              net_against_stock=self.selected_net_checkbox.isChecked()),
            self._on_order_updated, busy_message="Updating stock mode...")

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

    def _set_item(self) -> None:
        order_id = self.order_id_input.text().strip()
        if not order_id:
            self.show_error("Select or enter an order id first.")
            return
        item = self.edit_item_input.text().strip()
        if not item:
            self.show_error("Enter an item (type_id or name) first.")
            return
        try:
            quantity = float(self.edit_qty_input.text().strip())
        except ValueError:
            self.show_error("Quantity must be a number.")
            return
        if self.auto_recompute_checkbox.isChecked():
            self.run_action(
                functools.partial(preview_refresh.set_item_and_preview, order_id, item, quantity),
                self._on_item_mutated_with_plan, busy_message="Updating item and recomputing...")
        else:
            self.run_action(
                functools.partial(production_actions.do_set_special_order_item, order_id, item, quantity),
                self._on_item_set, busy_message="Updating special-order item...")

    def _on_item_set(self, result: dict) -> None:
        self._load_orders()
        populate(self.line_items_table, [_line_item_row_from_dict(r) for r in result["items"]])
        self.tabs.setCurrentWidget(self.line_items_table)
        names = ", ".join(f"{r['type_name']} x{r['quantity']:,.0f}" for r in result["items"])
        self.show_info(f"Special order {result['order'].order_id} items: {names}.")

    def _remove_item(self) -> None:
        order_id = self.order_id_input.text().strip()
        if not order_id:
            self.show_error("Select or enter an order id first.")
            return
        item = self.edit_item_input.text().strip()
        if not item:
            self.show_error("Enter an item (type_id or name) first.")
            return
        if self.auto_recompute_checkbox.isChecked():
            self.run_action(
                functools.partial(preview_refresh.remove_item_and_preview, order_id, item),
                self._on_item_mutated_with_plan, busy_message="Removing item and recomputing...")
        else:
            self.run_action(
                functools.partial(production_actions.do_remove_special_order_item, order_id, item),
                self._on_item_removed, busy_message="Removing special-order item...")

    def _on_item_removed(self, result: dict) -> None:
        self._load_orders()
        populate(self.line_items_table, [_line_item_row_from_dict(r) for r in result["items"]])
        self.tabs.setCurrentWidget(self.line_items_table)
        names = ", ".join(f"{r['type_name']} x{r['quantity']:,.0f}" for r in result["items"])
        self.show_info(f"Special order {result['order'].order_id} items: {names}.")

    def _on_item_mutated_with_plan(self, result: dict) -> None:
        self._load_orders()
        self._populate_plan(result["plan"])
        names = ", ".join(f"{r['type_name']} x{r['quantity']:,.0f}" for r in result["items"])
        self.show_info(self._plan_summary(
            result["plan"], f"Special order {result['order'].order_id} items: {names}. "))

    def _compute_order(self) -> None:
        order_id = self.order_id_input.text().strip()
        if not order_id:
            self.show_error("Select or enter an order id first.")
            return
        self.run_action(functools.partial(production_actions.do_compute_special_order, order_id),
                        self._on_computed, busy_message="Computing buy/build plan for this order...")

    def _compute_combined(self) -> None:
        ids = [part.strip() for part in self.combine_ids_input.text().split(",") if part.strip()]
        if not ids:
            self.show_error("Enter or select at least one order id to combine.")
            return
        self.run_action(
            functools.partial(production_actions.do_compute_combined_special_orders, ids,
                              self.combine_net_checkbox.isChecked()),
            self._on_combined, busy_message="Computing combined buy/build plan...")

    def _populate_plan(self, plan: dict) -> None:
        populate(self.line_items_table, [_line_item_row(r) for r in plan["line_items"]])
        populate(self.build_table_widget, [_build_row(r) for r in plan["build_list"]], default_sort=_BUILD_SORT)
        populate(self.buy_table, [_buy_row(r) for r in plan["buy_list"]], default_sort=_BUY_SORT)
        populate(self.invention_table, [_invention_row(r) for r in plan["invention_list"]],
                 default_sort=_INVENTION_SORT)
        populate(self.overlap_table, [_overlap_row(r) for r in plan["stock_overlap_warning"]])

    def _buy_total_isk(self, plan: dict) -> float | None:
        prices = [row.total_price for row in plan["buy_list"] if row.total_price is not None]
        if not prices:
            return None
        return sum(prices)

    def _plan_summary(self, plan: dict, prefix: str) -> str:
        total = self._buy_total_isk(plan)
        cost = f", buy total {fmt_isk(total)} ISK" if total is not None else ""
        return (
            f"{prefix}{len(plan['line_items'])} line item(s), "
            f"{len(plan['build_list'])} build job(s), {len(plan['buy_list'])} buy item(s), "
            f"{len(plan['invention_list'])} invention need(s){cost}."
        )

    def _on_combined(self, plan: dict) -> None:
        self._populate_plan(plan)
        extra = " (net against stock)" if self.combine_net_checkbox.isChecked() else " (from scratch)"
        self.show_info(self._plan_summary(
            plan, f"Combined preview{extra} - source orders unchanged. "))

    def _on_computed(self, plan: dict) -> None:
        self._populate_plan(plan)
        self.show_info(self._plan_summary(plan, "Computed - "))
