from __future__ import annotations

import pytest
import requests

from eve_trader_local.config import TradingConfig
from eve_trader_local.esi_client import ESIError, OrderStats
from eve_trader_local.goonmetrics_client import CurrentPrice
from eve_trader_local.production import pricing
from eve_trader_local.production.config import ProductionConfig


def price(type_id: int, sell: float, buy: float = 0.0) -> CurrentPrice:
    return CurrentPrice(type_id=type_id, updated="", buy=buy, sell=sell)


@pytest.fixture
def cfg():
    return ProductionConfig(jita_buy_broker_fee=0.10, haul_cost_per_m3=100.0)


class FakeESI:
    """Stand-in for ESIClient - records what it was asked for, so a test can
    assert the full order book was fetched once rather than per type_id."""

    def __init__(self, region=None, structure=None, indices=None, raises=None):
        self._region = region or {}
        self._structure = structure or {}
        self._indices = indices or {}
        self._raises = raises
        self.structure_calls: list[tuple[int, str]] = []
        self.region_calls = 0

    def region_order_stats_bulk(self, region_id, type_ids):
        self.region_calls += 1
        if isinstance(self._raises, Exception):
            raise self._raises
        return {tid: self._region[tid] for tid in type_ids if tid in self._region}

    def structure_order_stats_bulk(self, structure_id, type_ids, auth_role):
        self.structure_calls.append((structure_id, auth_role))
        if isinstance(self._raises, Exception):
            raise self._raises
        return {tid: self._structure[tid] for tid in type_ids if tid in self._structure}

    def get_system_cost_indices(self, system_id, activities=()):
        if isinstance(self._raises, Exception):
            raise self._raises
        return dict(self._indices)


class FakeTokens:
    def __init__(self, roles: list[str]):
        self._roles = roles

    def list_records(self, prefix=None):
        return [type("R", (), {"role": r})() for r in self._roles]


# ----------------------------------------------------------- buy price / source
def test_home_wins_when_cheaper_after_haul(cfg):
    home = {34: price(34, 100.0)}
    jita = {34: price(34, 95.0)}
    # home: 100 * 1.10 = 110. jita: 95 * 1.10 + 100 * 1.0 m3 = 204.5.
    assert pricing.buy_price(34, home, jita, 1.0, cfg) == pytest.approx(110.0)
    assert pricing.buy_source(34, home, jita, 1.0, cfg) == "home"


def test_jita_wins_when_home_is_expensive(cfg):
    home = {34: price(34, 500.0)}
    jita = {34: price(34, 100.0)}
    # home: 550. jita: 100 * 1.10 + 100 * 0.01 = 111.
    assert pricing.buy_price(34, home, jita, 0.01, cfg) == pytest.approx(111.0)
    assert pricing.buy_source(34, home, jita, 0.01, cfg) == "jita"


def test_haul_cost_scales_with_volume(cfg):
    jita = {34: price(34, 100.0)}
    cheap = pricing.buy_price(34, {}, jita, 1.0, cfg)
    bulky = pricing.buy_price(34, {}, jita, 10.0, cfg)
    assert bulky - cheap == pytest.approx(cfg.haul_cost_per_m3 * 9)


def test_missing_volume_charges_no_haul(cfg):
    jita = {34: price(34, 100.0)}
    assert pricing.buy_price(34, {}, jita, None, cfg) == pytest.approx(110.0)


def test_home_price_is_never_hauled(cfg):
    home = {34: price(34, 100.0)}
    assert pricing.buy_price(34, home, {}, 1000.0, cfg) == pytest.approx(110.0)


def test_only_listed_sources_are_considered(cfg):
    """A zero sell price means "nothing listed", not "free"."""
    home = {34: price(34, 0.0)}
    jita = {34: price(34, 100.0)}
    assert pricing.buy_source(34, home, jita, 0.0, cfg) == "jita"


def test_no_source_anywhere_is_none(cfg):
    assert pricing.buy_price(34, {}, {}, 1.0, cfg) is None
    assert pricing.buy_source(34, {}, {}, 1.0, cfg) is None
    assert pricing.buy_price(34, {34: price(34, 0.0)}, {}, 1.0, cfg) is None


def test_source_and_price_always_agree(cfg):
    home = {34: price(34, 100.0)}
    jita = {34: price(34, 90.0)}
    chosen = pricing.buy_source(34, home, jita, 0.05, cfg)
    expected = {"home": 110.0, "jita": 90 * 1.10 + 100 * 0.05}[chosen]
    assert pricing.buy_price(34, home, jita, 0.05, cfg) == pytest.approx(expected)


# ------------------------------------------------------------------- sourcing
def test_home_prices_read_the_live_structure_book_once(cfg, monkeypatch):
    cfg.home_location_id = 1234
    esi = FakeESI(structure={34: OrderStats(sell_percentile=5.0, sell_volume=1.0,
                                            buy_percentile=4.0, buy_volume=1.0)})
    monkeypatch.setattr(pricing, "TokenManager", lambda *a, **k: FakeTokens(["producer:1"]))
    result = pricing.home_prices([34, 35], cfg, client=esi)
    assert result[34].sell == 5.0
    assert 35 not in result           # not listed at the structure
    assert esi.structure_calls == [(1234, "producer:1")]


def test_home_prices_try_the_next_producer_after_an_esi_failure(cfg, monkeypatch):
    cfg.home_location_id = 1234
    cfg.home_market = None
    esi = FakeESI(raises=ESIError("no docking access"))
    monkeypatch.setattr(pricing, "TokenManager",
                        lambda *a, **k: FakeTokens(["producer:1", "producer:2"]))
    assert pricing.home_prices([34], cfg, client=esi) == {}
    assert [role for _, role in esi.structure_calls] == ["producer:1", "producer:2"]


def test_home_prices_fall_back_to_goonmetrics(cfg, monkeypatch):
    cfg.home_location_id = 1234
    cfg.home_market = "c-j"
    monkeypatch.setattr(pricing, "TokenManager", lambda *a, **k: FakeTokens([]))
    called = {}

    def fake_goon(market, type_ids, trading_cfg=None):
        called["market"] = market
        return {34: price(34, 7.0)}

    monkeypatch.setattr(pricing, "_goonmetrics_prices", fake_goon)
    assert pricing.home_prices([34], cfg, client=FakeESI())[34].sell == 7.0
    assert called["market"] == "c-j"


def test_home_prices_without_a_market_or_structure_are_empty(cfg, monkeypatch):
    """Never request a market literally named "None"."""
    monkeypatch.setattr(pricing, "_goonmetrics_prices",
                        lambda *a, **k: pytest.fail("should not be called"))
    assert pricing.home_prices([34], cfg, client=FakeESI()) == {}
    assert pricing.home_prices([], cfg, client=FakeESI()) == {}


def test_jita_prices_use_the_configured_region(cfg):
    esi = FakeESI(region={34: OrderStats(sell_percentile=6.0, sell_volume=1.0,
                                         buy_percentile=5.0, buy_volume=1.0)})
    result = pricing.jita_prices([34], client=esi, trading_cfg=TradingConfig())
    assert result[34].sell == 6.0
    assert result[34].buy == 5.0


def test_jita_prices_fall_back_to_goonmetrics_on_a_network_error(monkeypatch):
    esi = FakeESI(raises=requests.ConnectionError("down"))
    monkeypatch.setattr(pricing, "_goonmetrics_prices",
                        lambda market, type_ids, trading_cfg=None: {34: price(34, 9.0)})
    assert pricing.jita_prices([34], client=esi)[34].sell == 9.0


def test_jita_prices_skip_the_network_entirely_for_no_types():
    esi = FakeESI(raises=ESIError("should not be reached"))
    assert pricing.jita_prices([], client=esi) == {}
    assert esi.region_calls == 0


def test_unlisted_types_have_no_quote_rather_than_a_zero_one():
    """region_order_stats_bulk reports a missing item as an empty OrderStats;
    that must read as "no sell order", never as a free item."""
    esi = FakeESI(region={34: OrderStats(None, 0.0, None, 0.0)})
    quotes = pricing.jita_prices([34], client=esi)
    assert quotes[34].sell == 0.0
    assert pricing.buy_price(34, {}, quotes, 1.0, ProductionConfig()) is None


# --------------------------------------------------------------- cost indices
def test_system_cost_indices_passed_through():
    esi = FakeESI(indices={"manufacturing": 0.05, "reaction": 0.02})
    assert pricing.system_cost_indices_for(esi, 30000142) == {"manufacturing": 0.05, "reaction": 0.02}


def test_system_cost_indices_empty_without_a_system():
    esi = FakeESI(raises=ESIError("should not be reached"))
    assert pricing.system_cost_indices_for(esi, None) == {}


def test_system_cost_indices_empty_on_esi_failure():
    assert pricing.system_cost_indices_for(FakeESI(raises=ESIError("boom")), 30000142) == {}
