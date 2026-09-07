"""Production's Owned Blueprints tab: `do_list_owned_blueprints`
(`engine.list_owned_blueprints`) - a single, standalone read of every
owned blueprint (character + corp), aggregated across identical (type_id,
is_original, ME, TE, runs) groups. Reflects the last "Sync ESI Data" run -
purely a local `storage.load_owned_blueprints()` read, no network call of
its own, so (like `production_planner.py`'s stock-target list) it's loaded
synchronously on open rather than through `run_action`; Refresh re-runs the
same cheap local read after a sync.

Second section: manually registered blueprint-copy purchase costs (GitHub
issue #40) - items that can only be built from a BPC bought outright. Add
is an upsert (`do_add_manual_blueprint_copy_cost`); no inline table edit.
"""
from __future__ import annotations

import functools

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout

from ...production import actions as production_actions
from .. import icons
from .base import TableView
from .production_common import build_table, fmt_isk, populate

_COLUMNS = ["Item", "Kind", "Quantity", "ME", "TE", "Runs Left"]
_COLUMN_WIDTHS = [260, 90, None, None, None, None]
# engine.list_owned_blueprints's own docstring: "rows.sort(key=lambda r:
# r.type_name)".
_DEFAULT_SORT = (0, Qt.SortOrder.AscendingOrder)

_BPC_COLUMNS = ["Item", "Purchase cost", "Runs", "Cost/Run"]
_BPC_WIDTHS = [220, None, None, None]
# storage.load_manual_blueprint_copy_costs: "ORDER BY type_name".
_BPC_SORT = (0, Qt.SortOrder.AscendingOrder)


def _row_to_cells(row) -> list:
    kind = "BPO" if row.is_original else "BPC"
    runs = "-" if row.is_original else row.runs
    return [row.type_name, kind, row.quantity, row.material_efficiency, row.time_efficiency, runs]


def _bpc_cost_row(row) -> list:
    return [row.type_name, fmt_isk(row.purchase_cost), row.runs, fmt_isk(row.cost_per_run)]


class OwnedBlueprintsView(TableView):
    title = "Production - Owned Blueprints"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_toolbar([
            ("Refresh", self._refresh, "refresh"),
        ])
        self._build_table(_COLUMNS, column_widths=_COLUMN_WIDTHS, default_sort=_DEFAULT_SORT)
        self.root_layout.addWidget(self._build_bpc_cost_box())
        self._finish_status_row()
        self._refresh()

    def _build_bpc_cost_box(self) -> QGroupBox:
        box = QGroupBox("Blueprint Copies That Must Be Bought")
        outer = QVBoxLayout(box)

        form = QHBoxLayout()
        form.addWidget(QLabel("Item:"))
        self.bpc_item_input = QLineEdit()
        self.bpc_item_input.setPlaceholderText("type_id or exact item name")
        form.addWidget(self.bpc_item_input)
        form.addWidget(QLabel("Purchase cost:"))
        self.bpc_cost_input = QLineEdit()
        self.bpc_cost_input.setPlaceholderText("ISK")
        self.bpc_cost_input.setMaximumWidth(120)
        form.addWidget(self.bpc_cost_input)
        form.addWidget(QLabel("Runs:"))
        self.bpc_runs_input = QLineEdit()
        self.bpc_runs_input.setPlaceholderText("e.g. 10")
        self.bpc_runs_input.setMaximumWidth(80)
        form.addWidget(self.bpc_runs_input)
        add_btn = QPushButton(icons.icon("target"), "Add Cost")
        add_btn.clicked.connect(self._add_bpc_cost)
        form.addWidget(add_btn)
        remove_btn = QPushButton(icons.icon("remove"), "Remove Cost")
        remove_btn.clicked.connect(self._remove_bpc_cost)
        form.addWidget(remove_btn)
        form.addStretch(1)
        outer.addLayout(form)

        self.bpc_cost_table = build_table(_BPC_COLUMNS, column_widths=_BPC_WIDTHS)
        self.bpc_cost_table.setMaximumHeight(160)
        outer.addWidget(self.bpc_cost_table)
        return box

    def _refresh(self) -> None:
        result = production_actions.do_list_owned_blueprints()
        rows = result["rows"]
        self.populate_table([_row_to_cells(r) for r in rows])
        self._load_bpc_costs()
        self.show_info(f"{len(rows)} owned blueprint group(s)." if rows else
                       "No owned blueprints synced yet.")

    def _load_bpc_costs(self) -> None:
        result = production_actions.do_list_manual_blueprint_copy_costs()
        populate(self.bpc_cost_table, [_bpc_cost_row(r) for r in result["rows"]],
                 default_sort=_BPC_SORT)

    def _add_bpc_cost(self) -> None:
        item = self.bpc_item_input.text().strip()
        if not item:
            self.show_error("Enter an item (type_id or name) first.")
            return
        try:
            purchase_cost = float(self.bpc_cost_input.text().strip())
        except ValueError:
            self.show_error("Purchase cost must be a number.")
            return
        try:
            runs = int(self.bpc_runs_input.text().strip())
        except ValueError:
            self.show_error("Runs must be a whole number.")
            return
        self.run_action(
            functools.partial(production_actions.do_add_manual_blueprint_copy_cost,
                              item, purchase_cost, runs),
            self._on_bpc_cost_changed, busy_message="Saving blueprint copy cost...")

    def _remove_bpc_cost(self) -> None:
        item = self.bpc_item_input.text().strip()
        if not item:
            self.show_error("Enter an item (type_id or name) first.")
            return
        self.run_action(
            functools.partial(production_actions.do_remove_manual_blueprint_copy_cost, item),
            self._on_bpc_cost_changed, busy_message="Removing blueprint copy cost...")

    def _on_bpc_cost_changed(self, result: dict) -> None:
        self._load_bpc_costs()
        if "purchase_cost" in result:
            self.show_info(
                f"Blueprint copy cost set: {result['type_name']} -> "
                f"{result['purchase_cost']:,.0f} ISK / {result['runs']} runs.")
        else:
            self.show_info(f"Removed blueprint copy cost: {result['type_name']}")
