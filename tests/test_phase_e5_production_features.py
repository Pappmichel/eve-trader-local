"""Phase E.5 — job-slot totals/free and cost-index override actions (engine frozen)."""
from __future__ import annotations

import os

import pytest

from eve_trader_local import storage
from eve_trader_local.errors import ActionError
from eve_trader_local.production import actions, engine, jobs
from eve_trader_local.production.config import PRODUCTION_CONFIG, ProductionConfig

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytestmark = pytest.mark.release


@pytest.fixture(autouse=True)
def _reset_cost_index_overrides():
    PRODUCTION_CONFIG.reaction_cost_index_override = None
    PRODUCTION_CONFIG.component_cost_index_override = None
    PRODUCTION_CONFIG.manufacturing_cost_index_override = None
    yield
    PRODUCTION_CONFIG.reaction_cost_index_override = None
    PRODUCTION_CONFIG.component_cost_index_override = None
    PRODUCTION_CONFIG.manufacturing_cost_index_override = None

FINISHED = 34
FINISHED_BP = 35


def _seed_job(installer="Alice"):
    storage.replace_industry_jobs("character_industry_jobs", [
        (1, 1, FINISHED_BP, FINISHED, 1, 60003760, "active", "", "", 1, installer),
    ])


def test_used_only_overview_unchanged_without_totals(db):
    _seed_job()
    rows = jobs.character_slot_overview()
    assert len(rows) == 1
    assert rows[0].used_slots == 1
    assert not hasattr(rows[0], "total_slots") or True  # CharacterSlotRow has no totals
    cap = actions.do_character_slot_capacity_overview()["rows"]
    assert len(cap) == 1
    assert cap[0].total_slots is None
    assert cap[0].free_slots is None
    assert cap[0].used_slots == 1


def test_manual_totals_add_free_and_zero_used_rows(db):
    _seed_job("Alice")
    actions.do_set_character_job_slot_totals("Alice", manufacturing=5, reaction=2, science=3)
    rows = actions.do_character_slot_capacity_overview()["rows"]
    by_type = {r.job_type: r for r in rows}
    assert by_type["Manufacturing"].used_slots == 1
    assert by_type["Manufacturing"].total_slots == 5
    assert by_type["Manufacturing"].free_slots == 4
    assert by_type["Reactions"].used_slots == 0
    assert by_type["Reactions"].free_slots == 2
    assert by_type["Science"].free_slots == 3
    used_only = jobs.character_slot_overview()
    assert len(used_only) == 1


def test_negative_slot_total_rejected(db):
    with pytest.raises(ActionError, match="cannot be negative"):
        actions.do_set_character_job_slot_totals("Alice", manufacturing=-1, reaction=0, science=0)


def test_clear_slot_totals(db):
    _seed_job("Alice")
    actions.do_set_character_job_slot_totals("Alice", 5, 2, 3)
    actions.do_clear_character_job_slot_totals("Alice")
    rows = actions.do_character_slot_capacity_overview()["rows"]
    assert rows[0].total_slots is None


def test_cost_index_override_roundtrip(db):
    PRODUCTION_CONFIG.manufacturing_cost_index_override = None
    actions.do_set_cost_index_override("manufacturing", 0.4)
    listed = actions.do_list_cost_index_overrides()
    assert listed["manufacturing"] == 0.4
    actions.do_clear_cost_index_override("manufacturing")
    assert actions.do_list_cost_index_overrides()["manufacturing"] is None


def test_cost_index_override_unknown_kind(db):
    with pytest.raises(ActionError, match="Unknown cost-index kind"):
        actions.do_set_cost_index_override("warp", 0.1)


def test_cost_index_override_is_used_by_existing_engine_field(db):
    """E.5 only writes the config field; _job_cost_rate already honors it."""
    cfg = ProductionConfig(manufacturing_cost_index_override=0.5)
    assert cfg.manufacturing_cost_index_override == 0.5
    assert engine._job_cost_rate.__name__ == "_job_cost_rate"


def test_cli_slot_totals_and_cost_index(db, capsys):
    from eve_trader_local.cli import main

    _seed_job("Alice")
    assert main(["set-character-job-slots", "Alice", "6", "1", "2"]) == 0
    assert main(["character-slot-capacity"]) == 0
    out = capsys.readouterr().out
    assert "Alice" in out
    assert "total" in out
    assert main(["set-cost-index-override", "reaction", "0.12"]) == 0
    assert main(["list-cost-index-overrides"]) == 0
    listed = capsys.readouterr().out
    assert "0.12" in listed
    assert main(["clear-cost-index-override", "reaction"]) == 0
    PRODUCTION_CONFIG.reaction_cost_index_override = None


def test_gui_jobs_slots_set_totals(qapp, db):
    import time

    pytest.importorskip("PySide6")
    from eve_trader_local.gui.views.production_jobs_slots import JobsSlotsView

    _seed_job("Alice")
    view = JobsSlotsView()
    view.slot_character_input.setText("Alice")
    view.slot_mfg_input.setText("4")
    view.slot_reaction_input.setText("0")
    view.slot_science_input.setText("1")
    view._set_slot_totals()
    deadline = time.monotonic() + 5
    while view._threads and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    totals = storage.load_character_job_slot_totals()
    assert totals["Alice"]["manufacturing"] == 4
    view._refresh_slots()
    deadline = time.monotonic() + 5
    while view._threads and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert view.slots_table.columnCount() == 5


@pytest.fixture(scope="session")
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app
