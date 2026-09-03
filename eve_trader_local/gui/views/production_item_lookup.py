"""Production's Item Lookup tab: three "type in an item, get a report"
tools grouped into one screen since they share the same input (an item
name or type_id) and differ only in what report they produce -
`do_get_item_margin` (margin), `do_build_material_tree` (recursive BOM),
and `do_search_item_locations` (synced asset locations). Unlike
`production_margins_market.py`'s `discover-ship-margins` (a whole-catalog
scan), all three of these resolve one specific item the user names, so one
shared item-name field plus a per-report button fits better than the
margins/market view's independent-buttons layout.
"""
from __future__ import annotations

import functools

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QTabWidget

from ...production import actions as production_actions
from .base import BaseView
from .production_common import build_table, fmt_isk, fmt_pct, populate

_MARGIN_COLUMNS = ["Item", "Activity", "Home Price", "Jita Price", "Build Cost",
                   "Margin (Home)", "Margin (Jita)", "Meta Level"]
_MARGIN_WIDTHS = [200, 120, None, None, None, None, None, None]

_TREE_COLUMNS = ["Depth", "Item", "Activity", "Quantity", "Decryptor"]
_TREE_WIDTHS = [70, 260, 120, None, 140]

_LOCATION_COLUMNS = ["Location", "Owner", "Quantity"]
_LOCATION_WIDTHS = [260, 200, None]


def _margin_row(r) -> list:
    return [r.type_name, r.activity, fmt_isk(r.home_price), fmt_isk(r.jita_price), fmt_isk(r.build_cost),
            fmt_pct(r.margin_home), fmt_pct(r.margin_jita), r.meta_level]


def _tree_rows(node: dict, depth: int = 0, out: list | None = None) -> list:
    """Flattens `do_build_material_tree`'s recursive dict (see
    `engine.build_material_tree`'s own docstring) into table rows, indenting
    the item name with the same "  " x depth prefix `cli.py`'s own
    `cmd_material_tree._print` uses, so a Depth column and the visual
    indentation both agree with each other and with the CLI's own output."""
    if out is None:
        out = []
    out.append([depth, ("  " * depth) + node["type_name"], node["activity"],
                f"{node['quantity']:,.2f}", node.get("decryptor")])
    for child in node["children"]:
        _tree_rows(child, depth + 1, out)
    return out


def _location_row(loc) -> list:
    return [loc.location_name or f"location {loc.location_id}", loc.owner_name, f"{loc.quantity:,.0f}"]


class ItemLookupView(BaseView):
    title = "Production - Item Lookup"

    def __init__(self, parent=None):
        super().__init__(parent)

        form = QHBoxLayout()
        form.addWidget(QLabel("Item:"))
        self.item_input = QLineEdit()
        self.item_input.setPlaceholderText("type_id or exact item name")
        form.addWidget(self.item_input)
        form.addWidget(QLabel("Tree Quantity:"))
        self.quantity_input = QLineEdit("1")
        self.quantity_input.setMaximumWidth(80)
        form.addWidget(self.quantity_input)
        margin_btn = QPushButton("Look Up Margin")
        margin_btn.clicked.connect(self._lookup_margin)
        form.addWidget(margin_btn)
        tree_btn = QPushButton("Build Material Tree")
        tree_btn.clicked.connect(self._build_tree)
        form.addWidget(tree_btn)
        locations_btn = QPushButton("Search Locations")
        locations_btn.clicked.connect(self._search_locations)
        form.addWidget(locations_btn)
        form.addStretch(1)
        self.root_layout.addLayout(form)

        self.tabs = QTabWidget()
        self.margin_table = build_table(_MARGIN_COLUMNS, column_widths=_MARGIN_WIDTHS)
        self.tree_table = build_table(_TREE_COLUMNS, column_widths=_TREE_WIDTHS)
        self.locations_table = build_table(_LOCATION_COLUMNS, column_widths=_LOCATION_WIDTHS)
        self.tabs.addTab(self.margin_table, "Margin")
        self.tabs.addTab(self.tree_table, "Material Tree")
        self.tabs.addTab(self.locations_table, "Locations")
        self.root_layout.addWidget(self.tabs)

        self._finish_status_row()

    def _item(self) -> str | None:
        item = self.item_input.text().strip()
        if not item:
            self.show_error("Enter an item (type_id or name) first.")
            return None
        return item

    def _lookup_margin(self) -> None:
        item = self._item()
        if item is None:
            return
        self.run_action(functools.partial(production_actions.do_get_item_margin, item), self._on_margin,
                        busy_message=f"Looking up margin for {item}...")

    def _on_margin(self, row) -> None:
        populate(self.margin_table, [_margin_row(row)])
        self.tabs.setCurrentWidget(self.margin_table)
        self.show_info(f"{row.type_name}: margin (home) {fmt_pct(row.margin_home) or '-'}")

    def _build_tree(self) -> None:
        item = self._item()
        if item is None:
            return
        try:
            quantity = float(self.quantity_input.text().strip() or "1")
        except ValueError:
            self.show_error("Tree quantity must be a number.")
            return
        self.run_action(functools.partial(production_actions.do_build_material_tree, item, quantity=quantity),
                        self._on_tree, busy_message=f"Building material tree for {item}...")

    def _on_tree(self, tree: dict) -> None:
        rows = _tree_rows(tree)
        populate(self.tree_table, rows)
        self.tabs.setCurrentWidget(self.tree_table)
        self.show_info(f"{len(rows)} node(s) in the material tree.")

    def _search_locations(self) -> None:
        item = self._item()
        if item is None:
            return
        self.run_action(functools.partial(production_actions.do_search_item_locations, item), self._on_locations,
                        busy_message=f"Searching synced locations for {item}...")

    def _on_locations(self, result: dict) -> None:
        locations = result["locations"]
        populate(self.locations_table, [_location_row(loc) for loc in locations])
        self.tabs.setCurrentWidget(self.locations_table)
        self.show_info(f"{len(locations)} location(s) for {result['type_name']}." if locations else
                       f"No synced assets found for {result['type_name']}.")
