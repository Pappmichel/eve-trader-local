"""Small helpers shared by more than one Production view - formatting
functions matching trading_shortlist.py's own `_fmt_pct`/`_fmt_isk` shape,
plus a `QTableWidget` builder/populate pair for views that need more than
one table on screen at once (TableView's own `_build_table`/`populate_table`
only manage a single `self.table`, which doesn't fit views like the
Production Planner that show inventory/build/buy/invention tables side by
side)."""
from __future__ import annotations

from typing import Any, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTableWidget, QTableWidgetItem


def fmt_pct(value) -> str | None:
    return f"{value * 100:.1f}%" if value is not None else None


def fmt_isk(value) -> str | None:
    return f"{value:,.2f}" if value is not None else None


def build_table(headers: Sequence[str]) -> QTableWidget:
    """Same configuration as `views.base.TableView._build_table`, but
    returns a standalone table rather than assigning to `self.table` - for
    views that need several tables at once."""
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(list(headers))
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    table.setSortingEnabled(True)
    table.horizontalHeader().setStretchLastSection(True)
    return table


def populate(table: QTableWidget, rows: Sequence[Sequence[Any]]) -> None:
    """Same behavior as `views.base.TableView.populate_table`, parameterized
    over which table to fill."""
    table.setSortingEnabled(False)
    table.setRowCount(len(rows))
    for row_index, row in enumerate(rows):
        for col_index, value in enumerate(row):
            item = QTableWidgetItem("" if value is None else str(value))
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(row_index, col_index, item)
    table.setSortingEnabled(True)
