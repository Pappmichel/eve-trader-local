"""Common view shapes shared across tools - see the `views` package docstring
for what a "view" is. Two base classes cover almost everything the CLI's
commands need on screen:

- `BaseView`: a status bar (busy/error/info text) plus `run_action`, the one
  place every view calls into `actions.py` through `workers.run_action` -
  handles busy-state toggling and error display uniformly so individual
  views don't each reimplement it.
- `TableView(BaseView)`: adds a `QTableWidget` and `populate_table`, for the
  many views that are fundamentally "a table of rows plus a toolbar of
  actions" (the shortlist, build candidates, logistics rows, ...).
"""
from __future__ import annotations

from typing import Any, Callable, Optional, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ..workers import run_action


class BaseView(QWidget):
    """Subclass and set `title` (used as the tab label by MainWindow)."""

    title = "View"

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._threads: list = []  # keeps QThreads alive until they finish - see workers.run_action
        self.root_layout = QVBoxLayout(self)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)

    def _finish_status_row(self) -> None:
        """Call once, after subclasses have added their own widgets to
        `root_layout`, to place the status label last (errors/info always
        read at the bottom, under whatever content/toolbar the view has)."""
        self.root_layout.addWidget(self.status_label)

    def set_busy(self, busy: bool, message: str = "Wird geladen...") -> None:
        self.setEnabled(not busy)
        if busy:
            self.status_label.setStyleSheet("")
            self.status_label.setText(message)

    def show_error(self, message: str) -> None:
        self.status_label.setStyleSheet("color: #c0392b;")
        self.status_label.setText(message)

    def show_info(self, message: str) -> None:
        self.status_label.setStyleSheet("color: #27632a;")
        self.status_label.setText(message)

    def run_action(self, fn: Callable[[], Any], on_success: Callable[[Any], None],
                   busy_message: str = "Wird geladen...") -> None:
        """The one call every view makes to reach `actions.py`. Runs `fn` (a
        zero-arg callable, typically `functools.partial(do_thing, ...)`) on a
        worker thread; on success calls `on_success(result)` after clearing
        the busy state, on an ActionError shows its message in the status
        label. The view is disabled while busy so a second click can't fire
        the same action twice concurrently."""
        self.set_busy(True, busy_message)

        def _on_success(result):
            self.set_busy(False)
            on_success(result)

        def _on_failure(message):
            self.set_busy(False)
            self.show_error(message)

        thread = run_action(self, fn, _on_success, _on_failure)
        self._threads.append(thread)
        # Plain-lambda receiver, same caveat `workers._ResultBridge` explains
        # at length: Qt can't auto-queue onto a thread a non-QObject slot has
        # no affinity for, so forcing QueuedConnection here would silently
        # never fire. Left on the default connection - only Python-list
        # bookkeeping, not a widget touch, so it's harmless even if Qt ever
        # runs it off the UI thread.
        thread.finished.connect(lambda: self._threads.remove(thread) if thread in self._threads else None)


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
