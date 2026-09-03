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
from PySide6.QtWidgets import (QHBoxLayout, QHeaderView, QPushButton, QTableWidget,
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


class _SortableTableWidgetItem(QTableWidgetItem):
    """A `QTableWidgetItem` whose `<` comparison parses its own text as a
    number (stripping thousands-separator commas and a trailing '%') when
    both sides parse cleanly, falling back to plain string comparison
    otherwise. Plain `QTableWidgetItem` always compares its display text
    lexicographically - fine for a genuinely textual column (Item/Category/
    Decision) but wrong for a formatted numeric one ("1,234" sorts before
    "999" as text, "9.0%" sorts after "80.0%") - this is what makes
    `table.sortByColumn`/a header click order a Margin/ISK/quantity column
    by its real value instead of by string prefix, for every view's own
    `_build_table`-driven `default_sort` and any later manual header click
    alike."""

    @staticmethod
    def _numeric(text: str) -> Optional[float]:
        stripped = text.strip().rstrip("%").replace(",", "")
        try:
            return float(stripped)
        except ValueError:
            return None

    def __lt__(self, other: Any) -> bool:  # noqa: D105 - Qt's own comparison hook
        # Deliberately does NOT call super().__lt__(other) - re-entering
        # PySide's own operator resolution from inside an overridden __lt__
        # on a QTableWidgetItem subclass recurses back into this very method
        # (a known PySide binding quirk, confirmed live: it stack-overflows
        # the interpreter on the very first sort). Comparing `.text()`
        # directly is exactly what the base implementation does anyway for
        # two plain-text items, so nothing is lost by doing it ourselves.
        other_text = other.text() if isinstance(other, QTableWidgetItem) else str(other)
        mine, theirs = self._numeric(self.text()), self._numeric(other_text)
        if mine is not None and theirs is not None:
            return mine < theirs
        return self.text() < other_text


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

    def _build_table(self, headers: Sequence[str],
                     column_widths: Optional[Sequence[Optional[int]]] = None,
                     default_sort: Optional[tuple[int, Qt.SortOrder]] = None) -> None:
        """`column_widths`, if given, is one entry per header - an int pixel
        width for a column that holds a long text value (an item name, a
        category/decision/status string) and needs more room than Qt's
        uniform default, or `None` to leave that column at the default
        (numeric/short columns generally don't need an explicit width).
        `default_sort`, if given, is `(column_index, Qt.SortOrder)` applied
        once after the table is next populated - see `populate_table`. It
        should match the order the underlying `do_*`/storage call already
        returns its rows in (check that call's own docstring/query), not an
        arbitrary new ranking - see each view module's own comment for its
        reasoning."""
        self.table = QTableWidget(0, len(headers))
        self.table.setHorizontalHeaderLabels(list(headers))
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        if column_widths:
            for index, width in enumerate(column_widths):
                if width is not None:
                    self.table.horizontalHeader().setSectionResizeMode(index, QHeaderView.ResizeMode.Interactive)
                    self.table.setColumnWidth(index, width)
        self._default_sort = default_sort
        self.root_layout.addWidget(self.table)

    def populate_table(self, rows: Sequence[Sequence[Any]]) -> None:
        """`rows` is a sequence of already-formatted-for-display cell-value
        sequences, one per row, matching the column count passed to
        `_build_table`. Formatting (rounding a float, turning None into "-")
        is each view's own job - this just renders whatever it's handed.
        Applies `_build_table`'s own `default_sort` (if any) once the rows are
        in, so a freshly opened/refreshed view already reads in the most
        useful order before the user manually clicks a header."""
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for col_index, value in enumerate(row):
                item = _SortableTableWidgetItem("" if value is None else str(value))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row_index, col_index, item)
        self.table.setSortingEnabled(True)
        if getattr(self, "_default_sort", None) is not None:
            column, order = self._default_sort
            self.table.sortByColumn(column, order)
