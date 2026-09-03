"""Production's Logistics tab: multi-structure logistics (GitHub issue #4,
ported from the parent's Logistik tab - see SYNC.md's Production section)
- `logistics-status`/`distribution-recommendations` plus category-location
management (`set/clear/add/remove/list-category-location(-option)`),
grouped together since the category-location assignments are these two
reports' own only configuration input. Also carries the single-location
invention-logistics slice (`invention-logistics`/`t1-bpc-invention-needs`)
- previously ported but unwired in any GUI - since it's the same
"needed vs. available at a configured location" shape as Logistics Status,
just for `cfg.invention_location_id` instead of a per-category location;
and structure-name resolution (`resolve-structure-name`), wired as a
"Resolve" button next to the Location ID field, since a resolved structure
name is directly useful for labeling *this* view's own category-location
picker (the Location ID column above is otherwise a bare numeric ID).

Manual Build/Buy override (`set/clear/list-manual-build-buy`) also lives
here rather than in `production_planner.py`: unlike the manual-stock ledger
(a raw on-hand-quantity input the planner's own inventory numbers directly
consume), a Build/Buy override doesn't change any number the Planner
displays - it forces which *path* (buy vs. build) a job takes, which is
what ultimately decides which category/location it needs materials
delivered to. That's this view's own domain, not the Planner's.

Category-location assignments and manual Build/Buy overrides are both
cheap local reads (`storage.load_category_locations`/
`load_category_location_options`/`list_manual_build_buy`, no network) so
they're loaded on open; `logistics-status`/`distribution-recommendations`/
`invention-logistics`/`t1-bpc-invention-needs` all re-run the full planner
(`plan_production`) under the hood, so those four stay Refresh-button-only,
same reasoning as the Planner view."""
from __future__ import annotations

import functools

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QTabWidget, QVBoxLayout)

from ...production import actions as production_actions
from ...production.constants import JOB_CATEGORIES
from .base import BaseView
from .production_common import build_table, populate

_CATEGORY_LOCATION_COLUMNS = ["Category", "Active Location", "Options"]
_CATEGORY_LOCATION_WIDTHS = [140, 120, 220]

_LOGISTICS_COLUMNS = ["Category", "Location", "Item", "Needed", "Available", "Missing",
                      "Pull From", "Pull Avail."]
_LOGISTICS_WIDTHS = [110, 100, 180, None, None, None, 100, 100]
# logistics_status's own docstring: rows sorted by (category, -missing).
# There's no single numeric column for that compound key, so this defaults
# to the "Missing" column (index 5) desc - the more decision-relevant half
# of the compound sort, category grouping is still visible in the column.
_LOGISTICS_SORT = (5, Qt.SortOrder.DescendingOrder)

_DISTRIBUTION_COLUMNS = ["Item", "From Location", "To Category", "To Location", "Quantity"]
_DISTRIBUTION_WIDTHS = [200, 120, 120, 120, None]
# distribution_recommendations's own docstring: sorted by quantity desc.
_DISTRIBUTION_SORT = (4, Qt.SortOrder.DescendingOrder)

_INVENTION_LOGISTICS_COLUMNS = ["Item", "Needed", "Available", "Missing"]
_INVENTION_LOGISTICS_WIDTHS = [220, None, None, None]
# LogisticsRow rows for invention_logistics carry no compound category sort
# (there's only ever one category, "Invention") - default to Missing desc,
# same decision-relevant ordering as the multi-structure table above.
_INVENTION_LOGISTICS_SORT = (3, Qt.SortOrder.DescendingOrder)

_T1_BPC_COLUMNS = ["Item", "Needed", "Available", "Missing", "Stockpile %", "BPO On Site"]
_T1_BPC_WIDTHS = [220, None, None, None, None, 100]
_T1_BPC_SORT = (3, Qt.SortOrder.DescendingOrder)

_MANUAL_BB_COLUMNS = ["Item", "Decision"]
_MANUAL_BB_WIDTHS = [260, None]


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


def _invention_logistics_row(row) -> list:
    return [row.type_name, f"{row.needed:,.0f}", f"{row.available:,.0f}", f"{row.missing:,.0f}"]


def _t1_bpc_row(row) -> list:
    return [row.name, row.needed, row.available, row.missing, f"{row.stockpile_pct:.1f}%",
            "yes" if row.bpo_present else "no"]


def _manual_bb_row(row: tuple) -> list:
    _type_id, type_name, decision = row
    return [type_name, decision]


class LogisticsView(BaseView):
    title = "Production - Logistics"

    def __init__(self, parent=None):
        super().__init__(parent)

        self.root_layout.addWidget(self._build_category_location_box())
        self.root_layout.addWidget(self._build_manual_build_buy_box())

        toolbar = QHBoxLayout()
        logistics_btn = QPushButton("Refresh Logistics Status")
        logistics_btn.clicked.connect(self._refresh_logistics)
        toolbar.addWidget(logistics_btn)
        distribution_btn = QPushButton("Refresh Distribution Recommendations")
        distribution_btn.clicked.connect(self._refresh_distribution)
        toolbar.addWidget(distribution_btn)
        invention_logistics_btn = QPushButton("Refresh Invention Logistics")
        invention_logistics_btn.clicked.connect(self._refresh_invention_logistics)
        toolbar.addWidget(invention_logistics_btn)
        t1_bpc_btn = QPushButton("Refresh T1 BPC Invention Needs")
        t1_bpc_btn.clicked.connect(self._refresh_t1_bpc_needs)
        toolbar.addWidget(t1_bpc_btn)
        toolbar.addStretch(1)
        self.root_layout.addLayout(toolbar)

        self.tabs = QTabWidget()
        self.logistics_table = build_table(_LOGISTICS_COLUMNS, column_widths=_LOGISTICS_WIDTHS)
        self.distribution_table = build_table(_DISTRIBUTION_COLUMNS, column_widths=_DISTRIBUTION_WIDTHS)
        self.invention_logistics_table = build_table(_INVENTION_LOGISTICS_COLUMNS,
                                                      column_widths=_INVENTION_LOGISTICS_WIDTHS)
        self.t1_bpc_table = build_table(_T1_BPC_COLUMNS, column_widths=_T1_BPC_WIDTHS)
        self.tabs.addTab(self.logistics_table, "Logistics Status")
        self.tabs.addTab(self.distribution_table, "Distribution Recommendations")
        self.tabs.addTab(self.invention_logistics_table, "Invention Logistics")
        self.tabs.addTab(self.t1_bpc_table, "T1 BPC Invention Needs")
        self.root_layout.addWidget(self.tabs)

        self._finish_status_row()
        self._load_category_locations()
        self._load_manual_build_buy()

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
        resolve_btn = QPushButton("Resolve Name")
        resolve_btn.clicked.connect(self._resolve_structure_name)
        form.addWidget(resolve_btn)
        refresh_btn = QPushButton("Refresh List")
        refresh_btn.clicked.connect(self._load_category_locations)
        form.addWidget(refresh_btn)
        form.addStretch(1)
        outer.addLayout(form)

        self.category_location_table = build_table(_CATEGORY_LOCATION_COLUMNS,
                                                    column_widths=_CATEGORY_LOCATION_WIDTHS)
        self.category_location_table.setMaximumHeight(220)
        outer.addWidget(self.category_location_table)
        return box

    def _build_manual_build_buy_box(self) -> QGroupBox:
        box = QGroupBox("Manual Build/Buy Overrides")
        outer = QVBoxLayout(box)

        form = QHBoxLayout()
        form.addWidget(QLabel("Item:"))
        self.manual_bb_item_input = QLineEdit()
        self.manual_bb_item_input.setPlaceholderText("type_id or exact item name")
        form.addWidget(self.manual_bb_item_input)
        form.addWidget(QLabel("Decision:"))
        self.manual_bb_decision_combo = QComboBox()
        self.manual_bb_decision_combo.addItems(["Build", "Buy"])
        form.addWidget(self.manual_bb_decision_combo)
        set_bb_btn = QPushButton("Set Override")
        set_bb_btn.clicked.connect(self._set_manual_build_buy)
        form.addWidget(set_bb_btn)
        clear_bb_btn = QPushButton("Clear Override")
        clear_bb_btn.clicked.connect(self._clear_manual_build_buy)
        form.addWidget(clear_bb_btn)
        refresh_bb_btn = QPushButton("Refresh List")
        refresh_bb_btn.clicked.connect(self._load_manual_build_buy)
        form.addWidget(refresh_bb_btn)
        form.addStretch(1)
        outer.addLayout(form)

        self.manual_bb_table = build_table(_MANUAL_BB_COLUMNS, column_widths=_MANUAL_BB_WIDTHS)
        self.manual_bb_table.setMaximumHeight(160)
        outer.addWidget(self.manual_bb_table)
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
        populate(self.logistics_table, [_logistics_row(r) for r in rows], default_sort=_LOGISTICS_SORT)
        self.show_info(f"{len(rows)} logistics row(s)." if rows else
                       "No category has both an assigned location and planned jobs right now.")

    def _refresh_distribution(self) -> None:
        self.run_action(functools.partial(production_actions.do_get_distribution_recommendations),
                        self._on_distribution, busy_message="Computing distribution recommendations "
                                                            "(re-runs the planner)...")

    def _on_distribution(self, result: dict) -> None:
        rows = result["rows"]
        populate(self.distribution_table, [_distribution_row(r) for r in rows], default_sort=_DISTRIBUTION_SORT)
        self.show_info(f"{len(rows)} recommendation(s)." if rows else
                       "Nothing to move - either every category is covered, or no distribution "
                       "source is configured.")

    def _refresh_invention_logistics(self) -> None:
        self.run_action(functools.partial(production_actions.do_invention_logistics), self._on_invention_logistics,
                        busy_message="Computing invention logistics (re-runs the planner)...")

    def _on_invention_logistics(self, result: dict) -> None:
        rows = result["rows"]
        populate(self.invention_logistics_table, [_invention_logistics_row(r) for r in rows],
                default_sort=_INVENTION_LOGISTICS_SORT)
        self.show_info(f"{len(rows)} invention logistics row(s)." if rows else
                       "Nothing needed at the configured invention location right now.")

    def _refresh_t1_bpc_needs(self) -> None:
        self.run_action(functools.partial(production_actions.do_t1_bpc_invention_needs), self._on_t1_bpc_needs,
                        busy_message="Computing T1 BPC invention needs (re-runs the planner)...")

    def _on_t1_bpc_needs(self, result: dict) -> None:
        rows = result["rows"]
        populate(self.t1_bpc_table, [_t1_bpc_row(r) for r in rows], default_sort=_T1_BPC_SORT)
        self.show_info(f"{len(rows)} T1 BPC need(s)." if rows else
                       "Nothing needed at the configured invention location right now.")

    def _resolve_structure_name(self) -> None:
        location_id = self._location_id()
        if location_id is None:
            return
        self.run_action(functools.partial(production_actions.do_resolve_structure_name, location_id),
                        self._on_structure_resolved, busy_message=f"Resolving structure name for {location_id}...")

    def _on_structure_resolved(self, result: dict) -> None:
        if result["name"] is None:
            self.show_error(f"Could not resolve location {result['location_id']} - "
                            "no producer character can see it.")
            return
        cached = " (cached)" if result["cached"] else ""
        self.show_info(f"Location {result['location_id']}: {result['name']}{cached}")

    def _load_manual_build_buy(self) -> None:
        # storage-only cheap read (no network), same convention as
        # _load_category_locations above.
        result = production_actions.do_list_manual_build_buy()
        populate(self.manual_bb_table, [_manual_bb_row(r) for r in result["rows"]])

    def _set_manual_build_buy(self) -> None:
        item = self.manual_bb_item_input.text().strip()
        if not item:
            self.show_error("Enter an item (type_id or name) first.")
            return
        self.run_action(
            functools.partial(production_actions.do_set_manual_build_buy, item,
                              self.manual_bb_decision_combo.currentText()),
            self._on_manual_build_buy_changed, busy_message="Setting manual Build/Buy override...")

    def _clear_manual_build_buy(self) -> None:
        item = self.manual_bb_item_input.text().strip()
        if not item:
            self.show_error("Enter an item (type_id or name) first.")
            return
        self.run_action(functools.partial(production_actions.do_clear_manual_build_buy, item),
                        self._on_manual_build_buy_changed, busy_message="Clearing manual Build/Buy override...")

    def _on_manual_build_buy_changed(self, result: dict) -> None:
        self._load_manual_build_buy()
        self.show_info(f"{result['type_name']} -> {result['decision']}.")
