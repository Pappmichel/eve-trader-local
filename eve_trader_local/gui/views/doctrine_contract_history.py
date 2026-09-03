"""Doctrine's Contract History tab: `contract-history` (GitHub issue #19) on
its own - the permanent log of finished (accepted) contract sales,
independent of the live/wholesale-replaced contracts snapshot Stockpile
Status shows. A cheap local read (`storage.load_doctrine_contract_history`,
no network) so it loads on open, same convention as
production_special_orders.py's own orders list."""
from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton

from ...doctrine import actions as doctrine_actions
from .base import TableView

_COLUMNS = ["Contract ID", "Hull", "Fitting", "Character", "Price", "Buyer", "Issued", "Completed"]


def _row_to_cells(row: dict) -> list:
    hull = row["hull_name"] or row["fitting_name"] or "-"
    buyer = row["acceptor_name"] or (str(row["acceptor_id"]) if row["acceptor_id"] else "-")
    return [row["contract_id"], hull, row["fitting_name"] or "-", row["source_character_name"] or "-",
            f"{row['price']:,.0f}" if row["price"] is not None else "-", buyer,
            row["date_issued"] or "-", row["date_completed"] or "-"]


class ContractHistoryView(TableView):
    title = "Doctrine - Contract History"

    def __init__(self, parent=None):
        super().__init__(parent)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Doctrine ID (blank = all):"))
        self.doctrine_filter_input = QLineEdit()
        filter_row.addWidget(self.doctrine_filter_input)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._load_history)
        filter_row.addWidget(refresh_btn)
        filter_row.addStretch(1)
        self.root_layout.addLayout(filter_row)

        self._build_table(_COLUMNS)
        self._finish_status_row()
        self._load_history()

    def _load_history(self) -> None:
        doctrine_id = self.doctrine_filter_input.text().strip() or None
        rows = doctrine_actions.do_contract_history(doctrine_id)["rows"]
        self.populate_table([_row_to_cells(r) for r in rows])
        self.show_info(f"{len(rows)} finished contract(s)." if rows else "No finished contracts recorded yet.")
