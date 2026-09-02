"""SQLite persistence for eve-trader-local.

Single-user by construction: there is no tenant_id column anywhere, no row
level security, and no connection-scoped session variable to set - the
database file lives on one person's machine and everything in it belongs to
them. That is the whole reason this repo exists separately from the parent
eve-trader (see README).

Tables, ported from the parent's Postgres schema minus tenant scoping:
  tokens        <- tenant_tokens      (OAuth records, keyed by role)
  esi_sync_state <- esi_sync_state    (when each scope last synced)
  settings      <- tenant_settings    (config overrides, keyed by scope)
  sde_*         <- sde_*              (Fuzzwork SDE cache, see sde.py)

The sde_* tables carried no tenant_id even in the parent (they are CCP's own
static data, identical for everyone and refreshed globally), so they port
across unchanged apart from the SQL dialect.

JSON columns are plain TEXT holding json.dumps output - SQLite's own JSON1
functions aren't needed since nothing queries *into* these documents; they
are read and written whole.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional, Sequence

from .paths import db_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS tokens (
    role       TEXT PRIMARY KEY,
    record     TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS esi_sync_state (
    scope     TEXT PRIMARY KEY,
    synced_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    scope     TEXT PRIMARY KEY,
    overrides TEXT NOT NULL
);

-- ------------------------------------------------------------ SDE cache
-- One Fuzzwork dump snapshot (see sde.py). Column order is load-bearing:
-- replace_sde_data inserts positionally, matching the row tuples sde.py
-- builds, so a new column always goes at the *end* of a table.

CREATE TABLE IF NOT EXISTS sde_types (
    type_id         INTEGER PRIMARY KEY,
    group_id        INTEGER,
    type_name       TEXT,
    volume          REAL,
    published       INTEGER,
    market_group_id INTEGER,
    meta_level      INTEGER,
    -- The real SDE metaGroupID (invMetaTypes.csv): 1=Tech I, 2=Tech II,
    -- 3=Storyline, 4=Faction, 5=Officer, 6=Deadspace. This, not meta_level,
    -- is the authoritative field for "is this genuinely Tech II" - a
    -- Faction/Officer hull has an elevated metaLevel too, which is exactly
    -- how the parent repo once miscategorized the Machariel/Nestor as T2.
    -- NULL for a type with no invMetaTypes.csv row at all (most Tech I).
    meta_group_id   INTEGER,
    -- portionSize: the whole-batch unit reprocessing rounds down to before
    -- applying yield% (e.g. Veldspar = 100).
    portion_size    INTEGER
);

CREATE TABLE IF NOT EXISTS sde_groups (
    group_id    INTEGER PRIMARY KEY,
    category_id INTEGER,
    group_name  TEXT
);

CREATE TABLE IF NOT EXISTS sde_categories (
    category_id   INTEGER PRIMARY KEY,
    category_name TEXT
);

CREATE TABLE IF NOT EXISTS sde_market_groups (
    market_group_id   INTEGER PRIMARY KEY,
    parent_group_id   INTEGER,
    market_group_name TEXT
);

CREATE TABLE IF NOT EXISTS sde_blueprint_time (
    blueprint_type_id INTEGER,
    activity_id       INTEGER,
    time              REAL,
    PRIMARY KEY (blueprint_type_id, activity_id)
);

CREATE TABLE IF NOT EXISTS sde_blueprint_materials (
    blueprint_type_id INTEGER,
    activity_id       INTEGER,
    material_type_id  INTEGER,
    quantity          REAL
);

CREATE INDEX IF NOT EXISTS idx_sde_blueprint_materials_lookup
    ON sde_blueprint_materials (blueprint_type_id, activity_id);

CREATE TABLE IF NOT EXISTS sde_blueprint_products (
    blueprint_type_id INTEGER,
    activity_id       INTEGER,
    product_type_id   INTEGER,
    quantity          REAL,
    PRIMARY KEY (blueprint_type_id, activity_id, product_type_id)
);

CREATE INDEX IF NOT EXISTS idx_sde_blueprint_products_by_product
    ON sde_blueprint_products (product_type_id, activity_id);

CREATE TABLE IF NOT EXISTS sde_invention_probability (
    t1_blueprint_type_id INTEGER,
    product_type_id      INTEGER,
    probability          REAL,
    PRIMARY KEY (t1_blueprint_type_id, product_type_id)
);

CREATE TABLE IF NOT EXISTS sde_solar_systems (
    solar_system_id   INTEGER PRIMARY KEY,
    solar_system_name TEXT,
    security          REAL,
    region_id         INTEGER
);

CREATE TABLE IF NOT EXISTS sde_stations (
    station_id      INTEGER PRIMARY KEY,
    solar_system_id INTEGER,
    station_name    TEXT
);

-- type_id -> fitting slot, derived from the 6 slot-defining dogma effect IDs
-- (see sde.py's _SLOT_EFFECT_IDS).
CREATE TABLE IF NOT EXISTS sde_type_slots (
    type_id INTEGER PRIMARY KEY,
    slot    TEXT NOT NULL
);

-- invTypeMaterials.csv: type -> material yield per portion. Serves both the
-- ore/ice reprocessing path and the scrapmetal path.
CREATE TABLE IF NOT EXISTS sde_type_materials (
    type_id          INTEGER NOT NULL,
    material_type_id INTEGER NOT NULL,
    quantity         REAL NOT NULL,
    PRIMARY KEY (type_id, material_type_id)
);

CREATE INDEX IF NOT EXISTS idx_sde_type_materials_by_type ON sde_type_materials (type_id);

-- Single row (id = 1): when the SDE cache was last replaced, and the ETag
-- Fuzzwork served for the dump at that moment - compared against a fresh
-- HEAD request to tell whether a newer dump exists without downloading it.
CREATE TABLE IF NOT EXISTS sde_refresh_state (
    id           INTEGER PRIMARY KEY CHECK (id = 1),
    refreshed_at TEXT NOT NULL,
    dump_etag    TEXT
);
"""

# Insert order matters only for readability; the tuple arity per table is what
# must stay in step with sde.py's row builders and the SCHEMA above.
SDE_TABLES = (
    "sde_types", "sde_groups", "sde_market_groups", "sde_blueprint_time",
    "sde_blueprint_materials", "sde_blueprint_products", "sde_invention_probability",
    "sde_solar_systems", "sde_stations", "sde_categories", "sde_type_slots",
    "sde_type_materials",
)


@contextmanager
def connect(path: Optional[Path] = None) -> Iterator[sqlite3.Connection]:
    """The only place a connection is opened. Commits on clean exit, rolls
    back on exception, always closes.

    A fresh connection per use rather than one long-lived shared handle:
    sqlite3 connections are not safe to share across threads by default, and
    opening one against a local file is microseconds. WAL is set on every
    connect because it is a persistent property of the *database file* - the
    repeated PRAGMA is a no-op once the file is already in WAL mode, but it
    means a database created by any code path (including a test's tmp file)
    ends up in WAL without a separate initialisation step.
    """
    conn = sqlite3.connect(path or db_path())
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(path: Optional[Path] = None) -> None:
    """Idempotent - safe to call on every startup. At this stage there is no
    migration tool and none is needed: every statement is CREATE TABLE IF NOT
    EXISTS. Adding a *column* to an existing table later will need more than
    this; don't quietly assume this function keeps covering that case."""
    with connect(path) as conn:
        conn.executescript(SCHEMA)


# ------------------------------------------------------------------- tokens
def save_token(role: str, record: dict, path: Optional[Path] = None) -> None:
    """Upserts one OAuth token record (auth.TokenRecord via asdict)."""
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO tokens (role, record, updated_at) VALUES (?, ?, datetime('now')) "
            "ON CONFLICT(role) DO UPDATE SET record = excluded.record, updated_at = excluded.updated_at",
            (role, json.dumps(record)),
        )


def load_all_tokens(path: Optional[Path] = None) -> dict[str, dict]:
    """{role: record} for every stored token - TokenManager always operates
    on its whole store at once, never a single role in isolation."""
    with connect(path) as conn:
        rows = conn.execute("SELECT role, record FROM tokens").fetchall()
    return {row["role"]: json.loads(row["record"]) for row in rows}


def delete_token(role: str, path: Optional[Path] = None) -> None:
    """Idempotent - deleting a role with no stored row is a no-op, so callers
    never need to check existence first."""
    with connect(path) as conn:
        conn.execute("DELETE FROM tokens WHERE role = ?", (role,))


# ----------------------------------------------------------- esi sync state
def set_esi_sync_time(scope: str, synced_at: str, path: Optional[Path] = None) -> None:
    """`synced_at`: ISO-8601 UTC timestamp."""
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO esi_sync_state (scope, synced_at) VALUES (?, ?) "
            "ON CONFLICT(scope) DO UPDATE SET synced_at = excluded.synced_at",
            (scope, synced_at),
        )


def get_esi_sync_time(scope: str, path: Optional[Path] = None) -> Optional[str]:
    with connect(path) as conn:
        row = conn.execute("SELECT synced_at FROM esi_sync_state WHERE scope = ?", (scope,)).fetchone()
    return row["synced_at"] if row else None


# ----------------------------------------------------------------- settings
def save_settings(scope: str, updates: dict[str, Any], path: Optional[Path] = None) -> None:
    """Merges `updates` into the stored overrides for `scope` - per-key "last
    save wins", never clobbering unrelated keys. Postgres did this with its
    JSONB `||` operator; SQLite has no equivalent that works on a TEXT
    column, so the merge happens in Python inside one transaction (read and
    write share the same connection, so no other writer can interleave).

    Never validates - the caller (config.save_config_overrides) is
    responsible for that, same contract as the parent repo's version.
    """
    with connect(path) as conn:
        row = conn.execute("SELECT overrides FROM settings WHERE scope = ?", (scope,)).fetchone()
        merged = json.loads(row["overrides"]) if row else {}
        merged.update(updates)
        conn.execute(
            "INSERT INTO settings (scope, overrides) VALUES (?, ?) "
            "ON CONFLICT(scope) DO UPDATE SET overrides = excluded.overrides",
            (scope, json.dumps(merged)),
        )


def load_settings(scope: str, path: Optional[Path] = None) -> dict[str, Any]:
    """{} when nothing has been saved for `scope` yet."""
    with connect(path) as conn:
        row = conn.execute("SELECT overrides FROM settings WHERE scope = ?", (scope,)).fetchone()
    return json.loads(row["overrides"]) if row else {}


# ---------------------------------------------------------------- SDE cache
def replace_sde_data(
    types: Sequence[tuple], groups: Sequence[tuple], market_groups: Sequence[tuple],
    blueprint_time: Sequence[tuple], blueprint_materials: Sequence[tuple],
    blueprint_products: Sequence[tuple],
    invention_probability: Sequence[tuple] = (), solar_systems: Sequence[tuple] = (),
    stations: Sequence[tuple] = (), categories: Sequence[tuple] = (),
    type_slots: Sequence[tuple] = (), type_materials: Sequence[tuple] = (),
    path: Optional[Path] = None,
) -> None:
    """Wholesale-replaces the SDE cache tables (each refresh reflects one
    Fuzzwork dump snapshot, not an incremental merge - stale rows from a
    previous CCP patch would otherwise linger).

    Everything happens inside a single transaction: `connect()` rolls back on
    any exception, so a refresh that fails halfway (a malformed row, a full
    disk) leaves the previous SDE cache fully intact rather than half-deleted.
    """
    with connect(path) as conn:
        for table in SDE_TABLES:
            conn.execute(f"DELETE FROM {table}")
        conn.executemany("INSERT INTO sde_types VALUES (?,?,?,?,?,?,?,?,?)", types)
        conn.executemany("INSERT INTO sde_groups VALUES (?,?,?)", groups)
        conn.executemany("INSERT INTO sde_market_groups VALUES (?,?,?)", market_groups)
        conn.executemany("INSERT INTO sde_blueprint_time VALUES (?,?,?)", blueprint_time)
        conn.executemany("INSERT INTO sde_blueprint_materials VALUES (?,?,?,?)", blueprint_materials)
        conn.executemany("INSERT INTO sde_blueprint_products VALUES (?,?,?,?)", blueprint_products)
        conn.executemany("INSERT INTO sde_invention_probability VALUES (?,?,?)", invention_probability)
        conn.executemany("INSERT INTO sde_solar_systems VALUES (?,?,?,?)", solar_systems)
        conn.executemany("INSERT INTO sde_stations VALUES (?,?,?)", stations)
        conn.executemany("INSERT INTO sde_categories VALUES (?,?)", categories)
        conn.executemany("INSERT INTO sde_type_slots VALUES (?,?)", type_slots)
        conn.executemany("INSERT INTO sde_type_materials VALUES (?,?,?)", type_materials)


def sde_row_counts(path: Optional[Path] = None) -> dict[str, int]:
    """{table: row count} for every SDE table - what the CLI prints after a
    refresh, and the cheapest way to tell an empty cache from a populated
    one."""
    with connect(path) as conn:
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in SDE_TABLES}


def set_sde_refresh_state(refreshed_at: str, dump_etag: Optional[str],
                          path: Optional[Path] = None) -> None:
    """Records when refresh_sde() last completed and the Fuzzwork dump's ETag
    at that moment - see sde_refresh_state's schema comment."""
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO sde_refresh_state (id, refreshed_at, dump_etag) VALUES (1, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET refreshed_at = excluded.refreshed_at, "
            "dump_etag = excluded.dump_etag",
            (refreshed_at, dump_etag),
        )


def get_sde_refresh_state(path: Optional[Path] = None) -> Optional[tuple[str, Optional[str]]]:
    """(refreshed_at, dump_etag) from the last refresh_sde(), or None if the
    SDE cache has never been refreshed on this machine."""
    with connect(path) as conn:
        row = conn.execute(
            "SELECT refreshed_at, dump_etag FROM sde_refresh_state WHERE id = 1"
        ).fetchone()
    return tuple(row) if row else None


def get_sde_type(type_id: int, path: Optional[Path] = None) -> Optional[tuple]:
    """One sde_types row as a plain tuple, in the schema's column order
    (type_id, group_id, type_name, volume, published, market_group_id,
    meta_level, meta_group_id, portion_size), or None if the type isn't in
    the cache.

    Not cached (the parent repo memoises this with lru_cache because a
    recursive bill-of-materials traversal hits the same type_ids over and
    over) - no such caller exists here yet, and a cache would need explicit
    invalidation on every refresh."""
    with connect(path) as conn:
        row = conn.execute("SELECT * FROM sde_types WHERE type_id = ?", (type_id,)).fetchone()
    return tuple(row) if row else None


def search_sde_types(query: str, limit: int = 20,
                     path: Optional[Path] = None) -> list[tuple[int, str]]:
    """Substring name lookup over published types. An exact (case-insensitive)
    match always sorts first, regardless of `limit` - otherwise e.g. "Vexor"
    could get pushed out of a small limit by the many longer names
    ("Guardian-Vexor", ...) that also contain it as a substring and sort
    earlier."""
    query = query.strip()
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT type_id, type_name FROM sde_types "
            "WHERE published = 1 AND type_name LIKE ? "
            "ORDER BY (LOWER(type_name) <> LOWER(?)), type_name LIMIT ?",
            (f"%{query}%", query, limit),
        ).fetchall()
    return [tuple(r) for r in rows]
