"""Small helpers shared by more than one Production view - formatting
functions matching trading_shortlist.py's own `_fmt_pct`/`_fmt_isk` shape,
plus a `QTableWidget` builder/populate pair for views that need more than
one table on screen at once (TableView's own `_build_table`/`populate_table`
only manage a single `self.table`, which doesn't fit views like the
Production Planner that show inventory/build/buy/invention tables side by
side)."""
from __future__ import annotations

from typing import Any, Optional, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHeaderView, QTableWidget

from .base import _SortableTableWidgetItem


def fmt_pct(value) -> str | None:
    return f"{value * 100:.1f}%" if value is not None else None


def fmt_isk(value) -> str | None:
    return f"{value:,.2f}" if value is not None else None


def build_table(headers: Sequence[str],
                column_widths: Optional[Sequence[Optional[int]]] = None) -> QTableWidget:
    """Same configuration as `views.base.TableView._build_table`, but
    returns a standalone table rather than assigning to `self.table` - for
    views that need several tables at once. `column_widths` is the same
    optional per-column pixel-width list `_build_table` takes - see its own
    docstring."""
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(list(headers))
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    table.setSortingEnabled(True)
    table.horizontalHeader().setStretchLastSection(True)
    if column_widths:
        for index, width in enumerate(column_widths):
            if width is not None:
                table.horizontalHeader().setSectionResizeMode(index, QHeaderView.ResizeMode.Interactive)
                table.setColumnWidth(index, width)
    return table


def populate(table: QTableWidget, rows: Sequence[Sequence[Any]],
            default_sort: Optional[tuple[int, Qt.SortOrder]] = None) -> None:
    """Same behavior as `views.base.TableView.populate_table`, parameterized
    over which table to fill. `default_sort`, if given, is applied once after
    the rows are in - same `(column_index, Qt.SortOrder)` shape and
    "match the data's own already-designed order" reasoning as
    `views.base.TableView._build_table`'s own `default_sort` parameter
    (this helper has no persistent `_build_table`-style state to remember it
    across calls, so callers pass it directly to each `populate` call that
    should apply it - typically every call for a table whose data has one
    genuinely "right" default order, e.g. the Planner's Build List)."""
    table.setSortingEnabled(False)
    table.setRowCount(len(rows))
    for row_index, row in enumerate(rows):
        for col_index, value in enumerate(row):
            item = _SortableTableWidgetItem("" if value is None else str(value))
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(row_index, col_index, item)
    table.setSortingEnabled(True)
    if default_sort is not None:
        column, order = default_sort
        table.sortByColumn(column, order)
