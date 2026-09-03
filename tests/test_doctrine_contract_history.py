"""GitHub issue #19: doctrine_contract_history - a permanent "who bought
what and when" log, additive on top of the already-ported contract sync/
matching (test_doctrine_esi_sync.py). Nothing here touches the network -
same stub-ESIClient/stub-TokenManager pattern as that file."""
from __future__ import annotations

import pytest

from eve_trader_local import storage
from eve_trader_local.doctrine import actions as doctrine_actions
from eve_trader_local.doctrine import esi_sync
from eve_trader_local.doctrine.config import DoctrineConfig
from eve_trader_local.esi_client import ESIError

CHAR_ID = 2112625428
CORP_ID = 98000001
STRUCTURE_ID = 60003760
ACCEPTOR_ID = 90000001
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
             start_location_id=STRUCTURE_ID, issuer_id=CHAR_ID, price=1_000_000.0,
             acceptor_id=None, date_issued="2026-01-01T00:00:00Z", date_completed=None):
    return {
        "contract_id": contract_id, "type": "item_exchange", "status": status,
        "start_location_id": start_location_id, "issuer_id": issuer_id,
        "issuer_corporation_id": CORP_ID, "title": title, "price": price,
        "date_expired": "2026-01-08T00:00:00Z", "date_issued": date_issued,
        "date_completed": date_completed, "acceptor_id": acceptor_id,
    }


def _item(record_id, type_id, quantity, is_included=True, is_singleton=False):
    return {"record_id": record_id, "type_id": type_id, "quantity": quantity,
            "is_included": is_included, "is_singleton": is_singleton}


class StubClient:
    def __init__(self, contracts=None, items=None, corporation_id=CORP_ID, names=None):
        self.contracts = contracts or []
        self.items = items or {}
        self.corporation_id = corporation_id
        self.names = names or {}

    def character_contracts(self, character_id, auth_role):
        return self.contracts

    def corporation_contracts(self, corporation_id, auth_role):
        return []

    def character_contract_items(self, character_id, contract_id, auth_role):
        return self.items.get(contract_id, [])

    def corporation_contract_items(self, corporation_id, contract_id, auth_role):
        return self.items.get(contract_id, [])

    def character_public_info(self, character_id):
        return {"corporation_id": self.corporation_id}

    def resolve_names(self, ids):
        return {i: self.names[i] for i in ids if i in self.names}


@pytest.fixture
def one_character():
    return [_Record(f"{esi_sync.DOCTRINE_ROLE_PREFIX}:{CHAR_ID}", CHAR_ID, "Test Pilot")]


def _setup_matched_fitting(db):
    """A valid, currently-matched contract - sync_contracts must see it as
    "outstanding" once before it can ever transition to "finished" (history
    only ever reuses a fitting_id an earlier sync already matched, never
    re-matches a finished contract's items from scratch)."""
    _seed_sde()
    doctrine_id = doctrine_actions.do_create_doctrine("Rifter Fleet")["doctrine_id"]
    fitting = doctrine_actions.do_add_fitting(doctrine_id, _RAW_EFT)["fitting"]
    items = [
        _item(1, RIFTER, 1, is_singleton=True),
        _item(2, DAMAGE_CONTROL, 1),
        _item(3, AUTOCANNON, 1),
        _item(4, AMMO, 1),
    ]
    return doctrine_id, fitting, items


def test_finished_contract_produces_a_history_row(db, one_character):
    doctrine_id, fitting, items = _setup_matched_fitting(db)
    tm = StubTokenManager(one_character)
    cfg = DoctrineConfig(doctrine_structure_id=STRUCTURE_ID)

    # First sync: still outstanding, gets matched and stored as active.
    contract = _contract(555, status="outstanding")
    esi_sync.sync_contracts(cfg, client=StubClient(contracts=[contract], items={555: items}), tm=tm)
    assert len(storage.load_doctrine_contract_history()) == 0

    # Second sync: ESI now reports it finished (sold) - no longer syncable,
    # so it drops out of the active snapshot, but must land in history.
    finished = _contract(555, status="finished_issuer", acceptor_id=ACCEPTOR_ID,
                          date_completed="2026-01-02T00:00:00Z")
    esi_sync.sync_contracts(
        cfg, client=StubClient(contracts=[finished], items={555: items}, names={ACCEPTOR_ID: "Some Buyer"}), tm=tm,
    )

    assert storage.list_doctrine_contracts() == []  # dropped from the active table

    history = storage.load_doctrine_contract_history()
    assert len(history) == 1
    row = dict(zip(storage._CONTRACT_HISTORY_COLUMNS, history[0]))
    assert row["contract_id"] == 555
    assert row["fitting_id"] == fitting["fitting_id"]
    assert row["fitting_name"] == "C-J Doctrine Fit"
    assert row["hull_type_id"] == RIFTER
    assert row["acceptor_id"] == ACCEPTOR_ID
    assert row["acceptor_name"] == "Some Buyer"
    assert row["price"] == 1_000_000.0
    assert row["date_completed"] == "2026-01-02T00:00:00Z"


def test_finished_contract_never_matched_is_not_recorded(db, one_character):
    """A finished contract that was never previously matched to a fitting
    (e.g. it was already finished the very first time we ever saw it, or it's
    someone else's unrelated sale at our structure) is not a doctrine
    contract at all and must not clutter permanent history."""
    _seed_sde()
    tm = StubTokenManager(one_character)
    cfg = DoctrineConfig(doctrine_structure_id=STRUCTURE_ID)
    finished = _contract(999, status="finished")
    esi_sync.sync_contracts(cfg, client=StubClient(contracts=[finished]), tm=tm)
    assert storage.load_doctrine_contract_history() == []


def test_history_persists_independently_of_the_live_contract_row(db, one_character):
    """Once recorded, a history row survives even after later syncs no longer
    mention that contract at all (it's fallen out of ESI's own list)."""
    doctrine_id, fitting, items = _setup_matched_fitting(db)
    tm = StubTokenManager(one_character)
    cfg = DoctrineConfig(doctrine_structure_id=STRUCTURE_ID)

    esi_sync.sync_contracts(cfg, client=StubClient(contracts=[_contract(555)], items={555: items}), tm=tm)
    finished = _contract(555, status="finished_issuer", acceptor_id=ACCEPTOR_ID)
    esi_sync.sync_contracts(cfg, client=StubClient(contracts=[finished], items={555: items}), tm=tm)
    assert len(storage.load_doctrine_contract_history()) == 1

    # A later sync sees no contracts at all (ESI dropped it from the list) -
    # replace_doctrine_sync_snapshot wipes the active table each time, but
    # history must be untouched.
    esi_sync.sync_contracts(cfg, client=StubClient(contracts=[]), tm=tm)
    assert storage.list_doctrine_contracts() == []
    assert len(storage.load_doctrine_contract_history()) == 1


def test_upsert_updates_rather_than_duplicates(db):
    """A later sync resolving a previously-unresolvable acceptor_name updates
    the existing row in place (ON CONFLICT DO UPDATE), not a second row."""
    row = (555, "doctrine:1", "fit-1", "Rifter Fit", RIFTER, "title", 1_000_000.0,
           ACCEPTOR_ID, None, "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z")
    storage.upsert_doctrine_contract_history([row])
    assert len(storage.load_doctrine_contract_history()) == 1

    updated = (555, "doctrine:1", "fit-1", "Rifter Fit", RIFTER, "title", 1_000_000.0,
               ACCEPTOR_ID, "Some Buyer", "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z")
    storage.upsert_doctrine_contract_history([updated])
    history = storage.load_doctrine_contract_history()
    assert len(history) == 1
    assert dict(zip(storage._CONTRACT_HISTORY_COLUMNS, history[0]))["acceptor_name"] == "Some Buyer"


def test_do_contract_history_resolves_hull_and_character_names(db, one_character, monkeypatch):
    doctrine_id, fitting, items = _setup_matched_fitting(db)
    tm = StubTokenManager(one_character)
    cfg = DoctrineConfig(doctrine_structure_id=STRUCTURE_ID)
    esi_sync.sync_contracts(cfg, client=StubClient(contracts=[_contract(555)], items={555: items}), tm=tm)
    finished = _contract(555, status="finished_issuer", acceptor_id=ACCEPTOR_ID)
    esi_sync.sync_contracts(
        cfg, client=StubClient(contracts=[finished], items={555: items}, names={ACCEPTOR_ID: "Some Buyer"}), tm=tm,
    )

    # do_contract_history resolves source_character_name via esi_sync.
    # list_doctrine_characters' own default (real) TokenManager - swap it for
    # the stub so this test doesn't depend on a real OAuth token store.
    monkeypatch.setattr(doctrine_actions.esi_sync, "list_doctrine_characters",
                        lambda: [(r.role, r.character_id, r.character_name) for r in one_character])

    rows = doctrine_actions.do_contract_history()["rows"]
    assert len(rows) == 1
    r = rows[0]
    assert r["hull_name"] == "Rifter"
    assert r["source_character_name"] == "Test Pilot"
    assert r["acceptor_name"] == "Some Buyer"


def test_do_contract_history_filters_by_doctrine_id(db, one_character):
    doctrine_id, fitting, items = _setup_matched_fitting(db)
    other_doctrine_id = doctrine_actions.do_create_doctrine("Other Fleet")["doctrine_id"]
    tm = StubTokenManager(one_character)
    cfg = DoctrineConfig(doctrine_structure_id=STRUCTURE_ID)
    esi_sync.sync_contracts(cfg, client=StubClient(contracts=[_contract(555)], items={555: items}), tm=tm)
    finished = _contract(555, status="finished_issuer")
    esi_sync.sync_contracts(cfg, client=StubClient(contracts=[finished], items={555: items}), tm=tm)

    assert len(doctrine_actions.do_contract_history(doctrine_id)["rows"]) == 1
    assert doctrine_actions.do_contract_history(other_doctrine_id)["rows"] == []


def test_load_doctrine_contract_history_orders_completed_first_nulls_last(db):
    row_no_date = (1, "doctrine:1", None, None, None, None, None, None, None, None, None)
    row_earlier = (2, "doctrine:1", None, None, None, None, None, None, None, None, "2026-01-01T00:00:00Z")
    row_later = (3, "doctrine:1", None, None, None, None, None, None, None, None, "2026-02-01T00:00:00Z")
    storage.upsert_doctrine_contract_history([row_no_date, row_earlier, row_later])
    history = storage.load_doctrine_contract_history()
    assert [r[0] for r in history] == [3, 2, 1]
