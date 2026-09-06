"""The "Update Data" dialog - the single place in this app allowed to
trigger an ESI/Goonmetrics network call for game data (see esi_update.py's
own module docstring for the full rationale). One row per
esi_update.SCOPES entry: a checkbox, its label, when it was last synced,
and when it's next allowed to sync again - jEveAssets' own "pick what to
update, see the cache timer" model.

A row's checkbox is disabled (and unchecked) while its cache window hasn't
elapsed yet, the same reasoning ESI's own per-endpoint cache header exists
for: re-querying before then would just re-read the same data. "Refresh"
just recomputes the table (the countdown column) with no network call of
its own; "Update Selected" is the one action in this dialog that actually
runs a sync.

Not to be confused with `update_dialog.UpdateDialog`, the app's own
self-updater (checks GitHub for a newer eve-trader-local release, see that
module's docstring) - two unrelated kinds of "update" that happen to share
the word. That dialog is reachable from the App menu as "Check for App
Updates...", this one as "Update Data...".
"""
from __future__ import annotations

import datetime as dt
import functools

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QHeaderView, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout)

from ... import esi_update
from ..workers import BusyMixin

_COLUMNS = ["Update", "Scope", "Last Synced", "Next Update Available"]


def _format_timestamp(value: dt.datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value is not None else "never"


def _format_countdown(next_allowed_at: dt.datetime | None, due: bool) -> str:
    if due:
        return "now"
    remaining = max(dt.timedelta(0), next_allowed_at - dt.datetime.utcnow())
    total_seconds = int(remaining.total_seconds())
    return f"in {total_seconds // 60}m {total_seconds % 60}s"


class EsiUpdateDialog(QDialog, BusyMixin):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Update Data")
        self.resize(560, 300)
        self._init_busy()

        layout = QVBoxLayout(self)

        self.table = QTableWidget(0, len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table)

        button_row = QHBoxLayout()
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._refresh_table)
        button_row.addWidget(refresh_btn)
        button_row.addStretch(1)
        update_btn = QPushButton("Update Selected")
        update_btn.clicked.connect(self._update_selected)
        button_row.addWidget(update_btn)
        layout.addLayout(button_row)

        layout.addWidget(self.status_label)

        self._refresh_table()

    def _refresh_table(self) -> None:
        statuses = esi_update.all_status()
        self.table.setRowCount(len(statuses))
        for row, status in enumerate(statuses):
            check_item = QTableWidgetItem()
            check_item.setFlags(
                (Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
                if status.due else Qt.ItemFlag.NoItemFlags
            )
            check_item.setCheckState(Qt.CheckState.Checked if status.due else Qt.CheckState.Unchecked)
            check_item.setData(Qt.ItemDataRole.UserRole, status.key)
            self.table.setItem(row, 0, check_item)

            for col, text in enumerate([
                status.label, _format_timestamp(status.last_synced_at),
                _format_countdown(status.next_allowed_at, status.due),
            ], start=1):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, col, item)

    def _selected_keys(self) -> list[str]:
        keys = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                keys.append(item.data(Qt.ItemDataRole.UserRole))
        return keys

    def _update_selected(self) -> None:
        keys = self._selected_keys()
        if not keys:
            self.show_error("Nothing selected - every scope is either unchecked or not due yet.")
            return
        self.run_action(
            functools.partial(esi_update.run_selected, keys), self._on_updated,
            busy_message=f"Updating {len(keys)} scope(s) - this can take a while...")

    def _on_updated(self, results: dict) -> None:
        errors = {key: r["error"] for key, r in results.items() if isinstance(r, dict) and "error" in r}
        self._refresh_table()
        if errors:
            self.show_error("; ".join(f"{key}: {message}" for key, message in errors.items()))
        else:
            self.show_info(f"Updated: {', '.join(results.keys())}.")
