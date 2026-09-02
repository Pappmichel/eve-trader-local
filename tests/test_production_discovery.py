"""Tests for engine.discover_build_candidates - the SDE-wide build-vs-buy
scan (Production's equivalent of Trading's candidate_discovery.py). Runs
against a small synthetic SDE (storage.replace_sde_data, same pattern as
test_production_engine.py's `tree` fixture) with pricing.home_prices/
jita_prices and the ESI adjusted-price/cost-index lookups monkeypatched -
nothing here touches the network.
"""
from __future__ import annotations

import pytest

from eve_trader_local import storage
from eve_trader_local.goonmetrics_client import CurrentPrice, HistoryPoint
from eve_trader_local.production import engine
from eve_trader_local.production.config import ProductionConfig

MINERAL = 34            # Input - no blueprint at all
GOOD_ITEM = 90101        # Clears both min_margin and min_daily_profit
LOW_MARGIN_ITEM = 90102  # Clears the home-quote sanity check, fails min_margin
ILLIQUID_ITEM = 90103    # Clears min_margin, but Goonmetrics has zero history
BAD_QUOTE_ITEM = 90104   # buy == sell exactly - synthetic/fallback quote, skipped
GOOD_BP, LOW_MARGIN_BP, ILLIQUID_BP, BAD_QUOTE_BP = 91101, 91102, 91103, 91104


def _seed():
    storage.replace_sde_data(
        types=[
            (MINERAL, 18, "Tritanium", 0.01, 1, 100, 0, 1, 1),
            (GOOD_ITEM, 18, "Good Widget", 1.0, 1, 200, 0, 1, 1),
            (LOW_MARGIN_ITEM, 18, "Thin-Margin Widget", 1.0, 1, 200, 0, 1, 1),
            (ILLIQUID_ITEM, 18, "Illiquid Widget", 1.0, 1, 200, 0, 1, 1),
            (BAD_QUOTE_ITEM, 18, "Suspicious Widget", 1.0, 1, 200, 0, 1, 1),
            (GOOD_BP, 9, "Good Widget Blueprint", 0.01, 1, None, 0, None, 1),
            (LOW_MARGIN_BP, 9, "Thin-Margin Widget Blueprint", 0.01, 1, None, 0, None, 1),
            (ILLIQUID_BP, 9, "Illiquid Widget Blueprint", 0.01, 1, None, 0, None, 1),
            (BAD_QUOTE_BP, 9, "Suspicious Widget Blueprint", 0.01, 1, None, 0, None, 1),
        ],
        groups=[(18, 4, "Mineral"), (9, 9, "Blueprint")],
        market_groups=[(100, None, "Manufacture & Research"), (200, None, "Ship Equipment")],
        blueprint_time=[],
        blueprint_materials=[
            (GOOD_BP, 1, MINERAL, 10),
            (LOW_MARGIN_BP, 1, MINERAL, 10),
            (ILLIQUID_BP, 1, MINERAL, 10),
            (BAD_QUOTE_BP, 1, MINERAL, 10),
        ],
        blueprint_products=[
            (GOOD_BP, 1, GOOD_ITEM, 1),
            (LOW_MARGIN_BP, 1, LOW_MARGIN_ITEM, 1),
            (ILLIQUID_BP, 1, ILLIQUID_ITEM, 1),
            (BAD_QUOTE_BP, 1, BAD_QUOTE_ITEM, 1),
        ],
        categories=[(4, "Material")],
    )


def _cfg(**overrides) -> ProductionConfig:
    cfg = ProductionConfig(jita_buy_broker_fee=0.0, haul_cost_per_m3=0.0,
                           facility_tax_rate=0.0, market_fees=0.0,
                           min_margin=0.15, min_daily_profit=100.0)
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


# Material cost per unit: Tech I's flat "perfect BPO research" baseline
# (ME10, a 0.90 material multiplier - no owned BPO is synced here) reduces 10
# minerals to ceil(9) = 9 units at 5.0 each = 45.0; no job-cost-index is
# configured and no adjusted price is seeded for MINERAL, so EIV*rate = 0 -
# build_cost == 45.0 for every one of the four widgets above; only their home
# quotes differ.
HOME = {
    MINERAL: CurrentPrice(type_id=MINERAL, updated="", buy=4.5, sell=5.0),
    GOOD_ITEM: CurrentPrice(type_id=GOOD_ITEM, updated="", buy=90.0, sell=100.0),
    LOW_MARGIN_ITEM: CurrentPrice(type_id=LOW_MARGIN_ITEM, updated="", buy=50.0, sell=51.0),
    ILLIQUID_ITEM: CurrentPrice(type_id=ILLIQUID_ITEM, updated="", buy=90.0, sell=100.0),
    BAD_QUOTE_ITEM: CurrentPrice(type_id=BAD_QUOTE_ITEM, updated="", buy=100.0, sell=100.0),
}


class _FakeGoonmetricsClient:
    """Only GOOD_ITEM has real market history - ILLIQUID_ITEM (and everything
    else) gets nothing, exercising the "no history -> 0.0 movement" path."""
    def price_history_chunked(self, region_id, type_ids, **kwargs):
        return [HistoryPoint(region_id=region_id, type_id=GOOD_ITEM, date="2026-01-01",
                             min_price=95.0, max_price=105.0, avg_price=100.0,
                             movement=50.0, num_orders=3)] if GOOD_ITEM in type_ids else []


@pytest.fixture
def discovery_sde(db, monkeypatch):
    _seed()
    monkeypatch.setattr(engine.pricing, "home_prices", lambda type_ids, cfg=None, client=None: HOME)
    monkeypatch.setattr(engine.pricing, "jita_prices", lambda type_ids, client=None, trading_cfg=None: {})

    class _FakeESIClient:
        def __init__(self, *args, **kwargs):
            pass

        def get_adjusted_prices(self):
            return {}

    import eve_trader_local.esi_client as esi_client_module
    monkeypatch.setattr(esi_client_module, "ESIClient", _FakeESIClient)
    return db


def test_only_margin_and_movement_qualifying_items_are_returned(discovery_sde):
    results = engine.discover_build_candidates(_cfg(), client=_FakeGoonmetricsClient())

    type_ids = {r["type_id"] for r in results}
    assert type_ids == {GOOD_ITEM}


def test_below_min_margin_item_is_excluded(discovery_sde):
    # Thin-Margin Widget: build_cost 45.0, home sell 51.0 -> margin ~13% < 15%.
    results = engine.discover_build_candidates(_cfg(), client=_FakeGoonmetricsClient())

    assert LOW_MARGIN_ITEM not in {r["type_id"] for r in results}


def test_below_min_daily_profit_item_is_excluded_despite_a_healthy_margin(discovery_sde):
    # Illiquid Widget clears min_margin (build_cost 45.0 vs sell 100.0 = ~122%
    # margin) but Goonmetrics has no history for it, so daily_movement is 0.0
    # and potential_daily_profit is 0.0 < min_daily_profit.
    results = engine.discover_build_candidates(_cfg(min_margin=0.0), client=_FakeGoonmetricsClient())

    assert ILLIQUID_ITEM not in {r["type_id"] for r in results}


def test_a_buy_equals_sell_home_quote_is_treated_as_synthetic_and_skipped(discovery_sde):
    results = engine.discover_build_candidates(_cfg(min_margin=0.0, min_daily_profit=0.0),
                                                client=_FakeGoonmetricsClient())

    assert BAD_QUOTE_ITEM not in {r["type_id"] for r in results}


def test_potential_daily_profit_is_movement_times_margin_times_build_cost(discovery_sde):
    """Same theoretical-ceiling formula as Trading's Profit/Day - market-wide
    daily movement x margin x build_cost, never scoped to what one builder
    could personally sell."""
    results = engine.discover_build_candidates(_cfg(), client=_FakeGoonmetricsClient())

    good = next(r for r in results if r["type_id"] == GOOD_ITEM)
    assert good["build_cost"] == pytest.approx(45.0)
    assert good["daily_movement"] == pytest.approx(50.0)
    assert good["margin"] == pytest.approx((100.0 - 45.0) / 45.0)
    assert good["potential_daily_profit"] == pytest.approx(50.0 * good["margin"] * 45.0)


def test_top_n_slices_the_already_ranked_results(discovery_sde):
    results = engine.discover_build_candidates(_cfg(min_margin=0.0, min_daily_profit=0.0),
                                                client=_FakeGoonmetricsClient(), top_n=1)

    assert len(results) == 1
    assert results[0]["type_id"] == GOOD_ITEM  # highest potential_daily_profit


def test_do_discover_build_candidates_raises_without_an_sde_cache(db):
    from eve_trader_local.production.actions import do_discover_build_candidates
    from eve_trader_local.errors import ActionError

    with pytest.raises(ActionError):
        do_discover_build_candidates()
