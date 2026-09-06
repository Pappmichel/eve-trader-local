"""Production's Build Candidates tab: `discover-build-candidates`
(`production_actions.do_discover_build_candidates`) - a single, standalone
CLI command, but expensive (a full SDE scan) so it gets its own tab rather
than being folded into one of the planner views, matching the CLI's own
framing of "discovery" as a separate, occasional step from routine planning
(see `production_planner.py`'s own docstring for that split).

No cheap local snapshot to load on open (unlike Trading's Shortlist) - there
is no `storage` table caching the last discovery run, so the table starts
empty until the user clicks Discover."""
from __future__ import annotations

import functools

from PySide6.QtCore import Qt

from ...production import actions as production_actions
from .base import TableView
from .production_common import fmt_pct

_COLUMNS = ["Item", "Activity", "Build Cost", "Margin", "Daily Movement",
            "Potential Daily Profit", "Meta Level"]
_COLUMN_WIDTHS = [200, 120, None, None, None, None, None]
# discover_build_candidates ranks its own results by potential_daily_profit
# desc (production/engine.py) - the view's own docstring/status message
# already says "ranked by potential daily profit"; default-apply that same
# order rather than inventing a new one.
_DEFAULT_SORT = (5, Qt.SortOrder.DescendingOrder)


def _row_to_cells(row) -> list:
    return [row.type_name, row.activity, f"{row.build_cost:,.2f}", fmt_pct(row.margin),
            f"{row.daily_movement:,.0f}", f"{row.potential_daily_profit:,.0f}", row.meta_level]


class BuildCandidatesView(TableView):
    title = "Production - Build Candidates"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_toolbar([
            ("Discover", self._discover, "discover", True),
        ])
        self._build_table(_COLUMNS, column_widths=_COLUMN_WIDTHS, default_sort=_DEFAULT_SORT)
        self._finish_status_row()
        self.show_info("Click Discover to scan the SDE for build-vs-buy opportunities.")

    def _discover(self) -> None:
        self.run_action(functools.partial(production_actions.do_discover_build_candidates), self._on_result,
                        busy_message="Scanning the SDE for build-vs-buy opportunities (this can take a while)...")

    def _on_result(self, result: dict) -> None:
        rows = result["rows"]
        self.populate_table([_row_to_cells(r) for r in rows])
        if rows:
            self.show_info(f"{len(rows)} candidate(s), ranked by potential daily profit.")
        else:
            self.show_info("No build candidates cleared the configured margin/profit thresholds.")
