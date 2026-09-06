from __future__ import annotations

import pytest

from eve_trader_local import storage
from eve_trader_local.production import engine as production_engine


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Points the whole app at a throwaway data directory, so no test can
    ever touch the real ~/.eve-trader-local database."""
    monkeypatch.setenv("EVE_TRADER_LOCAL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("EVE_TRADER_LOCAL_CONFIG", raising=False)
    storage.init_db()
    return storage.db_path()


@pytest.fixture(autouse=True)
def _reset_sde_lookup_caches():
    """storage.get_sde_type/get_type_materials/get_type_category/
    get_blueprint_for_product/get_blueprint_materials/
    find_invention_recipe_candidates_by_product_type_id are lru_cache'd
    (see their own docstrings), and production.engine.discover_build_
    candidates has its own TTL cache - both are normally invalidated by
    storage.replace_sde_data()/invalidate_discover_cache() when real SDE/
    Settings changes happen, but many tests call storage.replace_sde_data()
    with the same default (no explicit `path`) across different tmp
    databases in one test session, and a stale lru_cache entry from an
    earlier test's data would otherwise silently leak into a later one
    (same class of bug this repo's own CLAUDE.md warns about for any
    module-level cache)."""
    storage.get_sde_type.cache_clear()
    storage.get_type_materials.cache_clear()
    storage.get_type_category.cache_clear()
    storage.get_blueprint_for_product.cache_clear()
    storage.get_blueprint_materials.cache_clear()
    storage.find_invention_recipe_candidates_by_product_type_id.cache_clear()
    production_engine.invalidate_discover_cache()
    yield
    storage.get_sde_type.cache_clear()
    storage.get_type_materials.cache_clear()
    storage.get_type_category.cache_clear()
    storage.get_blueprint_for_product.cache_clear()
    storage.get_blueprint_materials.cache_clear()
    storage.find_invention_recipe_candidates_by_product_type_id.cache_clear()
    production_engine.invalidate_discover_cache()
