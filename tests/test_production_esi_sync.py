"""Producer ESI sync tests.

Nothing here touches the network: a stub stands in for ESIClient at the module
boundary (same pattern as test_production_pricing.py) and a stub TokenManager
supplies the registered characters, so every assertion is about what sync_esi
does with the data, not about HTTP. Storage is the real SQLite database from
the `db` fixture - the snapshot-replacement behavior is the point, and mocking
it away would test nothing.
"""
from __future__ import annotations

import pytest

from eve_trader_local import storage
from eve_trader_local.errors import ActionError
from eve_trader_local.esi_client import ESIError
from eve_trader_local.production import actions as production_actions
from eve_trader_local.production import esi_sync


CHAR_ID = 2112625428
CORP_ID = 98000001
RIFTER_BP = 688
STATION = 60003760
CONTAINER = 1400000000000


class _Record:
    def __init__(self, role, character_id, character_name):
        self.role = role
        self.character_id = character_id
        self.character_name = character_name


class StubTokenManager:
    """Only the three methods esi_sync actually uses."""

    def __init__(self, records, failing_roles=()):
        self._records = records
        self._failing = set(failing_roles)

    def list_records(self, prefix=None):
        return [r for r in self._records if r.role.startswith(f"{prefix}:")]

    def get_token(self, role):
        if role in self._failing:
            raise ActionError(f"Refreshing the token for '{role}' failed (400).")
        return object()


class StubClient:
    """Returns one canned payload per endpoint. A payload that is an ESIError
    instance is raised instead, which is how the per-call isolation branches
    are exercised."""

    def __init__(self, **payloads):
        self.payloads = payloads
        self.calls: list[str] = []

    def _get(self, name):
        self.calls.append(name)
        value = self.payloads.get(name, [])
        if isinstance(value, ESIError):
            raise value
        return value

    def character_assets(self, character_id, auth_role):
        return self._get("character_assets")

    def character_industry_jobs(self, character_id, auth_role):
        return self._get("character_industry_jobs")

    def character_blueprints(self, character_id, auth_role):
        return self._get("character_blueprints")

    def character_public_info(self, character_id):
        return self._get("character_public_info") or {"corporation_id": CORP_ID}

    def corporation_public_info(self, corporation_id):
        return self._get("corporation_public_info") or {"name": "Test Corp"}

    def corporation_assets(self, corporation_id, auth_role):
        return self._get("corporation_assets")

    def corporation_industry_jobs(self, corporation_id, auth_role):
        return self._get("corporation_industry_jobs")

    def corporation_blueprints(self, corporation_id, auth_role):
        return self._get("corporation_blueprints")

    def resolve_names(self, ids):
        return self._get("resolve_names") or {i: f"Name {i}" for i in ids}


def _asset(item_id, type_id, quantity, location_id=STATION, flag="Hangar"):
    return {"item_id": item_id, "type_id": type_id, "location_id": location_id,
            "location_flag": flag, "quantity": quantity, "is_blueprint_copy": False}


def _blueprint(item_id, type_id, me, te, runs=-1, quantity=1, location_id=STATION):
    return {"item_id": item_id, "type_id": type_id, "location_id": location_id,
            "location_flag": "Hangar", "quantity": quantity,
            "material_efficiency": me, "time_efficiency": te, "runs": runs}


def _job(job_id, product_type_id, runs, status="active", activity_id=1):
    return {"job_id": job_id, "activity_id": activity_id, "blueprint_type_id": RIFTER_BP,
            "product_type_id": product_type_id, "runs": runs, "output_location_id": STATION,
            "status": status, "end_date": "2026-09-03T00:00:00Z",
            "start_date": "2026-09-01T00:00:00Z", "installer_id": CHAR_ID}


@pytest.fixture
def wired(db, monkeypatch):
    """Installs a stub TokenManager for one registered producer character and
    returns a helper that runs sync_esi against a given StubClient."""
    records = [_Record(f"producer:{CHAR_ID}", CHAR_ID, "Some Producer")]

    def run(client, failing_roles=()):
        tm = StubTokenManager(records, failing_roles)
        monkeypatch.setattr(esi_sync, "TokenManager", lambda cfg: tm)
        monkeypatch.setattr(esi_sync, "ESIClient", lambda tokens: client)
        return esi_sync.sync_esi()

    return run


# ------------------------------------------------------------- the happy path
def test_sync_populates_all_three_table_pairs(wired):
    client = StubClient(
        character_assets=[_asset(1, 34, 5000), _asset(2, 587, 3)],
        character_blueprints=[_blueprint(10, RIFTER_BP, 10, 20)],
        character_industry_jobs=[_job(100, 587, 4)],
        corporation_assets=[_asset(3, 34, 200)],
        corporation_blueprints=[_blueprint(11, RIFTER_BP, 8, 16)],
        corporation_industry_jobs=[_job(101, 587, 2)],
    )

    result = wired(client)

    assert result["characters"]["Some Producer"] == {
        "assets": 2, "blueprints": 1, "industry_jobs": 1}
    assert result["corporations"]["Test Corp"] == {
        "assets": 1, "blueprints": 1, "industry_jobs": 1}
    # 5000 held by the character + 200 by the corp, both at the same station.
    assert storage.esi_stock_at_location(34, STATION) == 5200
    assert storage.esi_incoming_industry_qty(587) == {"runs": 6.0, "jobs": 2}
    # Best ME and TE are taken independently across character + corp BPOs.
    assert storage.get_owned_bpo_best_me_te(RIFTER_BP) == (10, 20)


def test_owner_name_records_who_holds_each_asset(wired):
    client = StubClient(character_assets=[_asset(1, 34, 10)],
                        corporation_assets=[_asset(2, 34, 20)])

    wired(client)

    with storage.connect() as conn:
        assert conn.execute("SELECT owner_name FROM character_assets").fetchone()[0] == "Some Producer"
        assert conn.execute("SELECT owner_name FROM corp_assets").fetchone()[0] == "Test Corp (corp)"


# ------------------------------------------------------------- re-sync is a replace
def test_resync_replaces_rather_than_accumulates(wired):
    first = StubClient(character_assets=[_asset(1, 34, 5000), _asset(2, 587, 3)],
                       character_blueprints=[_blueprint(10, RIFTER_BP, 10, 20)],
                       character_industry_jobs=[_job(100, 587, 4)])
    wired(first)

    # Everything sold/delivered except a smaller Tritanium stack.
    second = StubClient(character_assets=[_asset(1, 34, 900)])
    wired(second)

    with storage.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM character_assets").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM character_blueprints").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM character_industry_jobs").fetchone()[0] == 0
    assert storage.esi_stock_at_location(34, STATION) == 900
    assert storage.get_owned_bpo_best_me_te(RIFTER_BP) is None


# --------------------------------------------------------------- data quirks
def test_nested_container_stock_resolves_to_the_outer_station(wired):
    """An asset inside a container inside a station must still count as stock
    at that station - resolving only one level was a real parent-repo bug."""
    client = StubClient(character_assets=[
        _asset(CONTAINER, 3465, 1, location_id=STATION),
        _asset(1, 34, 400, location_id=CONTAINER),
    ])

    wired(client)

    assert storage.esi_stock_at_location(34, STATION) == 400


def test_blueprint_in_a_container_resolves_against_the_asset_table(wired):
    client = StubClient(
        character_assets=[_asset(CONTAINER, 3465, 1, location_id=STATION)],
        character_blueprints=[_blueprint(10, RIFTER_BP, 10, 20, location_id=CONTAINER)],
    )

    wired(client)

    with storage.connect() as conn:
        resolved = conn.execute("SELECT resolved_location_id FROM character_blueprints").fetchone()[0]
    assert resolved == STATION


def test_live_reaction_activity_id_is_normalized_to_the_sde_one(wired):
    """The live jobs endpoint reports reactions as activity 9; the SDE files
    every reaction recipe under 11. Normalizing at ingestion keeps every
    downstream SDE lookup working."""
    client = StubClient(character_industry_jobs=[_job(100, 16672, 1, activity_id=9)])

    wired(client)

    with storage.connect() as conn:
        assert conn.execute("SELECT activity_id FROM character_industry_jobs").fetchone()[0] == 11


def test_asset_safety_is_not_counted_as_usable_stock(wired):
    client = StubClient(character_assets=[
        _asset(1, 34, 100),
        _asset(2, 34, 900, flag="AssetSafety"),
    ])

    wired(client)

    assert storage.esi_stock_at_location(34, STATION) == 100


def test_blueprint_copies_never_provide_owned_me_te(wired):
    """A BPC's ME/TE was fixed by whoever copied it, so only Originals
    (runs == -1) may answer 'what is my researched ME'."""
    client = StubClient(character_blueprints=[_blueprint(10, RIFTER_BP, 10, 20, runs=30, quantity=-2)])

    wired(client)

    assert storage.get_owned_bpo_best_me_te(RIFTER_BP) is None


# ------------------------------------------------------------ failure isolation
def test_a_corp_without_director_does_not_fail_the_sync(wired):
    client = StubClient(
        character_assets=[_asset(1, 34, 10)],
        corporation_assets=ESIError("403 Forbidden"),
    )

    result = wired(client)

    assert result["characters"]["Some Producer"]["assets"] == 1
    assert "missing Director role" in result["corporations"]["Test Corp"]
    assert storage.esi_stock_at_location(34, None) == 10


def test_a_character_whose_own_fetch_fails_is_reported_not_raised(wired):
    client = StubClient(character_assets=ESIError("403 Forbidden"))

    result = wired(client)

    assert result["characters"]["Some Producer"].startswith("skipped")


def test_a_dead_refresh_token_skips_that_character(wired):
    client = StubClient(character_assets=[_asset(1, 34, 10)])

    with pytest.raises(ActionError, match="failed to refresh"):
        wired(client, failing_roles=[f"producer:{CHAR_ID}"])

    # The character never reached the pool, so nothing was fetched at all.
    assert client.calls == []


def test_sync_with_no_registered_character_raises(db, monkeypatch):
    monkeypatch.setattr(esi_sync, "TokenManager", lambda cfg: StubTokenManager([]))

    with pytest.raises(ActionError, match="No producer character"):
        esi_sync.sync_esi()


# --------------------------------------------------------------------- action
def test_do_sync_esi_records_the_sync_time(db, monkeypatch, wired):
    monkeypatch.setattr(production_actions.esi_sync, "sync_esi",
                        lambda cfg: {"characters": {}, "corporations": {}})

    production_actions.do_sync_esi()

    assert production_actions.do_get_esi_sync_time()["synced_at"] is not None
