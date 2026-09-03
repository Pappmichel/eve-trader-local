"""Tests for station_trading/candidate_discovery.py - no network at all, both
GoonmetricsClient and ESIClient are stand-in stubs passed in directly (both
discover_candidates/confirm_live accept a client parameter for exactly this).
"""
from __future__ import annotations

from eve_trader_local.config import TradingConfig
from eve_trader_local.esi_client import OrderStats
from eve_trader_local.goonmetrics_client import CurrentPrice, HistoryPoint
from eve_trader_local.station_trading.candidate_discovery import confirm_live, discover_candidates
from eve_trader_local.station_trading.config import StationTradingConfig

TRITANIUM = 34
PYERITE = 35
MEGACYTE = 40


class StubGoonmetricsClient:
    def __init__(self, prices, history=None):
        self._prices = prices
        self._history = history or {}

    def current_prices(self, market):
        assert market == "jita"
        return self._prices

    def price_history_chunked(self, region_id, type_ids):
        for type_id in type_ids:
            for movement in self._history.get(type_id, []):
                yield HistoryPoint(region_id=region_id, type_id=type_id, date="2026-09-01",
                                   min_price=0, max_price=0, avg_price=0,
                                   movement=movement, num_orders=1)


def _cfg(**overrides) -> StationTradingConfig:
    cfg = StationTradingConfig(min_spread_threshold=0.08, min_daily_volume=1000.0)
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


def _price(type_id, buy, sell) -> CurrentPrice:
    return CurrentPrice(type_id=type_id, updated="2026-09-01", buy=buy, sell=sell)


# ------------------------------------------------------------- discover_candidates
def test_item_below_spread_threshold_is_excluded():
    client = StubGoonmetricsClient([_price(TRITANIUM, 4.9, 5.0)])  # 2% spread
    assert discover_candidates(_cfg(), client) == []


def test_item_clearing_spread_but_below_volume_is_excluded():
    client = StubGoonmetricsClient(
        [_price(TRITANIUM, 4.0, 5.0)],  # 20% spread
        history={TRITANIUM: [10.0, 20.0]},  # avg 15, well under 1000
    )
    assert discover_candidates(_cfg(), client) == []


def test_item_clearing_both_gates_is_returned():
    client = StubGoonmetricsClient(
        [_price(TRITANIUM, 4.0, 5.0)],
        history={TRITANIUM: [2000.0, 4000.0]},  # avg 3000
    )
    results = discover_candidates(_cfg(), client)
    assert len(results) == 1
    row = results[0]
    assert row["type_id"] == TRITANIUM
    assert row["buy"] == 4.0 and row["sell"] == 5.0
    assert row["spread_pct"] == 0.2
    assert row["avg_daily_volume"] == 3000.0


def test_no_history_at_all_means_zero_volume_and_is_excluded():
    """An item Goonmetrics has no history for is 0.0 movement, never
    estimated from something else - same "don't fabricate a number"
    convention Trading's own average_market_daily_volume follows."""
    client = StubGoonmetricsClient([_price(TRITANIUM, 4.0, 5.0)])
    results = discover_candidates(_cfg(min_daily_volume=0.0), client)
    assert results[0]["avg_daily_volume"] == 0.0


def test_synthetic_or_degenerate_quotes_are_skipped():
    client = StubGoonmetricsClient([
        _price(TRITANIUM, 0.0, 5.0),   # buy <= 0
        _price(PYERITE, 5.0, 0.0),     # sell <= 0
        _price(MEGACYTE, 5.0, 4.0),    # buy >= sell, inverted/bad quote
    ])
    assert discover_candidates(_cfg(), client) == []


def test_results_are_sorted_by_spread_times_volume_richest_first():
    client = StubGoonmetricsClient(
        [_price(TRITANIUM, 4.0, 5.0), _price(PYERITE, 8.0, 10.0)],  # both 20% spread
        history={TRITANIUM: [1000.0], PYERITE: [10000.0]},
    )
    results = discover_candidates(_cfg(), client)
    assert [r["type_id"] for r in results] == [PYERITE, TRITANIUM]


def test_top_n_caps_when_explicitly_passed():
    client = StubGoonmetricsClient(
        [_price(TRITANIUM, 4.0, 5.0), _price(PYERITE, 4.0, 5.0)],
        history={TRITANIUM: [5000.0], PYERITE: [5000.0]},
    )
    assert len(discover_candidates(_cfg(), client, top_n=1)) == 1


def test_no_cap_by_default_even_with_a_large_candidate_set():
    prices = [_price(tid, 4.0, 5.0) for tid in range(1000, 1050)]
    history = {tid: [5000.0] for tid in range(1000, 1050)}
    client = StubGoonmetricsClient(prices, history)
    assert len(discover_candidates(_cfg(), client)) == 50


def test_enforce_shortlist_cap_applies_max_active_shortlist_items():
    prices = [_price(tid, 4.0, 5.0) for tid in range(1000, 1050)]
    history = {tid: [5000.0] for tid in range(1000, 1050)}
    client = StubGoonmetricsClient(prices, history)
    cfg = _cfg(enforce_shortlist_cap=True, max_active_shortlist_items=5)
    assert len(discover_candidates(cfg, client)) == 5


def test_empty_market_returns_empty():
    assert discover_candidates(_cfg(), StubGoonmetricsClient([])) == []


# --------------------------------------------------------------- confirm_live
class StubESIClient:
    def __init__(self, stats):
        self.stats = stats
        self.calls = []

    def region_order_stats_bulk(self, region_id, type_ids):
        self.calls.append((region_id, type_ids))
        return {tid: self.stats.get(tid, OrderStats(None, 0.0, None, 0.0)) for tid in type_ids}


def test_confirm_live_returns_empty_for_no_type_ids():
    client = StubESIClient({})
    assert confirm_live([], client) == {}
    assert client.calls == []


def test_confirm_live_uses_jita_region_from_trading_config(monkeypatch):
    import eve_trader_local.station_trading.candidate_discovery as module
    monkeypatch.setattr(module, "TRADING_CONFIG", TradingConfig(jita_region_id=99999))
    client = StubESIClient({TRITANIUM: OrderStats(5.0, 100.0, 4.0, 50.0)})

    result = confirm_live([TRITANIUM], client)

    assert client.calls == [(99999, [TRITANIUM])]
    assert result[TRITANIUM].sell_percentile == 5.0
