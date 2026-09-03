"""Common view shapes shared across tools - see the `views` package docstring
for what a "view" is. Two base classes cover almost everything the CLI's
commands need on screen:

- `BaseView`: a status bar (busy/error/info text) plus `run_action`, the one
  place every view calls into `actions.py` through `workers.run_action` -
  handles busy-state toggling and error display uniformly so individual
  views don't each reimplement it. The actual busy/status/run_action
  machinery lives in `workers.BusyMixin`, shared with the app-level dialogs
  (`SettingsDialog`/`CharactersDialog`, both `QDialog`s rather than
  `QWidget`s) - see that class's own docstring for why it's a plain mixin
  rather than another shared QWidget base class.
- `TableView(BaseView)`: adds a `QTableWidget` and `populate_table`, for the
  many views that are fundamentally "a table of rows plus a toolbar of
  actions" (the shortlist, build candidates, logistics rows, ...).
"""
from __future__ import annotations

from typing import Any, Callable, Optional, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QHBoxLayout, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ..workers import BusyMixin


class BaseView(QWidget, BusyMixin):
    """Subclass and set `title` (used as the tab label by MainWindow)."""

    title = "View"

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._init_busy()
        self.root_layout = QVBoxLayout(self)

    def _finish_status_row(self) -> None:
        """Call once, after subclasses have added their own widgets to
        `root_layout`, to place the status label last (errors/info always
        read at the bottom, under whatever content/toolbar the view has)."""
        self.root_layout.addWidget(self.status_label)


class TableView(BaseView):
    """A table plus an optional row of toolbar buttons above it. Subclasses
    typically call `_build_toolbar([("Refresh", self._refresh), ...])` and
    `_build_table([...column headers...])` in `__init__`, then use
    `populate_table` inside their own success handlers."""

    def _build_toolbar(self, buttons: Sequence[tuple[str, Callable[[], None]]]) -> None:
        row = QHBoxLayout()
        for label, handler in buttons:
            btn = QPushButton(label)
            btn.clicked.connect(handler)
            row.addWidget(btn)
        row.addStretch(1)
        self.root_layout.addLayout(row)

    def _build_table(self, headers: Sequence[str]) -> None:
        self.table = QTableWidget(0, len(headers))
        self.table.setHorizontalHeaderLabels(list(headers))
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.root_layout.addWidget(self.table)

    def populate_table(self, rows: Sequence[Sequence[Any]]) -> None:
        """`rows` is a sequence of already-formatted-for-display cell-value
        sequences, one per row, matching the column count passed to
        `_build_table`. Formatting (rounding a float, turning None into "-")
        is each view's own job - this just renders whatever it's handed."""
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for col_index, value in enumerate(row):
                item = QTableWidgetItem("" if value is None else str(value))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row_index, col_index, item)
        self.table.setSortingEnabled(True)
