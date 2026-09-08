"""Production's Planner tab: the stock-aware buy/build planner
(`plan-production` / `production_actions.do_plan_production`), plus stock
target management (`set/update/remove/list-stock-target`) - grouped
together because stock targets are this planner's own only input; there is
nothing to plan without at least one of them, and no other view needs them.

`do_update_stock_target` (GitHub issue #16 - "should be able to change the
targets directly in the table") is genuinely different from
`do_add_stock_target`/"Set Target": it requires the target to already exist
(raises ActionError otherwise, rather than silently creating one) and each
field is independently optional - leaving Quantity blank or Sells At at
"(unchanged)" keeps that field's current value rather than resetting it,
same as `cli.py`'s own `update-stock-target --quantity`/`--jita`/`--home`
flags, all optional.

Also carries the manual-stock ledger (`set/remove/list-manual-stock`) -
previously ported but unwired in any GUI - since it's a direct override of
the on-hand quantities the planner's own Inventory table reads (see
`storage.py`'s `manual_stock` table comment: a genuinely separate signal
from ESI-synced assets, e.g. stock sitting somewhere not covered by a
producer character's own sync), so it belongs alongside stock-target
management as this planner's other real input, not off in Logistics
(which is about *where* a build happens, not how much is already on hand).

Decryptor overrides (`set/clear/list-selected-decryptors`) live here too:
they change the invented BPC's ME/TE (and therefore Cost/Unit and the
Build List / Invention Needs Decryptor column) for a Tech II/III *product*,
matching the parent's Stock Targets "Decryptor (Tech II only)" picker.
Clearing a row returns to automatic Best; choosing "None" is a real
decryptor (invent without one), not the same as Clear.

`do_plan_production` re-runs the full priced BOM traversal every time (no
"last plan" table to load cheaply on open, unlike Trading's Shortlist) -
so only the stock targets/manual stock/decryptor lists themselves are
loaded on open; the planner output tables start empty until "Run Planner"
is clicked.

Seven tabs on one `QTabWidget` - Stock Targets, Manual Stock Overrides,
Decryptor Overrides, then the four planner-output tables - rather than the
three management lists sitting above the output tabs in their own
fixed-height `QGroupBox`es (this view's previous shape). That old layout
meant every table on screen fought the others for a sliver of vertical
space and the three management tables were capped small enough to show
barely a row at a time regardless of window size - putting every table in
the same tab strip instead means whichever one is showing gets the view's
*entire* height, same as any other single-table tab elsewhere in this GUI.
"""
from __future__ import annotations

import functools

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QTabWidget, QVBoxLayout, QWidget)

from ...production import actions as production_actions
from ...production.constants import DECRYPTORS
from .. import icons
from .base import BaseView
from .production_common import build_table, fmt_isk, fmt_pct, populate

_STOCK_COLUMNS = ["Item", "Quantity", "Sells At"]
_STOCK_WIDTHS = [200, None, None]
# storage.load_stock_targets has no ORDER BY of its own - default to
# alphabetical by item name, the most usable order for a manually-maintained
# list with no other inherent ranking.
_STOCK_SORT = (0, Qt.SortOrder.AscendingOrder)

_MANUAL_STOCK_COLUMNS = ["Item", "Count"]
_MANUAL_STOCK_WIDTHS = [200, None]
# storage.list_manual_stock: "ORDER BY type_name" - alphabetical, same
# reasoning as the stock target list above.
_MANUAL_STOCK_SORT = (0, Qt.SortOrder.AscendingOrder)

_DECRYPTOR_COLUMNS = ["Item", "Decryptor"]
_DECRYPTOR_WIDTHS = [200, None]
_DECRYPTOR_SORT = (0, Qt.SortOrder.AscendingOrder)

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


def _manual_stock_row(row) -> list:
    type_id, type_name, count = row
    return [type_name, f"{count:,.0f}"]


def _decryptor_override_row(row) -> list:
    type_id, type_name, decryptor = row
    return [type_name, decryptor]


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
        run_btn = QPushButton(icons.icon("run", color="#06222b"), "Run Planner")
        run_btn.setProperty("cssClass", "primary")
        run_btn.clicked.connect(self._run_planner)
        toolbar.addWidget(run_btn)
        toolbar.addStretch(1)
        self.root_layout.addLayout(toolbar)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_stock_target_tab(), "Stock Targets")
        self.tabs.addTab(self._build_manual_stock_tab(), "Manual Stock Overrides")
        self.tabs.addTab(self._build_decryptor_override_tab(), "Decryptor Overrides")
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
        self._load_manual_stock()
        self._load_decryptor_overrides()

    def _build_stock_target_tab(self) -> QWidget:
        tab = QWidget()
        outer = QVBoxLayout(tab)

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
        set_btn = QPushButton(icons.icon("target"), "Set Target")
        set_btn.clicked.connect(self._set_stock_target)
        form.addWidget(set_btn)
        remove_btn = QPushButton(icons.icon("remove"), "Remove Target")
        remove_btn.clicked.connect(self._remove_stock_target)
        form.addWidget(remove_btn)
        refresh_btn = QPushButton(icons.icon("refresh"), "Refresh List")
        refresh_btn.clicked.connect(self._load_stock_targets)
        form.addWidget(refresh_btn)
        form.addStretch(1)
        outer.addLayout(form)

        # Edit-in-place row (do_update_stock_target - GitHub issue #16): both
        # fields are independently optional, unlike "Set Target"/
        # do_add_stock_target's upsert above - a blank Quantity or "(leave
        # unchanged)" Sells At keeps that field's current stored value rather
        # than resetting it, and it raises rather than creating a new row if
        # the target doesn't already exist. Reuses the Item field above -
        # "which item" is the one thing every one of these actions shares.
        update_form = QHBoxLayout()
        update_form.addWidget(QLabel("Update quantity to:"))
        self.update_quantity_input = QLineEdit()
        self.update_quantity_input.setPlaceholderText("(leave unchanged)")
        self.update_quantity_input.setMaximumWidth(120)
        update_form.addWidget(self.update_quantity_input)
        update_form.addWidget(QLabel("Sells at:"))
        self.update_jita_combo = QComboBox()
        self.update_jita_combo.addItem("(leave unchanged)", None)
        self.update_jita_combo.addItem("home", False)
        self.update_jita_combo.addItem("Jita", True)
        update_form.addWidget(self.update_jita_combo)
        update_btn = QPushButton(icons.icon("save"), "Update Target")
        update_btn.clicked.connect(self._update_stock_target)
        update_form.addWidget(update_btn)
        update_form.addStretch(1)
        outer.addLayout(update_form)

        self.stock_target_table = build_table(_STOCK_COLUMNS, column_widths=_STOCK_WIDTHS)
        outer.addWidget(self.stock_target_table)
        return tab

    def _build_manual_stock_tab(self) -> QWidget:
        tab = QWidget()
        outer = QVBoxLayout(tab)

        form = QHBoxLayout()
        form.addWidget(QLabel("Item:"))
        self.manual_stock_item_input = QLineEdit()
        self.manual_stock_item_input.setPlaceholderText("type_id or exact item name")
        form.addWidget(self.manual_stock_item_input)
        form.addWidget(QLabel("Count:"))
        self.manual_stock_count_input = QLineEdit()
        self.manual_stock_count_input.setPlaceholderText("e.g. 50")
        self.manual_stock_count_input.setMaximumWidth(100)
        form.addWidget(self.manual_stock_count_input)
        set_btn = QPushButton(icons.icon("target"), "Set Count")
        set_btn.clicked.connect(self._set_manual_stock)
        form.addWidget(set_btn)
        remove_btn = QPushButton(icons.icon("remove"), "Remove Override")
        remove_btn.clicked.connect(self._remove_manual_stock)
        form.addWidget(remove_btn)
        refresh_btn = QPushButton(icons.icon("refresh"), "Refresh List")
        refresh_btn.clicked.connect(self._load_manual_stock)
        form.addWidget(refresh_btn)
        form.addStretch(1)
        outer.addLayout(form)

        self.manual_stock_table = build_table(_MANUAL_STOCK_COLUMNS, column_widths=_MANUAL_STOCK_WIDTHS)
        outer.addWidget(self.manual_stock_table)
        return tab

    def _build_decryptor_override_tab(self) -> QWidget:
        tab = QWidget()
        outer = QVBoxLayout(tab)

        form = QHBoxLayout()
        form.addWidget(QLabel("Item:"))
        self.decryptor_item_input = QLineEdit()
        self.decryptor_item_input.setPlaceholderText("type_id or exact item name")
        form.addWidget(self.decryptor_item_input)
        form.addWidget(QLabel("Decryptor:"))
        self.decryptor_combo = QComboBox()
        for name in DECRYPTORS:
            self.decryptor_combo.addItem(name, name)
        form.addWidget(self.decryptor_combo)
        set_btn = QPushButton(icons.icon("target"), "Set Override")
        set_btn.clicked.connect(self._set_decryptor_override)
        form.addWidget(set_btn)
        clear_btn = QPushButton(icons.icon("clear"), "Clear (auto / Best)")
        clear_btn.clicked.connect(self._clear_decryptor_override)
        form.addWidget(clear_btn)
        refresh_btn = QPushButton(icons.icon("refresh"), "Refresh List")
        refresh_btn.clicked.connect(self._load_decryptor_overrides)
        form.addWidget(refresh_btn)
        form.addStretch(1)
        outer.addLayout(form)

        self.decryptor_override_table = build_table(_DECRYPTOR_COLUMNS, column_widths=_DECRYPTOR_WIDTHS)
        outer.addWidget(self.decryptor_override_table)
        return tab

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

    def _update_stock_target(self) -> None:
        item = self.item_input.text().strip()
        if not item:
            self.show_error("Enter an item (type_id or name) first.")
            return
        quantity_text = self.update_quantity_input.text().strip()
        if quantity_text:
            try:
                quantity = float(quantity_text)
            except ValueError:
                self.show_error("Quantity must be a number.")
                return
        else:
            quantity = None  # leave unchanged - see do_update_stock_target's own docstring
        jita_target = self.update_jita_combo.currentData()  # None = leave unchanged
        self.run_action(
            functools.partial(production_actions.do_update_stock_target, item,
                              quantity=quantity, jita_target=jita_target),
            self._on_stock_target_updated, busy_message="Updating stock target...")

    def _on_stock_target_updated(self, result: dict) -> None:
        self._load_stock_targets()
        where = "Jita" if result["jita_target"] else "home"
        self.show_info(f"Stock target updated: {result['type_name']} -> {result['quantity']:,.0f} units "
                       f"(sells at {where}).")

    def _load_manual_stock(self) -> None:
        # storage-only cheap read (no network), same convention as
        # _load_stock_targets above.
        result = production_actions.do_list_manual_stock()
        populate(self.manual_stock_table, [_manual_stock_row(r) for r in result["rows"]],
                default_sort=_MANUAL_STOCK_SORT)

    def _set_manual_stock(self) -> None:
        item = self.manual_stock_item_input.text().strip()
        if not item:
            self.show_error("Enter an item (type_id or name) first.")
            return
        try:
            count = float(self.manual_stock_count_input.text().strip())
        except ValueError:
            self.show_error("Count must be a number.")
            return
        self.run_action(functools.partial(production_actions.do_set_manual_stock, item, count),
                        self._on_manual_stock_changed, busy_message="Setting manual stock override...")

    def _remove_manual_stock(self) -> None:
        item = self.manual_stock_item_input.text().strip()
        if not item:
            self.show_error("Enter an item (type_id or name) first.")
            return
        self.run_action(functools.partial(production_actions.do_remove_manual_stock, item),
                        self._on_manual_stock_changed, busy_message="Removing manual stock override...")

    def _on_manual_stock_changed(self, result: dict) -> None:
        self._load_manual_stock()
        if "count" in result:
            self.show_info(f"Manual stock set: {result['type_name']} -> {result['count']:,.0f} units.")
        else:
            self.show_info(f"Removed manual stock override: {result['type_name']}")

    def _load_decryptor_overrides(self) -> None:
        result = production_actions.do_list_selected_decryptors()
        populate(self.decryptor_override_table,
                 [_decryptor_override_row(r) for r in result["rows"]],
                 default_sort=_DECRYPTOR_SORT)

    def _set_decryptor_override(self) -> None:
        item = self.decryptor_item_input.text().strip()
        if not item:
            self.show_error("Enter an item (type_id or name) first.")
            return
        decryptor = self.decryptor_combo.currentData()
        self.run_action(
            functools.partial(production_actions.do_set_selected_decryptor, item, decryptor),
            self._on_decryptor_override_changed, busy_message="Setting decryptor override...")

    def _clear_decryptor_override(self) -> None:
        item = self.decryptor_item_input.text().strip()
        if not item:
            self.show_error("Enter an item (type_id or name) first.")
            return
        self.run_action(
            functools.partial(production_actions.do_clear_selected_decryptor, item),
            self._on_decryptor_override_changed, busy_message="Clearing decryptor override...")

    def _on_decryptor_override_changed(self, result: dict) -> None:
        self._load_decryptor_overrides()
        if result["decryptor"] == "Best":
            self.show_info(f"Decryptor override for {result['type_name']} cleared (auto / Best).")
        else:
            self.show_info(f"Invention of {result['type_name']} will use decryptor {result['decryptor']}.")

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
