"""Production's Logistics tab: multi-structure logistics (GitHub issue #4,
ported from the parent's Logistik tab - see SYNC.md's Production section)
- `logistics-status`/`distribution-recommendations` plus category-location
management (`set/clear/add/remove/list-category-location(-option)`),
grouped together since the category-location assignments are these two
reports' own only configuration input.

Category-location assignments are a cheap local read (`storage.
load_category_locations`/`load_category_location_options`, no network) so
they're loaded on open; `logistics-status`/`distribution-recommendations`
both re-run the full planner (`plan_production`) under the hood, so those
two stay Refresh-button-only, same reasoning as the Planner view."""
from __future__ import annotations

import functools

from PySide6.QtWidgets import (QComboBox, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QTabWidget, QVBoxLayout)

from ...production import actions as production_actions
from ...production.constants import JOB_CATEGORIES
from .base import BaseView
from .production_common import build_table, populate

_CATEGORY_LOCATION_COLUMNS = ["Category", "Active Location", "Options"]
_LOGISTICS_COLUMNS = ["Category", "Location", "Item", "Needed", "Available", "Missing",
                      "Pull From", "Pull Avail."]
_DISTRIBUTION_COLUMNS = ["Item", "From Location", "To Category", "To Location", "Quantity"]


def _category_location_row(category: str, assigned: dict, options: dict) -> list:
    active = assigned.get(category)
    opts = options.get(category, [])
    return [category, active if active is not None else "-",
            ", ".join(str(o) for o in opts) if opts else "-"]


def _logistics_row(row) -> list:
    return [row.category, row.location_id, row.type_name, f"{row.needed:,.0f}", f"{row.available:,.0f}",
            f"{row.missing:,.0f}", row.pull_from_location_id,
            f"{row.pull_from_available:,.0f}" if row.pull_from_available is not None else None]


def _distribution_row(row) -> list:
    return [row.type_name, row.from_location_id, row.to_category, row.to_location_id, f"{row.quantity:,.0f}"]


class LogisticsView(BaseView):
    title = "Production - Logistics"

    def __init__(self, parent=None):
        super().__init__(parent)

        self.root_layout.addWidget(self._build_category_location_box())

        toolbar = QHBoxLayout()
        logistics_btn = QPushButton("Refresh Logistics Status")
        logistics_btn.clicked.connect(self._refresh_logistics)
        toolbar.addWidget(logistics_btn)
        distribution_btn = QPushButton("Refresh Distribution Recommendations")
        distribution_btn.clicked.connect(self._refresh_distribution)
        toolbar.addWidget(distribution_btn)
        toolbar.addStretch(1)
        self.root_layout.addLayout(toolbar)

        self.tabs = QTabWidget()
        self.logistics_table = build_table(_LOGISTICS_COLUMNS)
        self.distribution_table = build_table(_DISTRIBUTION_COLUMNS)
        self.tabs.addTab(self.logistics_table, "Logistics Status")
        self.tabs.addTab(self.distribution_table, "Distribution Recommendations")
        self.root_layout.addWidget(self.tabs)

        self._finish_status_row()
        self._load_category_locations()

    def _build_category_location_box(self) -> QGroupBox:
        box = QGroupBox("Category Locations")
        outer = QVBoxLayout(box)

        form = QHBoxLayout()
        form.addWidget(QLabel("Category:"))
        self.category_combo = QComboBox()
        self.category_combo.addItems(JOB_CATEGORIES)
        form.addWidget(self.category_combo)
        form.addWidget(QLabel("Location ID:"))
        self.location_input = QLineEdit()
        self.location_input.setMaximumWidth(140)
        form.addWidget(self.location_input)
        set_btn = QPushButton("Set Active")
        set_btn.clicked.connect(self._set_category_location)
        form.addWidget(set_btn)
        clear_btn = QPushButton("Clear Active")
        clear_btn.clicked.connect(self._clear_category_location)
        form.addWidget(clear_btn)
        add_opt_btn = QPushButton("Add Option")
        add_opt_btn.clicked.connect(self._add_category_location_option)
        form.addWidget(add_opt_btn)
        remove_opt_btn = QPushButton("Remove Option")
        remove_opt_btn.clicked.connect(self._remove_category_location_option)
        form.addWidget(remove_opt_btn)
        refresh_btn = QPushButton("Refresh List")
        refresh_btn.clicked.connect(self._load_category_locations)
        form.addWidget(refresh_btn)
        form.addStretch(1)
        outer.addLayout(form)

        self.category_location_table = build_table(_CATEGORY_LOCATION_COLUMNS)
        self.category_location_table.setMaximumHeight(220)
        outer.addWidget(self.category_location_table)
        return box

    def _location_id(self) -> int | None:
        text = self.location_input.text().strip()
        if not text.isdigit():
            self.show_error("Location ID must be a whole number.")
            return None
        return int(text)

    def _load_category_locations(self) -> None:
        # storage-only cheap read (no network), same "load synchronously on
        # open" convention as trading_shortlist.py's own snapshot load.
        result = production_actions.do_list_category_locations()
        assigned, options = result["assigned"], result["options"]
        populate(self.category_location_table,
                [_category_location_row(c, assigned, options) for c in JOB_CATEGORIES])

    def _set_category_location(self) -> None:
        location_id = self._location_id()
        if location_id is None:
            return
        self.run_action(
            functools.partial(production_actions.do_set_category_location,
                              self.category_combo.currentText(), location_id),
            self._on_category_location_changed, busy_message="Setting category location...")

    def _clear_category_location(self) -> None:
        self.run_action(
            functools.partial(production_actions.do_clear_category_location, self.category_combo.currentText()),
            self._on_category_location_changed, busy_message="Clearing category location...")

    def _add_category_location_option(self) -> None:
        location_id = self._location_id()
        if location_id is None:
            return
        self.run_action(
            functools.partial(production_actions.do_add_category_location_option,
                              self.category_combo.currentText(), location_id),
            self._on_category_location_changed, busy_message="Adding option...")

    def _remove_category_location_option(self) -> None:
        location_id = self._location_id()
        if location_id is None:
            return
        self.run_action(
            functools.partial(production_actions.do_remove_category_location_option,
                              self.category_combo.currentText(), location_id),
            self._on_category_location_changed, busy_message="Removing option...")

    def _on_category_location_changed(self, result: dict) -> None:
        self._load_category_locations()
        self.show_info(f"Category '{result['category']}' updated.")

    def _refresh_logistics(self) -> None:
        self.run_action(functools.partial(production_actions.do_get_logistics_status), self._on_logistics,
                        busy_message="Computing logistics status (re-runs the planner)...")

    def _on_logistics(self, result: dict) -> None:
        rows = result["rows"]
        populate(self.logistics_table, [_logistics_row(r) for r in rows])
        self.show_info(f"{len(rows)} logistics row(s)." if rows else
                       "No category has both an assigned location and planned jobs right now.")

    def _refresh_distribution(self) -> None:
        self.run_action(functools.partial(production_actions.do_get_distribution_recommendations),
                        self._on_distribution, busy_message="Computing distribution recommendations "
                                                            "(re-runs the planner)...")

    def _on_distribution(self, result: dict) -> None:
        rows = result["rows"]
        populate(self.distribution_table, [_distribution_row(r) for r in rows])
        self.show_info(f"{len(rows)} recommendation(s)." if rows else
                       "Nothing to move - either every category is covered, or no distribution "
                       "source is configured.")
