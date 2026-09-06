"""Production's Owned Blueprints tab: `do_list_owned_blueprints`
(`engine.list_owned_blueprints`) - a single, standalone read of every
owned blueprint (character + corp), aggregated across identical (type_id,
is_original, ME, TE, runs) groups. Reflects the last "Sync ESI Data" run -
purely a local `storage.load_owned_blueprints()` read, no network call of
its own, so (like `production_planner.py`'s stock-target list) it's loaded
synchronously on open rather than through `run_action`; Refresh re-runs the
same cheap local read after a sync."""
from __future__ import annotations

from PySide6.QtCore import Qt

from ...production import actions as production_actions
from .base import TableView

_COLUMNS = ["Item", "Kind", "Quantity", "ME", "TE", "Runs Left"]
_COLUMN_WIDTHS = [260, 90, None, None, None, None]
# engine.list_owned_blueprints's own docstring: "rows.sort(key=lambda r:
# r.type_name)".
_DEFAULT_SORT = (0, Qt.SortOrder.AscendingOrder)


def _row_to_cells(row) -> list:
    kind = "BPO" if row.is_original else "BPC"
    runs = "-" if row.is_original else row.runs
    return [row.type_name, kind, row.quantity, row.material_efficiency, row.time_efficiency, runs]


class OwnedBlueprintsView(TableView):
    title = "Production - Owned Blueprints"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_toolbar([
            ("Refresh", self._refresh, "refresh"),
        ])
        self._build_table(_COLUMNS, column_widths=_COLUMN_WIDTHS, default_sort=_DEFAULT_SORT)
        self._finish_status_row()
        self._refresh()

    def _refresh(self) -> None:
        result = production_actions.do_list_owned_blueprints()
        rows = result["rows"]
        self.populate_table([_row_to_cells(r) for r in rows])
        self.show_info(f"{len(rows)} owned blueprint group(s)." if rows else
                       "No owned blueprints synced yet.")
