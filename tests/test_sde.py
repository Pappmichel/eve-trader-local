"""SDE importer tests.

Nothing here touches the network: `requests.Session.get`/`.head` are the only
seams the real code uses, and the fake below replaces both with tiny in-memory
CSV bodies (a real refresh downloads ~19MB across 12 files). The row counts
are therefore deliberately absurd - what matters is that each CSV lands in the
right table with the right shape, that irrelevant activity IDs are filtered
out, and that a failed fetch cannot leave the cache half-replaced.
"""
from __future__ import annotations

import pytest
import requests

from eve_trader_local import sde, storage
from eve_trader_local.errors import ActionError

# One row per file, minimal but with real column names - the parser indexes
# these by name, so a wrong header here fails the same way a changed Fuzzwork
# dump would.
CSVS = {
    "invTypes.csv": (
        "typeID,groupID,typeName,volume,published,marketGroupID,metaLevel,portionSize\n"
        "34,18,Tritanium,0.01,1,1857,0,1\n"
        "1230,462,Veldspar,0.1,1,514,0,100\n"
        "999,25,Unpublished Thing,5.0,0,,0,1\n"
    ),
    "invGroups.csv": "groupID,categoryID,groupName\n18,4,Mineral\n462,25,Veldspar\n25,25,Asteroid\n",
    "invCategories.csv": "categoryID,categoryName\n4,Material\n25,Asteroid\n",
    "invMarketGroups.csv": "marketGroupID,parentGroupID,marketGroupName\n1857,,Minerals\n514,,Ore\n",
    "invMetaTypes.csv": "typeID,parentTypeID,metaGroupID\n1230,1230,1\n",
    # activityID 3 (research) and 4 (ME research) must be filtered out; 1, 5,
    # 8 and 11 must survive.
    "industryActivity.csv": (
        "typeID,activityID,time\n"
        "1001,1,600\n1001,5,4800\n1001,3,210\n1002,11,3600\n1003,8,7200\n"
    ),
    "industryActivityMaterials.csv": (
        "typeID,activityID,materialTypeID,quantity\n"
        "1001,1,34,1000\n1001,4,34,5\n1002,11,1230,200\n"
    ),
    "industryActivityProducts.csv": (
        "typeID,activityID,productTypeID,quantity\n"
        "1001,1,2001,1\n1003,8,2003,1\n1001,3,2001,1\n"
    ),
    "industryActivityProbabilities.csv": (
        "typeID,activityID,productTypeID,probability\n"
        "1003,8,2003,0.34\n1003,1,2003,0.99\n"
    ),
    "mapSolarSystems.csv": (
        "regionID,solarSystemID,solarSystemName,security\n"
        "10000002,30000142,Jita,0.946\n"
        "10000009,30001234,C-J,\n"  # blank security -> skipped
    ),
    "staStations.csv": (
        "stationID,solarSystemID,stationName\n"
        "60003760,30000142,Jita IV - Moon 4\n"
        "60000001,,Nowhere\n"  # no solar system -> skipped
    ),
    # effectID 12 = high slot, 2663 = rig; 42 is an unrelated dogma effect
    # and must not produce a slot row.
    "dgmTypeEffects.csv": (
        "typeID,effectID,isDefault\n"
        "500,12,1\n501,2663,1\n502,42,1\n"
    ),
    "invTypeMaterials.csv": "typeID,materialTypeID,quantity\n1230,34,415\n",
}

ETAG = '"sde-2026-09-01"'


class FakeResponse:
    def __init__(self, body: str = "", headers: dict | None = None):
        self.content = body.encode("utf-8")
        self.headers = headers or {}

    def raise_for_status(self):
        return None


class FakeSession:
    """Stands in for requests.Session. `fail` maps a filename to an exception
    (or a list of exceptions consumed one per attempt, for retry tests)."""

    def __init__(self, fail: dict | None = None, etag: str | None = ETAG):
        self.fail = fail or {}
        self.etag = etag
        self.headers: dict = {}
        self.gets: list[str] = []
        self.heads: list[str] = []

    def _maybe_fail(self, filename: str):
        planned = self.fail.get(filename)
        if planned is None:
            return
        if isinstance(planned, list):
            if planned:
                raise planned.pop(0)
            return
        raise planned

    def get(self, url, **kwargs):
        filename = url.rsplit("/", 1)[-1]
        self.gets.append(filename)
        self._maybe_fail(filename)
        return FakeResponse(CSVS[filename])

    def head(self, url, **kwargs):
        filename = url.rsplit("/", 1)[-1]
        self.heads.append(filename)
        self._maybe_fail(filename)
        if self.etag is None:
            raise requests.ConnectionError("no route to host")
        return FakeResponse(headers={"ETag": self.etag})


@pytest.fixture
def fake_session(monkeypatch):
    """Installs a FakeSession and makes time.sleep instant, so the
    retry-with-backoff path costs nothing in the suite."""
    monkeypatch.setattr(sde.time, "sleep", lambda _s: None)

    def install(**kwargs):
        session = FakeSession(**kwargs)
        monkeypatch.setattr(sde, "_session", lambda: session)
        return session

    return install


# --------------------------------------------------------------------------
# a successful refresh
# --------------------------------------------------------------------------

def test_refresh_populates_every_table(db, fake_session):
    fake_session()
    counts = sde.refresh_sde()

    assert counts["sde_types"] == 3
    assert counts["sde_groups"] == 3
    assert counts["sde_categories"] == 2
    assert counts["sde_market_groups"] == 2
    assert counts["sde_type_materials"] == 1
    assert counts == storage.sde_row_counts()


def test_refresh_filters_irrelevant_activities(db, fake_session):
    """Manufacturing/Reaction/Invention/Copying only - research activities
    (3/4) are never modelled by this tool, and Reverse Engineering (7) has no
    rows in CCP's data at all any more."""
    fake_session()
    counts = sde.refresh_sde()

    assert counts["sde_blueprint_time"] == 4        # 1, 5, 11, 8 - not the activityID 3 row
    assert counts["sde_blueprint_materials"] == 2   # the activityID 4 row dropped
    assert counts["sde_blueprint_products"] == 2    # the activityID 3 row dropped
    assert counts["sde_invention_probability"] == 1  # invention only


def test_refresh_skips_rows_missing_required_columns(db, fake_session):
    """A blank security / missing solarSystemID is a real shape in Fuzzwork's
    dump (wormhole systems, station-less rows), not corruption - skipped, not
    fatal."""
    fake_session()
    counts = sde.refresh_sde()
    assert counts["sde_solar_systems"] == 1
    assert counts["sde_stations"] == 1


def test_refresh_maps_only_slot_defining_effects(db, fake_session):
    fake_session()
    sde.refresh_sde()
    with storage.connect() as conn:
        rows = dict(conn.execute("SELECT type_id, slot FROM sde_type_slots").fetchall())
    assert rows == {500: "high", 501: "rig"}


def test_refresh_records_state_and_etag(db, fake_session):
    fake_session()
    sde.refresh_sde()
    refreshed_at, etag = storage.get_sde_refresh_state()
    assert etag == ETAG
    assert refreshed_at.startswith("20")


def test_meta_group_and_portion_size_land_on_types(db, fake_session):
    fake_session()
    sde.refresh_sde()
    veldspar = storage.get_sde_type(1230)
    assert veldspar[2] == "Veldspar"
    assert veldspar[7] == 1     # meta_group_id, from invMetaTypes.csv
    assert veldspar[8] == 100   # portion_size
    # No invMetaTypes.csv row at all - the common Tech I case, not an error.
    assert storage.get_sde_type(34)[7] is None
    assert storage.get_sde_type(123456) is None


def test_search_finds_published_types_only(db, fake_session):
    fake_session()
    sde.refresh_sde()
    assert storage.search_sde_types("veld") == [(1230, "Veldspar")]
    assert storage.search_sde_types("Unpublished") == []


# --------------------------------------------------------------------------
# failure handling
# --------------------------------------------------------------------------

def test_transient_failure_is_retried(db, fake_session):
    session = fake_session(fail={"invGroups.csv": [requests.ConnectionError("blip")]})
    sde.refresh_sde()
    assert session.gets.count("invGroups.csv") == 2


def test_persistent_failure_raises_actionerror(db, fake_session):
    session = fake_session(fail={"invGroups.csv": requests.ConnectionError("down")})
    with pytest.raises(ActionError, match="invGroups.csv"):
        sde.refresh_sde()
    assert session.gets.count("invGroups.csv") == 3  # three attempts, then give up


def test_failed_fetch_leaves_existing_data_untouched(db, fake_session):
    fake_session()
    sde.refresh_sde()
    before = storage.sde_row_counts()
    before_state = storage.get_sde_refresh_state()

    fake_session(fail={"staStations.csv": requests.ConnectionError("down")})
    with pytest.raises(ActionError):
        sde.refresh_sde()

    assert storage.sde_row_counts() == before
    assert storage.get_sde_refresh_state() == before_state


def test_replace_rolls_back_on_a_bad_row(db, fake_session):
    """The delete-then-insert has to be one transaction: a row that violates
    the schema partway through must not leave the cache emptied."""
    fake_session()
    sde.refresh_sde()
    before = storage.sde_row_counts()

    with pytest.raises(Exception):
        storage.replace_sde_data(
            types=[(1, 2, "ok", 1.0, 1, None, 0, None, 1)],
            groups=[], market_groups=[],
            blueprint_time=[], blueprint_materials=[], blueprint_products=[],
            type_slots=[(500, None)],  # slot is NOT NULL
        )
    assert storage.sde_row_counts() == before


# --------------------------------------------------------------------------
# freshness check
# --------------------------------------------------------------------------

def test_status_reports_no_local_refresh(db, fake_session):
    fake_session()
    status = sde.check_for_newer_sde()
    assert status["local_refreshed_at"] is None
    assert status["remote_check_succeeded"]
    # Never "newer available" purely because nothing is cached yet.
    assert not status["newer_sde_available"]


def test_status_detects_a_newer_dump(db, fake_session):
    fake_session()
    sde.refresh_sde()
    fake_session(etag='"sde-2026-10-01"')
    assert sde.check_for_newer_sde()["newer_sde_available"]


def test_status_is_quiet_when_etags_match(db, fake_session):
    fake_session()
    sde.refresh_sde()
    fake_session()
    status = sde.check_for_newer_sde()
    assert not status["newer_sde_available"]
    assert status["local_refreshed_at"] is not None


def test_status_survives_an_unreachable_fuzzwork(db, fake_session):
    fake_session()
    sde.refresh_sde()
    fake_session(etag=None)
    status = sde.check_for_newer_sde()
    assert not status["remote_check_succeeded"]
    assert not status["newer_sde_available"]  # unknown is not "newer"


def test_refresh_survives_an_unavailable_etag(db, fake_session):
    """A HEAD failure must not block a real refresh - the dump itself is what
    matters, the ETag is only a later convenience."""
    fake_session(etag=None)
    sde.refresh_sde()
    _refreshed_at, etag = storage.get_sde_refresh_state()
    assert etag is None
