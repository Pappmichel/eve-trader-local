from __future__ import annotations

import pytest

from eve_trader_local import storage


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Points the whole app at a throwaway data directory, so no test can
    ever touch the real ~/.eve-trader-local database."""
    monkeypatch.setenv("EVE_TRADER_LOCAL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("EVE_TRADER_LOCAL_CONFIG", raising=False)
    storage.init_db()
    return storage.db_path()
