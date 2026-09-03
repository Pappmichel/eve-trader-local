"""Tests for station_trading/undercut.py - a real live ESI order-book fetch
per side, no Goonmetrics snapshot involved at all (see the module's own
docstring for why). Both client.character_orders and client.region_orders_raw
are stubbed directly, no network.
"""
from __future__ import annotations

from eve_trader_local.config import TradingConfig
from eve_trader_local.station_trading.config import StationTradingConfig
from eve_trader_local.station_trading.undercut import check_buy_undercut_pooled, check_undercut_pooled

STATION = 60003760
TRITANIUM = 34
PYERITE = 35


class StubClient:
    def __init__(self, orders_by_character, region_orders):
        self.orders_by_character = orders_by_character
        self.region_orders = region_orders

    def character_orders(self, character_id, auth_role):
        return self.orders_by_character.get(character_id, [])

    def region_orders_raw(self, region_id, type_id):
        return self.region_orders.get(type_id, [])


def _order(order_id, type_id, price, is_buy_order, location_id=STATION):
    return {"order_id": order_id, "type_id": type_id, "price": price,
            "is_buy_order": is_buy_order, "location_id": location_id}


def _cfg() -> StationTradingConfig:
    return StationTradingConfig(station_id=STATION)


# --------------------------------------------------------------- sell side
def test_no_traders_means_no_undercuts():
    client = StubClient({}, {})
    assert check_undercut_pooled([], client, _cfg()) == []


def test_cheaper_competitor_sell_order_is_flagged():
    client = StubClient(
        orders_by_character={111: [_order(1, TRITANIUM, 10.0, False)]},
        region_orders={TRITANIUM: [_order(2, TRITANIUM, 9.0, False)]},
    )
    result = check_undercut_pooled([(111, "trader:111")], client, _cfg())
    assert len(result) == 1
    assert result[0] == {"type_id": TRITANIUM, "my_price": 10.0, "competitor_price": 9.0, "difference": 1.0}


def test_own_order_never_counts_as_its_own_competitor():
    """Confirmed against the real ESI schema: the structure order book
    carries no owning-character field, so an order can only be excluded by
    matching order_id - never by price/type_id, which would false-positive
    on a coincidentally identical own price."""
    client = StubClient(
        orders_by_character={111: [_order(1, TRITANIUM, 10.0, False)]},
        region_orders={TRITANIUM: [_order(1, TRITANIUM, 10.0, False)]},  # same order_id
    )
    assert check_undercut_pooled([(111, "trader:111")], client, _cfg()) == []


def test_orders_at_a_different_station_are_ignored():
    client = StubClient(
        orders_by_character={111: [_order(1, TRITANIUM, 10.0, False, location_id=999)]},
        region_orders={},
    )
    assert check_undercut_pooled([(111, "trader:111")], client, _cfg()) == []


def test_pooled_across_multiple_own_characters_not_flagged_as_undercut():
    """A cheaper order from another of the trader's own registered
    characters must never read as an undercut - same pooling reasoning as
    own_orders.py's own check_undercut_pooled (parent issue #46)."""
    client = StubClient(
        orders_by_character={
            111: [_order(1, TRITANIUM, 10.0, False)],
            222: [_order(2, TRITANIUM, 9.0, False)],
        },
        region_orders={TRITANIUM: [_order(2, TRITANIUM, 9.0, False)]},
    )
    result = check_undercut_pooled([(111, "trader:111"), (222, "trader:222")], client, _cfg())
    assert result == []


def test_equal_or_higher_competitor_price_is_not_an_undercut():
    client = StubClient(
        orders_by_character={111: [_order(1, TRITANIUM, 10.0, False)]},
        region_orders={TRITANIUM: [_order(2, TRITANIUM, 10.0, False)]},
    )
    assert check_undercut_pooled([(111, "trader:111")], client, _cfg()) == []


def test_only_my_best_sell_price_per_type_is_checked():
    client = StubClient(
        orders_by_character={111: [_order(1, TRITANIUM, 10.0, False), _order(2, TRITANIUM, 12.0, False)]},
        region_orders={TRITANIUM: [_order(3, TRITANIUM, 11.0, False)]},
    )
    # 11.0 beats my worse order (12.0) but not my best (10.0) - not flagged.
    assert check_undercut_pooled([(111, "trader:111")], client, _cfg()) == []


def test_results_sorted_by_difference_descending():
    client = StubClient(
        orders_by_character={111: [_order(1, TRITANIUM, 10.0, False), _order(2, PYERITE, 10.0, False)]},
        region_orders={
            TRITANIUM: [_order(3, TRITANIUM, 9.5, False)],
            PYERITE: [_order(4, PYERITE, 5.0, False)],
        },
    )
    result = check_undercut_pooled([(111, "trader:111")], client, _cfg())
    assert [r["type_id"] for r in result] == [PYERITE, TRITANIUM]


def test_uses_jita_region_from_trading_config(monkeypatch):
    import eve_trader_local.station_trading.undercut as module
    monkeypatch.setattr(module, "TRADING_CONFIG", TradingConfig(jita_region_id=99999))

    class RecordingClient(StubClient):
        def region_orders_raw(self, region_id, type_id):
            self.seen_region = region_id
            return super().region_orders_raw(region_id, type_id)

    client = RecordingClient(
        orders_by_character={111: [_order(1, TRITANIUM, 10.0, False)]},
        region_orders={TRITANIUM: [_order(2, TRITANIUM, 9.0, False)]},
    )
    check_undercut_pooled([(111, "trader:111")], client, _cfg())
    assert client.seen_region == 99999


# ---------------------------------------------------------------- buy side
def test_buy_side_flags_a_higher_competing_bid():
    client = StubClient(
        orders_by_character={111: [_order(1, TRITANIUM, 4.0, True)]},
        region_orders={TRITANIUM: [_order(2, TRITANIUM, 4.5, True)]},
    )
    result = check_buy_undercut_pooled([(111, "trader:111")], client, _cfg())
    assert result == [{"type_id": TRITANIUM, "my_price": 4.0, "competitor_price": 4.5, "difference": 0.5}]


def test_buy_side_a_lower_competing_bid_is_not_an_outbid():
    client = StubClient(
        orders_by_character={111: [_order(1, TRITANIUM, 4.0, True)]},
        region_orders={TRITANIUM: [_order(2, TRITANIUM, 3.5, True)]},
    )
    assert check_buy_undercut_pooled([(111, "trader:111")], client, _cfg()) == []


def test_buy_side_only_considers_buy_orders_from_the_region_book():
    """A cheap sell order at the same station/type must never be mistaken
    for a competing buy order."""
    client = StubClient(
        orders_by_character={111: [_order(1, TRITANIUM, 4.0, True)]},
        region_orders={TRITANIUM: [_order(2, TRITANIUM, 100.0, False)]},
    )
    assert check_buy_undercut_pooled([(111, "trader:111")], client, _cfg()) == []
