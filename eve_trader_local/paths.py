"""Where this app keeps its files on the user's own machine.

Separate from config.py because storage.py needs DB_PATH while config.py
imports storage (to read stored overrides) - putting these in config.py
would make that import cycle real.

Unlike the parent eve-trader repo, data does *not* live inside the checkout:
a local-first desktop app is expected to keep working when it's installed
read-only somewhere, so the default is a per-user directory - *unless* this
is the packaged `.exe` (see `is_frozen()`), which is meant to be portable:
copy the folder anywhere (a USB stick, a different machine) and it keeps its
own data with it, rather than scattering files into that machine's user
profile. Both the data directory and the config file can still be pointed
elsewhere by env var in either case, which is also what the test suite uses
to stay off the real user's database.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def is_frozen() -> bool:
    """True only inside a PyInstaller-built `.exe` (see
    `packaging/eve-trader-local.spec`) - `sys.frozen` is a PyInstaller
    bootloader attribute that plain `python`/an editable source checkout
    never sets."""
    return bool(getattr(sys, "frozen", False))


def _default_data_dir() -> Path:
    if is_frozen():
        # sys.executable is the real, user-placed .exe path for a onefile
        # build (unlike sys._MEIPASS, which is a throwaway extraction temp
        # dir) - exactly the "next to the .exe" location portability needs.
        return Path(sys.executable).resolve().parent / ".eve-trader-local"
    return Path.home() / ".eve-trader-local"


def data_dir() -> Path:
    """Resolved per call, not cached at import time - tests (and a future
    GUI's "choose data folder") set the env var after this module is already
    imported."""
    raw = os.getenv("EVE_TRADER_LOCAL_DATA_DIR")
    path = Path(raw).expanduser() if raw else _default_data_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return data_dir() / "eve-trader-local.sqlite3"


def config_path() -> Path:
    raw = os.getenv("EVE_TRADER_LOCAL_CONFIG")
    return Path(raw).expanduser() if raw else data_dir() / "config.yaml"
