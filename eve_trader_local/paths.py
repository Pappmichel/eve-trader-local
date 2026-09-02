"""Where this app keeps its files on the user's own machine.

Separate from config.py because storage.py needs DB_PATH while config.py
imports storage (to read stored overrides) - putting these in config.py
would make that import cycle real.

Unlike the parent eve-trader repo, data does *not* live inside the checkout:
a local-first desktop app is expected to keep working when it's installed
read-only somewhere, so the default is a per-user directory. Both the data
directory and the config file can be pointed elsewhere by env var, which is
also what the test suite uses to stay off the real user's database.
"""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """Resolved per call, not cached at import time - tests (and a future
    GUI's "choose data folder") set the env var after this module is already
    imported."""
    raw = os.getenv("EVE_TRADER_LOCAL_DATA_DIR")
    path = Path(raw).expanduser() if raw else Path.home() / ".eve-trader-local"
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return data_dir() / "eve-trader-local.sqlite3"


def config_path() -> Path:
    raw = os.getenv("EVE_TRADER_LOCAL_CONFIG")
    return Path(raw).expanduser() if raw else data_dir() / "config.yaml"
