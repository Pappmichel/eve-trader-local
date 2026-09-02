"""SQLite persistence for eve-trader-local.

Single-user by construction: there is no tenant_id column anywhere, no row
level security, and no connection-scoped session variable to set - the
database file lives on one person's machine and everything in it belongs to
them. That is the whole reason this repo exists separately from the parent
eve-trader (see README).

Three tables, ported from the parent's Postgres schema minus tenant scoping:
  tokens        <- tenant_tokens      (OAuth records, keyed by role)
  esi_sync_state <- esi_sync_state    (when each scope last synced)
  settings      <- tenant_settings    (config overrides, keyed by scope)

JSON columns are plain TEXT holding json.dumps output - SQLite's own JSON1
functions aren't needed since nothing queries *into* these documents; they
are read and written whole.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

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
"""


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
