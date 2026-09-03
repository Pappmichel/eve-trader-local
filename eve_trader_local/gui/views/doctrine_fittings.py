"""Doctrine's Fittings tab: doctrine management (`create-doctrine`/
`list-doctrines`) plus fitting management (`add-fitting`/`list-fittings`/
`do_delete_fitting` - there is no CLI `remove-fitting` command yet, but the
action exists and this is a reasonable place to expose it) - grouped
together since you can't paste a fitting without a doctrine to put it under,
same "the input list and the thing that consumes it share a screen"
reasoning as production_special_orders.py.

Both lists are cheap local reads (`storage.list_doctrines`/
`list_fittings_for_doctrine`, no network) so they're loaded on open, same
convention as production_logistics.py's category-location table."""
from __future__ import annotations

import functools

from PySide6.QtWidgets import (QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout)

from ...doctrine import actions as doctrine_actions
from .base import BaseView
from .production_common import build_table, populate

_DOCTRINE_COLUMNS = ["Doctrine ID", "Name", "Description", "Active"]
_FITTING_COLUMNS = ["Fitting ID", "Doctrine ID", "Name", "Hull Type ID",
                    "Contract Target", "Stockpile Target", "Active"]


def _doctrine_row(row: dict) -> list:
    return [row["doctrine_id"], row["name"], row["description"] or "-", "yes" if row["active"] else "no"]


def _fitting_row(row: dict) -> list:
    return [row["fitting_id"], row["doctrine_id"], row["name"], row["hull_type_id"],
            row["contract_target"], row["stockpile_target"], "yes" if row["active"] else "no"]


class FittingsView(BaseView):
    title = "Doctrine - Fittings"

    def __init__(self, parent=None):
        super().__init__(parent)

        self.root_layout.addWidget(self._build_doctrine_box())
        self.root_layout.addWidget(self._build_add_fitting_box())

        toolbar = QHBoxLayout()
        refresh_btn = QPushButton("Refresh Fittings")
        refresh_btn.clicked.connect(self._load_fittings)
        toolbar.addWidget(refresh_btn)
        toolbar.addWidget(QLabel("Remove Fitting ID:"))
        self.remove_fitting_input = QLineEdit()
        self.remove_fitting_input.setMaximumWidth(280)
        toolbar.addWidget(self.remove_fitting_input)
        remove_btn = QPushButton("Remove")
        remove_btn.clicked.connect(self._remove_fitting)
        toolbar.addWidget(remove_btn)
        toolbar.addStretch(1)
        self.root_layout.addLayout(toolbar)

        self.fittings_table = build_table(_FITTING_COLUMNS)
        self.fittings_table.itemSelectionChanged.connect(self._on_fitting_selected)
        self.root_layout.addWidget(self.fittings_table)

        self._finish_status_row()
        self._load_doctrines()
        self._load_fittings()

    def _build_doctrine_box(self) -> QGroupBox:
        box = QGroupBox("Doctrines")
        outer = QVBoxLayout(box)
        form = QHBoxLayout()
        form.addWidget(QLabel("New doctrine name:"))
        self.doctrine_name_input = QLineEdit()
        form.addWidget(self.doctrine_name_input)
        form.addWidget(QLabel("Description:"))
        self.doctrine_description_input = QLineEdit()
        form.addWidget(self.doctrine_description_input)
        create_btn = QPushButton("Create Doctrine")
        create_btn.clicked.connect(self._create_doctrine)
        form.addWidget(create_btn)
        refresh_btn = QPushButton("Refresh List")
        refresh_btn.clicked.connect(self._load_doctrines)
        form.addWidget(refresh_btn)
        outer.addLayout(form)
        self.doctrine_table = build_table(_DOCTRINE_COLUMNS)
        self.doctrine_table.setMaximumHeight(160)
        outer.addWidget(self.doctrine_table)
        return box

    def _build_add_fitting_box(self) -> QGroupBox:
        box = QGroupBox("Add Fitting (paste EFT)")
        outer = QVBoxLayout(box)
        form = QHBoxLayout()
        form.addWidget(QLabel("Doctrine ID:"))
        self.fitting_doctrine_input = QLineEdit()
        self.fitting_doctrine_input.setPlaceholderText("paste a doctrine id from the table above")
        form.addWidget(self.fitting_doctrine_input)
        form.addWidget(QLabel("Name (optional):"))
        self.fitting_name_input = QLineEdit()
        form.addWidget(self.fitting_name_input)
        form.addWidget(QLabel("Contract target:"))
        self.contract_target_input = QSpinBox()
        self.contract_target_input.setMaximum(100000)
        form.addWidget(self.contract_target_input)
        form.addWidget(QLabel("Stockpile target:"))
        self.stockpile_target_input = QSpinBox()
        self.stockpile_target_input.setMaximum(1000000)
        form.addWidget(self.stockpile_target_input)
        outer.addLayout(form)

        self.eft_input = QPlainTextEdit()
        self.eft_input.setPlaceholderText("Paste an EFT fitting here (the [Hull, Fit Name] header plus module "
                                          "lines)...")
        self.eft_input.setMaximumHeight(160)
        outer.addWidget(self.eft_input)

        add_btn = QPushButton("Add Fitting")
        add_btn.clicked.connect(self._add_fitting)
        outer.addWidget(add_btn)
        return box

    def _load_doctrines(self) -> None:
        rows = doctrine_actions.do_list_doctrines()["rows"]
        populate(self.doctrine_table, [_doctrine_row(r) for r in rows])

    def _create_doctrine(self) -> None:
        name = self.doctrine_name_input.text().strip()
        if not name:
            self.show_error("Doctrine name is required.")
            return
        description = self.doctrine_description_input.text().strip() or None
        self.run_action(functools.partial(doctrine_actions.do_create_doctrine, name, description),
                        self._on_doctrine_created, busy_message="Creating doctrine...")

    def _on_doctrine_created(self, result: dict) -> None:
        self._load_doctrines()
        self.doctrine_name_input.clear()
        self.doctrine_description_input.clear()
        self.show_info(f"Created doctrine '{result['name']}' ({result['doctrine_id']}).")

    def _load_fittings(self) -> None:
        rows = doctrine_actions.do_list_fittings()["rows"]
        populate(self.fittings_table, [_fitting_row(r) for r in rows])

    def _add_fitting(self) -> None:
        doctrine_id = self.fitting_doctrine_input.text().strip()
        if not doctrine_id:
            self.show_error("Doctrine ID is required - copy one from the Doctrines table above.")
            return
        raw_eft = self.eft_input.toPlainText()
        if not raw_eft.strip():
            self.show_error("Paste an EFT fitting first.")
            return
        name = self.fitting_name_input.text().strip() or None
        self.run_action(
            functools.partial(doctrine_actions.do_add_fitting, doctrine_id, raw_eft, name=name,
                              contract_target=self.contract_target_input.value(),
                              stockpile_target=self.stockpile_target_input.value()),
            self._on_fitting_added, busy_message="Parsing and adding fitting...")

    def _on_fitting_added(self, result: dict) -> None:
        self._load_fittings()
        fitting = result["fitting"]
        self.eft_input.clear()
        self.fitting_name_input.clear()
        message = f"Added fitting '{fitting['name']}' ({fitting['fitting_id']})."
        if result["issues"]:
            message += f" {len(result['issues'])} parse issue(s) - see the fitting detail for specifics."
        self.show_info(message)

    def _on_fitting_selected(self) -> None:
        rows = self.fittings_table.selectionModel().selectedRows()
        if not rows:
            return
        fitting_id_item = self.fittings_table.item(rows[0].row(), 0)
        if fitting_id_item is not None:
            self.remove_fitting_input.setText(fitting_id_item.text())

    def _remove_fitting(self) -> None:
        fitting_id = self.remove_fitting_input.text().strip()
        if not fitting_id:
            self.show_error("Select a row, or paste a fitting id, first.")
            return
        self.run_action(functools.partial(doctrine_actions.do_delete_fitting, fitting_id),
                        self._on_fitting_removed, busy_message="Removing fitting...")

    def _on_fitting_removed(self, result: dict) -> None:
        self._load_fittings()
        self.remove_fitting_input.clear()
        self.show_info(f"Removed fitting {result['deleted']}.")
