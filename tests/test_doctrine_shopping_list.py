"""Tests for doctrine/engine.py's shopping_list_rows/_shopping_prices - the
Build-vs-Buy-C-J-vs-Buy-Jita comparison over real stockpile shortfalls.

No real network/SDE-blueprint-walk needed: stockpile_rows_for_doctrine,
production_pricing.home_prices/jita_prices/cached_system_cost_indices/
cached_adjusted_prices, unit_cost_detail, _haul_volume and
structural_material_closure are all monkeypatched at the doctrine.engine
module boundary, matching this repo's established no-network test pattern
(test_production_pricing.py, test_doctrine_engine.py). home_prices/
jita_prices/cached_system_cost_indices/cached_adjusted_prices are
cache-only reads now (see esi_update.py's own docstring) - engine.py itself
never constructs an ESIClient anymore, so there is nothing left to fake at
that level."""
from __future__ import annotations

from eve_trader_local.doctrine import engine
from eve_trader_local.doctrine.config import DoctrineConfig
from eve_trader_local.doctrine.models import StockpileRow
from eve_trader_local.goonmetrics_client import CurrentPrice

TYPE_BUILD_WINS = 1   # Build cheapest of the three
TYPE_JITA_WINS = 2    # Buy-Jita cheapest of the three
TYPE_UNPRICEABLE = 3  # no build recipe, no market data anywhere


def _stockpile_row(type_id: int, type_name: str, shortfall: float) -> StockpileRow:
    return StockpileRow(
        fitting_id="f1", fitting_name="Fit A", doctrine_id="d1", doctrine_name="Doctrine",
        type_id=type_id, type_name=type_name, slot_section="low",
        required_total=shortfall, available=0, shortfall=shortfall, severity="critical",
    )


def _price(type_id: int, sell: float) -> CurrentPrice:
    return CurrentPrice(type_id=type_id, updated="", buy=0.0, sell=sell)


def _patch_pricing_boundary(monkeypatch, home: dict, jita: dict, build_costs: dict):
    """Stubs everything shopping_list_rows reaches for outside pure logic:
    cached home/jita quotes, cached cost-index/adjusted-price lookups, and
    Production's own unit_cost_detail (build_costs maps type_id -> build_cost
    or None, mirroring unit_cost_detail's (best, build_cost, buy) shape)."""
    monkeypatch.setattr(engine.production_pricing, "home_prices", lambda type_ids, cfg: home)
    monkeypatch.setattr(engine.production_pricing, "jita_prices", lambda type_ids: jita)
    monkeypatch.setattr(engine.production_pricing, "cached_system_cost_indices", lambda system_id: {})
    monkeypatch.setattr(engine.production_pricing, "cached_adjusted_prices", lambda: {})
    monkeypatch.setattr(engine, "structural_material_closure", lambda seed_ids: set(seed_ids))
    monkeypatch.setattr(engine, "_haul_volume", lambda type_id, cfg: 10.0)

    def fake_unit_cost_detail(type_id, cfg, home_, jita_, memo, decryptors, t2_memo, cost_indices, adjusted_prices):
        build_cost = build_costs.get(type_id)
        buy = None
        best = build_cost if buy is None else min(buy, build_cost) if build_cost is not None else buy
        return best, build_cost, buy

    monkeypatch.setattr(engine, "unit_cost_detail", fake_unit_cost_detail)


def test_shopping_list_prices_a_shortfall_item_all_three_ways(db, monkeypatch):
    rows = [_stockpile_row(TYPE_BUILD_WINS, "Damage Control II", shortfall=5)]
    monkeypatch.setattr(engine, "stockpile_rows_for_doctrine", lambda doctrine_id=None, cfg=None: (rows, True))

    home = {TYPE_BUILD_WINS: _price(TYPE_BUILD_WINS, sell=200.0)}
    jita = {TYPE_BUILD_WINS: _price(TYPE_BUILD_WINS, sell=150.0)}
    build_costs = {TYPE_BUILD_WINS: 100.0}
    _patch_pricing_boundary(monkeypatch, home, jita, build_costs)

    cfg = DoctrineConfig(import_cost_per_m3=900.0)
    result = engine.shopping_list_rows(cfg=cfg)
    assert len(result) == 1
    row = result[0]

    broker_fee = engine.PRODUCTION_CONFIG.jita_buy_broker_fee
    expected_cj = 200.0 * (1 + broker_fee)
    expected_jita = 150.0 * (1 + broker_fee) + 900.0 * 10.0
    assert row.build_cost == 100.0
    assert row.cj_price == expected_cj
    assert row.jita_landed_price == expected_jita
    # Build is unambiguously the cheapest of the three -> it's recommended,
    # and total_cost is the recommended price times the real shortfall.
    assert row.recommended_source == "Build"
    assert row.total_cost == 100.0 * 5


def test_shopping_list_cheapest_source_is_correctly_identified_when_buy_wins(db, monkeypatch):
    rows = [_stockpile_row(TYPE_JITA_WINS, "Cheap Mineral", shortfall=10)]
    monkeypatch.setattr(engine, "stockpile_rows_for_doctrine", lambda doctrine_id=None, cfg=None: (rows, True))

    # Jita landed (after broker fee + haul) undercuts both Build and C-J.
    home = {TYPE_JITA_WINS: _price(TYPE_JITA_WINS, sell=500.0)}
    jita = {TYPE_JITA_WINS: _price(TYPE_JITA_WINS, sell=10.0)}
    build_costs = {TYPE_JITA_WINS: 300.0}
    _patch_pricing_boundary(monkeypatch, home, jita, build_costs)

    cfg = DoctrineConfig(import_cost_per_m3=1.0)  # tiny haul cost so Jita really is cheapest
    result = engine.shopping_list_rows(cfg=cfg)
    row = result[0]
    assert row.recommended_source == "Jita"
    assert row.total_cost == row.jita_landed_price * 10


def test_shopping_list_unpriceable_item_is_never_zero_isk(db, monkeypatch):
    """No blueprint (build_cost None) and no market data at all on either
    leg (type_id absent from both home/jita quote dicts) - recommended_source
    and total_cost must come back None, never a silent 0 ISK."""
    rows = [_stockpile_row(TYPE_UNPRICEABLE, "Obscure Faction Widget", shortfall=3)]
    monkeypatch.setattr(engine, "stockpile_rows_for_doctrine", lambda doctrine_id=None, cfg=None: (rows, True))

    _patch_pricing_boundary(monkeypatch, home={}, jita={}, build_costs={TYPE_UNPRICEABLE: None})

    result = engine.shopping_list_rows(cfg=DoctrineConfig())
    row = result[0]
    assert row.build_cost is None
    assert row.cj_price is None
    assert row.jita_landed_price is None
    assert row.recommended_source is None
    assert row.total_cost is None


def test_shopping_list_skips_items_with_no_real_shortfall(db, monkeypatch):
    # shortfall == 0 -> not included, matching aggregate_stockpile_rows'
    # filter (this module filters r.shortfall > 0 before pricing anything).
    rows = [_stockpile_row(TYPE_BUILD_WINS, "Fully Stocked Item", shortfall=0)]
    monkeypatch.setattr(engine, "stockpile_rows_for_doctrine", lambda doctrine_id=None, cfg=None: (rows, True))
    _patch_pricing_boundary(monkeypatch, home={}, jita={}, build_costs={})
    assert engine.shopping_list_rows(cfg=DoctrineConfig()) == []
