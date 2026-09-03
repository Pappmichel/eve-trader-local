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
  type_packaged_volume <- type_packaged_volume  (ESI-only per-type constant)
  candidate_universe / focused_candidates <- same names (see
                                             candidate_discovery.py)
  shortlist / shortlist_snapshot <- same names (see shortlist.py)
  realized_trades <- same name (see trade_reconciliation.py)

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
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional, Sequence

from .models import Candidate, NewCandidateResult, RealizedTrade, ShortlistItem, ShortlistRow
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

-- type_id -> packaged (repackaged/cargo) volume, as answered by ESI. Not part
-- of the SDE dump at all - only ESI exposes it - so this is a cache of a live
-- lookup, not a replace_sde_data target, and it deliberately survives an SDE
-- refresh (a type's packaged volume is a static game constant; re-fetching it
-- per refresh would be hundreds of pointless ESI round-trips).
CREATE TABLE IF NOT EXISTS type_packaged_volume (
    type_id         INTEGER PRIMARY KEY,
    packaged_volume REAL NOT NULL
);

-- ------------------------------------------------- candidate discovery
-- The market-group-derived candidate universe, and the focused subset built
-- from it (see candidate_discovery.py). Both are "current state" snapshots,
-- not append-only history: each run replaces the whole table (run_ts records
-- when that happened), so runs never accumulate duplicates.
CREATE TABLE IF NOT EXISTS candidate_universe (
    run_ts            TEXT,
    item              TEXT,
    type_id           INTEGER,
    volume_m3         REAL,
    category          TEXT,
    market_group_path TEXT,
    meta_level        INTEGER
);

CREATE TABLE IF NOT EXISTS focused_candidates (
    run_ts            TEXT,
    item              TEXT,
    type_id           INTEGER,
    volume_m3         REAL,
    category          TEXT,
    market_group_path TEXT,
    meta_level        INTEGER
);

-- ---------------------------------------------------------- shortlist
-- Two tables with deliberately different shapes (the parent repo's own
-- split): `shortlist` is the live membership list, keyed by item_id so an
-- upsert edits the one row in place; `shortlist_snapshot` is append-only
-- history, one full set of evaluated rows per run_ts, so past runs stay
-- readable after an item's economics change or it gets deactivated.
CREATE TABLE IF NOT EXISTS shortlist (
    item_id    INTEGER PRIMARY KEY,
    item       TEXT NOT NULL,
    category   TEXT,
    volume_m3  REAL,
    active     INTEGER DEFAULT 1,
    meta_level INTEGER
);

CREATE TABLE IF NOT EXISTS shortlist_snapshot (
    run_ts               TEXT,
    item_id              INTEGER,
    item                 TEXT,
    category             TEXT,
    landed_cost          REAL,
    net_sell             REAL,
    sell_volume          REAL,
    own_orders_remaining REAL,
    profit_per_unit      REAL,
    margin               REAL,
    profit_per_m3        REAL,
    decision             TEXT,
    active               INTEGER,
    volume_m3            REAL,
    jita_sell            REAL,
    import_cost          REAL,
    meta_level           INTEGER,
    avg_daily_volume     REAL
);
-- The parent needed an explicit DOUBLE PRECISION widening for the six
-- ISK-denominated columns above (Postgres REAL is single precision, ~7
-- significant digits - not enough for a capital hull's landed cost).
-- SQLite's REAL is already a 64-bit IEEE double, so there is nothing to
-- widen here; the same column names are kept for readability.

-- ------------------------------------------------- realized trade history
-- One row per FIFO-matched buy/sell pair from the last reconciliation run
-- (see trade_reconciliation.py). Replaced wholesale every run, not appended:
-- a run re-matches the entire lookback window from scratch, so older runs'
-- rows are both near-fully redundant and unreachable (every read filters to
-- MAX(run_ts)). The parent repo learned this the hard way - a naked INSERT
-- there had bloated the table to ~19.8k rows across only 13 runs.
CREATE TABLE IF NOT EXISTS realized_trades (
    run_ts          TEXT,
    type_id         INTEGER,
    item            TEXT,
    buy_date        TEXT,
    buy_qty         INTEGER,
    buy_unit_price  REAL,
    sell_date       TEXT,
    sell_qty        INTEGER,
    sell_unit_price REAL,
    matched_qty     INTEGER,
    realized_profit REAL,
    margin          REAL
);

-- ------------------------------------------------ candidate search results
-- One row per backtested candidate per search run (see history_backtest.py).
-- Append-only, unlike candidate_universe/focused_candidates: a "safe" run
-- only ever covers a rotating window of the universe, so previous runs'
-- results are the rest of the picture, not stale duplicates. Reads always
-- filter to MAX(run_ts) themselves.
CREATE TABLE IF NOT EXISTS new_candidates (
    run_ts            TEXT,
    item              TEXT,
    category          TEXT,
    type_id           INTEGER,
    volume_m3         REAL,
    paired_days       INTEGER,
    profitable_days   INTEGER,
    hit_rate          REAL,
    latest_margin     REAL,
    best_margin       REAL,
    avg_profit_m3     REAL,
    avg_sell_movement REAL,
    score             REAL,
    recommendation    TEXT,
    add_flag          INTEGER,
    meta_level        INTEGER
);

-- Goonmetrics region price history, cached as it is fetched during a
-- candidate search (history_sink) so the same days don't have to be
-- re-downloaded to answer a later question about them (margin trends).
CREATE TABLE IF NOT EXISTS goonmetrics_history (
    region_id  INTEGER,
    type_id    INTEGER,
    date       TEXT,
    min_price  REAL,
    max_price  REAL,
    avg_price  REAL,
    movement   REAL,
    num_orders INTEGER,
    PRIMARY KEY (region_id, type_id, date)
);

-- Single row (id = 1): where the next "safe" (rate-limited) candidate search
-- resumes from. Safe mode only tests a window of the universe per run, so
-- without a persisted cursor the same head of the list would be re-tested
-- every time and the tail never at all.
CREATE TABLE IF NOT EXISTS candidate_search_cursor (
    id           INTEGER PRIMARY KEY CHECK (id = 1),
    offset_value INTEGER NOT NULL
);

-- When each item's current unbroken "No market data"/"Skip" streak started.
-- A row exists only while a streak is in progress: it is deleted the moment
-- the item is profitable again, and once a streak has actually led to
-- deactivation (see actions.do_refresh_and_prune_candidates).
CREATE TABLE IF NOT EXISTS shortlist_skip_streak (
    item_id    INTEGER PRIMARY KEY,
    skip_since TEXT NOT NULL
);

-- ------------------------------------------- Production: ESI-synced ownership
-- What a producer character (and their corp) actually owns right now: assets,
-- blueprints with their real ME/TE, and industry jobs in progress. All six are
-- "current snapshot" tables like candidate_universe/realized_trades - every
-- sync replaces them wholesale, so a sold blueprint or a delivered job
-- disappears instead of lingering forever (see production/esi_sync.py).
--
-- location_id is the item's *immediate* parent, which may itself be a
-- container, a ship, or a corp Office rather than a station/structure -
-- resolved_location_id is that chain walked all the way up to the outermost
-- station/structure, computed once at sync time (see _resolve_locations) so
-- every read filters on it directly. Resolving only one level, the parent
-- repo's original bug, made stock inside a container inside a corp hangar
-- read as simply absent.

CREATE TABLE IF NOT EXISTS character_assets (
    item_id              INTEGER PRIMARY KEY,
    type_id              INTEGER,
    location_id          INTEGER,
    location_flag        TEXT,
    quantity             INTEGER,
    is_blueprint_copy    INTEGER,
    owner_name           TEXT,
    resolved_location_id INTEGER
);

CREATE INDEX IF NOT EXISTS idx_character_assets_type_resolved_location
    ON character_assets (type_id, resolved_location_id);

CREATE TABLE IF NOT EXISTS corp_assets (
    item_id              INTEGER PRIMARY KEY,
    type_id              INTEGER,
    location_id          INTEGER,
    location_flag        TEXT,
    quantity             INTEGER,
    is_blueprint_copy    INTEGER,
    owner_name           TEXT,
    resolved_location_id INTEGER
);

CREATE INDEX IF NOT EXISTS idx_corp_assets_type_resolved_location
    ON corp_assets (type_id, resolved_location_id);

CREATE TABLE IF NOT EXISTS character_industry_jobs (
    job_id             INTEGER PRIMARY KEY,
    activity_id        INTEGER,
    blueprint_type_id  INTEGER,
    product_type_id    INTEGER,
    runs               INTEGER,
    output_location_id INTEGER,
    status             TEXT,
    end_date           TEXT,
    start_date         TEXT,
    installer_id       INTEGER,
    installer_name     TEXT
);

CREATE INDEX IF NOT EXISTS idx_character_industry_jobs_product_status
    ON character_industry_jobs (product_type_id, status);

CREATE TABLE IF NOT EXISTS corp_industry_jobs (
    job_id             INTEGER PRIMARY KEY,
    activity_id        INTEGER,
    blueprint_type_id  INTEGER,
    product_type_id    INTEGER,
    runs               INTEGER,
    output_location_id INTEGER,
    status             TEXT,
    end_date           TEXT,
    start_date         TEXT,
    installer_id       INTEGER,
    installer_name     TEXT
);

CREATE INDEX IF NOT EXISTS idx_corp_industry_jobs_product_status
    ON corp_industry_jobs (product_type_id, status);

-- ESI's blueprint model overloads two columns with sentinels: runs = -1 marks
-- an Original (a BPO, infinitely reusable), and quantity = -2 marks a copy
-- (a BPC). Only a BPO's ME/TE reflects *your* research - a BPC's was fixed by
-- whoever copied it - which is why get_owned_bpo_best_me_te filters on
-- runs = -1.
CREATE TABLE IF NOT EXISTS character_blueprints (
    item_id              INTEGER PRIMARY KEY,
    type_id              INTEGER,
    location_id          INTEGER,
    location_flag        TEXT,
    quantity             INTEGER,
    material_efficiency  INTEGER,
    time_efficiency      INTEGER,
    runs                 INTEGER,
    resolved_location_id INTEGER
);

CREATE INDEX IF NOT EXISTS idx_character_blueprints_type_runs
    ON character_blueprints (type_id, runs);

CREATE TABLE IF NOT EXISTS corp_blueprints (
    item_id              INTEGER PRIMARY KEY,
    type_id              INTEGER,
    location_id          INTEGER,
    location_flag        TEXT,
    quantity             INTEGER,
    material_efficiency  INTEGER,
    time_efficiency      INTEGER,
    runs                 INTEGER,
    resolved_location_id INTEGER
);

CREATE INDEX IF NOT EXISTS idx_corp_blueprints_type_runs
    ON corp_blueprints (type_id, runs);

-- Single row (id = 1): when the SDE cache was last replaced, and the ETag
-- Fuzzwork served for the dump at that moment - compared against a fresh
-- HEAD request to tell whether a newer dump exists without downloading it.
CREATE TABLE IF NOT EXISTS sde_refresh_state (
    id           INTEGER PRIMARY KEY CHECK (id = 1),
    refreshed_at TEXT NOT NULL,
    dump_etag    TEXT
);

-- What the stock-aware planner (production/engine.py's plan_production) plans
-- for: "I want `quantity` units of `type_id` kept in stock". Deliberately
-- simpler than the parent repo's three-way backup/home-market/Jita-market
-- split (three independent target quantities plus live sell-order-listing
-- nets against each) - the user confirmed the simplest useful shape instead:
-- one target quantity, plus jita_target as the whole "where would this sell"
-- distinction, feeding engine._build_margin's home-vs-Jita dispatch. Nothing
-- here tracks live market-listing volume the way the parent's
-- sell_order_qty_at_location/_in_region checks do.
CREATE TABLE IF NOT EXISTS stock_targets (
    type_id     INTEGER PRIMARY KEY,
    type_name   TEXT NOT NULL,
    quantity    REAL NOT NULL,
    jita_target INTEGER NOT NULL DEFAULT 0
);

-- ------------------------------------------------------------------ Doctrine
-- Ported from the parent's docs/doctrine_schema.sql minus tenant_id/RLS - see
-- that file for the full multi-tenant reasoning behind each shape. ids are
-- plain TEXT UUIDs generated in Python (str(uuid.uuid4())) rather than
-- Postgres's gen_random_uuid() default.
--
-- Deliberately not ported: doctrine_contract_history (GitHub issue #19's
-- separate append-only "who bought what and when" log) - a real feature, but
-- additive on top of the contract sync/matching this step exists to prove
-- end-to-end; left for a future pass, same "documented, not started" status
-- as the Shopping List (needs DoctrineConfig.import_cost_per_m3 plus
-- Production's build-cost engine, no consumer here yet either).

CREATE TABLE IF NOT EXISTS doctrines (
    doctrine_id TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS doctrine_fittings (
    fitting_id           TEXT PRIMARY KEY,
    doctrine_id          TEXT NOT NULL,
    name                 TEXT NOT NULL,
    variant_label        TEXT,
    hull_type_id         INTEGER NOT NULL,
    raw_eft              TEXT NOT NULL,
    contract_target      INTEGER NOT NULL DEFAULT 0,
    stockpile_target     INTEGER NOT NULL DEFAULT 0,
    cargo_tolerance_pct  REAL,
    active               INTEGER NOT NULL DEFAULT 1,
    created_at           TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at           TEXT NOT NULL DEFAULT (datetime('now')),
    -- GitHub issue #18: Fuel Bay / Ship Maintenance Bay plain-text item
    -- lists, entered/stored separately from raw_eft - see doctrine/
    -- parser.py's parse_bay_items. NULL means "not a capital fit".
    fuel_bay_text              TEXT,
    ship_maintenance_bay_text  TEXT
);
CREATE INDEX IF NOT EXISTS idx_doctrine_fittings_doctrine ON doctrine_fittings (doctrine_id);

-- Composite PK (fitting_id, line_no, item_index): one EFT line can produce
-- two items (a module + its comma-paired charge) sharing the same line_no -
-- item_index (0, then 1) discriminates them, assigned by
-- replace_fitting_items from input order, never passed in by callers.
CREATE TABLE IF NOT EXISTS doctrine_fitting_items (
    fitting_id    TEXT NOT NULL,
    line_no       INTEGER NOT NULL,
    item_index    INTEGER NOT NULL DEFAULT 0,
    slot_section  TEXT NOT NULL,
    type_id       INTEGER NOT NULL,
    quantity      REAL NOT NULL DEFAULT 1,
    is_offline    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (fitting_id, line_no, item_index)
);
CREATE INDEX IF NOT EXISTS idx_doctrine_fitting_items_fitting ON doctrine_fitting_items (fitting_id);

CREATE TABLE IF NOT EXISTS doctrine_fitting_parse_issues (
    fitting_id  TEXT NOT NULL,
    line_no     INTEGER NOT NULL,
    raw_line    TEXT NOT NULL,
    issue_kind  TEXT NOT NULL,
    message     TEXT NOT NULL,
    PRIMARY KEY (fitting_id, line_no)
);

-- Single-user, so no tenant/contract_id collision risk (the parent's
-- composite PK exists purely for that) - contract_id alone is the PK here.
CREATE TABLE IF NOT EXISTS doctrine_contracts (
    contract_id         INTEGER PRIMARY KEY,
    source_role         TEXT NOT NULL,
    for_corporation     INTEGER NOT NULL DEFAULT 0,
    issuer_id           INTEGER,
    start_location_id   INTEGER,
    status              TEXT NOT NULL,
    title               TEXT,
    price               REAL,
    date_expired        TEXT,
    matched_fitting_id  TEXT,
    match_score         REAL,
    validation_status   TEXT NOT NULL,
    synced_at           TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_doctrine_contracts_fitting ON doctrine_contracts (matched_fitting_id);
CREATE INDEX IF NOT EXISTS idx_doctrine_contracts_status ON doctrine_contracts (validation_status);

CREATE TABLE IF NOT EXISTS doctrine_contract_items (
    contract_id   INTEGER NOT NULL,
    record_id     INTEGER NOT NULL,
    type_id       INTEGER NOT NULL,
    quantity      REAL NOT NULL,
    is_included   INTEGER NOT NULL DEFAULT 1,
    is_singleton  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (contract_id, record_id)
);
CREATE INDEX IF NOT EXISTS idx_doctrine_contract_items_contract ON doctrine_contract_items (contract_id);

CREATE TABLE IF NOT EXISTS doctrine_contract_deviations (
    contract_id   INTEGER NOT NULL,
    type_id       INTEGER NOT NULL,
    kind          TEXT NOT NULL,
    expected_qty  REAL NOT NULL DEFAULT 0,
    actual_qty    REAL NOT NULL DEFAULT 0,
    severity      TEXT NOT NULL,
    PRIMARY KEY (contract_id, type_id, kind)
);
CREATE INDEX IF NOT EXISTS idx_doctrine_deviations_contract ON doctrine_contract_deviations (contract_id);

-- Doctrine's own ESI-synced asset cache (Stockpile's Ist side) - kept
-- separate from Production's character_assets/corp_assets so Doctrine works
-- standalone without ever requiring Production to have been set up, same
-- reasoning the parent's own schema comment gives. Same shape/resolved_
-- location_id convention as those tables (see replace_assets).
CREATE TABLE IF NOT EXISTS doctrine_character_assets (
    item_id               INTEGER PRIMARY KEY,
    type_id               INTEGER,
    location_id           INTEGER,
    location_flag         TEXT,
    quantity              INTEGER,
    is_blueprint_copy     INTEGER,
    owner_name            TEXT,
    resolved_location_id  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_doctrine_character_assets_type_resolved_location
    ON doctrine_character_assets (type_id, resolved_location_id);

CREATE TABLE IF NOT EXISTS doctrine_corp_assets (
    item_id               INTEGER PRIMARY KEY,
    type_id               INTEGER,
    location_id           INTEGER,
    location_flag         TEXT,
    quantity              INTEGER,
    is_blueprint_copy     INTEGER,
    owner_name            TEXT,
    resolved_location_id  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_doctrine_corp_assets_type_resolved_location
    ON doctrine_corp_assets (type_id, resolved_location_id);
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


def get_portion_size(type_id: int, path: Optional[Path] = None) -> Optional[int]:
    """The SDE's `portionSize` for `type_id` (GitHub issue #90, "Ore &
    Minerals") - reprocessing rounds down to whole portions before applying
    yield% (see refining/reprocessing.py's apply_reprocessing_yield), not a
    continuous approximation. None if the type isn't in the SDE cache or
    predates this field."""
    row = get_sde_type(type_id, path)
    return row[8] if row else None


def get_type_materials(type_id: int, path: Optional[Path] = None) -> list[tuple[int, float]]:
    """Returns [(material_type_id, quantity_per_portion), ...] from the SDE's
    invTypeMaterials.csv - GitHub issue #90's "Ore & Minerals" feature. Used
    by both the ore/ice and scrapmetal reprocessing paths (refining/
    reprocessing.py) - both are "type -> material yield" lookups against this
    same table. `quantity_per_portion` is the raw SDE quantity, before any
    yield% or portion-size-batch rounding is applied (see
    apply_reprocessing_yield). Not cached - see get_sde_type's own docstring
    for why."""
    with connect(path) as conn:
        return conn.execute(
            "SELECT material_type_id, quantity FROM sde_type_materials WHERE type_id = ? "
            "ORDER BY material_type_id",
            (type_id,),
        ).fetchall()


def get_type_category(type_id: int, path: Optional[Path] = None) -> Optional[int]:
    """The SDE category_id for a type (via its group), or None if either the
    type or its group is missing from the cache. Category - not group - is
    what distinguishes a ship/module from everything else (see
    esi_client.resolve_effective_volume)."""
    with connect(path) as conn:
        row = conn.execute(
            "SELECT g.category_id FROM sde_types t "
            "JOIN sde_groups g ON g.group_id = t.group_id WHERE t.type_id = ?",
            (type_id,),
        ).fetchone()
    return row[0] if row else None


def get_system_security(system_id: Optional[int], path: Optional[Path] = None) -> Optional[float]:
    """Static per-system security status from the local SDE cache (it never
    changes in-game, so there is no reason to ask ESI for it) - used to scale
    a structure's rig ME/TE bonus, see production/constants.py's
    rig_security_multiplier. None for an unset or unknown system, which that
    function treats as "no rig bonus at all" rather than guessing a value."""
    if system_id is None:
        return None
    with connect(path) as conn:
        row = conn.execute(
            "SELECT security FROM sde_solar_systems WHERE solar_system_id = ?", (system_id,)
        ).fetchone()
    return row[0] if row else None


def get_cached_packaged_volume(type_id: int, path: Optional[Path] = None) -> Optional[float]:
    with connect(path) as conn:
        row = conn.execute(
            "SELECT packaged_volume FROM type_packaged_volume WHERE type_id = ?", (type_id,)
        ).fetchone()
    return row[0] if row else None


def set_cached_packaged_volume(type_id: int, volume: float, path: Optional[Path] = None) -> None:
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO type_packaged_volume (type_id, packaged_volume) VALUES (?, ?) "
            "ON CONFLICT (type_id) DO UPDATE SET packaged_volume = excluded.packaged_volume",
            (type_id, volume),
        )


def get_blueprint_for_product(product_type_id: int,
                              path: Optional[Path] = None) -> Optional[tuple[int, int, float]]:
    """(blueprint_type_id, activity_id, product_qty) for the blueprint/formula
    that produces `product_type_id` via Manufacturing (1) or Reaction (11),
    preferring Manufacturing if (implausibly) both exist. None if the type
    isn't producible.

    Confirmed real bug in the parent repo: some products (e.g. Tungsten
    Carbide, type 16672) have a leftover *unpublished* blueprint row in the
    SDE (CCP test/legacy data, e.g. "Test Reaction Blueprint") alongside the
    real published one, with wildly different quantity/materials -
    `t.published = 1` excludes those rows outright rather than merely
    deprioritizing them, since a product whose *only* blueprint is unpublished
    (e.g. Freki/Utu - CCP-unpublished, Faction-meta ships that
    classify_activity would otherwise treat as buildable, confirmed live
    2026-08-19) isn't actually buildable by any player either. The extra
    `blueprint_type_id` tiebreak keeps the choice deterministic if several
    published blueprints somehow tie."""
    with connect(path) as conn:
        row = conn.execute(
            "SELECT p.blueprint_type_id, p.activity_id, p.quantity FROM sde_blueprint_products p "
            "JOIN sde_types t ON t.type_id = p.blueprint_type_id "
            "WHERE p.product_type_id = ? AND p.activity_id IN (1, 11) AND t.published = 1 "
            "ORDER BY p.activity_id, p.blueprint_type_id LIMIT 1",
            (product_type_id,),
        ).fetchone()
    return tuple(row) if row else None


def find_invention_recipe_candidates_by_product_type_id(
    product_blueprint_type_id: int, path: Optional[Path] = None
) -> tuple[int, ...]:
    """Given a T2/T3 *blueprint*'s type_id (e.g. from
    get_blueprint_for_product), every valid invention source for it
    (best-probability-first), or an empty tuple if it isn't an invented type
    (a T1 item, or a BPO that was never invention-sourced). This emptiness is
    what production/engine.classify_activity relies on to keep Faction/Officer
    items out of "Tech II".

    Confirmed real in the parent repo: 79 T2/T3 products have *more than one*
    valid invention source in the SDE - most visibly every Tech III
    hull/subsystem, which has 3 relic "blueprints" (Intact/Malfunctioning/
    Wrecked, see constants.ANCIENT_RELIC_CATEGORY_ID) with materially
    different success probability (0.26/0.21/0.14) *and* output runs
    (20/10/3) but identical materials/time otherwise - a real
    buy-cost-vs-odds tradeoff, not a tiebreak to collapse away. An earlier
    single-result version (`ORDER BY probability DESC LIMIT 1`) silently made
    the cheaper Malfunctioning/Wrecked grades unconsiderable (reported by a
    user, 2026-08-30). The ordering here just gives a deterministic first
    element; picking the cheapest net cost across candidates x decryptors is
    the invention module's job, which isn't ported here yet."""
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT p.blueprint_type_id FROM sde_blueprint_products p "
            "LEFT JOIN sde_invention_probability prob "
            "  ON prob.t1_blueprint_type_id = p.blueprint_type_id "
            "  AND prob.product_type_id = p.product_type_id "
            "WHERE p.activity_id = 8 AND p.product_type_id = ? "
            "ORDER BY prob.probability DESC, p.blueprint_type_id",
            (product_blueprint_type_id,),
        ).fetchall()
    return tuple(row[0] for row in rows)


def get_blueprint_materials(blueprint_type_id: int, activity_id: int,
                            path: Optional[Path] = None) -> list[tuple[int, float]]:
    """[(material_type_id, quantity), ...] for ONE run at ME 0, before any
    reduction - applying a blueprint's/decryptor's own ME is the caller's
    job."""
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT material_type_id, quantity FROM sde_blueprint_materials "
            "WHERE blueprint_type_id = ? AND activity_id = ?",
            (blueprint_type_id, activity_id),
        ).fetchall()
    return [tuple(r) for r in rows]


def get_invention_recipe(t1_blueprint_type_id: int,
                         path: Optional[Path] = None) -> Optional[dict]:
    """The full invention (activity 8) job definition for
    `t1_blueprint_type_id` - the invention *source*, which is a genuine T1
    blueprint for Tech II and a Sleeper relic for Tech III (see
    constants.ANCIENT_RELIC_CATEGORY_ID). None if it invents nothing at all.

    Keys: product_type_id (the invented blueprint), base_runs (the resulting
    BPC's run count before any decryptor bonus), base_probability (None when
    the SDE has the recipe but no probability row - callers must treat that as
    "can't estimate", never as 0), job_time, and datacores
    ([(material_type_id, quantity), ...]: activity 8's "materials" are the
    datacores one attempt consumes).

    Not cached, same reasoning as get_sde_type. The parent repo memoises this
    one specifically because its production planner re-queries the same
    blueprint once per decryptor candidate (~12x per item, thousands of times
    per run); no such planner exists here yet, and a cache would need explicit
    invalidation on every SDE refresh."""
    with connect(path) as conn:
        product = conn.execute(
            "SELECT product_type_id, quantity FROM sde_blueprint_products "
            "WHERE blueprint_type_id = ? AND activity_id = 8",
            (t1_blueprint_type_id,),
        ).fetchone()
        if product is None:
            return None
        product_type_id, base_runs = product
        prob = conn.execute(
            "SELECT probability FROM sde_invention_probability "
            "WHERE t1_blueprint_type_id = ? AND product_type_id = ?",
            (t1_blueprint_type_id, product_type_id),
        ).fetchone()
        time_row = conn.execute(
            "SELECT time FROM sde_blueprint_time WHERE blueprint_type_id = ? AND activity_id = 8",
            (t1_blueprint_type_id,),
        ).fetchone()
        materials = conn.execute(
            "SELECT material_type_id, quantity FROM sde_blueprint_materials "
            "WHERE blueprint_type_id = ? AND activity_id = 8",
            (t1_blueprint_type_id,),
        ).fetchall()
    return {
        "product_type_id": product_type_id,
        "base_runs": base_runs,
        "base_probability": prob[0] if prob else None,
        "job_time": time_row[0] if time_row else None,
        "datacores": [tuple(r) for r in materials],
    }


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


def get_type_slot(type_id: int, path: Optional[Path] = None) -> Optional[str]:
    """Returns the type's fitting slot ("low"|"med"|"high"|"rig"|"subsystem"|
    "service"), derived from dgmTypeEffects.csv's slot-defining dogma
    effects (see production/sde.py's refresh_sde) - None if the type has no
    slot effect at all (not fittable - ammo, drones, ships, ...). Used by
    doctrine/parser.py's SDE-verification step (injected as a callable, not
    called by parser.py directly), not by anything Trading/Production-side.
    Not cached - see get_sde_type's own docstring for why."""
    with connect(path) as conn:
        row = conn.execute("SELECT slot FROM sde_type_slots WHERE type_id = ?", (type_id,)).fetchone()
    return row[0] if row else None


def resolve_sde_type_by_name(name: str, path: Optional[Path] = None) -> Optional[tuple]:
    """Exact, case-insensitive type-name lookup for doctrine/parser.py's
    injected name resolver - (type_id, group_id, category_id, meta_group_id,
    meta_level, type_name), or None. Deliberately exact-only (no fuzzy
    matching in real resolution, ever - see parser.py's own docstring);
    Levenshtein suggestions for a *failed* lookup use list_hull_type_names
    separately, only for the hull-name case."""
    with connect(path) as conn:
        row = conn.execute(
            "SELECT t.type_id, t.group_id, g.category_id, t.meta_group_id, t.meta_level, t.type_name "
            "FROM sde_types t JOIN sde_groups g ON g.group_id = t.group_id "
            "WHERE LOWER(t.type_name) = LOWER(?) LIMIT 1",
            (name,),
        ).fetchone()
    return tuple(row) if row else None


def list_hull_type_names(path: Optional[Path] = None) -> list[str]:
    """Every published ship/structure type name - used only to compute a
    Levenshtein "did you mean" suggestion when a fitting's hull name fails
    to resolve (doctrine/parser.py's hull_name_candidates)."""
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT t.type_name FROM sde_types t JOIN sde_groups g ON g.group_id = t.group_id "
            "WHERE g.category_id IN (6, 65) AND t.published = 1"
        ).fetchall()
    return [r[0] for r in rows]


def load_sde_category_names(path: Optional[Path] = None) -> dict[int, str]:
    """category_id -> real SDE category name (e.g. 7 -> "Module", 20 ->
    "Implant") - lets candidate_discovery.guess_category show the actual EVE
    category instead of a crude Module-vs-everything-else split."""
    with connect(path) as conn:
        rows = conn.execute("SELECT category_id, category_name FROM sde_categories").fetchall()
    return {r[0]: r[1] for r in rows}


def load_sde_market_groups(path: Optional[Path] = None) -> list[tuple[int, Optional[int], str]]:
    """(market_group_id, parent_group_id, market_group_name) for every cached
    market group - static data (see sde.py), used by candidate_discovery.py to
    build the candidate universe locally instead of walking ESI's market-group
    tree live."""
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT market_group_id, parent_group_id, market_group_name FROM sde_market_groups"
        ).fetchall()
    return [tuple(r) for r in rows]


def load_sde_types_with_market_group(
    path: Optional[Path] = None,
) -> list[tuple[int, str, float, int, Optional[int], Optional[int]]]:
    """(type_id, type_name, volume, market_group_id, meta_level, category_id)
    for every published, market-grouped type. category_id (joined via
    sde_groups) is what lets candidate_discovery.guess_category classify by
    real SDE data instead of string-matching "module"/"rig" in the item name
    or market-group path."""
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT t.type_id, t.type_name, t.volume, t.market_group_id, t.meta_level, g.category_id "
            "FROM sde_types t JOIN sde_groups g ON g.group_id = t.group_id "
            "WHERE t.published = 1 AND t.market_group_id IS NOT NULL"
        ).fetchall()
    return [tuple(r) for r in rows]


def load_sde_type_groups(path: Optional[Path] = None) -> dict[int, int]:
    """type_id -> group_id for every SDE type - used by candidate_discovery.
    guess_category to tell Boosters/Drugs apart from real Cyberimplants, which
    otherwise share category_id 20 ("Implant") and can't be distinguished from
    category_id alone. Kept separate rather than widening
    load_sde_types_with_market_group()'s tuple shape, which other callers
    depend on."""
    with connect(path) as conn:
        rows = conn.execute("SELECT type_id, group_id FROM sde_types").fetchall()
    return {r[0]: r[1] for r in rows}


def load_ore_ice_candidate_types(path: Optional[Path] = None) -> list[tuple[int, str, float, str]]:
    """Returns (type_id, type_name, volume, group_name) for every published
    compressed ore/ice type - the Ore Shortlist's fixed, SDE-derived candidate
    universe (see refining/candidate_discovery.py). category_id 25 is Ore
    (mirrors refining.constants.ORE_ICE_CATEGORY_ID as a bare literal here,
    same "storage.py doesn't import a submodule's own constants" reasoning as
    the parent's own copy of this function).

    Filters on t.type_name LIKE 'Compressed%', not group_name - the parent
    confirmed live against a real Fuzzwork-fetched SDE that a compressed
    ore/ice type shares its *raw* ore's own group ("Compressed Veldspar"
    lives in group "Veldspar" alongside raw "Veldspar" itself) - there is no
    dedicated "Compressed <Family>" group in the real data, so filtering on
    group_name instead silently produces an empty candidate universe."""
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT t.type_id, t.type_name, t.volume, g.group_name FROM sde_types t "
            "JOIN sde_groups g ON g.group_id = t.group_id "
            "WHERE g.category_id = 25 AND t.type_name LIKE 'Compressed%' AND t.published = 1"
        ).fetchall()
    return [tuple(r) for r in rows]


# -------------------------------------------------------- candidate universe
_CANDIDATE_TABLES = ("candidate_universe", "focused_candidates")


def save_candidate_universe(candidates: Sequence[Candidate], run_ts: str,
                            table: str = "candidate_universe",
                            path: Optional[Path] = None) -> None:
    """Replaces `table`'s contents with `candidates` - these tables are read
    in full as a current-state snapshot, never filtered by run_ts, so
    appending would just accumulate duplicates forever."""
    if table not in _CANDIDATE_TABLES:
        raise ValueError(f"unknown candidate table {table!r}")
    with connect(path) as conn:
        conn.execute(f"DELETE FROM {table}")
        conn.executemany(
            f"INSERT INTO {table} (run_ts, item, type_id, volume_m3, market_group_path, category, meta_level) "
            "VALUES (?,?,?,?,?,?,?)",
            [(run_ts, c.item, c.type_id, c.volume_m3, c.market_group_path, c.category, c.meta_level)
             for c in candidates],
        )


def load_candidate_universe(table: str = "candidate_universe",
                            path: Optional[Path] = None) -> list[Candidate]:
    """Everything currently in `table`, as Candidate objects. The parent repo
    hands its callers a pandas DataFrame here (its web layer wants one); this
    repo has no pandas dependency and every caller wants the dataclass back."""
    if table not in _CANDIDATE_TABLES:
        raise ValueError(f"unknown candidate table {table!r}")
    with connect(path) as conn:
        rows = conn.execute(
            f"SELECT item, type_id, volume_m3, category, market_group_path, meta_level FROM {table}"
        ).fetchall()
    return [Candidate(item=r["item"], type_id=r["type_id"], volume_m3=r["volume_m3"],
                      category=r["category"], market_group_path=r["market_group_path"],
                      meta_level=r["meta_level"]) for r in rows]


def get_candidate_universe_built_at(path: Optional[Path] = None) -> Optional[str]:
    """When the candidate universe was last (re)built, or None if never. Worth
    comparing against get_sde_refresh_state(): an item added by a newer SDE
    dump isn't a candidate until this snapshot is rebuilt."""
    with connect(path) as conn:
        row = conn.execute("SELECT MAX(run_ts) FROM candidate_universe").fetchone()
    return row[0] if row and row[0] else None


# ------------------------------------------------- candidate search results
def save_new_candidates(results: Sequence[NewCandidateResult], run_ts: str,
                        path: Optional[Path] = None) -> None:
    """Appends one search batch's scored candidates. Called once per internal
    batch (see history_backtest's results_sink), not once per run - a full
    scan takes minutes, and an interrupted one must keep whatever it already
    scored."""
    with connect(path) as conn:
        conn.executemany(
            "INSERT INTO new_candidates (run_ts, item, category, type_id, volume_m3, paired_days, "
            "profitable_days, hit_rate, latest_margin, best_margin, avg_profit_m3, avg_sell_movement, "
            "score, recommendation, add_flag, meta_level) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(run_ts, r.item, r.category, r.type_id, r.volume_m3, r.paired_days, r.profitable_days,
              r.hit_rate, r.latest_margin, r.best_margin, r.avg_profit_m3, r.avg_sell_movement,
              r.score, r.recommendation, int(r.add), r.meta_level) for r in results],
        )


def latest_new_candidates(only_recommended: bool = False,
                          path: Optional[Path] = None) -> list[NewCandidateResult]:
    """The most recent search run's scored candidates, as NewCandidateResult
    objects (the parent hands its web layer a DataFrame here; same reasoning
    as latest_shortlist_snapshot). `only_recommended` keeps just the rows the
    backtest actually flagged for adding - what do_add_to_shortlist wants."""
    with connect(path) as conn:
        run_ts = conn.execute("SELECT MAX(run_ts) FROM new_candidates").fetchone()[0]
        if not run_ts:
            return []
        sql = "SELECT * FROM new_candidates WHERE run_ts = ?"
        if only_recommended:
            sql += " AND add_flag = 1"
        rows = conn.execute(sql, (run_ts,)).fetchall()
    return [NewCandidateResult(item=r["item"], category=r["category"], type_id=r["type_id"],
                               volume_m3=r["volume_m3"], paired_days=r["paired_days"],
                               profitable_days=r["profitable_days"], hit_rate=r["hit_rate"],
                               latest_margin=r["latest_margin"], best_margin=r["best_margin"],
                               avg_profit_m3=r["avg_profit_m3"],
                               avg_sell_movement=r["avg_sell_movement"], score=r["score"],
                               recommendation=r["recommendation"], add=bool(r["add_flag"]),
                               meta_level=r["meta_level"]) for r in rows]


def save_goonmetrics_history(points: Sequence, path: Optional[Path] = None) -> None:
    """Upserts price-history days (goonmetrics_client.HistoryPoint). Deliberately
    typed loosely: importing HistoryPoint here would pull goonmetrics_client ->
    config -> storage into an import cycle, and every attribute used is part of
    that dataclass' documented shape."""
    with connect(path) as conn:
        conn.executemany(
            "INSERT INTO goonmetrics_history VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(region_id, type_id, date) DO UPDATE SET "
            "min_price=excluded.min_price, max_price=excluded.max_price, avg_price=excluded.avg_price, "
            "movement=excluded.movement, num_orders=excluded.num_orders",
            [(p.region_id, p.type_id, p.date, p.min_price, p.max_price, p.avg_price,
              p.movement, p.num_orders) for p in points],
        )


def read_goonmetrics_history_for_types(type_ids: Sequence[int],
                                       path: Optional[Path] = None) -> list:
    """Cached history for just these type_ids, as HistoryPoint objects.
    Filtered in SQL rather than read whole: the table also holds every
    candidate ever backtested, and the only consumer (margin trends over the
    shortlist) would discard all of that anyway."""
    from .goonmetrics_client import HistoryPoint  # local import - see save_goonmetrics_history
    type_ids = list(type_ids)
    if not type_ids:
        return []
    placeholders = ",".join("?" * len(type_ids))
    with connect(path) as conn:
        rows = conn.execute(
            f"SELECT * FROM goonmetrics_history WHERE type_id IN ({placeholders})", type_ids
        ).fetchall()
    return [HistoryPoint(region_id=r["region_id"], type_id=r["type_id"], date=r["date"],
                         min_price=r["min_price"], max_price=r["max_price"],
                         avg_price=r["avg_price"], movement=r["movement"],
                         num_orders=r["num_orders"]) for r in rows]


def get_candidate_search_offset(path: Optional[Path] = None) -> int:
    """Where the next safe-mode search resumes - 0 if none has run yet."""
    with connect(path) as conn:
        row = conn.execute("SELECT offset_value FROM candidate_search_cursor WHERE id = 1").fetchone()
    return row[0] if row else 0


def set_candidate_search_offset(offset: int, path: Optional[Path] = None) -> None:
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO candidate_search_cursor (id, offset_value) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET offset_value = excluded.offset_value",
            (offset,),
        )


def sde_type_names(type_ids: Sequence[int], path: Optional[Path] = None) -> dict[int, str]:
    """type_id -> type_name for whichever ids are in the SDE cache. A missing
    id is simply absent from the result, not an error - the cache only covers
    published, market-grouped types, so an id from a wallet transaction can
    legitimately miss."""
    type_ids = list(type_ids)
    if not type_ids:
        return {}
    placeholders = ",".join("?" * len(type_ids))
    with connect(path) as conn:
        rows = conn.execute(
            f"SELECT type_id, type_name FROM sde_types WHERE type_id IN ({placeholders})", type_ids
        ).fetchall()
    return {r[0]: r[1] for r in rows}


# ------------------------------------------------------------------ shortlist
# Shortlist *membership* is genuinely persisted state (unlike a candidate
# search, which is a stateless computation over its inputs), but it lives
# here rather than in shortlist.py - the parent's shortlist.py imports no
# storage at all, and this repo already keeps every table read/write in this
# one module. shortlist.py stays a pure, network-free, storage-free
# computation over values handed to it.
def upsert_shortlist(items: Sequence[ShortlistItem], path: Optional[Path] = None) -> None:
    """Adds or updates shortlist entries by item_id. `meta_level` is
    COALESCEd rather than overwritten: a caller adding an item from a source
    that doesn't know its meta level must not blank out one already
    backfilled from ESI."""
    with connect(path) as conn:
        conn.executemany(
            "INSERT INTO shortlist (item_id, item, category, volume_m3, active, meta_level) "
            "VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(item_id) DO UPDATE SET item=excluded.item, category=excluded.category, "
            "volume_m3=excluded.volume_m3, active=excluded.active, "
            "meta_level=COALESCE(excluded.meta_level, shortlist.meta_level)",
            [(i.item_id, i.item, i.category, i.volume_m3, int(i.active), i.meta_level) for i in items],
        )


def load_shortlist(path: Optional[Path] = None) -> list[ShortlistItem]:
    """Every shortlist entry, active or not - callers that only want the
    active ones filter themselves. An inactive item is still fully priced by
    shortlist.evaluate_shortlist_item (only its `decision` short-circuits),
    so it has to come back from here too."""
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT item_id, item, category, volume_m3, active, meta_level FROM shortlist"
        ).fetchall()
    return [ShortlistItem(item=r["item"], item_id=r["item_id"], category=r["category"],
                          volume_m3=r["volume_m3"], active=bool(r["active"]),
                          meta_level=r["meta_level"]) for r in rows]


def deactivate_shortlist_items(item_ids: Sequence[int], path: Optional[Path] = None) -> None:
    """Sets active=0 - deliberately not a DELETE, so the item's
    shortlist_snapshot history survives and it can be reactivated later."""
    if not item_ids:
        return
    with connect(path) as conn:
        conn.executemany("UPDATE shortlist SET active = 0 WHERE item_id = ?", [(i,) for i in item_ids])


def activate_shortlist_items(item_ids: Sequence[int], path: Optional[Path] = None) -> None:
    """The reactivation counterpart. Needed because shortlist._decision
    short-circuits to "Inactive" without ever re-checking the real numbers -
    without this, an item deactivated once would stay inactive forever even
    after its economics recovered (a real bug in the parent repo, its GitHub
    issue #35)."""
    if not item_ids:
        return
    with connect(path) as conn:
        conn.executemany("UPDATE shortlist SET active = 1 WHERE item_id = ?", [(i,) for i in item_ids])


def update_shortlist_meta_levels(meta_levels: dict[int, int], path: Optional[Path] = None) -> None:
    """Backfills meta_level for shortlist items that don't have one cached
    yet (it comes from a dogma-attribute lookup, not from whatever added the
    item)."""
    if not meta_levels:
        return
    with connect(path) as conn:
        conn.executemany("UPDATE shortlist SET meta_level = ? WHERE item_id = ?",
                         [(level, item_id) for item_id, level in meta_levels.items()])


def get_shortlist_skip_since(path: Optional[Path] = None) -> dict[int, str]:
    """{item_id: when its current unbroken Skip streak started} for every item
    currently mid-streak - an item absent from this dict isn't on one."""
    with connect(path) as conn:
        rows = conn.execute("SELECT item_id, skip_since FROM shortlist_skip_streak").fetchall()
    return {r[0]: r[1] for r in rows}


def start_shortlist_skip_streak(item_ids: Sequence[int], since: str,
                                path: Optional[Path] = None) -> None:
    """Records `since` as the streak start for each item that doesn't already
    have one in progress. DO NOTHING, not an update: a streak's start must
    stay where it was, otherwise every further Skip run would push the
    deactivation deadline out and it could never be reached."""
    item_ids = list(item_ids)
    if not item_ids:
        return
    with connect(path) as conn:
        conn.executemany(
            "INSERT INTO shortlist_skip_streak (item_id, skip_since) VALUES (?, ?) "
            "ON CONFLICT(item_id) DO NOTHING",
            [(i, since) for i in item_ids],
        )


def clear_shortlist_skip_streak(item_ids: Sequence[int], path: Optional[Path] = None) -> None:
    """Ends the tracked streak for each item - called both when an item is
    profitable again (streak broken) and once a streak has led to
    deactivation (nothing left to track)."""
    item_ids = list(item_ids)
    if not item_ids:
        return
    with connect(path) as conn:
        conn.executemany("DELETE FROM shortlist_skip_streak WHERE item_id = ?", [(i,) for i in item_ids])


def save_shortlist_snapshot(rows: Sequence[ShortlistRow], run_ts: str,
                            path: Optional[Path] = None) -> None:
    """Appends one evaluation run's rows. Append-only on purpose - the
    snapshot is the record of what the numbers looked like at that moment."""
    with connect(path) as conn:
        conn.executemany(
            "INSERT INTO shortlist_snapshot (run_ts, item_id, item, category, landed_cost, net_sell, "
            "sell_volume, own_orders_remaining, profit_per_unit, margin, profit_per_m3, decision, active, "
            "volume_m3, jita_sell, import_cost, meta_level, avg_daily_volume) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(run_ts, r.item_id, r.item, r.category, r.landed_cost, r.net_sell, r.sell_volume,
              r.own_orders_remaining, r.profit_per_unit, r.margin, r.profit_per_m3, r.decision,
              int(r.active), r.volume_m3, r.jita_sell, r.import_cost, r.meta_level,
              r.avg_daily_volume) for r in rows],
        )


def latest_shortlist_snapshot(path: Optional[Path] = None) -> list[ShortlistRow]:
    """The most recent run's rows, as ShortlistRow objects (the parent hands
    its web layer a pandas DataFrame here; same reasoning as
    load_candidate_universe - no pandas here, and every caller wants the
    dataclass). Empty list if no run has been saved yet."""
    with connect(path) as conn:
        run_ts = conn.execute("SELECT MAX(run_ts) FROM shortlist_snapshot").fetchone()[0]
        if not run_ts:
            return []
        rows = conn.execute("SELECT * FROM shortlist_snapshot WHERE run_ts = ?", (run_ts,)).fetchall()
    return [ShortlistRow(item=r["item"], category=r["category"], landed_cost=r["landed_cost"],
                         net_sell=r["net_sell"], sell_volume=r["sell_volume"],
                         own_orders_remaining=r["own_orders_remaining"],
                         profit_per_unit=r["profit_per_unit"], margin=r["margin"],
                         profit_per_m3=r["profit_per_m3"], decision=r["decision"],
                         active=bool(r["active"]), item_id=r["item_id"], volume_m3=r["volume_m3"],
                         jita_sell=r["jita_sell"], import_cost=r["import_cost"],
                         meta_level=r["meta_level"], avg_daily_volume=r["avg_daily_volume"])
            for r in rows]


# ------------------------------------------------- realized trade history
def get_station_ids_in_region(region_id: int,
                              path: Optional[Path] = None) -> frozenset[int]:
    """Every NPC station_id in `region_id`, from the SDE cache. Used by
    trade_reconciliation to decide whether a wallet transaction happened in
    the buy hub's region at all - wallet transactions carry only a
    station/structure location_id, never a region_id, so there is nothing to
    filter on without this lookup. Region-wide, not Jita's own solar system:
    a trader can legitimately buy from any station in The Forge (confirmed
    with the user in the parent repo). Empty until the SDE cache has been
    refreshed."""
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT s.station_id FROM sde_stations s "
            "JOIN sde_solar_systems sys ON sys.solar_system_id = s.solar_system_id "
            "WHERE sys.region_id = ?", (region_id,),
        ).fetchall()
    return frozenset(r[0] for r in rows)


def get_station_ids_in_system(system_id: int,
                              path: Optional[Path] = None) -> frozenset[int]:
    """Every NPC station_id in one solar system, from the SDE cache - lets a
    caller ask "is this asset's location_id a Jita station" without a live
    ESI lookup per station. Narrower than get_station_ids_in_region on
    purpose: own_orders.fetch_buyer_already_covered asks about Jita itself
    (stock already sitting in the buy hub), not about the whole Forge.
    Empty until the SDE cache has been refreshed."""
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT station_id FROM sde_stations WHERE solar_system_id = ?", (system_id,)
        ).fetchall()
    return frozenset(r[0] for r in rows)


def save_realized_trades(trades: Sequence[RealizedTrade], run_ts: str,
                         path: Optional[Path] = None) -> None:
    """Replaces the whole table with one run's matched pairs - see the
    realized_trades schema comment for why this is not an append."""
    with connect(path) as conn:
        conn.execute("DELETE FROM realized_trades")
        conn.executemany(
            "INSERT INTO realized_trades (run_ts, type_id, item, buy_date, buy_qty, buy_unit_price, "
            "sell_date, sell_qty, sell_unit_price, matched_qty, realized_profit, margin) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [(run_ts, t.type_id, t.item, t.buy_date, t.buy_qty, t.buy_unit_price,
              t.sell_date, t.sell_qty, t.sell_unit_price, t.matched_qty,
              t.realized_profit, t.margin) for t in trades],
        )


def latest_realized_trades(path: Optional[Path] = None) -> list[RealizedTrade]:
    """The most recent run's matched pairs, as RealizedTrade objects (same
    reasoning as latest_shortlist_snapshot - the parent hands its web layer a
    pandas DataFrame here; no pandas here, and every caller wants the
    dataclass). Empty list if reconciliation has never run."""
    with connect(path) as conn:
        run_ts = conn.execute("SELECT MAX(run_ts) FROM realized_trades").fetchone()[0]
        if not run_ts:
            return []
        rows = conn.execute("SELECT * FROM realized_trades WHERE run_ts = ?", (run_ts,)).fetchall()
    return [RealizedTrade(type_id=r["type_id"], item=r["item"], buy_date=r["buy_date"],
                          buy_qty=r["buy_qty"], buy_unit_price=r["buy_unit_price"],
                          sell_date=r["sell_date"], sell_qty=r["sell_qty"],
                          sell_unit_price=r["sell_unit_price"], matched_qty=r["matched_qty"],
                          realized_profit=r["realized_profit"], margin=r["margin"])
            for r in rows]


# ------------------------------------------- Production: ESI-synced ownership
def _resolve_locations(rows: Sequence[tuple], location_index: int = 2) -> list[int]:
    """For each row, walks its own location_id (at `location_index`) up through
    however many nested containers (ship cargo, corp Office, station container,
    ...) it is parented under, to the outermost station/structure id.

    `rows` supplies its own parent map (item_id at index 0 -> location_id at
    `location_index`) - correct as long as every container a row could be
    nested under is itself present in `rows`, true for assets (containers are
    ordinary assets in the same per-owner table) but not for blueprints, whose
    immediate container lives in the *asset* table instead (see
    replace_blueprints, which builds its parent map from there).

    Capped at 10 hops as a defensive bound against a cyclical edge case, not a
    realistic EVE nesting depth."""
    parent_of = {row[0]: row[location_index] for row in rows}
    resolved = []
    for row in rows:
        root = row[location_index]
        hops = 0
        while root in parent_of and hops < 10:
            root = parent_of[root]
            hops += 1
        resolved.append(root)
    return resolved


_ASSET_TABLES = ("character_assets", "corp_assets")
# Doctrine's own independent asset cache (Stockpile's Ist side) - a separate
# pair from _ASSET_TABLES above so Doctrine works standalone without
# Production ever having been synced (see the schema's own comment).
DOCTRINE_ASSET_TABLES = ("doctrine_character_assets", "doctrine_corp_assets")
_ALL_ASSET_TABLES = _ASSET_TABLES + DOCTRINE_ASSET_TABLES
_BLUEPRINT_TABLES = ("character_blueprints", "corp_blueprints")
_JOB_TABLES = ("character_industry_jobs", "corp_industry_jobs")


def replace_assets(table: str, rows: Sequence[tuple], path: Optional[Path] = None) -> None:
    """`rows`: (item_id, type_id, location_id, location_flag, quantity,
    is_blueprint_copy, owner_name). resolved_location_id is computed here, not
    by the caller, so every caller gets it just by going through this one
    function - see _resolve_locations."""
    if table not in _ALL_ASSET_TABLES:
        raise ValueError(f"unknown asset table {table!r}")
    resolved = _resolve_locations(rows)
    with connect(path) as conn:
        conn.execute(f"DELETE FROM {table}")
        conn.executemany(
            f"INSERT INTO {table} (item_id, type_id, location_id, location_flag, quantity, "
            "is_blueprint_copy, owner_name, resolved_location_id) VALUES (?,?,?,?,?,?,?,?)",
            [tuple(row) + (root,) for row, root in zip(rows, resolved)],
        )


def replace_industry_jobs(table: str, rows: Sequence[tuple], path: Optional[Path] = None) -> None:
    """`rows`: (job_id, activity_id, blueprint_type_id, product_type_id, runs,
    output_location_id, status, end_date, start_date, installer_id,
    installer_name)."""
    if table not in _JOB_TABLES:
        raise ValueError(f"unknown industry job table {table!r}")
    with connect(path) as conn:
        conn.execute(f"DELETE FROM {table}")
        conn.executemany(
            f"INSERT INTO {table} (job_id, activity_id, blueprint_type_id, product_type_id, runs, "
            "output_location_id, status, end_date, start_date, installer_id, installer_name) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )


def replace_blueprints(table: str, rows: Sequence[tuple], path: Optional[Path] = None) -> None:
    """`rows`: (item_id, type_id, location_id, location_flag, quantity,
    material_efficiency, time_efficiency, runs).

    resolved_location_id is resolved against the *matching asset table*
    (character_assets for character_blueprints, corp_assets for corp_blueprints)
    rather than against `rows` itself - a blueprint's immediate container is
    always a regular asset, never another blueprint. This depends on that asset
    table already being written for the same sync; production/esi_sync.py calls
    replace_assets before replace_blueprints for each of character/corp. If the
    asset table is empty (this function called standalone, as in tests) every
    row simply resolves to its own unwrapped location_id."""
    if table not in _BLUEPRINT_TABLES:
        raise ValueError(f"unknown blueprint table {table!r}")
    asset_table = "character_assets" if table == "character_blueprints" else "corp_assets"
    with connect(path) as conn:
        parent_of = dict(conn.execute(f"SELECT item_id, location_id FROM {asset_table}").fetchall())
        conn.execute(f"DELETE FROM {table}")
        resolved_rows = []
        for row in rows:
            root = row[2]  # location_id
            hops = 0
            while root in parent_of and hops < 10:
                root = parent_of[root]
                hops += 1
            resolved_rows.append(tuple(row) + (root,))
        conn.executemany(
            f"INSERT INTO {table} (item_id, type_id, location_id, location_flag, quantity, "
            "material_efficiency, time_efficiency, runs, resolved_location_id) VALUES (?,?,?,?,?,?,?,?,?)",
            resolved_rows,
        )


# Hangar divisions an item can sit in that don't count as usable stock: Asset
# Safety needs a paid retrieval trip first, and the various delivery/market
# flags are items in transit or already sold, not material you can start a job
# with today.
NON_STOCK_LOCATION_FLAGS = ("AssetSafety", "Deliveries", "CorpDeliveries", "CorpMarket")


def esi_stock_at_location(type_id: int, location_id: Optional[int],
                          path: Optional[Path] = None,
                          tables: tuple = _ASSET_TABLES) -> float:
    """Owned quantity of `type_id` across `tables` (default: Production's
    character + corp assets), optionally filtered to one station/structure
    (None = everywhere, which is what a setup with no home structure id
    configured wants). Excludes NON_STOCK_LOCATION_FLAGS.

    `tables` lets a caller point this at a different asset-table pair -
    doctrine/engine.py's stockpile computation passes DOCTRINE_ASSET_TABLES,
    since Doctrine keeps its own independent synced-asset cache (see the
    schema's own comment on doctrine_character_assets).

    Filters on resolved_location_id, not the raw location_id column - see
    replace_assets."""
    flags = ",".join("?" * len(NON_STOCK_LOCATION_FLAGS))
    total = 0.0
    with connect(path) as conn:
        for table in tables:
            sql = (f"SELECT COALESCE(SUM(quantity), 0) FROM {table} WHERE type_id = ? "
                   f"AND (location_flag IS NULL OR location_flag NOT IN ({flags}))")
            params: tuple = (type_id, *NON_STOCK_LOCATION_FLAGS)
            if location_id is not None:
                sql += " AND resolved_location_id = ?"
                params = params + (location_id,)
            total += conn.execute(sql, params).fetchone()[0]
    return total


def esi_incoming_industry_qty(product_type_id: int,
                              path: Optional[Path] = None) -> dict[str, float]:
    """{'runs': outstanding job runs, 'jobs': job count} for `product_type_id`
    across character + corp industry jobs. Converting runs to an output
    quantity needs the blueprint's product qty/run (get_blueprint_for_product),
    which is the caller's job, not this one's.

    'ready' counts alongside 'active'/'paused': a finished-but-not-yet-
    delivered job's output already exists, so treating it as nothing incoming
    (the parent repo's original bug) understates supply and plans a rebuild of
    something already sitting there."""
    runs = 0.0
    jobs = 0
    with connect(path) as conn:
        for table in _JOB_TABLES:
            row = conn.execute(
                f"SELECT COALESCE(SUM(runs), 0), COUNT(*) FROM {table} "
                "WHERE product_type_id = ? AND status IN ('active', 'paused', 'ready')",
                (product_type_id,),
            ).fetchone()
            runs += row[0]
            jobs += row[1]
    return {"runs": runs, "jobs": jobs}


def get_owned_bpo_best_me_te(blueprint_type_id: int,
                             path: Optional[Path] = None) -> Optional[tuple[int, int]]:
    """Best (highest) ME and TE, independently, across every owned *Original*
    of `blueprint_type_id` - character and corp blueprints together. None when
    you don't own that BPO at all.

    runs = -1 is ESI's Original marker (see the character_blueprints schema
    comment): a BPC's ME/TE was fixed by whoever copied it, not by your own
    research, so copies must not be considered here. Used by
    production/engine.py's _owned_bpo_mods to price Tech I builds off your real
    research level instead of the flat perfect-research assumption."""
    best_me: Optional[int] = None
    best_te: Optional[int] = None
    with connect(path) as conn:
        for table in _BLUEPRINT_TABLES:
            row = conn.execute(
                f"SELECT MAX(material_efficiency), MAX(time_efficiency) FROM {table} "
                "WHERE type_id = ? AND runs = -1",
                (blueprint_type_id,),
            ).fetchone()
            if row is None:
                continue
            if row[0] is not None:
                best_me = row[0] if best_me is None else max(best_me, row[0])
            if row[1] is not None:
                best_te = row[1] if best_te is None else max(best_te, row[1])
    if best_me is None or best_te is None:
        return None
    return (best_me, best_te)


# -------------------------------------------------------------- stock targets
def upsert_stock_target(type_id: int, type_name: str, quantity: float, jita_target: bool = False,
                        path: Optional[Path] = None) -> None:
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO stock_targets (type_id, type_name, quantity, jita_target) VALUES (?,?,?,?) "
            "ON CONFLICT(type_id) DO UPDATE SET type_name=excluded.type_name, "
            "quantity=excluded.quantity, jita_target=excluded.jita_target",
            (type_id, type_name, quantity, int(jita_target)),
        )


def delete_stock_target(type_id: int, path: Optional[Path] = None) -> None:
    with connect(path) as conn:
        conn.execute("DELETE FROM stock_targets WHERE type_id = ?", (type_id,))


def load_stock_targets(path: Optional[Path] = None) -> list[tuple[int, str, float, bool]]:
    """(type_id, type_name, quantity, jita_target) for every configured stock
    target - see engine.plan_production and discover_build_candidates's
    existing_target_ids exclusion."""
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT type_id, type_name, quantity, jita_target FROM stock_targets"
        ).fetchall()
    return [(r[0], r[1], r[2], bool(r[3])) for r in rows]


# -------------------------------------------------------------------- Doctrine
# Thin plain-tuple storage layer only, matching the parent's own division of
# labour (doctrine/engine.py wraps these into doctrine.models dataclasses,
# not this module - see that module's docstring).

def create_doctrine(name: str, description: Optional[str], path: Optional[Path] = None) -> str:
    doctrine_id = str(uuid.uuid4())
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO doctrines (doctrine_id, name, description) VALUES (?, ?, ?)",
            (doctrine_id, name, description),
        )
    return doctrine_id


def list_doctrines(path: Optional[Path] = None) -> list[tuple]:
    """(doctrine_id, name, description, active, created_at), most-recently-
    created first."""
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT doctrine_id, name, description, active, created_at FROM doctrines ORDER BY created_at DESC"
        ).fetchall()
    return [tuple(r) for r in rows]


def get_doctrine(doctrine_id: str, path: Optional[Path] = None) -> Optional[tuple]:
    with connect(path) as conn:
        row = conn.execute(
            "SELECT doctrine_id, name, description, active, created_at FROM doctrines WHERE doctrine_id = ?",
            (doctrine_id,),
        ).fetchone()
    return tuple(row) if row else None


def update_doctrine(doctrine_id: str, updates: dict, path: Optional[Path] = None) -> None:
    """Caller (doctrine/actions.py) is responsible for only passing real
    column names - same "thin storage layer" convention as every other
    update_* function in this file."""
    if not updates:
        return
    cols = ", ".join(f"{k} = ?" for k in updates)
    with connect(path) as conn:
        conn.execute(f"UPDATE doctrines SET {cols} WHERE doctrine_id = ?", (*updates.values(), doctrine_id))


def delete_doctrine(doctrine_id: str, path: Optional[Path] = None) -> None:
    with connect(path) as conn:
        conn.execute("DELETE FROM doctrines WHERE doctrine_id = ?", (doctrine_id,))


def create_fitting(doctrine_id: str, name: str, hull_type_id: int, raw_eft: str,
                   variant_label: Optional[str], contract_target: int, stockpile_target: int,
                   cargo_tolerance_pct: Optional[float], fuel_bay_text: Optional[str],
                   ship_maintenance_bay_text: Optional[str], path: Optional[Path] = None) -> str:
    fitting_id = str(uuid.uuid4())
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO doctrine_fittings (fitting_id, doctrine_id, name, hull_type_id, raw_eft, "
            "variant_label, contract_target, stockpile_target, cargo_tolerance_pct, fuel_bay_text, "
            "ship_maintenance_bay_text) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (fitting_id, doctrine_id, name, hull_type_id, raw_eft, variant_label, contract_target,
             stockpile_target, cargo_tolerance_pct, fuel_bay_text, ship_maintenance_bay_text),
        )
    return fitting_id


_FITTING_COLUMNS = ("fitting_id", "doctrine_id", "name", "variant_label", "hull_type_id", "raw_eft",
                    "contract_target", "stockpile_target", "cargo_tolerance_pct", "active", "created_at",
                    "updated_at", "fuel_bay_text", "ship_maintenance_bay_text")


def get_fitting(fitting_id: str, path: Optional[Path] = None) -> Optional[tuple]:
    with connect(path) as conn:
        row = conn.execute(
            f"SELECT {', '.join(_FITTING_COLUMNS)} FROM doctrine_fittings WHERE fitting_id = ?",
            (fitting_id,),
        ).fetchone()
    return tuple(row) if row else None


def list_fittings_for_doctrine(doctrine_id: str, path: Optional[Path] = None) -> list[tuple]:
    with connect(path) as conn:
        rows = conn.execute(
            f"SELECT {', '.join(_FITTING_COLUMNS)} FROM doctrine_fittings WHERE doctrine_id = ? ORDER BY created_at",
            (doctrine_id,),
        ).fetchall()
    return [tuple(r) for r in rows]


def list_active_fittings(path: Optional[Path] = None) -> list[tuple]:
    """Every active fitting belonging to an active doctrine, across the whole
    install - the candidate pool for contract matching and stockpile
    computation, in doctrine-then-fitting creation order."""
    with connect(path) as conn:
        rows = conn.execute(
            f"SELECT {', '.join('f.' + c for c in _FITTING_COLUMNS)} FROM doctrine_fittings f "
            "JOIN doctrines d ON d.doctrine_id = f.doctrine_id "
            "WHERE f.active = 1 AND d.active = 1 ORDER BY d.created_at, f.created_at"
        ).fetchall()
    return [tuple(r) for r in rows]


def update_fitting(fitting_id: str, updates: dict, path: Optional[Path] = None) -> None:
    if not updates:
        return
    cols = ", ".join(f"{k} = ?" for k in updates)
    with connect(path) as conn:
        conn.execute(
            f"UPDATE doctrine_fittings SET {cols}, updated_at = datetime('now') WHERE fitting_id = ?",
            (*updates.values(), fitting_id),
        )


def delete_fitting(fitting_id: str, path: Optional[Path] = None) -> None:
    with connect(path) as conn:
        conn.execute("DELETE FROM doctrine_fitting_items WHERE fitting_id = ?", (fitting_id,))
        conn.execute("DELETE FROM doctrine_fitting_parse_issues WHERE fitting_id = ?", (fitting_id,))
        conn.execute("DELETE FROM doctrine_fittings WHERE fitting_id = ?", (fitting_id,))


def unmatch_contracts_for_fitting(fitting_id: str, path: Optional[Path] = None) -> None:
    """Deleting a fitting must never delete the contracts matched to it (a
    contract is real inventory-tracking data, independent of whichever
    fitting definition it happened to match) - just clear the match."""
    with connect(path) as conn:
        conn.execute(
            "UPDATE doctrine_contracts SET matched_fitting_id = NULL, match_score = NULL, "
            "validation_status = 'unmatched' WHERE matched_fitting_id = ?",
            (fitting_id,),
        )
        conn.execute("DELETE FROM doctrine_contract_deviations WHERE contract_id IN "
                     "(SELECT contract_id FROM doctrine_contracts WHERE matched_fitting_id IS NULL)")


def replace_fitting_items(fitting_id: str, items: Sequence[tuple], path: Optional[Path] = None) -> None:
    """`items`: (line_no, slot_section, type_id, quantity, is_offline).
    item_index (0, then 1 for a same-line_no comma-paired charge) is derived
    here automatically from input order, never passed in by callers."""
    with connect(path) as conn:
        conn.execute("DELETE FROM doctrine_fitting_items WHERE fitting_id = ?", (fitting_id,))
        seen: dict[int, int] = {}
        rows = []
        for line_no, slot_section, type_id, quantity, is_offline in items:
            item_index = seen.get(line_no, 0)
            seen[line_no] = item_index + 1
            rows.append((fitting_id, line_no, item_index, slot_section, type_id, quantity, int(is_offline)))
        conn.executemany(
            "INSERT INTO doctrine_fitting_items (fitting_id, line_no, item_index, slot_section, type_id, "
            "quantity, is_offline) VALUES (?,?,?,?,?,?,?)",
            rows,
        )


def load_fitting_items(fitting_id: str, path: Optional[Path] = None) -> list[tuple]:
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT line_no, slot_section, type_id, quantity, is_offline FROM doctrine_fitting_items "
            "WHERE fitting_id = ? ORDER BY line_no, item_index",
            (fitting_id,),
        ).fetchall()
    return [(r[0], r[1], r[2], r[3], bool(r[4])) for r in rows]


def replace_fitting_parse_issues(fitting_id: str, issues: Sequence[tuple], path: Optional[Path] = None) -> None:
    with connect(path) as conn:
        conn.execute("DELETE FROM doctrine_fitting_parse_issues WHERE fitting_id = ?", (fitting_id,))
        conn.executemany(
            "INSERT INTO doctrine_fitting_parse_issues (fitting_id, line_no, raw_line, issue_kind, message) "
            "VALUES (?,?,?,?,?)",
            [(fitting_id, *row) for row in issues],
        )


def load_fitting_parse_issues(fitting_id: str, path: Optional[Path] = None) -> list[tuple]:
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT line_no, raw_line, issue_kind, message FROM doctrine_fitting_parse_issues "
            "WHERE fitting_id = ? ORDER BY line_no",
            (fitting_id,),
        ).fetchall()
    return [tuple(r) for r in rows]


_CONTRACT_COLUMNS = ("contract_id", "source_role", "for_corporation", "issuer_id", "start_location_id",
                     "status", "title", "price", "date_expired", "matched_fitting_id", "match_score",
                     "validation_status", "synced_at")


def load_doctrine_contracts(path: Optional[Path] = None) -> list[tuple]:
    """Every synced contract, unfiltered - esi_sync.sync_contracts' own
    "reuse an unchanged contract's already-fetched items" optimization reads
    this to find what's already known before touching ESI again."""
    with connect(path) as conn:
        return [tuple(r) for r in
                conn.execute(f"SELECT {', '.join(_CONTRACT_COLUMNS)} FROM doctrine_contracts").fetchall()]


def load_doctrine_contract_items(contract_id: int, path: Optional[Path] = None) -> list[tuple]:
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT record_id, type_id, quantity, is_included, is_singleton FROM doctrine_contract_items "
            "WHERE contract_id = ?",
            (contract_id,),
        ).fetchall()
    return [(r[0], r[1], r[2], bool(r[3]), bool(r[4])) for r in rows]


def load_doctrine_contract_deviations(contract_id: int, path: Optional[Path] = None) -> list[tuple]:
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT type_id, kind, expected_qty, actual_qty, severity FROM doctrine_contract_deviations "
            "WHERE contract_id = ?",
            (contract_id,),
        ).fetchall()
    return [tuple(r) for r in rows]


def replace_doctrine_sync_snapshot(contracts: Sequence[tuple], items: Sequence[tuple],
                                   deviations: Sequence[tuple], path: Optional[Path] = None) -> None:
    """Wholesale-replaces all three doctrine-contract tables in one
    transaction - a contract sync's snapshot is the complete current picture
    (Phase 2 E.3), not an incremental merge, so a contract that finished/
    expired off ESI's own list must disappear here too."""
    with connect(path) as conn:
        conn.execute("DELETE FROM doctrine_contract_deviations")
        conn.execute("DELETE FROM doctrine_contract_items")
        conn.execute("DELETE FROM doctrine_contracts")
        conn.executemany(
            f"INSERT INTO doctrine_contracts ({', '.join(_CONTRACT_COLUMNS)}) "
            f"VALUES ({', '.join('?' * len(_CONTRACT_COLUMNS))})",
            contracts,
        )
        conn.executemany(
            "INSERT INTO doctrine_contract_items (contract_id, record_id, type_id, quantity, is_included, "
            "is_singleton) VALUES (?,?,?,?,?,?)",
            items,
        )
        conn.executemany(
            "INSERT INTO doctrine_contract_deviations (contract_id, type_id, kind, expected_qty, actual_qty, "
            "severity) VALUES (?,?,?,?,?,?)",
            deviations,
        )


def list_doctrine_contracts(fitting_id: Optional[str] = None, status: Optional[str] = None,
                            path: Optional[Path] = None) -> list[tuple]:
    query = f"SELECT {', '.join(_CONTRACT_COLUMNS)} FROM doctrine_contracts WHERE 1=1"
    params: list = []
    if fitting_id is not None:
        query += " AND matched_fitting_id = ?"
        params.append(fitting_id)
    if status is not None:
        query += " AND validation_status = ?"
        params.append(status)
    with connect(path) as conn:
        return [tuple(r) for r in conn.execute(query, params).fetchall()]


def has_any_doctrine_synced_assets(path: Optional[Path] = None) -> bool:
    """True if either doctrine_character_assets or doctrine_corp_assets has
    ever been populated - lets Stockpile distinguish "you have zero of
    everything" from "you've never run a Doctrine asset sync at all"."""
    with connect(path) as conn:
        row = conn.execute(
            "SELECT EXISTS(SELECT 1 FROM doctrine_character_assets) OR EXISTS(SELECT 1 FROM doctrine_corp_assets)"
        ).fetchone()
    return bool(row[0])
