"""The "Check for App Updates..." dialog (`main_window.py`'s App menu).

Not to be confused with `esi_update_dialog.EsiUpdateDialog` ("Update
Data..." in the same menu) - that one refreshes game data from ESI/
Goonmetrics, this one checks GitHub for a newer eve-trader-local release.
Two unrelated kinds of "update" that happen to share the word.

Branches on `paths.is_frozen()` right at construction, same signal
`updater.py`'s own Stage-1/Stage-2 split is built around:

- Frozen (the packaged .exe): Stage 2 - `updater.check_for_binary_update`/
  `download_and_apply_binary_update`, comparing this build's baked-in
  `_version.VERSION` against the latest GitHub Release tag. A successful
  download+checksum-verify hands off to a detached helper script that
  replaces the running .exe once this process exits (see that function's
  own docstring) - `_on_binary_applied` below calls `QApplication.quit()`
  immediately after, which is what lets the helper's wait-loop proceed.
- Not frozen (a source checkout): Stage 1 - the existing git-based
  `updater.check_for_update`/`apply_update`, same flow `cli.py`'s
  `cmd_update` already offers, just from the GUI. No exe file-lock problem
  here, so this path just tells the user to restart manually rather than
  auto-relaunching.

Both paths go through `workers.BusyMixin.run_action` exactly like every
other dialog/view in this app (`SettingsDialog`, `CharactersDialog`) - see
`workers.py`'s own docstring for why a bare Qt signal/slot from a worker
thread wouldn't be safe here.
"""
from __future__ import annotations

from PySide6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel,
                               QPushButton, QVBoxLayout)

from ... import updater
from ...paths import is_frozen
from ..workers import BusyMixin


class UpdateDialog(QDialog, BusyMixin):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Check for Updates")
        self.resize(480, 200)
        self._init_busy()
        self._frozen = is_frozen()

        layout = QVBoxLayout(self)

        self.info_label = QLabel("Checking for updates...")
        self.info_label.setWordWrap(True)
        layout.addWidget(self.info_label)

        button_row = QHBoxLayout()
        self.action_btn = QPushButton("Update")
        self.action_btn.setEnabled(False)
        self.action_btn.clicked.connect(self._on_action)
        button_row.addWidget(self.action_btn)
        recheck_btn = QPushButton("Check Again")
        recheck_btn.clicked.connect(self._check)
        button_row.addWidget(recheck_btn)
        button_row.addStretch(1)
        layout.addLayout(button_row)

        layout.addWidget(self.status_label)

        self._check()

    # ------------------------------------------------------------- checking
    def _check(self) -> None:
        self.action_btn.setEnabled(False)
        if self._frozen:
            self.run_action(updater.check_for_binary_update, self._on_binary_status,
                            busy_message="Checking GitHub for a newer release...")
        else:
            self.run_action(updater.check_for_update, self._on_git_status,
                            busy_message="Checking GitHub for a newer commit...")

    def _on_binary_status(self, status) -> None:
        installed = status.installed_version or "unknown"
        self.info_label.setText(f"Installed version: {installed}\n{status.summary()}")
        if status.update_available:
            self.action_btn.setText(f"Download && Install {status.latest_tag}")
            self.action_btn.setEnabled(True)

    def _on_git_status(self, status) -> None:
        self.info_label.setText(status.summary())
        if status.update_available:
            self.action_btn.setText("Update (git reset --hard + reinstall)")
            self.action_btn.setEnabled(True)

    # ------------------------------------------------------------- applying
    def _on_action(self) -> None:
        self.action_btn.setEnabled(False)
        if self._frozen:
            self._download_binary_update()
        else:
            self._apply_git_update()

    def _download_binary_update(self) -> None:
        def _fetch_and_apply():
            release = updater.latest_release()
            updater.download_and_apply_binary_update(release)
            return release

        self.run_action(_fetch_and_apply, self._on_binary_applied,
                        busy_message="Downloading and verifying the update...")

    def _on_binary_applied(self, release) -> None:
        self.show_info(f"Update {release.tag} downloaded and verified. Restarting...")
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def _apply_git_update(self) -> None:
        self.run_action(updater.apply_update, self._on_git_applied,
                        busy_message="Fetching and resetting to origin/main...")

    def _on_git_applied(self, result) -> None:
        if result.previous_sha == result.new_sha:
            self.show_info("Already up to date; nothing was changed.")
            return
        suffix = " and reinstalled dependencies." if result.dependencies_reinstalled else "."
        self.show_info(
            f"Updated {result.previous_sha[:8]} -> {result.new_sha[:8]}{suffix} "
            "Restart eve-trader-local to run the new version."
        )
