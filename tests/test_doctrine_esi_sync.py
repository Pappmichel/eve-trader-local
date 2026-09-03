"""Doctrine ESI sync tests. Nothing here touches the network: a stub stands
in for ESIClient at the module boundary (same pattern as
test_production_esi_sync.py) and a stub TokenManager supplies the registered
characters. Storage is the real SQLite database from the `db` fixture."""
from __future__ import annotations

import pytest

from eve_trader_local import storage
from eve_trader_local.doctrine import actions as doctrine_actions
from eve_trader_local.doctrine import esi_sync
from eve_trader_local.doctrine.config import DoctrineConfig
from eve_trader_local.errors import ActionError
from eve_trader_local.esi_client import ESIError

CHAR_ID = 2112625428
CORP_ID = 98000001
STRUCTURE_ID = 60003760
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


class _Record:
    def __init__(self, role, character_id, character_name):
        self.role = role
        self.character_id = character_id
        self.character_name = character_name


class StubTokenManager:
    def __init__(self, records):
        self._records = records

    def list_records(self, prefix=None):
        return [r for r in self._records if r.role.startswith(f"{prefix}:")]

    def get_token(self, role):
        return object()


def _contract(contract_id, status="outstanding", title="Rifter Fit",
             start_location_id=STRUCTURE_ID, issuer_id=CHAR_ID, price=1_000_000.0):
    return {
        "contract_id": contract_id, "type": "item_exchange", "status": status,
        "start_location_id": start_location_id, "issuer_id": issuer_id,
        "issuer_corporation_id": CORP_ID, "title": title, "price": price, "date_expired": "2026-01-01T00:00:00Z",
    }


def _item(record_id, type_id, quantity, is_included=True, is_singleton=False):
    return {"record_id": record_id, "type_id": type_id, "quantity": quantity,
            "is_included": is_included, "is_singleton": is_singleton}


class StubClient:
    def __init__(self, contracts=None, items=None, corporation_id=CORP_ID, fail_contracts=False):
        self.contracts = contracts or []
        self.items = items or {}
        self.corporation_id = corporation_id
        self.fail_contracts = fail_contracts
        self.corp_contract_calls = 0

    def character_contracts(self, character_id, auth_role):
        if self.fail_contracts:
            raise ESIError("no scope")
        return self.contracts

    def corporation_contracts(self, corporation_id, auth_role):
        self.corp_contract_calls += 1
        return []  # no separate corp contracts in these tests

    def character_contract_items(self, character_id, contract_id, auth_role):
        return self.items.get(contract_id, [])

    def corporation_contract_items(self, corporation_id, contract_id, auth_role):
        return self.items.get(contract_id, [])

    def character_public_info(self, character_id):
        return {"corporation_id": self.corporation_id}

    def character_assets(self, character_id, auth_role):
        return []

    def corporation_assets(self, corporation_id, auth_role):
        return []

    def corporation_public_info(self, corporation_id):
        return {"name": "Test Corp"}

    def resolve_names(self, ids):
        return {}


@pytest.fixture
def one_character():
    return [_Record(f"{esi_sync.DOCTRINE_ROLE_PREFIX}:{CHAR_ID}", CHAR_ID, "Test Pilot")]


def test_sync_contracts_requires_structure_configured(db, one_character):
    _seed_sde()
    tm = StubTokenManager(one_character)
    client = StubClient(contracts=[_contract(1)])
    with pytest.raises(ActionError, match="No structure configured"):
        esi_sync.sync_contracts(DoctrineConfig(), client=client, tm=tm)


def test_sync_contracts_requires_a_registered_character(db):
    with pytest.raises(ActionError, match="No doctrine character"):
        esi_sync.sync_contracts(DoctrineConfig(doctrine_structure_id=STRUCTURE_ID), tm=StubTokenManager([]))


def test_sync_contracts_matches_and_persists_a_valid_contract(db, one_character):
    """The end-to-end payoff: parse+store a fitting, then a synced contract
    exactly matching it comes back "valid" and is written to the doctrine_
    contracts/items/deviations tables."""
    _seed_sde()
    doctrine_id = doctrine_actions.do_create_doctrine("Rifter Fleet")["doctrine_id"]
    fitting = doctrine_actions.do_add_fitting(doctrine_id, _RAW_EFT)["fitting"]

    contract = _contract(555)
    items = [
        _item(1, RIFTER, 1, is_singleton=True),
        _item(2, DAMAGE_CONTROL, 1),
        _item(3, AUTOCANNON, 1),
        _item(4, AMMO, 1),
    ]
    tm = StubTokenManager(one_character)
    client = StubClient(contracts=[contract], items={555: items})
    cfg = DoctrineConfig(doctrine_structure_id=STRUCTURE_ID)

    result = esi_sync.sync_contracts(cfg, client=client, tm=tm)
    assert result["contracts_synced"] == 1
    assert result["contracts_no_relevant_hull"] == 0

    stored = storage.list_doctrine_contracts()
    assert len(stored) == 1
    row = stored[0]
    assert row[0] == 555
    assert row[9] == fitting["fitting_id"]  # matched_fitting_id
    assert row[11] == "valid"  # validation_status
    assert storage.load_doctrine_contract_items(555)
    assert storage.load_doctrine_contract_deviations(555) == []


def test_sync_contracts_drops_contract_with_no_matching_hull(db, one_character):
    _seed_sde()
    doctrine_id = doctrine_actions.do_create_doctrine("Rifter Fleet")["doctrine_id"]
    doctrine_actions.do_add_fitting(doctrine_id, _RAW_EFT)

    contract = _contract(777)
    tm = StubTokenManager(one_character)
    client = StubClient(contracts=[contract], items={777: [_item(1, AMMO, 100)]})  # no Rifter hull at all
    cfg = DoctrineConfig(doctrine_structure_id=STRUCTURE_ID)

    result = esi_sync.sync_contracts(cfg, client=client, tm=tm)
    assert result["contracts_synced"] == 0
    assert result["contracts_no_relevant_hull"] == 1
    assert storage.list_doctrine_contracts() == []


def test_sync_contracts_ignores_contracts_at_a_different_structure(db, one_character):
    _seed_sde()
    doctrine_id = doctrine_actions.do_create_doctrine("Rifter Fleet")["doctrine_id"]
    doctrine_actions.do_add_fitting(doctrine_id, _RAW_EFT)

    contract = _contract(888, start_location_id=STRUCTURE_ID + 1)
    tm = StubTokenManager(one_character)
    client = StubClient(contracts=[contract])
    cfg = DoctrineConfig(doctrine_structure_id=STRUCTURE_ID)

    result = esi_sync.sync_contracts(cfg, client=client, tm=tm)
    assert result["contracts_synced"] == 0
    assert storage.list_doctrine_contracts() == []


def test_sync_contracts_skips_character_missing_scope(db, one_character):
    """A character whose token/scope fails degrades to a per-character
    "skipped" report rather than aborting the whole sync."""
    _seed_sde()
    tm = StubTokenManager(one_character)
    client = StubClient(fail_contracts=True)
    cfg = DoctrineConfig(doctrine_structure_id=STRUCTURE_ID)
    result = esi_sync.sync_contracts(cfg, client=client, tm=tm)
    assert result["contracts_synced"] == 0
    assert "skipped" in result["characters"]["Test Pilot"]


def test_sync_assets_populates_doctrine_asset_tables(db, one_character):
    tm = StubTokenManager(one_character)

    class AssetClient(StubClient):
        def character_assets(self, character_id, auth_role):
            return [{"item_id": 1, "type_id": DAMAGE_CONTROL, "location_id": STRUCTURE_ID,
                     "location_flag": "Hangar", "quantity": 3, "is_blueprint_copy": False}]

    result = esi_sync.sync_assets(client=AssetClient(), tm=tm)
    assert result["characters"]["Test Pilot"] == {"assets": 1}
    assert storage.esi_stock_at_location(DAMAGE_CONTROL, STRUCTURE_ID, tables=storage.DOCTRINE_ASSET_TABLES) == 3


def test_sync_assets_requires_a_registered_character(db):
    with pytest.raises(ActionError, match="No doctrine character"):
        esi_sync.sync_assets(tm=StubTokenManager([]))


def test_sync_doctrine_isolates_each_half_and_persists_partial_success(db, one_character, monkeypatch):
    """One half (contracts, no structure configured) fails; the other
    (assets) should still run and be persisted, and the overall call should
    not raise since at least one half succeeded."""
    _seed_sde()

    class AssetOnlyClient(StubClient):
        def character_assets(self, character_id, auth_role):
            return [{"item_id": 1, "type_id": DAMAGE_CONTROL, "location_id": STRUCTURE_ID,
                     "location_flag": "Hangar", "quantity": 5, "is_blueprint_copy": False}]

    monkeypatch.setattr(esi_sync, "TokenManager", lambda cfg=None: StubTokenManager(one_character))
    monkeypatch.setattr(esi_sync, "ESIClient", lambda tokens=None: AssetOnlyClient())

    cfg = DoctrineConfig()  # doctrine_structure_id unset, TRADING_CONFIG.structure_id also unset -> contracts fail
    result = esi_sync.sync_doctrine(cfg)
    assert "error" in result["contracts"]
    assert result["assets"]["characters"]["Test Pilot"] == {"assets": 1}
    assert storage.get_esi_sync_time("doctrine") is not None
