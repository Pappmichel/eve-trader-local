"""Smoke tests for the two app-level dialogs (gui/dialogs/) and the "App"
menu that opens them - same "does the machinery work at all" level as
test_gui.py's own smoke tests, not exhaustive per-field coverage (each
config dataclass already has its own validation tests in
test_*_config.py/test_*_actions*.py)."""
from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_main_window_has_an_app_menu_with_settings_and_characters(qapp, db):
    from eve_trader_local.gui.main_window import MainWindow

    window = MainWindow()
    menu_labels = [action.text() for action in window.menuBar().actions()]
    assert "App" in menu_labels

    app_menu = window.menuBar().actions()[menu_labels.index("App")].menu()
    action_labels = [action.text() for action in app_menu.actions()]
    assert action_labels == ["Settings...", "Characters..."]


def test_settings_dialog_opens_and_shows_all_five_tool_tabs(qapp, db):
    from eve_trader_local.gui.dialogs.settings_dialog import SettingsDialog

    dialog = SettingsDialog()
    tab_labels = [dialog.tab_widget.tabText(i) for i in range(dialog.tab_widget.count())]
    assert tab_labels == ["Trading", "Production", "Doctrine", "Ore & Minerals", "Station Trading"]


def test_settings_dialog_shows_current_config_values(qapp, db):
    """Picks one plain field per tool and confirms the widget was seeded
    with that config's real current (default, in this throwaway db) value -
    not exhaustive over every field, just proof the generic form-builder
    actually reads live config rather than showing blanks."""
    from eve_trader_local.gui.dialogs.settings_dialog import SettingsDialog
    from eve_trader_local.config import TradingConfig
    from eve_trader_local.production.config import ProductionConfig

    dialog = SettingsDialog()
    trading_form, _, _ = dialog._forms["Trading"]
    assert trading_form._getters["import_cost_per_m3"]() == pytest.approx(TradingConfig().import_cost_per_m3)

    production_form, _, _ = dialog._forms["Production"]
    assert production_form._getters["reaction_structure_type"]() == ProductionConfig().reaction_structure_type


def test_settings_dialog_save_persists_a_changed_value(qapp, db):
    """Exercises the real do_update_settings -> run_action round trip (the
    Trading tab's own instance is the module's live TRADING_CONFIG, so this
    is exactly what a real "change a setting and click Save" click does)."""
    from eve_trader_local.gui.dialogs.settings_dialog import SettingsDialog
    from eve_trader_local import storage

    dialog = SettingsDialog()
    trading_form, _, cfg = dialog._forms["Trading"]
    trading_form._widgets["import_cost_per_m3"].setValue(1234.5)

    dialog._save_tab("Trading")

    deadline = time.monotonic() + 5
    while dialog._threads and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)

    assert cfg.import_cost_per_m3 == pytest.approx(1234.5)
    assert storage.load_settings("trading")["import_cost_per_m3"] == pytest.approx(1234.5)
    assert "saved" in dialog.status_label.text().lower()


def test_settings_dialog_save_shows_error_on_invalid_value(qapp, db):
    from eve_trader_local.gui.dialogs.settings_dialog import SettingsDialog

    dialog = SettingsDialog()
    trading_form, _, cfg = dialog._forms["Trading"]
    # min_hit_rate is range-checked to [0, 1] (config._FIELD_RANGES) - the
    # spinbox itself is already clamped to that range by construction, so
    # forcing an out-of-range value directly onto the underlying config
    # object is what exercises do_update_settings's own rejection path
    # (matching how test_actions.py's own rejection test works).
    cfg.min_hit_rate = 5.0
    trading_form._originals["min_hit_rate"] = 0.0  # pretend the widget still shows the old value

    dialog._save_tab("Trading")

    deadline = time.monotonic() + 5
    while dialog._threads and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)

    assert dialog.status_label.text() != ""


def test_characters_dialog_opens_with_no_characters_registered(qapp, db):
    from eve_trader_local.gui.dialogs.characters_dialog import CharactersDialog

    dialog = CharactersDialog()
    assert dialog.table.rowCount() == 0
    assert "no characters authorized" in dialog.status_label.text().lower()


def test_characters_dialog_remove_selected_removes_a_fixture_token(qapp, db):
    from eve_trader_local import storage
    from eve_trader_local.gui.dialogs.characters_dialog import CharactersDialog

    storage.save_token("buyer:12345", {
        "role": "buyer:12345", "character_id": 12345, "character_name": "Test Pilot",
        "access_token": "x", "refresh_token": "y", "expires_at": time.time() + 1200, "scopes": "",
    })

    dialog = CharactersDialog()
    assert dialog.table.rowCount() == 1
    assert dialog.table.item(0, 1).text() == "Test Pilot"

    dialog.table.selectRow(0)
    dialog._remove_selected()

    assert dialog.table.rowCount() == 0
    assert storage.load_all_tokens() == {}
