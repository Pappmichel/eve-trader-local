from __future__ import annotations

import sqlite3

import pytest

from eve_trader_local import storage


def test_init_db_is_idempotent(db):
    storage.init_db()
    storage.init_db()
    with storage.connect() as conn:
        names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"tokens", "esi_sync_state", "settings"} <= names


def test_wal_mode_enabled(db):
    with storage.connect() as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_connect_rolls_back_on_exception(db):
    with pytest.raises(RuntimeError):
        with storage.connect() as conn:
            conn.execute("INSERT INTO settings (scope, overrides) VALUES ('trading', '{}')")
            raise RuntimeError("boom")
    assert storage.load_settings("trading") == {}


# ------------------------------------------------------------------- tokens
RECORD = {
    "role": "buyer:42",
    "character_id": 42,
    "character_name": "Some Pilot",
    "access_token": "at",
    "refresh_token": "rt",
    "expires_at": 1700000000.0,
    "scopes": "esi-markets.read_character_orders.v1",
}


def test_token_round_trip(db):
    storage.save_token("buyer:42", RECORD)
    assert storage.load_all_tokens() == {"buyer:42": RECORD}


def test_save_token_upserts_same_role(db):
    storage.save_token("buyer:42", RECORD)
    storage.save_token("buyer:42", {**RECORD, "access_token": "at2"})
    stored = storage.load_all_tokens()
    assert len(stored) == 1
    assert stored["buyer:42"]["access_token"] == "at2"


def test_tokens_for_different_roles_coexist(db):
    storage.save_token("buyer:42", RECORD)
    storage.save_token("seller:43", {**RECORD, "role": "seller:43", "character_id": 43})
    assert set(storage.load_all_tokens()) == {"buyer:42", "seller:43"}


def test_delete_token_is_idempotent(db):
    storage.save_token("buyer:42", RECORD)
    storage.delete_token("buyer:42")
    storage.delete_token("buyer:42")  # no row left - must not raise
    assert storage.load_all_tokens() == {}


# ----------------------------------------------------------- esi sync state
def test_sync_time_missing_is_none(db):
    assert storage.get_esi_sync_time("trading") is None


def test_sync_time_round_trip_and_overwrite(db):
    storage.set_esi_sync_time("trading", "2026-01-01T00:00:00")
    storage.set_esi_sync_time("trading", "2026-01-02T00:00:00")
    storage.set_esi_sync_time("production", "2026-01-03T00:00:00")
    assert storage.get_esi_sync_time("trading") == "2026-01-02T00:00:00"
    assert storage.get_esi_sync_time("production") == "2026-01-03T00:00:00"


# ----------------------------------------------------------------- settings
def test_settings_missing_is_empty_dict(db):
    assert storage.load_settings("trading") == {}


def test_settings_merge_preserves_unrelated_keys(db):
    storage.save_settings("trading", {"structure_id": 123, "lookback_days": 7})
    storage.save_settings("trading", {"lookback_days": 14})
    assert storage.load_settings("trading") == {"structure_id": 123, "lookback_days": 14}


def test_settings_scopes_are_independent(db):
    storage.save_settings("trading", {"lookback_days": 7})
    storage.save_settings("other", {"lookback_days": 99})
    assert storage.load_settings("trading") == {"lookback_days": 7}


def test_no_tenant_columns_anywhere(db):
    """The point of this repo: single-user, so nothing carries a tenant id."""
    with storage.connect() as conn:
        for table in ("tokens", "esi_sync_state", "settings"):
            cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            assert not any("tenant" in c for c in cols), (table, cols)


# ------------------------------------------------------------ order book cache
def test_order_book_stats_missing_is_empty(db):
    assert storage.load_order_book_stats("structure:60003760") == {}
    assert storage.load_order_book_stats("structure:60003760", [34]) == {}


def test_order_book_stats_round_trip(db):
    storage.save_order_book_stats("structure:60003760",
                                  {34: (4.5, 5.0, 100.0, 50.0), 35: (None, None, 0.0, 0.0)},
                                  "2026-01-01T00:00:00")
    assert storage.load_order_book_stats("structure:60003760") == {
        34: (4.5, 5.0, 100.0, 50.0), 35: (None, None, 0.0, 0.0)}
    assert storage.load_order_book_stats("structure:60003760", [34]) == {34: (4.5, 5.0, 100.0, 50.0)}
    assert storage.load_order_book_stats("structure:60003760", []) == {}


def test_order_book_stats_upsert_overwrites_only_given_type_ids(db):
    """Two different sync bundles (e.g. Trading and Ore & Minerals) can share
    one region's cache - one bundle's fetch must not wipe another's still-
    good rows for type_ids it didn't ask about."""
    storage.save_order_book_stats("region:10000002", {34: (1.0, 2.0, 1.0, 1.0)}, "2026-01-01T00:00:00")
    storage.save_order_book_stats("region:10000002", {35: (3.0, 4.0, 1.0, 1.0)}, "2026-01-02T00:00:00")
    assert storage.load_order_book_stats("region:10000002") == {
        34: (1.0, 2.0, 1.0, 1.0), 35: (3.0, 4.0, 1.0, 1.0)}
    storage.save_order_book_stats("region:10000002", {34: (9.0, 9.0, 9.0, 9.0)}, "2026-01-03T00:00:00")
    assert storage.load_order_book_stats("region:10000002")[34] == (9.0, 9.0, 9.0, 9.0)
    assert storage.load_order_book_stats("region:10000002")[35] == (3.0, 4.0, 1.0, 1.0)


def test_order_book_stats_markets_are_independent(db):
    storage.save_order_book_stats("structure:1", {34: (1.0, 1.0, 1.0, 1.0)}, "2026-01-01T00:00:00")
    storage.save_order_book_stats("region:2", {34: (2.0, 2.0, 2.0, 2.0)}, "2026-01-01T00:00:00")
    assert storage.load_order_book_stats("structure:1") == {34: (1.0, 1.0, 1.0, 1.0)}
    assert storage.load_order_book_stats("region:2") == {34: (2.0, 2.0, 2.0, 2.0)}


def test_order_book_snapshot_time_missing_is_none(db):
    assert storage.order_book_snapshot_time("structure:1") is None


def test_order_book_snapshot_time_is_the_latest_write(db):
    storage.save_order_book_stats("structure:1", {34: (1.0, 1.0, 1.0, 1.0)}, "2026-01-01T00:00:00")
    storage.save_order_book_stats("structure:1", {35: (1.0, 1.0, 1.0, 1.0)}, "2026-01-02T00:00:00")
    assert storage.order_book_snapshot_time("structure:1") == "2026-01-02T00:00:00"


# ----------------------------------------------------------------- result cache
def test_result_cache_missing_is_none(db):
    assert storage.load_result_cache("trading:wallet_balance:buyer:1") is None


def test_result_cache_round_trip(db):
    storage.save_result_cache("trading:wallet_balance:buyer:1", {"balance": 1234.5}, "2026-01-01T00:00:00")
    payload, computed_at = storage.load_result_cache("trading:wallet_balance:buyer:1")
    assert payload == {"balance": 1234.5}
    assert computed_at == "2026-01-01T00:00:00"


def test_result_cache_upsert_overwrites(db):
    storage.save_result_cache("k", {"v": 1}, "2026-01-01T00:00:00")
    storage.save_result_cache("k", {"v": 2}, "2026-01-02T00:00:00")
    payload, computed_at = storage.load_result_cache("k")
    assert payload == {"v": 2}
    assert computed_at == "2026-01-02T00:00:00"


def test_result_cache_keys_are_independent(db):
    storage.save_result_cache("a", {"v": 1}, "2026-01-01T00:00:00")
    storage.save_result_cache("b", {"v": 2}, "2026-01-01T00:00:00")
    assert storage.load_result_cache("a")[0] == {"v": 1}
    assert storage.load_result_cache("b")[0] == {"v": 2}


def test_stock_target_round_trip(db):
    storage.upsert_stock_target(34, "Tritanium", 1000.0, jita_target=False)
    storage.upsert_stock_target(35, "Pyerite", 500.0, jita_target=True)

    rows = {r[0]: r for r in storage.load_stock_targets()}
    assert rows[34] == (34, "Tritanium", 1000.0, False)
    assert rows[35] == (35, "Pyerite", 500.0, True)


def test_stock_target_upsert_overwrites_existing_row(db):
    storage.upsert_stock_target(34, "Tritanium", 1000.0, jita_target=False)
    storage.upsert_stock_target(34, "Tritanium", 2000.0, jita_target=True)

    rows = storage.load_stock_targets()
    assert rows == [(34, "Tritanium", 2000.0, True)]


def test_delete_stock_target_is_idempotent(db):
    storage.upsert_stock_target(34, "Tritanium", 1000.0)
    storage.delete_stock_target(34)
    storage.delete_stock_target(34)  # no error on a second delete
    assert storage.load_stock_targets() == []


def test_manual_stock_round_trip(db):
    storage.upsert_manual_stock(34, "Tritanium", 500.0)
    storage.upsert_manual_stock(35, "Pyerite", 250.0)

    assert storage.load_manual_stock() == {34: 500.0, 35: 250.0}
    assert storage.list_manual_stock() == [(35, "Pyerite", 250.0), (34, "Tritanium", 500.0)]


def test_manual_stock_upsert_overwrites_existing_row(db):
    storage.upsert_manual_stock(34, "Tritanium", 500.0)
    storage.upsert_manual_stock(34, "Tritanium", 750.0)

    assert storage.load_manual_stock() == {34: 750.0}


def test_delete_manual_stock_is_idempotent(db):
    storage.upsert_manual_stock(34, "Tritanium", 500.0)
    storage.delete_manual_stock(34)
    storage.delete_manual_stock(34)
    assert storage.load_manual_stock() == {}


# ------------------------------------------------- category locations (issue #4)
def test_category_location_round_trip(db):
    storage.upsert_category_location("Reactions", 1000000000001)
    storage.upsert_category_location("Advanced Components", 1000000000002)

    assert storage.load_category_locations() == {
        "Reactions": 1000000000001, "Advanced Components": 1000000000002,
    }


def test_category_location_upsert_overwrites_existing_row(db):
    storage.upsert_category_location("Reactions", 1000000000001)
    storage.upsert_category_location("Reactions", 1000000000002)

    assert storage.load_category_locations() == {"Reactions": 1000000000002}


def test_delete_category_location_is_idempotent(db):
    storage.upsert_category_location("Reactions", 1000000000001)
    storage.delete_category_location("Reactions")
    storage.delete_category_location("Reactions")  # no error on a second delete
    assert storage.load_category_locations() == {}


def test_category_location_options_round_trip(db):
    storage.add_category_location_option("Reactions", 1000000000001)
    storage.add_category_location_option("Reactions", 1000000000002)
    storage.add_category_location_option("Equipment", 1000000000003)

    assert storage.load_category_location_options() == {
        "Reactions": [1000000000001, 1000000000002],
        "Equipment": [1000000000003],
    }


def test_category_location_option_add_is_idempotent(db):
    storage.add_category_location_option("Reactions", 1000000000001)
    storage.add_category_location_option("Reactions", 1000000000001)  # same (category, location) twice

    assert storage.load_category_location_options() == {"Reactions": [1000000000001]}


def test_delete_category_location_option_is_idempotent(db):
    storage.add_category_location_option("Reactions", 1000000000001)
    storage.delete_category_location_option("Reactions", 1000000000001)
    storage.delete_category_location_option("Reactions", 1000000000001)
    assert storage.load_category_location_options() == {}


def test_available_blueprint_copies_sums_runs_not_copy_count(db):
    """Confirmed real bug in the parent this ports the fix for: a single
    300-run copy supports 300 invention attempts, not 1 - COUNT(*) would
    understate this by two orders of magnitude."""
    storage.replace_assets("character_assets", [
        (1, 999, 60003760, None, 1, 1, "Test Character"),
    ])
    storage.replace_blueprints("character_blueprints", [
        (1, 999, 60003760, None, -2, 0, 0, 300),  # one 300-run copy
    ])
    storage.replace_assets("corp_assets", [
        (2, 999, 60003761, None, 1, 1, "Test Corp"),
    ])
    storage.replace_blueprints("corp_blueprints", [
        (2, 999, 60003761, None, -2, 0, 0, 50),  # one 50-run copy elsewhere
    ])

    assert storage.available_blueprint_copies(999, None) == 350.0
    assert storage.available_blueprint_copies(999, 60003760) == 300.0


def test_available_blueprint_copies_excludes_originals(db):
    storage.replace_assets("character_assets", [(1, 999, 60003760, None, 1, 1, "Test")])
    storage.replace_blueprints("character_blueprints", [
        (1, 999, 60003760, None, -1, 10, 20, -1),  # an Original, not a copy
    ])
    assert storage.available_blueprint_copies(999, None) == 0.0


def test_has_bpo_at_location(db):
    storage.replace_assets("character_assets", [(1, 999, 60003760, None, 1, 1, "Test")])
    storage.replace_blueprints("character_blueprints", [
        (1, 999, 60003760, None, -1, 10, 20, -1),
    ])
    assert storage.has_bpo_at_location(999, 60003760) is True
    assert storage.has_bpo_at_location(999, 60003761) is False
    assert storage.has_bpo_at_location(1234, 60003760) is False


def test_get_blueprint_time(db):
    storage.replace_sde_data(
        types=[], groups=[], market_groups=[],
        blueprint_time=[(92201, 1, 3600.0)],
        blueprint_materials=[], blueprint_products=[],
    )
    assert storage.get_blueprint_time(92201, 1) == 3600.0
    assert storage.get_blueprint_time(92201, 8) is None


def test_explicit_path_argument_is_honoured(tmp_path):
    other = tmp_path / "other.sqlite3"
    storage.init_db(other)
    storage.save_token("buyer:1", RECORD, path=other)
    assert storage.load_all_tokens(path=other) == {"buyer:1": RECORD}
    assert other.exists()
    with pytest.raises(sqlite3.OperationalError):
        # A file that was never init_db'd has no tables - proves the path
        # argument really is what selected the database above.
        storage.load_all_tokens(path=tmp_path / "empty.sqlite3")


# --------------------------------------------------------- schema migrations
def test_fresh_db_starts_at_the_latest_schema_version(db):
    storage.init_db()
    with storage.connect() as conn:
        version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
    assert version == len(storage.MIGRATIONS)


def test_migration_is_applied_to_a_pre_existing_database_missing_a_column(db, monkeypatch):
    """Simulates the real scenario this mechanism exists for: a database
    created before some migration existed gets brought up to date the next
    time init_db() runs, without anyone manually intervening."""
    with storage.connect() as conn:
        conn.execute("CREATE TABLE probe_table (id INTEGER PRIMARY KEY)")

    def _add_probe_column(conn: sqlite3.Connection) -> None:
        if not storage._column_exists(conn, "probe_table", "probe_column"):
            conn.execute("ALTER TABLE probe_table ADD COLUMN probe_column TEXT")

    monkeypatch.setattr(storage, "MIGRATIONS", [(1, "add probe_column to probe_table", _add_probe_column)])
    storage.init_db()

    with storage.connect() as conn:
        assert storage._column_exists(conn, "probe_table", "probe_column")
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 1


def test_migrations_are_not_reapplied_once_the_version_matches(db, monkeypatch):
    calls = []

    def _tracked_migration(conn: sqlite3.Connection) -> None:
        calls.append(1)

    monkeypatch.setattr(storage, "MIGRATIONS", [(1, "tracked", _tracked_migration)])
    storage.init_db()
    storage.init_db()
    assert calls == [1]


def test_migration_functions_must_be_idempotent_even_if_replayed(db, monkeypatch):
    """The real safety net: even if `schema_version` somehow lagged behind
    (a manually edited database, a partially-applied migration from a crash),
    a migration written the documented way - checking `_column_exists` first -
    must not blow up on a column that's already there."""
    with storage.connect() as conn:
        conn.execute("CREATE TABLE probe_table (id INTEGER PRIMARY KEY, probe_column TEXT)")

    def _add_probe_column(conn: sqlite3.Connection) -> None:
        if not storage._column_exists(conn, "probe_table", "probe_column"):
            conn.execute("ALTER TABLE probe_table ADD COLUMN probe_column TEXT")

    monkeypatch.setattr(storage, "MIGRATIONS", [(1, "add probe_column to probe_table", _add_probe_column)])
    # schema_version starts at 0 even though the column already exists here
    # (mimicking a database created after SCHEMA already had the column, but
    # before this test's monkeypatched MIGRATIONS list existed) - must not
    # raise "duplicate column name".
    storage.init_db()
