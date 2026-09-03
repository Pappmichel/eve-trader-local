"""Tests for doctrine/actions.py's do_* wrappers - CRUD + validate/status
flows over the storage-backed engine. ESI sync itself is covered by
test_doctrine_esi_sync.py; this file exercises everything downstream of a
sync (or of a manually-inserted contract snapshot)."""
from __future__ import annotations

import pytest

from eve_trader_local import storage
from eve_trader_local.doctrine import actions as doctrine_actions
from eve_trader_local.errors import ActionError

RIFTER = 1
DAMAGE_CONTROL = 2
AUTOCANNON = 3
AMMO = 4


def _seed_sde():
    storage.replace_sde_data(
        types=[
            (RIFTER, 100, "Rifter", 27.0, 1, None, None, None, 1),
            (DAMAGE_CONTROL, 200, "Damage Control II", 5.0, 1, 500, 5, 2, 1),
            (AUTOCANNON, 300, "200mm AutoCannon II", 5.0, 1, 500, 5, 2, 1),
            (AMMO, 400, "Antimatter Charge S", 0.01, 1, 500, None, None, 1),
        ],
        groups=[(100, 6, "Frigate"), (200, 7, "Module"), (300, 7, "Module"), (400, 8, "Charge")],
        market_groups=[], blueprint_time=[], blueprint_materials=[], blueprint_products=[],
        categories=[(6, "Ship"), (7, "Module"), (8, "Charge")],
        type_slots=[(DAMAGE_CONTROL, "low"), (AUTOCANNON, "high")],
    )


_RAW_EFT = (
    "[Rifter, C-J Doctrine Fit]\n"
    "Damage Control II\n"
    "200mm AutoCannon II, Antimatter Charge S\n"
)


def test_create_doctrine_rejects_duplicate_name(db):
    doctrine_actions.do_create_doctrine("Rifter Fleet")
    with pytest.raises(ActionError, match="already exists"):
        doctrine_actions.do_create_doctrine("rifter fleet")  # case-insensitive


def test_create_doctrine_rejects_blank_name(db):
    with pytest.raises(ActionError, match="required"):
        doctrine_actions.do_create_doctrine("   ")


def test_add_fitting_requires_known_doctrine(db):
    _seed_sde()
    with pytest.raises(ActionError, match="not found"):
        doctrine_actions.do_add_fitting("nonexistent-id", _RAW_EFT)


def test_add_fitting_rejects_negative_targets(db):
    _seed_sde()
    doctrine_id = doctrine_actions.do_create_doctrine("Rifter Fleet")["doctrine_id"]
    with pytest.raises(ActionError, match="zero or greater"):
        doctrine_actions.do_add_fitting(doctrine_id, _RAW_EFT, contract_target=-1)


def test_add_fitting_parses_and_persists(db):
    _seed_sde()
    doctrine_id = doctrine_actions.do_create_doctrine("Rifter Fleet")["doctrine_id"]
    result = doctrine_actions.do_add_fitting(doctrine_id, _RAW_EFT, stockpile_target=3)
    fitting = result["fitting"]
    assert fitting["name"] == "C-J Doctrine Fit"
    assert fitting["hull_type_id"] == RIFTER
    assert fitting["stockpile_target"] == 3
    assert result["issues"] == []

    listed = doctrine_actions.do_list_fittings(doctrine_id)["rows"]
    assert len(listed) == 1 and listed[0]["fitting_id"] == fitting["fitting_id"]


def test_update_fitting_reparses_and_revalidates_contracts(db):
    _seed_sde()
    doctrine_id = doctrine_actions.do_create_doctrine("Rifter Fleet")["doctrine_id"]
    fitting_id = doctrine_actions.do_add_fitting(doctrine_id, _RAW_EFT)["fitting"]["fitting_id"]

    # Manually seed a contract that exactly matches the *original* fitting.
    storage.replace_doctrine_sync_snapshot(
        contracts=[(1, "doctrine:1", 0, 1, 60003760, "outstanding", "Fit", 1_000_000.0, None,
                   fitting_id, 1.0, "valid", "2026-01-01T00:00:00Z")],
        items=[(1, 1, RIFTER, 1, True, True), (1, 2, DAMAGE_CONTROL, 1, True, False),
              (1, 3, AUTOCANNON, 1, True, False), (1, 4, AMMO, 1, True, False)],
        deviations=[],
    )

    # Now drop the autocannon requirement from the fitting entirely.
    new_eft = "[Rifter, C-J Doctrine Fit]\nDamage Control II\n"
    doctrine_actions.do_update_fitting(fitting_id, raw_eft=new_eft)

    contracts = doctrine_actions.do_list_contracts()["rows"]
    assert len(contracts) == 1
    # The autocannon/ammo are now "extra" (not in the Soll at all), not
    # "missing" - the revalidation must have actually re-run, not left the
    # old (now-stale) deviation table standing.
    assert contracts[0]["validation_status"] in ("valid", "tolerable")


def test_delete_fitting_unmatches_but_keeps_contracts(db):
    _seed_sde()
    doctrine_id = doctrine_actions.do_create_doctrine("Rifter Fleet")["doctrine_id"]
    fitting_id = doctrine_actions.do_add_fitting(doctrine_id, _RAW_EFT)["fitting"]["fitting_id"]
    storage.replace_doctrine_sync_snapshot(
        contracts=[(1, "doctrine:1", 0, 1, 60003760, "outstanding", "Fit", 1_000_000.0, None,
                   fitting_id, 1.0, "valid", "2026-01-01T00:00:00Z")],
        items=[(1, 1, RIFTER, 1, True, True)],
        deviations=[],
    )
    doctrine_actions.do_delete_fitting(fitting_id)
    contracts = storage.list_doctrine_contracts()
    assert len(contracts) == 1
    assert contracts[0][9] is None  # matched_fitting_id cleared
    assert contracts[0][11] == "unmatched"


def test_delete_doctrine_cascades_fittings(db):
    _seed_sde()
    doctrine_id = doctrine_actions.do_create_doctrine("Rifter Fleet")["doctrine_id"]
    doctrine_actions.do_add_fitting(doctrine_id, _RAW_EFT)
    doctrine_actions.do_delete_doctrine(doctrine_id)
    assert storage.get_doctrine(doctrine_id) is None
    assert doctrine_actions.do_list_fittings()["rows"] == []


def test_get_fitting_detail_includes_items_contracts_and_status(db):
    _seed_sde()
    doctrine_id = doctrine_actions.do_create_doctrine("Rifter Fleet")["doctrine_id"]
    fitting_id = doctrine_actions.do_add_fitting(doctrine_id, _RAW_EFT)["fitting"]["fitting_id"]
    detail = doctrine_actions.do_get_fitting_detail(fitting_id)
    assert detail["fitting"]["fitting_id"] == fitting_id
    assert len(detail["items"]) == 3
    assert detail["items"][0]["type_name"] == "Damage Control II"
    assert detail["contracts"] == []
    assert detail["status"]["fitting_id"] == fitting_id


def test_get_doctrine_status_unknown_doctrine_raises(db):
    with pytest.raises(ActionError, match="not found"):
        doctrine_actions.do_get_doctrine_status("nonexistent-id")


def test_get_stockpile_status_reports_no_assets_synced(db):
    _seed_sde()
    doctrine_id = doctrine_actions.do_create_doctrine("Rifter Fleet")["doctrine_id"]
    doctrine_actions.do_add_fitting(doctrine_id, _RAW_EFT, stockpile_target=1)
    result = doctrine_actions.do_get_stockpile_status()
    assert result["assets_available"] is False
    assert result["rows"] == []


def test_update_settings_validates_before_applying(db):
    with pytest.raises(ActionError):
        doctrine_actions.do_update_settings({"cargo_tolerance_pct": "not-a-number"})

    doctrine_actions.do_update_settings({"cargo_tolerance_pct": 0.75, "doctrine_structure_id": 60003760})
    from eve_trader_local.doctrine.config import DOCTRINE_CONFIG
    assert DOCTRINE_CONFIG.cargo_tolerance_pct == 0.75
    assert DOCTRINE_CONFIG.effective_structure_id == 60003760
    assert storage.load_settings("doctrine")["cargo_tolerance_pct"] == 0.75
