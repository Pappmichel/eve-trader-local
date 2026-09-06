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
  order_book_cache / result_cache <- new here, no parent equivalent (see
                                     esi_update.py: every ESI/market call in
                                     this app is now made only by that
                                     module's sync bundles and cached here;
                                     everything else reads these tables)
  sde_*         <- sde_*              (Fuzzwork SDE cache, see sde.py)
  type_packaged_volume <- type_packaged_volume  (ESI-only per-type constant)
  candidate_universe / focused_candidates <- same names (see
                                             candidate_discovery.py)
  shortlist / shortlist_snapshot <- same names (see shortlist.py)
  realized_trades <- same name (see trade_reconciliation.py)
  schema_version  <- new here, no parent equivalent (Postgres migrations are
                     applied by hand via docs/phase*_schema.sql there); see
                     `MIGRATIONS`/`init_db` below for what it's for.

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
from typing import Any, Callable, Iterator, Optional, Sequence

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

-- One row per (market, type_id) order-book summary, refreshed only by the
-- esi_update.py sync bundles - see that module's own docstring for why every
-- other call site reads this table instead of ever hitting ESI/Goonmetrics
-- live. `market` is "structure:<id>" or "region:<id>" so both C-J's and
-- Jita's books can share one table without colliding on type_id.
CREATE TABLE IF NOT EXISTS order_book_cache (
    market           TEXT NOT NULL,
    type_id          INTEGER NOT NULL,
    buy_percentile   REAL,
    sell_percentile  REAL,
    buy_volume       REAL,
    sell_volume      REAL,
    snapshotted_at   TEXT NOT NULL,
    PRIMARY KEY (market, type_id)
);

-- Small JSON-blob cache for whatever a sync bundle computed that isn't an
-- order book (wallet balances, undercut rows, skill summaries, ...) - same
-- shape as `settings` above, but keyed by an arbitrary cache key instead of
-- a config scope, and each row carries its own computed_at rather than
-- relying on esi_sync_state (a bundle can cache several distinct results
-- under one sync).
CREATE TABLE IF NOT EXISTS result_cache (
    cache_key   TEXT PRIMARY KEY,
    payload     TEXT NOT NULL,
    computed_at TEXT NOT NULL
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

-- Manual stock override: a genuinely separate signal from ESI-synced assets,
-- not a simplification of them - "I physically counted N units in my hangar
-- and it doesn't match what ESI/the plan thinks" (a delivery ESI hasn't
-- caught up on yet, stock kept somewhere no producer character can see, a
-- deliberate correction). Ported from the parent's own manual_stock table
-- (storage.upsert_manual_stock/load_manual_stock) minus tenant_id - confirmed
-- by reading the parent's real code that this is a distinct, user-maintained
-- count added on top of ESI assets (engine._current_stock/_stock_on_hand),
-- not just "ESI assets without incoming jobs" (which would have needed no new
-- table at all). type_name is carried here purely for display (CLI listing) -
-- engine.py itself only ever reads the count by type_id.
CREATE TABLE IF NOT EXISTS manual_stock (
    type_id   INTEGER PRIMARY KEY,
    type_name TEXT NOT NULL,
    count     REAL NOT NULL DEFAULT 0
);

-- Manual Build/Buy override: forces "always Build" or "always Buy" for a
-- specific type_id regardless of what the modeled unit cost would otherwise
-- decide - ported from the parent's manual_build_buy table (storage.
-- upsert_manual_build_buy/load_manual_build_buy/delete_manual_build_buy)
-- minus tenant_id. A genuinely separate table from selected_decryptors
-- (which this repo doesn't have either - see SYNC.md): that one picks WHICH
-- decryptor a Tech II/III item uses, this one skips the buy-vs-build cost
-- comparison entirely for a type_id. Consulted by engine.
-- _buy_or_build_decision via a manual_overrides dict built from
-- load_manual_build_buy() - see plan_production/plan_asset_optimized/
-- discover_build_candidates/discover_ship_margins.
CREATE TABLE IF NOT EXISTS manual_build_buy (
    type_id  INTEGER PRIMARY KEY,
    decision TEXT NOT NULL CHECK (decision IN ('Build', 'Buy'))
);

-- Structure-name cache: location_id -> human name, resolved once via ESI
-- (do_resolve_structure_name) and cached indefinitely - ported from the
-- parent's structure_names table minus tenant_id/RLS. A row with name=NULL
-- means resolution was attempted and failed (no producer character could
-- see that structure), distinct from "never attempted" (no row at all) - see
-- get_cached_structure_name's own (was_cached, name) return shape. One
-- shared table (not two) since the parent's own do_wallet_transactions and
-- Production's structure picker both cache the same kind of location_id ->
-- name lookup.
CREATE TABLE IF NOT EXISTS structure_names (
    location_id     INTEGER PRIMARY KEY,
    name            TEXT,
    solar_system_id INTEGER
);

-- Multi-structure production logistics (production/engine.py's
-- logistics_status/distribution_recommendations - ported from the parent's
-- job_category_locations/category_location_options, minus tenant_id). One
-- location per job_category (JOB_CATEGORIES) - which structure a user has
-- assigned that category's jobs to for the Logistik-tab material-netting
-- view - kept separate from category_location_options below (the parent's
-- own separation): this table is "which one is active right now", the other
-- is "the pick-list of locations this category has ever been pointed at"
-- (a quick-switch convenience - do_set_category_location upserts into both).
CREATE TABLE IF NOT EXISTS job_category_locations (
    category    TEXT PRIMARY KEY,
    location_id INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS category_location_options (
    category    TEXT NOT NULL,
    location_id INTEGER NOT NULL,
    PRIMARY KEY (category, location_id)
);

-- Special Orders (production/engine.py's plan_special_order): one-off build
-- orders, tracked separately from the permanent stock_targets list above -
-- ported from the parent's docs/special_orders_schema.sql minus tenant_id/
-- RLS, same "plain TEXT UUID generated in Python" convention the Doctrine
-- tables below already use. No composite (order_id, type_id) PK needed for
-- the natural-key reason the parent's own schema comment gives (a type_id
-- appears at most once per order) - single-user, so order_id alone would
-- have worked too, but keeping the same natural key as the parent avoids
-- silently diverging on the one thing that actually differs (tenant_id).
CREATE TABLE IF NOT EXISTS special_orders (
    order_id           TEXT PRIMARY KEY,
    note               TEXT,
    net_against_stock  INTEGER NOT NULL DEFAULT 0,
    status             TEXT NOT NULL DEFAULT 'open',
    created_at         TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS special_order_items (
    order_id   TEXT NOT NULL,
    type_id    INTEGER NOT NULL,
    type_name  TEXT NOT NULL,
    quantity   REAL NOT NULL,
    PRIMARY KEY (order_id, type_id)
);
CREATE INDEX IF NOT EXISTS idx_special_order_items_order ON special_order_items (order_id);

-- ------------------------------------------------------------------ Doctrine
-- Ported from the parent's docs/doctrine_schema.sql minus tenant_id/RLS - see
-- that file for the full multi-tenant reasoning behind each shape. ids are
-- plain TEXT UUIDs generated in Python (str(uuid.uuid4())) rather than
-- Postgres's gen_random_uuid() default.
--
-- doctrine_contract_history (GitHub issue #19): a permanent, append-only
-- "who bought what and when" log - independent of doctrine_contracts' own
-- active/wholesale-replaced snapshot, so a finished contract's record
-- survives that snapshot dropping it (and survives it eventually expiring
-- out of ESI's own contract list entirely). fitting_name/hull_type_id are a
-- denormalized snapshot captured when the contract finished, not a live
-- join - stays meaningful even if that fitting is later edited/deactivated/
-- deleted. Single-user, so contract_id alone is the PK (same reasoning as
-- doctrine_contracts above).

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

CREATE TABLE IF NOT EXISTS doctrine_contract_history (
    contract_id      INTEGER PRIMARY KEY,
    source_role      TEXT NOT NULL,
    fitting_id       TEXT,
    fitting_name     TEXT,
    hull_type_id     INTEGER,
    title            TEXT,
    price            REAL,
    acceptor_id      INTEGER,
    acceptor_name    TEXT,
    date_issued      TEXT,
    date_completed   TEXT
);
CREATE INDEX IF NOT EXISTS idx_doctrine_contract_history_fitting ON doctrine_contract_history (fitting_id);

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

-- ------------------------------------------------------------- Ore & Minerals
-- Ore Shortlist (GitHub issue #91 in the parent) - same two-table shape as
-- Trading's own shortlist/shortlist_snapshot above: `ore_shortlist` is the
-- live membership list, keyed by item_id so an upsert edits the one row in
-- place; `ore_shortlist_snapshot` is append-only history, one full set of
-- evaluated rows per run_ts.
CREATE TABLE IF NOT EXISTS ore_shortlist (
    item_id INTEGER PRIMARY KEY,
    item    TEXT NOT NULL,
    family  TEXT NOT NULL,
    is_ice  INTEGER NOT NULL DEFAULT 0,
    active  INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS ore_shortlist_snapshot (
    run_ts           TEXT,
    item_id          INTEGER,
    item             TEXT,
    family           TEXT,
    is_ice           INTEGER,
    active           INTEGER,
    volume_m3        REAL,
    landed_cost      REAL,
    yield_pct        REAL,
    mineral_value    REAL,
    refining_tax     REAL,
    net_sell         REAL,
    sell_listed_qty  REAL,
    profit_per_unit  REAL,
    margin           REAL,
    profit_per_m3    REAL,
    decision         TEXT
);

-- Mineral Shopping List (GitHub issue #93 in the parent) - the saved
-- "I need this many of this mineral" requirement list the optimizer solves
-- for by default (do_optimize_mineral_shopping_list can also solve an ad-hoc
-- list without touching this table).
CREATE TABLE IF NOT EXISTS mineral_requirements (
    mineral_type_id INTEGER PRIMARY KEY,
    mineral_name    TEXT NOT NULL,
    required_qty    REAL NOT NULL
);

-- Station Trading shortlist - the live membership list of Jita spread/
-- volume candidates (station_trading.candidate_discovery.discover_
-- candidates), same single-table-live-list shape as ore_shortlist above
-- (no append-only snapshot counterpart - unlike Trading/Ore & Minerals,
-- nothing here needs a "what did this look like on an earlier run" history:
-- every row is re-priced live off the current order book on every read, see
-- station_trading/actions.py's _build_shortlist_rows).
CREATE TABLE IF NOT EXISTS station_trading_shortlist (
    type_id          INTEGER PRIMARY KEY,
    spread_pct       REAL,
    avg_daily_volume REAL,
    discovered_at    TEXT,
    active           INTEGER NOT NULL DEFAULT 1
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


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row[1] == column for row in conn.execute(f"PRAGMA table_info({table})"))


# ---------------------------------------------------------- schema migrations
# `SCHEMA` above (CREATE TABLE IF NOT EXISTS everywhere) is enough for a
# brand-new table, but does nothing once a table already exists - adding a
# column to an existing table needs a real `ALTER TABLE`, applied once, to
# every database that predates that column. `MIGRATIONS` is that mechanism:
# an ordered list of (version, description, apply(conn)) entries, replayed in
# order against whatever `schema_version` a given database file is
# currently at (a fresh install and a five-versions-old one converge on the
# exact same end state either way).
#
# Two rules for adding an entry here, always together:
#   1. Update `SCHEMA` itself too, so a genuinely fresh `CREATE TABLE IF NOT
#      EXISTS` already includes the new column - a new install should never
#      have to "migrate" through history it never lived.
#   2. Make the migration function itself idempotent (check `_column_exists`,
#      or the equivalent, before altering) - because of rule 1, the very
#      same migration will run once against old databases (where it does
#      real work) and once against every database created after this change
#      shipped (where the column already exists via `SCHEMA`, so it must be
#      a safe no-op). This also makes replaying migrations against a
#      partially-migrated or manually-edited database harmless instead of a
#      crash.
#
# Example shape for the first real entry (kept here, commented out, so the
# pattern is copy-pasteable instead of reverse-engineered from scratch):
#
#   def _migration_0001_add_example_column(conn: sqlite3.Connection) -> None:
#       if not _column_exists(conn, "shortlist", "example_column"):
#           conn.execute("ALTER TABLE shortlist ADD COLUMN example_column TEXT")
#
#   MIGRATIONS: list[tuple[int, str, Callable[[sqlite3.Connection], None]]] = [
#       (1, "add example_column to shortlist", _migration_0001_add_example_column),
#   ]
MIGRATIONS: list[tuple[int, str, Callable[[sqlite3.Connection], None]]] = []


def _run_migrations(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    if conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 0:
        # No row yet: either a genuinely fresh database, or one created
        # before this table existed at all. Either way, 0 is the correct
        # starting point - `MIGRATIONS` entries are individually idempotent
        # (see the module comment above) specifically so this single "start
        # from 0" rule never needs to special-case "was this DB really
        # fresh?", which SQLite has no reliable way to answer after the fact.
        conn.execute("INSERT INTO schema_version (version) VALUES (0)")
    current = conn.execute("SELECT version FROM schema_version").fetchone()[0]
    for version, _description, apply in MIGRATIONS:
        if version <= current:
            continue
        apply(conn)
        conn.execute("UPDATE schema_version SET version = ?", (version,))
        current = version


def init_db(path: Optional[Path] = None) -> None:
    """Idempotent - safe to call on every startup. `SCHEMA` creates every
    table in its current, latest shape (`CREATE TABLE IF NOT EXISTS`, a
    no-op on a database that already has them); `_run_migrations` then
    brings an existing database's *columns* up to date too - see the
    `MIGRATIONS` comment above for the mechanism and the two rules for
    adding an entry."""
    with connect(path) as conn:
        conn.executescript(SCHEMA)
        _run_migrations(conn)


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


# ------------------------------------------------------------ order book cache
def save_order_book_stats(market: str, rows: dict[int, tuple], snapshotted_at: str,
                          path: Optional[Path] = None) -> None:
    """Upserts one summary row per type_id for `market` ("structure:<id>" or
    "region:<id>") - never a wholesale replace-for-market, since more than
    one esi_update.py sync bundle can share the same region cache (Trading
    and Ore & Minerals both price against Jita) and each only ever fetches
    its own subset of type_ids per run; wiping the market first would erase
    another bundle's still-good rows.

    `rows` values are (buy_percentile, sell_percentile, buy_volume,
    sell_volume) plain tuples - esi_client.OrderStats' own field order, not
    that type itself: esi_client.py already imports this module, so storage
    importing OrderStats back would be circular. Callers convert."""
    with connect(path) as conn:
        conn.executemany(
            "INSERT INTO order_book_cache "
            "(market, type_id, buy_percentile, sell_percentile, buy_volume, sell_volume, snapshotted_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(market, type_id) DO UPDATE SET "
            "buy_percentile = excluded.buy_percentile, sell_percentile = excluded.sell_percentile, "
            "buy_volume = excluded.buy_volume, sell_volume = excluded.sell_volume, "
            "snapshotted_at = excluded.snapshotted_at",
            [(market, type_id, buy_pct, sell_pct, buy_vol, sell_vol, snapshotted_at)
             for type_id, (buy_pct, sell_pct, buy_vol, sell_vol) in rows.items()],
        )


def load_order_book_stats(market: str, type_ids: Optional[Sequence[int]] = None,
                          path: Optional[Path] = None) -> dict[int, tuple]:
    """{type_id: (buy_percentile, sell_percentile, buy_volume, sell_volume)}
    for `market`, optionally filtered to `type_ids`. A type_id with nothing
    cached yet is simply absent from the result - the same "no data" shape
    every caller already handles for a live ESI failure today."""
    if type_ids is not None and not type_ids:
        return {}
    with connect(path) as conn:
        if type_ids is None:
            cursor = conn.execute(
                "SELECT type_id, buy_percentile, sell_percentile, buy_volume, sell_volume "
                "FROM order_book_cache WHERE market = ?", (market,))
        else:
            placeholders = ",".join("?" * len(type_ids))
            cursor = conn.execute(
                "SELECT type_id, buy_percentile, sell_percentile, buy_volume, sell_volume "
                f"FROM order_book_cache WHERE market = ? AND type_id IN ({placeholders})",
                (market, *type_ids))
        rows = cursor.fetchall()
    return {row["type_id"]: (row["buy_percentile"], row["sell_percentile"], row["buy_volume"], row["sell_volume"])
            for row in rows}


def order_book_snapshot_time(market: str, path: Optional[Path] = None) -> Optional[str]:
    """Newest snapshotted_at across `market`'s cached rows, or None before
    the first sync ever populates it."""
    with connect(path) as conn:
        row = conn.execute(
            "SELECT MAX(snapshotted_at) AS latest FROM order_book_cache WHERE market = ?", (market,)
        ).fetchone()
    return row["latest"] if row else None


# ----------------------------------------------------------------- result cache
def save_result_cache(cache_key: str, payload: Any, computed_at: str, path: Optional[Path] = None) -> None:
    """Upserts one JSON blob under `cache_key` - the generic counterpart to
    `order_book_cache` for sync results that aren't a price (wallet
    balances, undercut rows, skill summaries, ...)."""
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO result_cache (cache_key, payload, computed_at) VALUES (?, ?, ?) "
            "ON CONFLICT(cache_key) DO UPDATE SET payload = excluded.payload, computed_at = excluded.computed_at",
            (cache_key, json.dumps(payload), computed_at),
        )


def load_result_cache(cache_key: str, path: Optional[Path] = None) -> Optional[tuple[Any, str]]:
    """(payload, computed_at), or None when `cache_key` has never been
    cached."""
    with connect(path) as conn:
        row = conn.execute(
            "SELECT payload, computed_at FROM result_cache WHERE cache_key = ?", (cache_key,)
        ).fetchone()
    return (json.loads(row["payload"]), row["computed_at"]) if row else None


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


def find_invention_recipe_by_product_name(product_name: str,
                                          path: Optional[Path] = None) -> Optional[tuple[int, int]]:
    """Given a T2/T3 blueprint's name (the thing you want to invent), returns
    (t1_blueprint_type_id, product_type_id) - the invention recipe that
    produces it. None if `product_name` isn't an invented type. Ported from
    the parent's function of the same name for do_estimate_invention (see
    production/actions.py), which needs to resolve a bare product name before
    it can call invention.compare_recipes_and_decryptors/
    best_recipe_for_decryptor.

    Case-insensitive and tolerant of incidental leading/trailing whitespace,
    matching search_sde_types' own .strip()/LOWER() handling elsewhere in
    this file. Note: for a Tech III product with multiple relic-grade
    candidates, this returns only one (arbitrary, non-deterministic)
    t1_blueprint_type_id - callers that need every candidate grade should use
    find_invention_recipe_candidates_by_product_type_id with the returned
    product_type_id's blueprint instead (see do_estimate_invention, which
    only uses this function's product_type_id half for exactly that reason)."""
    product_name = product_name.strip()
    with connect(path) as conn:
        row = conn.execute(
            "SELECT p.blueprint_type_id, p.product_type_id FROM sde_blueprint_products p "
            "JOIN sde_types t ON t.type_id = p.product_type_id "
            "WHERE p.activity_id = 8 AND LOWER(t.type_name) = LOWER(?)",
            (product_name,),
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


def get_blueprint_time(blueprint_type_id: int, activity_id: int,
                       path: Optional[Path] = None) -> Optional[float]:
    """Base job time (seconds, ME/TE-unadjusted) for one run - ported from the
    parent's own function of the same name (sde_blueprint_time is already
    populated by sde.py; only nothing here read it back until now)."""
    with connect(path) as conn:
        row = conn.execute(
            "SELECT time FROM sde_blueprint_time WHERE blueprint_type_id = ? AND activity_id = ?",
            (blueprint_type_id, activity_id),
        ).fetchone()
    return row[0] if row else None


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


# ------------------------------------------------------- Ore & Minerals: Ore Shortlist
# Same two-table shape as the shortlist/shortlist_snapshot functions above
# (composite key-only live list + no-PK append snapshot). Deliberately raw
# tuples in/out, not refining.models dataclasses - storage.py never imports a
# submodule's own models (same "the submodule does its own wrapping"
# precedent as production/doctrine's storage helpers) - refining/actions.py
# does that wrapping.
def upsert_ore_shortlist(rows: Sequence[tuple[int, str, str, bool, bool]], path: Optional[Path] = None) -> None:
    """rows: (item_id, item, family, is_ice, active)."""
    with connect(path) as conn:
        conn.executemany(
            "INSERT INTO ore_shortlist (item_id, item, family, is_ice, active) VALUES (?,?,?,?,?) "
            "ON CONFLICT(item_id) DO UPDATE SET item=excluded.item, family=excluded.family, "
            "is_ice=excluded.is_ice, active=excluded.active",
            [(item_id, item, family, int(bool(is_ice)), int(bool(active)))
             for item_id, item, family, is_ice, active in rows],
        )


def load_ore_shortlist(path: Optional[Path] = None) -> list[tuple[int, str, str, bool, bool]]:
    """Returns (item_id, item, family, is_ice, active) rows."""
    with connect(path) as conn:
        rows = conn.execute("SELECT item_id, item, family, is_ice, active FROM ore_shortlist").fetchall()
    return [(r["item_id"], r["item"], r["family"], bool(r["is_ice"]), bool(r["active"])) for r in rows]


def deactivate_ore_shortlist_items(item_ids: Sequence[int], path: Optional[Path] = None) -> None:
    item_ids = list(item_ids)
    if not item_ids:
        return
    with connect(path) as conn:
        conn.executemany("UPDATE ore_shortlist SET active = 0 WHERE item_id = ?", [(i,) for i in item_ids])


def activate_ore_shortlist_items(item_ids: Sequence[int], path: Optional[Path] = None) -> None:
    """Reactivation counterpart - same reasoning as Trading's own
    activate_shortlist_items: without this, an item deactivated once would
    stay inactive forever even after its economics recovered, since
    pricing._decision short-circuits to "Inactive" whenever active=False."""
    item_ids = list(item_ids)
    if not item_ids:
        return
    with connect(path) as conn:
        conn.executemany("UPDATE ore_shortlist SET active = 1 WHERE item_id = ?", [(i,) for i in item_ids])


# ------------------------------------------------ Station Trading shortlist
def upsert_station_trading_shortlist(
        rows: Sequence[tuple[int, float, float, str]], path: Optional[Path] = None) -> None:
    """rows: (type_id, spread_pct, avg_daily_volume, discovered_at). `active`
    is deliberately never touched on conflict - a re-discovery run must not
    silently reactivate a type_id the user explicitly deactivated (matches
    do_refresh_shortlist's own documented contract: newly-discovered rows
    all start active, a previously-deactivated one stays deactivated)."""
    with connect(path) as conn:
        conn.executemany(
            "INSERT INTO station_trading_shortlist "
            "(type_id, spread_pct, avg_daily_volume, discovered_at, active) VALUES (?,?,?,?,1) "
            "ON CONFLICT(type_id) DO UPDATE SET spread_pct=excluded.spread_pct, "
            "avg_daily_volume=excluded.avg_daily_volume, discovered_at=excluded.discovered_at",
            list(rows),
        )


def load_station_trading_shortlist(path: Optional[Path] = None) -> list[tuple[int, float, float, str, bool]]:
    """Returns (type_id, spread_pct, avg_daily_volume, discovered_at, active)
    rows, active or not - same "an inactive item still comes back, only its
    decision short-circuits" contract Trading's own load_shortlist has."""
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT type_id, spread_pct, avg_daily_volume, discovered_at, active "
            "FROM station_trading_shortlist"
        ).fetchall()
    return [(r["type_id"], r["spread_pct"], r["avg_daily_volume"], r["discovered_at"], bool(r["active"]))
            for r in rows]


def deactivate_station_trading_shortlist_items(type_ids: Sequence[int], path: Optional[Path] = None) -> None:
    type_ids = list(type_ids)
    if not type_ids:
        return
    with connect(path) as conn:
        conn.executemany(
            "UPDATE station_trading_shortlist SET active = 0 WHERE type_id = ?", [(i,) for i in type_ids])


def activate_station_trading_shortlist_items(type_ids: Sequence[int], path: Optional[Path] = None) -> None:
    """Reactivation counterpart - same reasoning as Trading's own
    activate_shortlist_items/Ore & Minerals' activate_ore_shortlist_items."""
    type_ids = list(type_ids)
    if not type_ids:
        return
    with connect(path) as conn:
        conn.executemany(
            "UPDATE station_trading_shortlist SET active = 1 WHERE type_id = ?", [(i,) for i in type_ids])


def save_ore_shortlist_snapshot(rows: list[tuple], run_ts: str, path: Optional[Path] = None) -> None:
    """rows: (item_id, item, family, is_ice, active, volume_m3, landed_cost,
    yield_pct, mineral_value, refining_tax, net_sell, sell_listed_qty,
    profit_per_unit, margin, profit_per_m3, decision) - see refining/models.py's
    OreShortlistRow for field meanings."""
    with connect(path) as conn:
        conn.executemany(
            "INSERT INTO ore_shortlist_snapshot (run_ts, item_id, item, family, is_ice, active, volume_m3, "
            "landed_cost, yield_pct, mineral_value, refining_tax, net_sell, sell_listed_qty, profit_per_unit, "
            "margin, profit_per_m3, decision) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(run_ts, *row) for row in rows],
        )


def latest_ore_shortlist_snapshot(path: Optional[Path] = None) -> list[tuple]:
    """The most recent run's rows, in ore_shortlist_snapshot's own column
    order (see save_ore_shortlist_snapshot) - refining/actions.py wraps these
    into OreShortlistRow objects for its caller. Empty list if no run has
    been saved yet."""
    with connect(path) as conn:
        run_ts = conn.execute("SELECT MAX(run_ts) FROM ore_shortlist_snapshot").fetchone()[0]
        if not run_ts:
            return []
        rows = conn.execute(
            "SELECT item_id, item, family, is_ice, active, volume_m3, landed_cost, yield_pct, mineral_value, "
            "refining_tax, net_sell, sell_listed_qty, profit_per_unit, margin, profit_per_m3, decision "
            "FROM ore_shortlist_snapshot WHERE run_ts = ?", (run_ts,)
        ).fetchall()
    return [tuple(r) for r in rows]


# --------------------------------------------- Ore & Minerals: Mineral Shopping List
# Same simple key-value shape as stock_targets - raw tuples in/out, same
# reasoning as the Ore Shortlist section above.
def replace_mineral_requirements(rows: Sequence[tuple[int, str, float]], path: Optional[Path] = None) -> None:
    """rows: (mineral_type_id, mineral_name, required_qty). Replace-all, not
    upsert-only: the shopping list's editor always submits the whole list, so
    a mineral removed there has to actually disappear here - an upsert-only
    write would silently keep it, and the optimizer would keep solving for a
    requirement the user already deleted."""
    rows = [(int(type_id), name, float(qty)) for type_id, name, qty in rows]
    with connect(path) as conn:
        conn.execute("DELETE FROM mineral_requirements")
        if rows:
            conn.executemany(
                "INSERT INTO mineral_requirements (mineral_type_id, mineral_name, required_qty) VALUES (?,?,?)",
                rows,
            )


def load_mineral_requirements(path: Optional[Path] = None) -> list[tuple[int, str, float]]:
    """Returns (mineral_type_id, mineral_name, required_qty) rows, name-ordered
    so the editor and the optimizer's output always list minerals the same way."""
    with connect(path) as conn:
        return [tuple(r) for r in conn.execute(
            "SELECT mineral_type_id, mineral_name, required_qty FROM mineral_requirements ORDER BY mineral_name"
        ).fetchall()]


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


def get_product_quantity(blueprint_type_id: int, activity_id: int, product_type_id: int,
                         path: Optional[Path] = None) -> Optional[float]:
    """Output quantity per run for one (blueprint, activity, product) triple -
    ported from the parent's function of the same name, used by
    jobs.list_current_jobs to turn an industry job's `runs` into an actual
    output quantity."""
    with connect(path) as conn:
        row = conn.execute(
            "SELECT quantity FROM sde_blueprint_products "
            "WHERE blueprint_type_id = ? AND activity_id = ? AND product_type_id = ?",
            (blueprint_type_id, activity_id, product_type_id),
        ).fetchone()
    return row[0] if row else None


def list_industry_jobs(path: Optional[Path] = None) -> list[tuple]:
    """Every active/paused/ready character + corp industry job, each with the
    product's type_name joined in: (job_id, activity_id, blueprint_type_id,
    product_type_id, type_name, runs, output_location_id, status, end_date,
    start_date, installer_name). Ported from the parent's function of the
    same name for jobs.list_current_jobs/character_slot_overview - filtered
    to the three "in progress" statuses here (the parent's own version is
    unfiltered, but every real caller only ever wants these; a finished job
    ESI hasn't reported as delivered yet, or a cancelled/reverted one, isn't
    a *current* job)."""
    with connect(path) as conn:
        rows = []
        for table in _JOB_TABLES:
            rows.extend(conn.execute(
                f"SELECT j.job_id, j.activity_id, j.blueprint_type_id, j.product_type_id, "
                f"t.type_name, j.runs, j.output_location_id, j.status, j.end_date, "
                f"j.start_date, j.installer_name "
                f"FROM {table} j LEFT JOIN sde_types t ON t.type_id = j.product_type_id "
                f"WHERE j.status IN ('active', 'paused', 'ready')"
            ).fetchall())
    return [tuple(r) for r in rows]


def load_owned_blueprints(path: Optional[Path] = None) -> list[tuple]:
    """Returns (type_id, quantity, material_efficiency, time_efficiency, runs)
    across character + corp blueprints, for informational display - ported
    from the parent's function of the same name for
    engine.list_owned_blueprints."""
    with connect(path) as conn:
        rows = []
        for table in _BLUEPRINT_TABLES:
            rows.extend(conn.execute(
                f"SELECT type_id, quantity, material_efficiency, time_efficiency, runs FROM {table}"
            ).fetchall())
    return [tuple(r) for r in rows]


def available_blueprint_copies(type_id: int, location_id: Optional[int],
                               tables: tuple[str, str] = _BLUEPRINT_TABLES) -> float:
    """Sums remaining *runs* across owned blueprint copies (quantity == -2,
    ESI's Copy marker - see get_owned_bpo_best_me_te's own runs == -1 check for
    the BPO side of this sentinel) of `type_id` at `location_id` (None = all
    locations, mirroring esi_stock_at_location's own None branch), excluding
    NON_STOCK_LOCATION_FLAGS the same way esi_stock_at_location does - a copy
    sitting in Asset Safety needs its own retrieval trip first.

    Sums *runs*, not the number of separate copy rows - ported from the
    parent's own fixed version of this function (its docstring: one invention
    attempt consumes exactly one run from a T1 BPC, so a single 300-run copy
    supports 300 attempts, not 1 - COUNT(*) would understate real invention
    capacity by up to two orders of magnitude). COALESCE guards a table with
    no matching rows, which SUM alone would return NULL for."""
    flags = ",".join("?" * len(NON_STOCK_LOCATION_FLAGS))
    total = 0.0
    with connect() as conn:
        for table in tables:
            sql = (f"SELECT COALESCE(SUM(runs), 0) FROM {table} WHERE type_id = ? AND quantity = -2 "
                   f"AND (location_flag IS NULL OR location_flag NOT IN ({flags}))")
            params: tuple = (type_id, *NON_STOCK_LOCATION_FLAGS)
            if location_id is not None:
                sql += " AND resolved_location_id = ?"
                params = params + (location_id,)
            total += conn.execute(sql, params).fetchone()[0]
    return total


def has_bpo_at_location(type_id: int, location_id: int,
                        tables: tuple[str, str] = _BLUEPRINT_TABLES) -> bool:
    """Whether an original BPO (runs == -1) of `type_id` sits at
    `location_id` - lets a caller show "can be reprinted on site instead of
    imported" for a missing blueprint copy. Same NON_STOCK_LOCATION_FLAGS
    exclusion as available_blueprint_copies."""
    flags = ",".join("?" * len(NON_STOCK_LOCATION_FLAGS))
    with connect() as conn:
        for table in tables:
            row = conn.execute(
                f"SELECT 1 FROM {table} WHERE type_id = ? AND resolved_location_id = ? AND runs = -1 "
                f"AND (location_flag IS NULL OR location_flag NOT IN ({flags})) LIMIT 1",
                (type_id, location_id, *NON_STOCK_LOCATION_FLAGS),
            ).fetchone()
            if row is not None:
                return True
    return False


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


# -------------------------------------------------------------- manual stock
def upsert_manual_stock(type_id: int, type_name: str, count: float, path: Optional[Path] = None) -> None:
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO manual_stock (type_id, type_name, count) VALUES (?,?,?) "
            "ON CONFLICT(type_id) DO UPDATE SET type_name=excluded.type_name, count=excluded.count",
            (type_id, type_name, count),
        )


def delete_manual_stock(type_id: int, path: Optional[Path] = None) -> None:
    with connect(path) as conn:
        conn.execute("DELETE FROM manual_stock WHERE type_id = ?", (type_id,))


def load_manual_stock(path: Optional[Path] = None) -> dict[int, float]:
    """{type_id: count} - see engine._current_stock/_stock_on_hand, the two
    callers that fold this into owned stock."""
    with connect(path) as conn:
        rows = conn.execute("SELECT type_id, count FROM manual_stock").fetchall()
    return {r[0]: r[1] for r in rows}


def list_manual_stock(path: Optional[Path] = None) -> list[tuple[int, str, float]]:
    """(type_id, type_name, count) for every manual override - CLI listing
    only; engine.py reads load_manual_stock's dict form instead."""
    with connect(path) as conn:
        return [tuple(r) for r in conn.execute(
            "SELECT type_id, type_name, count FROM manual_stock ORDER BY type_name").fetchall()]


# --------------------------------------------------- manual Build/Buy override
def upsert_manual_build_buy(type_id: int, decision: str, path: Optional[Path] = None) -> None:
    assert decision in ("Build", "Buy")
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO manual_build_buy (type_id, decision) VALUES (?,?) "
            "ON CONFLICT(type_id) DO UPDATE SET decision=excluded.decision",
            (type_id, decision),
        )


def delete_manual_build_buy(type_id: int, path: Optional[Path] = None) -> None:
    with connect(path) as conn:
        conn.execute("DELETE FROM manual_build_buy WHERE type_id = ?", (type_id,))


def load_manual_build_buy(path: Optional[Path] = None) -> dict[int, str]:
    """{type_id: "Build"|"Buy"} - see engine._buy_or_build_decision, the one
    caller that consults this to force a sourcing decision regardless of
    modeled cost."""
    with connect(path) as conn:
        rows = conn.execute("SELECT type_id, decision FROM manual_build_buy").fetchall()
    return {r[0]: r[1] for r in rows}


def list_manual_build_buy(path: Optional[Path] = None) -> list[tuple[int, str, str]]:
    """(type_id, type_name, decision) for every override - CLI listing only;
    engine.py reads load_manual_build_buy's dict form instead. type_name is
    joined from sde_types at read time (no type_name column on the table
    itself, unlike manual_stock) since a name is only ever needed for display,
    never for the engine's own lookup by type_id."""
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT b.type_id, COALESCE(t.type_name, '?'), b.decision FROM manual_build_buy b "
            "LEFT JOIN sde_types t ON t.type_id = b.type_id ORDER BY COALESCE(t.type_name, '?')"
        ).fetchall()
    return [tuple(r) for r in rows]


# --------------------------------------------------------- structure names
def get_cached_structure_name(location_id: int,
                              path: Optional[Path] = None) -> tuple[bool, Optional[str]]:
    """Returns (was_cached, name). was_cached=False means resolution has
    never been attempted for this location_id - was_cached=True with
    name=None means it *was* attempted and failed (no producer character
    could see that structure), so callers know not to silently keep retrying
    every page load."""
    with connect(path) as conn:
        row = conn.execute(
            "SELECT name FROM structure_names WHERE location_id = ?", (location_id,),
        ).fetchone()
    if row is None:
        return False, None
    return True, row[0]


def set_cached_structure_name(location_id: int, name: Optional[str],
                              solar_system_id: Optional[int] = None,
                              path: Optional[Path] = None) -> None:
    """solar_system_id is best-effort - do_resolve_structure_name passes it
    whenever the ESI response it just resolved `name` from happened to
    include one; a resolution failure (name=None) never has one. Only ever
    overwritten with a non-None value (COALESCE) so a later best-effort call
    with solar_system_id=None can't blow away a value an earlier call already
    captured."""
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO structure_names (location_id, name, solar_system_id) VALUES (?, ?, ?) "
            "ON CONFLICT(location_id) DO UPDATE SET name=excluded.name, "
            "solar_system_id=COALESCE(excluded.solar_system_id, structure_names.solar_system_id)",
            (location_id, name, solar_system_id),
        )


def list_cached_structure_names(path: Optional[Path] = None) -> list[tuple[int, Optional[str]]]:
    """Every location_id ever attempted, success or failure - name is None
    for an attempted-but-failed resolution."""
    with connect(path) as conn:
        return conn.execute(
            "SELECT location_id, name FROM structure_names ORDER BY name").fetchall()


def search_item_stock_locations(type_id: int,
                                path: Optional[Path] = None) -> list[tuple[int, Optional[str], str, float]]:
    """For `type_id`, every character/corp asset (excluding
    NON_STOCK_LOCATION_FLAGS - see esi_stock_at_location above), grouped by
    (resolved_location_id, owner) summing quantity - resolved_location_id is
    already the outermost station/structure id (see replace_assets).

    Returns [(location_id, location_name, owner_name, quantity), ...] sorted
    by quantity descending. location_name comes from the structure_names
    cache (player structures - populated via resolve-structure-name) first,
    then sde_stations.station_name (NPC stations); None if neither has it
    yet. owner_name is whichever character or "<corp> (corp)" the sync
    attributed this asset to - "?" for data synced before owner_name existed."""
    flag_placeholders = ",".join("?" * len(NON_STOCK_LOCATION_FLAGS))
    with connect(path) as conn:
        raw_rows: list[tuple[int, str, float]] = []  # (resolved_location_id, owner_name, quantity)
        for table in _ASSET_TABLES:
            raw_rows.extend(conn.execute(
                f"SELECT resolved_location_id, owner_name, quantity FROM {table} "
                f"WHERE type_id = ? AND (location_flag IS NULL OR location_flag NOT IN ({flag_placeholders}))",
                (type_id, *NON_STOCK_LOCATION_FLAGS),
            ).fetchall())

        grouped: dict[tuple[int, str], float] = {}
        for location_id, owner_name, quantity in raw_rows:
            key = (location_id, owner_name or "?")
            grouped[key] = grouped.get(key, 0.0) + quantity

        results: list[tuple[int, Optional[str], str, float]] = []
        for (location_id, owner_name), quantity in grouped.items():
            name_row = conn.execute(
                "SELECT name FROM structure_names WHERE location_id = ?", (location_id,)
            ).fetchone()
            name = name_row[0] if name_row else None
            if name is None:
                station_row = conn.execute(
                    "SELECT station_name FROM sde_stations WHERE station_id = ?", (location_id,)
                ).fetchone()
                name = station_row[0] if station_row else None
            results.append((location_id, name, owner_name, quantity))

    results.sort(key=lambda r: r[3], reverse=True)
    return results


# --------------------------------------------------- category locations
def upsert_category_location(category: str, location_id: int, path: Optional[Path] = None) -> None:
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO job_category_locations (category, location_id) VALUES (?,?) "
            "ON CONFLICT(category) DO UPDATE SET location_id=excluded.location_id",
            (category, location_id),
        )


def load_category_locations(path: Optional[Path] = None) -> dict[str, int]:
    with connect(path) as conn:
        rows = conn.execute("SELECT category, location_id FROM job_category_locations").fetchall()
    return {r[0]: r[1] for r in rows}


def delete_category_location(category: str, path: Optional[Path] = None) -> None:
    with connect(path) as conn:
        conn.execute("DELETE FROM job_category_locations WHERE category = ?", (category,))


def add_category_location_option(category: str, location_id: int, path: Optional[Path] = None) -> None:
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO category_location_options (category, location_id) VALUES (?,?) "
            "ON CONFLICT(category, location_id) DO NOTHING",
            (category, location_id),
        )


def load_category_location_options(path: Optional[Path] = None) -> dict[str, list[int]]:
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT category, location_id FROM category_location_options ORDER BY category, location_id"
        ).fetchall()
    result: dict[str, list[int]] = {}
    for category, location_id in rows:
        result.setdefault(category, []).append(location_id)
    return result


def delete_category_location_option(category: str, location_id: int, path: Optional[Path] = None) -> None:
    with connect(path) as conn:
        conn.execute(
            "DELETE FROM category_location_options WHERE category = ? AND location_id = ?",
            (category, location_id),
        )


# -------------------------------------------------------------- special orders
# One-off build orders, tracked separately from the permanent stock_targets
# list (production/engine.py's plan_special_order) - see the special_orders/
# special_order_items schema comment above. Header/child pair, same CRUD
# shape as the Doctrine tables below.

_SPECIAL_ORDER_COLUMNS = ("order_id", "note", "net_against_stock", "status", "created_at")


def create_special_order(note: Optional[str], net_against_stock: bool, path: Optional[Path] = None) -> str:
    order_id = str(uuid.uuid4())
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO special_orders (order_id, note, net_against_stock) VALUES (?, ?, ?)",
            (order_id, note, int(net_against_stock)),
        )
    return order_id


def list_special_orders(path: Optional[Path] = None) -> list[tuple]:
    """(order_id, note, net_against_stock, status, created_at), most-recently-
    created first."""
    with connect(path) as conn:
        rows = conn.execute(
            f"SELECT {', '.join(_SPECIAL_ORDER_COLUMNS)} FROM special_orders ORDER BY created_at DESC"
        ).fetchall()
    return [(r[0], r[1], bool(r[2]), r[3], r[4]) for r in rows]


def get_special_order(order_id: str, path: Optional[Path] = None) -> Optional[tuple]:
    with connect(path) as conn:
        row = conn.execute(
            f"SELECT {', '.join(_SPECIAL_ORDER_COLUMNS)} FROM special_orders WHERE order_id = ?",
            (order_id,),
        ).fetchone()
    return (row[0], row[1], bool(row[2]), row[3], row[4]) if row else None


def update_special_order(order_id: str, updates: dict, path: Optional[Path] = None) -> None:
    """`updates` keys must be a subset of {"note", "net_against_stock",
    "status"} - caller (production/actions.py's do_update_special_order) is
    responsible for only passing real fields, same "thin storage layer"
    convention as storage.update_doctrine."""
    if not updates:
        return
    cols = ", ".join(f"{k} = ?" for k in updates)
    values = [int(v) if k == "net_against_stock" else v for k, v in updates.items()]
    with connect(path) as conn:
        conn.execute(f"UPDATE special_orders SET {cols} WHERE order_id = ?", (*values, order_id))


def delete_special_order(order_id: str, path: Optional[Path] = None) -> None:
    """Deletes the order's own items first - no DB-level cascade, matching
    this codebase's explicit-not-implicit convention for cross-table deletes
    (see delete_doctrine/delete_fitting precedent)."""
    with connect(path) as conn:
        conn.execute("DELETE FROM special_order_items WHERE order_id = ?", (order_id,))
        conn.execute("DELETE FROM special_orders WHERE order_id = ?", (order_id,))


def upsert_special_order_item(order_id: str, type_id: int, type_name: str, quantity: float,
                              path: Optional[Path] = None) -> None:
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO special_order_items (order_id, type_id, type_name, quantity) VALUES (?,?,?,?) "
            "ON CONFLICT(order_id, type_id) DO UPDATE SET "
            "type_name=excluded.type_name, quantity=excluded.quantity",
            (order_id, type_id, type_name, quantity),
        )


def list_special_order_items(order_id: str, path: Optional[Path] = None) -> list[tuple[int, str, float]]:
    """(type_id, type_name, quantity), name-ordered."""
    with connect(path) as conn:
        return conn.execute(
            "SELECT type_id, type_name, quantity FROM special_order_items WHERE order_id = ? ORDER BY type_name",
            (order_id,),
        ).fetchall()


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


_CONTRACT_HISTORY_COLUMNS = ("contract_id", "source_role", "fitting_id", "fitting_name", "hull_type_id",
                             "title", "price", "acceptor_id", "acceptor_name", "date_issued", "date_completed")


def upsert_doctrine_contract_history(rows: list[tuple], path: Optional[Path] = None) -> None:
    """GitHub issue #19 - rows matching _CONTRACT_HISTORY_COLUMNS. Upserted
    (never wholesale-replaced, unlike doctrine_contracts' own snapshot) -
    this table is permanent, append-only history: a contract only reaches a
    FINISHED_CONTRACT_STATUSES status once, but ON CONFLICT still updates
    rather than doing nothing, since a later sync might resolve an
    acceptor_name that failed to resolve (e.g. a transient ESI error) the
    first time it was recorded."""
    if not rows:
        return
    set_clause = ", ".join(f"{c}=excluded.{c}" for c in _CONTRACT_HISTORY_COLUMNS if c != "contract_id")
    with connect(path) as conn:
        conn.executemany(
            f"INSERT INTO doctrine_contract_history ({', '.join(_CONTRACT_HISTORY_COLUMNS)}) "
            f"VALUES ({', '.join('?' for _ in _CONTRACT_HISTORY_COLUMNS)}) "
            f"ON CONFLICT(contract_id) DO UPDATE SET {set_clause}",
            rows,
        )


def load_doctrine_contract_history(path: Optional[Path] = None) -> list[tuple]:
    """Most-recently-completed first; a history row with no date_completed at
    all (shouldn't normally happen - ESI sets it the moment a contract
    finishes - but the field is nullable in ESI's own model) sorts last
    rather than first. SQLite's own NULLS LAST support (3.30+) is assumed -
    same baseline this codebase already relies on elsewhere."""
    with connect(path) as conn:
        return [tuple(r) for r in conn.execute(
            f"SELECT {', '.join(_CONTRACT_HISTORY_COLUMNS)} FROM doctrine_contract_history "
            "ORDER BY date_completed DESC NULLS LAST"
        ).fetchall()]


def has_any_doctrine_synced_assets(path: Optional[Path] = None) -> bool:
    """True if either doctrine_character_assets or doctrine_corp_assets has
    ever been populated - lets Stockpile distinguish "you have zero of
    everything" from "you've never run a Doctrine asset sync at all"."""
    with connect(path) as conn:
        row = conn.execute(
            "SELECT EXISTS(SELECT 1 FROM doctrine_character_assets) OR EXISTS(SELECT 1 FROM doctrine_corp_assets)"
        ).fetchone()
    return bool(row[0])
