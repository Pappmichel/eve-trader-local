""""Refresh Static Data..." dialog - the GUI's only way to populate the SDE
cache (`sde.refresh_sde()`), which every production/refining/candidate-
discovery action needs before it can resolve type/group/material info.

This used to be CLI-only (`eve-trader-local refresh-sde`) - fine for a dev
checkout, useless for the packaged .exe, which ships no command line at
all. A first-run user hit a dead-end error message ("run: eve-trader-local
refresh-sde") with no way to actually run it.

Deliberately its own dialog rather than folded into `EsiUpdateDialog`: the
SDE is Fuzzwork's public static data export, not an ESI/Goonmetrics call
against the user's character - the "every ESI/Goonmetrics call goes through
one dialog" rule (see esi_update.py's docstring) is about live game-state
data, not this."""
from __future__ import annotations

from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from ... import sde, storage
from ...production import engine as production_engine
from .. import icons, theme
from ..workers import BusyMixin


def _format_last_refreshed() -> str:
    state = storage.get_sde_refresh_state()
    if state is None:
        return "Never refreshed."
    refreshed_at, _etag = state
    return f"Last refreshed: {refreshed_at}"


class SdeRefreshDialog(QDialog, BusyMixin):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Refresh Static Data (SDE)")
        self.resize(420, 140)
        self._init_busy()

        layout = QVBoxLayout(self)
        theme.apply_layout_rhythm(layout)
        layout.addWidget(QLabel(
            "Downloads the EVE Static Data Export (type/group/material info) "
            "from Fuzzwork. Needed once before Production, Refining, or "
            "Candidate Discovery can resolve items - and again whenever a "
            "newer dump is published."))

        self.last_refreshed_label = QLabel(_format_last_refreshed())
        layout.addWidget(self.last_refreshed_label)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        refresh_btn = QPushButton(icons.icon("refresh-sde", color="#06222b"), "Refresh Static Data")
        refresh_btn.setProperty("cssClass", "primary")
        refresh_btn.clicked.connect(self._refresh)
        button_row.addWidget(refresh_btn)
        layout.addLayout(button_row)

        layout.addWidget(self.status_row)

    def _refresh(self) -> None:
        self.run_action(sde.refresh_sde, self._on_refreshed,
                         busy_message="Downloading the SDE from Fuzzwork - this takes a minute...")

    def _on_refreshed(self, counts: dict) -> None:
        # A fresh SDE changes discover_build_candidates' whole result set -
        # don't leave the cached scan serving the previous SDE's numbers.
        production_engine.invalidate_discover_cache()
        self.last_refreshed_label.setText(_format_last_refreshed())
        total = sum(counts.values())
        self.show_info(f"SDE refreshed: {total:,} rows across {len(counts)} tables.")
