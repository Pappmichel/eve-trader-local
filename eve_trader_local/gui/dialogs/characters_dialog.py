"""The Characters dialog: lists every currently-authorized character
(`auth.TokenManager.list_records`, same data `cli.py`'s `cmd_whoami` prints),
lets the user start a new SSO login for any of the five roles this app uses,
and remove a registered character.

Login (`TokenManager.login`) is a genuinely blocking call - it opens the
system browser and waits (up to `auth.LOGIN_TIMEOUT_SECONDS`) for the
loopback OAuth callback - so it always runs through `workers.BusyMixin.
run_action`, never directly on the UI thread. The busy message says as much,
since unlike every other `run_action` call in this app, the "loading" isn't
this process doing work - it's waiting on the user to finish an out-of-band
step in their browser.

Role -> scopes mapping mirrors `cli.py`'s `cmd_auth` exactly (see that
function's own comment for why the scopes differ by role): "buyer"/"seller"
pass `scopes=None` (TokenManager.login then falls back to OAuthConfig's own
default scopes), the other three pass their tool's own scopes list.
"""
from __future__ import annotations

import functools
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QHeaderView,
                               QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout)

from ...auth import TokenManager
from ...doctrine import esi_sync as doctrine_esi_sync
from ...production import esi_sync as production_esi_sync
from ...station_trading import esi_sync as station_trading_esi_sync
from ..workers import BusyMixin

# (dropdown label, role prefix, scopes) - scopes=None means "use OAuthConfig's
# own default list", exactly like cli.py's cmd_auth for buyer/seller.
_LOGIN_ROLES: list[tuple[str, str, list | None]] = [
    ("Buyer (Trading)", "buyer", None),
    ("Seller (Trading / Ore & Minerals)", "seller", None),
    ("Producer (Production)", production_esi_sync.PRODUCER_ROLE_PREFIX, production_esi_sync.PRODUCTION_SCOPES),
    ("Doctrine", doctrine_esi_sync.DOCTRINE_ROLE_PREFIX, doctrine_esi_sync.DOCTRINE_SCOPES),
    ("Trader (Station Trading)", station_trading_esi_sync.STATION_TRADING_ROLE_PREFIX,
     station_trading_esi_sync.STATION_TRADING_SCOPES),
]

_COLUMNS = ["Role", "Character", "Character ID", "Token"]


class CharactersDialog(QDialog, BusyMixin):
    """See module docstring. Mixes in `workers.BusyMixin` directly rather
    than subclassing `views.base.BaseView` for the same reason
    `settings_dialog.SettingsDialog` does - a `QDialog` can't also inherit a
    `QWidget`-based common base."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Characters")
        self.resize(680, 420)
        self._init_busy()
        self._token_manager = TokenManager()

        layout = QVBoxLayout(self)

        self.table = QTableWidget(0, len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)

        login_row = QHBoxLayout()
        self.role_combo = QComboBox()
        self.role_combo.addItems([label for label, _, _ in _LOGIN_ROLES])
        login_row.addWidget(self.role_combo)
        login_btn = QPushButton("Log In...")
        login_btn.clicked.connect(self._start_login)
        login_row.addWidget(login_btn)
        login_row.addStretch(1)
        remove_btn = QPushButton("Remove Selected")
        remove_btn.clicked.connect(self._remove_selected)
        login_row.addWidget(remove_btn)
        layout.addLayout(login_row)

        layout.addWidget(self.status_label)

        self._refresh_table()

    def _refresh_table(self) -> None:
        records = self._token_manager.list_records()
        self.table.setRowCount(len(records))
        for row, record in enumerate(records):
            remaining = record.expires_at - time.time()
            state = "expired" if record.is_expired() else f"valid for {int(remaining // 60)}m"
            for col, text in enumerate([record.role, record.character_name, str(record.character_id), state]):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, col, item)
        if not records:
            self.show_info("No characters authorized yet - pick a role above and click Log In.")

    def _start_login(self) -> None:
        index = self.role_combo.currentIndex()
        label, role_prefix, scopes = _LOGIN_ROLES[index]
        self.run_action(
            functools.partial(self._token_manager.login, role_prefix, scopes),
            functools.partial(self._on_logged_in, label),
            busy_message=f"Opening your browser to log in as {label} - complete the EVE SSO "
                         "login there, this dialog will update once it's done...")

    def _on_logged_in(self, label: str, record) -> None:
        self.show_info(f"Authorized {record.character_name} ({record.character_id}) as '{record.role}'.")
        self._refresh_table()

    def _remove_selected(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            self.show_error("Select a character row to remove first.")
            return
        role = self.table.item(row, 0).text()
        character_name = self.table.item(row, 1).text()
        self._token_manager.remove_token(role)
        self._refresh_table()
        self.show_info(f"Removed {character_name} ('{role}').")
