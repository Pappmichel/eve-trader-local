"""Doctrine's Stockpile Status tab: the offline-revalidation action
(`validate-contracts`) and the two read-only reports it produces
(`doctrine-status`'s per-fitting contract/stockpile ampel, `stockpile-
status`'s aggregated shortfalls) plus the synced contracts list
(`list-contracts`) - grouped together because "look at the ampel/
shortfalls/contracts a sync produced" is one workflow, matching
production_logistics.py's own "the config input and the report(s) it feeds"
grouping. The ESI sync itself (`sync-doctrine`) has no button here anymore -
it only runs from App > Update Data...'s Doctrine scope now (see
esi_update.py's own docstring).

`do_get_doctrine_status`/`do_get_stockpile_status` are pure local
computation (re-derived from already-synced contracts/assets, no network -
see doctrine/engine.py's own `stockpile_rows_for_doctrine`/`fitting_status`)
but still routed through `run_action` rather than loaded synchronously on
open, matching production_logistics.py's own treatment of its (also
locally-computed but potentially slow) planner-backed reports.
`do_validate_contracts` likewise never touches ESI (re-matches already-
synced contracts against the current Fitting definitions). Contracts are a
cheap local read (`storage.list_doctrine_contracts`) so that list loads on
open."""
from __future__ import annotations

import functools

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QTabWidget, QVBoxLayout)

from ...doctrine import actions as doctrine_actions
from .. import icons
from .base import BaseView
from .production_common import build_table, fmt_pct, populate

_FITTING_STATUS_COLUMNS = ["Doctrine", "Fitting", "Hull", "Contract Status", "Valid/Target",
                          "Stockpile Status", "Worst Shortfall %"]
_FITTING_STATUS_WIDTHS = [140, 180, 140, 120, None, 130, None]
# No explicit sort in doctrine_status/fitting_status - rows come back in
# doctrine/fitting creation order (not itself a displayed column), so no
# default_sort here, same reasoning as doctrine_fittings.py's own tables.

_STOCKPILE_COLUMNS = ["Item", "Required", "Available", "Shortfall", "Severity", "# Fittings"]
_STOCKPILE_WIDTHS = [200, None, None, None, 100, None]
# aggregate_stockpile_rows's own call site: "aggregated.sort(key=lambda r:
# r.shortfall, reverse=True)".
_STOCKPILE_SORT = (3, Qt.SortOrder.DescendingOrder)

_CONTRACT_COLUMNS = ["Contract ID", "Hull", "Character", "Validation", "Status", "Price"]
_CONTRACT_WIDTHS = [220, 140, 160, 100, 100, None]
# storage.list_doctrine_contracts (this synced/live snapshot, distinct from
# Contract History's own permanent log) has no ORDER BY of its own -
# no default_sort.


def _fitting_status_row(doctrine_name: str, row: dict) -> list:
    return [doctrine_name, row["fitting_name"], row["hull_name"], row["contract_status"],
            f"{row['valid_contracts']}/{row['contract_target']}", row["stockpile_status"],
            fmt_pct(row["worst_stockpile_shortfall_pct"])]


def _stockpile_row(row: dict) -> list:
    return [row["type_name"], f"{row['required_total']:,.0f}", f"{row['available']:,.0f}",
            f"{row['shortfall']:,.0f}", row["severity"] or "-", row["fitting_count"]]


def _contract_row(row: dict) -> list:
    return [row["contract_id"], row["hull_name"] or "-", row["source_character_name"] or "-",
            row["validation_status"], row["status"], f"{row['price']:,.0f}" if row["price"] is not None else "-"]


class StockpileStatusView(BaseView):
    title = "Doctrine - Stockpile Status"

    def __init__(self, parent=None):
        super().__init__(parent)

        self.root_layout.addWidget(self._build_filter_and_sync_box())

        self.tabs = QTabWidget()
        self.fitting_status_table = build_table(_FITTING_STATUS_COLUMNS, column_widths=_FITTING_STATUS_WIDTHS)
        self.stockpile_table = build_table(_STOCKPILE_COLUMNS, column_widths=_STOCKPILE_WIDTHS)
        self.contracts_table = build_table(_CONTRACT_COLUMNS, column_widths=_CONTRACT_WIDTHS)
        self.tabs.addTab(self.fitting_status_table, "Fitting Status")
        self.tabs.addTab(self.stockpile_table, "Stockpile Shortfalls")
        self.tabs.addTab(self.contracts_table, "Synced Contracts")
        self.root_layout.addWidget(self.tabs)

        self._add_staleness_label(["assets", "contracts"])
        self._finish_status_row()
        self._load_contracts()

    def _build_filter_and_sync_box(self) -> QGroupBox:
        box = QGroupBox("Filter")
        outer = QVBoxLayout(box)
        form = QHBoxLayout()
        form.addWidget(QLabel("Doctrine ID (blank = all):"))
        self.doctrine_filter_input = QLineEdit()
        form.addWidget(self.doctrine_filter_input)
        validate_btn = QPushButton(icons.icon("validate"), "Validate Contracts")
        validate_btn.clicked.connect(self._validate)
        form.addWidget(validate_btn)
        refresh_btn = QPushButton(icons.icon("refresh"), "Refresh Status")
        refresh_btn.clicked.connect(self._refresh_status)
        form.addWidget(refresh_btn)
        outer.addLayout(form)
        return box

    def _doctrine_filter(self) -> str | None:
        return self.doctrine_filter_input.text().strip() or None

    def _validate(self) -> None:
        self.run_action(functools.partial(doctrine_actions.do_validate_contracts), self._on_validated,
                        busy_message="Re-matching and re-validating every synced contract...")

    def _on_validated(self, result: dict) -> None:
        self.show_info(f"Revalidated {result['revalidated']:,} contract(s).")
        self._load_contracts()

    def _refresh_status(self) -> None:
        doctrine_id = self._doctrine_filter()

        def _fetch_both() -> dict:
            return {
                "doctrine_status": doctrine_actions.do_get_doctrine_status(doctrine_id),
                "stockpile_status": doctrine_actions.do_get_stockpile_status(doctrine_id),
            }

        self.run_action(_fetch_both, self._on_status_refreshed, busy_message="Computing status report...")

    def _on_status_refreshed(self, result: dict) -> None:
        doctrines = result["doctrine_status"]["doctrines"]
        fitting_rows = [_fitting_status_row(d["doctrine_name"], f) for d in doctrines for f in d["fittings"]]
        populate(self.fitting_status_table, fitting_rows)

        stockpile = result["stockpile_status"]
        populate(self.stockpile_table, [_stockpile_row(r) for r in stockpile["aggregated_rows"]],
                default_sort=_STOCKPILE_SORT)
        if not stockpile["assets_available"]:
            self.show_info("No Doctrine asset sync has ever run - stockpile figures are unavailable. "
                           "Click Sync ESI first.")
        else:
            self.show_info(f"{len(fitting_rows)} fitting(s), "
                           f"{len(stockpile['aggregated_rows'])} stockpile shortfall row(s).")

    def _load_contracts(self) -> None:
        rows = doctrine_actions.do_list_contracts()["rows"]
        doctrine_id = self._doctrine_filter()
        if doctrine_id is not None:
            # list-contracts has no doctrine filter of its own (fittings, not
            # contracts, belong to a doctrine) - filter client-side the same
            # way do_contract_history's own doctrine_id filter works.
            from ... import storage
            fitting_ids = {row[0] for row in storage.list_fittings_for_doctrine(doctrine_id)}
            rows = [r for r in rows if r["matched_fitting_id"] in fitting_ids]
        populate(self.contracts_table, [_contract_row(r) for r in rows])
        self._refresh_staleness_label()
