"""Production's Current Jobs & Slots tab: `do_list_current_jobs`
(`jobs.list_current_jobs`) and `do_character_slot_overview`
(`jobs.character_slot_overview`), grouped into one tab since both are
"what's happening with my characters' industry right now" reports - the
same "small independent read-only reports" reasoning
`production_margins_market.py`'s own docstring uses for its three buttons,
except these two are both fresh ESI-reflecting reads rather than
recomputed plans, so each gets its own Refresh button and sub-tab."""
from __future__ import annotations

import functools

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTabWidget

from ...production import actions as production_actions
from .. import icons
from .base import BaseView
from .production_common import build_table, fmt_isk, populate

_JOBS_COLUMNS = ["Item", "Activity", "Runs", "Quantity", "Status", "Start", "End",
                 "Remaining", "Installer", "Output Value"]
_JOBS_WIDTHS = [220, 120, None, None, 90, 140, 140, 90, 160, None]
# jobs.list_current_jobs's own docstring: "sorted by soonest-completing
# first" - Remaining column (seconds ascending).
_JOBS_SORT = (7, Qt.SortOrder.AscendingOrder)

_SLOTS_COLUMNS = ["Character", "Job Type", "Used", "Total", "Free"]
_SLOTS_WIDTHS = [220, 140, None, None, None]


def _job_row(row) -> list:
    remaining = f"{row.remaining_seconds / 3600:.1f}h" if row.remaining_seconds is not None else None
    return [row.type_name, row.activity, row.runs, f"{row.quantity:,.0f}" if row.quantity is not None else None,
            row.status, row.start_date, row.end_date, remaining, row.installer_name, fmt_isk(row.output_value)]


def _slot_row(row) -> list:
    total = str(row.total_slots) if row.total_slots is not None else "—"
    free = str(row.free_slots) if row.free_slots is not None else "—"
    return [row.character_name, row.job_type, row.used_slots, total, free]


class JobsSlotsView(BaseView):
    title = "Production - Current Jobs && Slots"

    def __init__(self, parent=None):
        super().__init__(parent)

        toolbar = QHBoxLayout()
        jobs_btn = QPushButton(icons.icon("refresh"), "Refresh Current Jobs")
        jobs_btn.clicked.connect(self._refresh_jobs)
        toolbar.addWidget(jobs_btn)
        slots_btn = QPushButton(icons.icon("refresh"), "Refresh Character Slots")
        slots_btn.clicked.connect(self._refresh_slots)
        toolbar.addWidget(slots_btn)
        toolbar.addStretch(1)
        self.root_layout.addLayout(toolbar)

        box = QGroupBox("Manual slot totals (optional)")
        form = QHBoxLayout(box)
        form.addWidget(QLabel("Character:"))
        self.slot_character_input = QLineEdit()
        form.addWidget(self.slot_character_input)
        form.addWidget(QLabel("Mfg:"))
        self.slot_mfg_input = QLineEdit()
        self.slot_mfg_input.setMaximumWidth(60)
        form.addWidget(self.slot_mfg_input)
        form.addWidget(QLabel("Reaction:"))
        self.slot_reaction_input = QLineEdit()
        self.slot_reaction_input.setMaximumWidth(60)
        form.addWidget(self.slot_reaction_input)
        form.addWidget(QLabel("Science:"))
        self.slot_science_input = QLineEdit()
        self.slot_science_input.setMaximumWidth(60)
        form.addWidget(self.slot_science_input)
        set_btn = QPushButton(icons.icon("save"), "Set Totals")
        set_btn.clicked.connect(self._set_slot_totals)
        form.addWidget(set_btn)
        clear_btn = QPushButton(icons.icon("remove"), "Clear Totals")
        clear_btn.clicked.connect(self._clear_slot_totals)
        form.addWidget(clear_btn)
        form.addStretch(1)
        self.root_layout.addWidget(box)

        self.tabs = QTabWidget()
        self.jobs_table = build_table(_JOBS_COLUMNS, column_widths=_JOBS_WIDTHS)
        self.slots_table = build_table(_SLOTS_COLUMNS, column_widths=_SLOTS_WIDTHS)
        self.tabs.addTab(self.jobs_table, "Current Jobs")
        self.tabs.addTab(self.slots_table, "Character Slots")
        self.root_layout.addWidget(self.tabs)

        self._finish_status_row()

    def _refresh_jobs(self) -> None:
        self.run_action(functools.partial(production_actions.do_list_current_jobs), self._on_jobs,
                        busy_message="Loading current industry jobs...")

    def _on_jobs(self, result: dict) -> None:
        rows = result["rows"]
        populate(self.jobs_table, [_job_row(r) for r in rows], default_sort=_JOBS_SORT)
        self.show_info(f"{len(rows)} active job(s)." if rows else "No active industry jobs.")

    def _refresh_slots(self) -> None:
        self.run_action(functools.partial(production_actions.do_character_slot_capacity_overview), self._on_slots,
                        busy_message="Summarizing character slot usage...")

    def _on_slots(self, result: dict) -> None:
        rows = result["rows"]
        populate(self.slots_table, [_slot_row(r) for r in rows])
        self.show_info(f"{len(rows)} character/job-type row(s)." if rows else
                       "No active industry jobs or slot totals to summarize.")

    def _set_slot_totals(self) -> None:
        name = self.slot_character_input.text().strip()
        try:
            manufacturing = int(self.slot_mfg_input.text().strip())
            reaction = int(self.slot_reaction_input.text().strip())
            science = int(self.slot_science_input.text().strip())
        except ValueError:
            self.show_error("Manufacturing, reaction, and science totals must be integers.")
            return
        self.run_action(
            functools.partial(production_actions.do_set_character_job_slot_totals,
                              name, manufacturing, reaction, science),
            self._on_slot_totals_saved, busy_message="Saving slot totals...")

    def _on_slot_totals_saved(self, result: dict) -> None:
        self._refresh_slots()
        self.show_info(f"Slot totals saved for {result['character_name']}.")

    def _clear_slot_totals(self) -> None:
        name = self.slot_character_input.text().strip()
        if not name:
            self.show_error("Enter a character name first.")
            return
        self.run_action(
            functools.partial(production_actions.do_clear_character_job_slot_totals, name),
            self._on_slot_totals_cleared, busy_message="Clearing slot totals...")

    def _on_slot_totals_cleared(self, result: dict) -> None:
        self._refresh_slots()
        self.show_info(f"Cleared slot totals for {result['removed']}.")
