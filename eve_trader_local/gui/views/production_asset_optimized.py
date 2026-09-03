"""Production's Asset-Optimized Planner tab: `plan-asset-optimized`
(`production_actions.do_plan_asset_optimized`) - the readiness-focused
second planner (which jobs are startable right now vs. blocked upstream on
a scarcer material). Kept as its own tab rather than folded into the
regular Planner view: it answers a different question ("what can I start
this minute") from `plan_production`'s ("what do I need to buy/build in
total"), and the two never disagree on *whether* to build something (see
`engine.plan_asset_optimized`'s own docstring) - only on how netting against
owned stock is distributed across the BOM.

Same stock-target precondition as the Planner view, but stock targets are
managed there, not duplicated here - this tab is planner-output-only."""
from __future__ import annotations

import functools

from ...production import actions as production_actions
from .base import TableView
from .production_common import fmt_isk, fmt_pct

_COLUMNS = ["Item", "Activity", "Runs", "Ready Now", "Cost/Unit", "Margin",
            "Stock Coverage", "Category", "Decryptor"]


def _row_to_cells(row) -> list:
    coverage = f"{row.stock_coverage * 100:.1f}%" if row.stock_coverage is not None else None
    return [row.type_name, row.activity, row.job_runs, row.runs_ready_now, fmt_isk(row.unit_build_cost),
            fmt_pct(row.margin), coverage, row.job_category, row.decryptor]


class AssetOptimizedPlannerView(TableView):
    title = "Production - Asset-Optimized Planner"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_toolbar([
            ("Run Planner", self._run_planner),
        ])
        self._build_table(_COLUMNS)
        self._finish_status_row()
        self.show_info("Click Run Planner to see which jobs are startable right now.")

    def _run_planner(self) -> None:
        self.run_action(functools.partial(production_actions.do_plan_asset_optimized), self._on_result,
                        busy_message="Planning readiness against configured stock targets "
                                     "(this can take a while)...")

    def _on_result(self, plan: dict) -> None:
        jobs = plan["jobs"]
        self.populate_table([_row_to_cells(r) for r in jobs])
        if jobs:
            self.show_info(f"{len(jobs)} job(s) planned.")
        else:
            self.show_info("Nothing to build right now.")
