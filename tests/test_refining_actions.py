"""Tests for eve_trader_local/refining/actions.py - the Ore & Minerals
orchestration layer (mirrors eve_trader/refining/actions.py). Everything that
would touch ESI/Goonmetrics/EVE SSO is replaced at the module boundary
(refining_actions.ESIClient / .TokenManager / .GoonmetricsClient), same
pattern tests/test_actions.py uses for the top-level actions.py - no network
at all. SDE-dependent computation (yield math, portion sizing) runs against a
real synthetic SDE cache seeded via storage.replace_sde_data, matching
tests/test_refining_candidate_discovery.py's own convention.
"""
from __future__ import annotations

import pytest

from eve_trader_local import storage
from eve_trader_local.config import TradingConfig
from eve_trader_local.errors import ActionError
from eve_trader_local.esi_client import ESIError, OrderStats
from eve_trader_local.production.config import ProductionConfig
from eve_trader_local.refining import actions
from eve_trader_local.refining.config import RefiningConfig

VELDSPAR = 17471       # Compressed Veldspar
TRITANIUM = 34
ORE_CATEGORY_ID = 25


def _seed_sde():
    storage.replace_sde_data(
        types=[
            (VELDSPAR, 1884, "Compressed Veldspar", 0.01, 1, 100, None, None, 100),
            (TRITANIUM, 18, "Tritanium", 0.01, 1, 101, None, None, 1),
        ],
        groups=[(1884, ORE_CATEGORY_ID, "Veldspar"), (18, 4, "Mineral")],
        market_groups=[(100, None, "Ore"), (101, None, "Minerals")],
        blueprint_time=[], blueprint_materials=[], blueprint_products=[],
        categories=[(4, "Material")],
        type_materials=[(VELDSPAR, TRITANIUM, 415)],
    )


def stats(sell_percentile, sell_volume=1000.0) -> OrderStats:
    return OrderStats(sell_percentile=sell_percentile, sell_volume=sell_volume,
                      buy_percentile=None, buy_volume=0.0)


def _seed_order_book(market: str, stats_by_id: dict) -> None:
    """Seeds storage.order_book_cache directly, standing in for a sync
    bundle's own live fetch - do_quote_reprocessing/do_optimize_mineral_
    shopping_list are cache-only reads now (see esi_update.py's own
    docstring) and never construct an ESIClient themselves."""
    rows = {tid: (s.buy_percentile, s.sell_percentile, s.buy_volume, s.sell_volume)
           for tid, s in stats_by_id.items()}
    storage.save_order_book_stats(market, rows, "2026-01-01T00:00:00")


@pytest.fixture
def trading_cfg() -> TradingConfig:
    return TradingConfig(jita_region_id=10000002, structure_id=1234567890123,
                         structure_market_slug="cj", jita_buy_broker_fee=0.0,
                         structure_sell_haircut=1.0, import_cost_per_m3=0.0,
                         min_profit_threshold=0.0, min_margin_threshold=0.05)


@pytest.fixture
def refining_cfg() -> RefiningConfig:
    return RefiningConfig(refining_tax_rate=0.0)


# ------------------------------------------------------------------- stubs
class StubRecord:
    def __init__(self, role):
        self.role = role


class StubTokenManager:
    def __init__(self, records=()):
        self.records = list(records)

    def list_records(self, prefix=None):
        if prefix is None:
            return list(self.records)
        return [r for r in self.records if r.role.startswith(f"{prefix}:")]


class StubClient:
    def __init__(self, *, jita_stats=None, structure_stats=None, structure_error=None):
        self.jita_stats = jita_stats or {}
        self.structure_stats = structure_stats or {}
        self.structure_error = structure_error

    def region_order_stats_bulk(self, region_id, type_ids):
        return {tid: self.jita_stats.get(tid, stats(None)) for tid in type_ids}

    def structure_order_stats_bulk_or_goonmetrics(self, structure_id, type_ids, auth_role,
                                                  goonmetrics_market_slug):
        if self.structure_error:
            raise self.structure_error
        return {tid: self.structure_stats.get(tid, stats(None)) for tid in type_ids}, False


class StubGoonmetrics:
    def __init__(self, prices=()):
        self.prices = list(prices)

    def current_prices(self, market):
        return list(self.prices)


def install(monkeypatch, *, client=None, tokens=None, goonmetrics=None):
    if client is not None:
        monkeypatch.setattr(actions, "ESIClient", lambda *a, **k: client)
    if tokens is not None:
        monkeypatch.setattr(actions, "TokenManager", lambda *a, **k: tokens)
    if goonmetrics is not None:
        monkeypatch.setattr(actions, "GoonmetricsClient", lambda *a, **k: goonmetrics)


# --------------------------------------------------------------- Ore Shortlist
def test_add_ore_to_shortlist_empty_sde_raises(db):
    with pytest.raises(ActionError):
        actions.do_add_ore_to_shortlist()


def test_add_ore_to_shortlist_adds_new_candidates(db):
    _seed_sde()
    result = actions.do_add_ore_to_shortlist()
    assert result == {"added": 1, "already_tracked": 0}
    assert storage.load_ore_shortlist() == [(VELDSPAR, "Compressed Veldspar", "Veldspar", False, True)]

    # A second run finds it already tracked, adds nothing new.
    result = actions.do_add_ore_to_shortlist()
    assert result == {"added": 0, "already_tracked": 1}


def test_refresh_ore_shortlist_empty_raises(db, monkeypatch, trading_cfg, refining_cfg):
    _seed_sde()
    install(monkeypatch, client=StubClient(), tokens=StubTokenManager())
    with pytest.raises(ActionError, match="empty"):
        actions.do_refresh_ore_shortlist(trading_cfg, refining_cfg)


def test_refresh_ore_shortlist_happy_path(db, monkeypatch, trading_cfg, refining_cfg):
    """_cache_ore_mineral_prices (Market Prices scope) does the live fetch;
    do_refresh_ore_shortlist (pure local, see esi_update.py's own docstring)
    then recomputes from that cache alone."""
    _seed_sde()
    actions.do_add_ore_to_shortlist()
    client = StubClient(jita_stats={VELDSPAR: stats(1.0)},
                        structure_stats={TRITANIUM: stats(10.0)})
    install(monkeypatch, client=client, tokens=StubTokenManager([StubRecord("seller:1")]))

    price_result = actions._cache_ore_mineral_prices(trading_cfg)
    assert price_result["priced_via_fallback"] is False

    result = actions.do_refresh_ore_shortlist(trading_cfg, refining_cfg)
    assert result["evaluated"] == 1
    assert result["import_candidates"] == 1

    rows = actions.do_get_ore_shortlist()["rows"]
    assert len(rows) == 1
    assert rows[0].item == "Compressed Veldspar"
    assert rows[0].decision == "Import"


def test_refresh_ore_shortlist_structure_error_raises(db, monkeypatch, trading_cfg, refining_cfg):
    _seed_sde()
    actions.do_add_ore_to_shortlist()
    client = StubClient(jita_stats={VELDSPAR: stats(1.0)}, structure_error=ESIError("no docking access"))
    install(monkeypatch, client=client, tokens=StubTokenManager([StubRecord("seller:1")]))
    with pytest.raises(ActionError, match="docking access"):
        actions._cache_ore_mineral_prices(trading_cfg)


def test_deactivate_and_activate_ore_shortlist_items(db):
    _seed_sde()
    actions.do_add_ore_to_shortlist()
    assert actions.do_deactivate_ore_shortlist_items([VELDSPAR]) == {"deactivated": 1}
    assert storage.load_ore_shortlist()[0][4] is False
    assert actions.do_activate_ore_shortlist_items([VELDSPAR]) == {"activated": 1}
    assert storage.load_ore_shortlist()[0][4] is True


def test_get_ore_shortlist_before_any_refresh_is_empty(db):
    assert actions.do_get_ore_shortlist() == {"rows": []}


# ---------------------------------------------------------- Reprocessing quote
def test_quote_reprocessing_empty_paste_raises(db, trading_cfg, refining_cfg):
    with pytest.raises(ActionError, match="empty"):
        actions.do_quote_reprocessing("   ", trading_cfg, refining_cfg)


def test_quote_reprocessing_happy_path(db, trading_cfg, refining_cfg):
    _seed_sde()
    _seed_order_book(f"structure:{trading_cfg.structure_id}",
                     {VELDSPAR: stats(2.0), TRITANIUM: stats(10.0)})

    paste = "Compressed Veldspar\t100\tVeldspar\tAsteroid\t\t\t0.01\t\t"
    result = actions.do_quote_reprocessing(paste, trading_cfg, refining_cfg)
    assert len(result["rows"]) == 1
    row = result["rows"][0]
    assert row.type_id == VELDSPAR
    assert row.error is None
    assert "reprocess_count" in result["totals"]


def test_quote_reprocessing_unresolvable_item(db, trading_cfg, refining_cfg):
    _seed_sde()
    paste = "Not A Real Item\t5\tJunk\tMisc\t\t\t1.0\t\t"
    result = actions.do_quote_reprocessing(paste, trading_cfg, refining_cfg)
    assert result["rows"][0].decision == "Unknown item"


def test_quote_reprocessing_with_nothing_cached_is_unpriced(db, trading_cfg, refining_cfg):
    """No sync has ever cached this item's structure-book price - a cache-
    only read must degrade to "unpriced", never raise (see esi_update.py's
    own docstring for why nothing here can fall back to a live call)."""
    _seed_sde()
    paste = "Compressed Veldspar\t100\tVeldspar\tAsteroid\t\t\t0.01\t\t"
    result = actions.do_quote_reprocessing(paste, trading_cfg, refining_cfg)
    row = result["rows"][0]
    assert row.type_id == VELDSPAR
    assert row.sell_as_is_value is None


# ------------------------------------------------- Mineral requirement CRUD
def test_save_and_load_mineral_requirements(db):
    _seed_sde()
    result = actions.do_save_mineral_requirements([{"type_id": TRITANIUM, "required_qty": 500}])
    assert result == {"saved": 1}
    assert actions.do_load_mineral_requirements() == [
        {"type_id": TRITANIUM, "name": "Tritanium", "required_qty": 500.0}]


def test_save_mineral_requirements_rejects_unknown_type(db):
    with pytest.raises(ActionError):
        actions.do_save_mineral_requirements([{"type_id": 99999, "required_qty": 10}])


def test_save_mineral_requirements_rejects_non_positive_qty(db):
    _seed_sde()
    with pytest.raises(ActionError):
        actions.do_save_mineral_requirements([{"type_id": TRITANIUM, "required_qty": 0}])


def test_save_mineral_requirements_rejects_duplicate_type(db):
    _seed_sde()
    with pytest.raises(ActionError, match="twice"):
        actions.do_save_mineral_requirements([
            {"type_id": TRITANIUM, "required_qty": 10}, {"type_id": TRITANIUM, "required_qty": 20}])


def test_set_mineral_requirement_by_name_is_additive(db):
    _seed_sde()
    actions.do_set_mineral_requirement("Tritanium", 100.0)
    storage.replace_sde_data(
        types=[(TRITANIUM, 18, "Tritanium", 0.01, 1, 101, None, None, 1),
              (35, 18, "Pyerite", 0.01, 1, 101, None, None, 1)],
        groups=[(18, 4, "Mineral")], market_groups=[(101, None, "Minerals")],
        blueprint_time=[], blueprint_materials=[], blueprint_products=[], categories=[(4, "Material")],
    )
    actions.do_set_mineral_requirement(str(35), 50.0)
    rows = {r["type_id"]: r["required_qty"] for r in actions.do_load_mineral_requirements()}
    assert rows == {TRITANIUM: 100.0, 35: 50.0}


def test_remove_mineral_requirement(db):
    _seed_sde()
    actions.do_set_mineral_requirement("Tritanium", 100.0)
    result = actions.do_remove_mineral_requirement("Tritanium")
    assert result == {"removed": TRITANIUM, "type_name": "Tritanium"}
    assert actions.do_load_mineral_requirements() == []


def test_list_refinable_minerals(db):
    _seed_sde()
    minerals = actions.do_list_refinable_minerals()
    assert minerals == [{"type_id": TRITANIUM, "name": "Tritanium"}]


# --------------------------------------------------------- Mineral Shopping List
def test_optimize_shopping_list_no_requirements_raises(db, trading_cfg, refining_cfg):
    _seed_sde()
    with pytest.raises(ActionError, match="requirements"):
        actions.do_optimize_mineral_shopping_list([], trading_cfg, refining_cfg, ProductionConfig())


def test_optimize_shopping_list_no_ore_universe_raises(db, trading_cfg, refining_cfg):
    with pytest.raises(ActionError, match="SDE cache"):
        actions.do_optimize_mineral_shopping_list(
            [{"type_id": TRITANIUM, "required_qty": 100, "name": "Tritanium"}],
            trading_cfg, refining_cfg, ProductionConfig())


def test_optimize_shopping_list_raises_when_nothing_is_cached(db, trading_cfg, refining_cfg):
    """No sync has ever cached Jita prices - a cache-only read must degrade
    to "nothing prices this", never a live call (see esi_update.py's own
    docstring); the LP solve itself is what raises here, same as a genuinely
    unpriceable mineral would."""
    _seed_sde()
    with pytest.raises(ActionError, match="No way to source"):
        actions.do_optimize_mineral_shopping_list(
            [{"type_id": TRITANIUM, "required_qty": 100, "name": "Tritanium"}],
            trading_cfg, refining_cfg, ProductionConfig())


def test_optimize_shopping_list_end_to_end(db, trading_cfg, refining_cfg):
    """discover ore candidates -> price them -> solve for a mineral
    requirement, with cached Jita prices - a real, if small, LP solve."""
    _seed_sde()
    _seed_order_book(f"region:{trading_cfg.jita_region_id}",
                     {VELDSPAR: stats(1.0), TRITANIUM: stats(50.0)})

    plan = actions.do_optimize_mineral_shopping_list(
        [{"type_id": TRITANIUM, "required_qty": 415, "name": "Tritanium"}],
        trading_cfg, refining_cfg, ProductionConfig())

    assert plan["total_cost"] > 0
    assert plan["coverage"][0].type_id == TRITANIUM
    assert plan["coverage"][0].delivered >= 415
    # Buying one whole portion of Veldspar (100 units @ 1 ISK + haul) is far
    # cheaper than buying 415 Tritanium outright at 50 ISK/unit - the ore
    # route should win here.
    assert plan["ore_purchases"]
    assert plan["ore_cost"] < plan["all_direct_cost"]


def test_optimize_shopping_list_defaults_to_saved_requirements(db, trading_cfg, refining_cfg):
    _seed_sde()
    actions.do_save_mineral_requirements([{"type_id": TRITANIUM, "required_qty": 415}])
    _seed_order_book(f"region:{trading_cfg.jita_region_id}",
                     {VELDSPAR: stats(1.0), TRITANIUM: stats(50.0)})

    plan = actions.do_optimize_mineral_shopping_list(None, trading_cfg, refining_cfg, ProductionConfig())
    assert plan["coverage"][0].required == 415.0


# ------------------------------------------------------------------ settings
def test_update_settings_validates_and_applies(db):
    cfg = RefiningConfig()
    result = actions.do_update_settings({"refining_tax_rate": 0.05}, cfg)
    assert result == {"updated": ["refining_tax_rate"]}
    assert cfg.refining_tax_rate == 0.05


def test_update_settings_rejects_bad_structure_type(db):
    cfg = RefiningConfig()
    with pytest.raises(ActionError):
        actions.do_update_settings({"structure_type": "Not A Real Structure"}, cfg)
