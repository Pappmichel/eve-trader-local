"""Own-order checks against synthetic ESI payloads - no network.

The ESI client is a stub returning hand-written order/asset dicts in exactly
the shape ESI does, so every filter (location, buy-vs-sell, location_flag,
own-order exclusion) can be exercised on its own. Only
fetch_buyer_already_covered touches storage (the SDE station list), so only
its tests need the `db` fixture.
"""
from __future__ import annotations

import pytest

from eve_trader_local import own_orders, storage
from eve_trader_local.config import TradingConfig

JITA_REGION = 10000002
JITA_STATION = 60003760
AMARR_STATION = 60008494
STRUCTURE = 1234567890123
OTHER_STRUCTURE = 9999999999999
TRIT = 34
PYERITE = 35
MEXALLON = 36


@pytest.fixture
def cfg() -> TradingConfig:
    return TradingConfig(jita_region_id=JITA_REGION, structure_id=STRUCTURE)


class StubClient:
    """Stands in for ESIClient: canned per-character orders/assets and one
    structure order book."""

    def __init__(self, orders=None, assets=None, book=None):
        self.orders = orders or {}
        self.assets = assets or {}
        self.book = book or []
        self.book_fetches = 0

    def character_orders(self, character_id, auth_role):
        return list(self.orders.get(character_id, []))

    def character_assets(self, character_id, auth_role):
        return list(self.assets.get(character_id, []))

    def structure_orders_raw(self, structure_id, auth_role):
        assert structure_id == STRUCTURE
        self.book_fetches += 1
        return list(self.book)


def sell_order(order_id, type_id, price, volume_remain=10, location_id=STRUCTURE):
    return {"order_id": order_id, "type_id": type_id, "price": price,
            "volume_remain": volume_remain, "location_id": location_id,
            "is_buy_order": False}


def asset(type_id, quantity, location_id=STRUCTURE, location_flag="Hangar"):
    return {"type_id": type_id, "quantity": quantity, "location_id": location_id,
            "location_flag": location_flag}


# ------------------------------------------------------ own sell orders
def test_own_sell_orders_sums_only_sells_at_the_structure(cfg):
    client = StubClient(orders={1: [
        sell_order(10, TRIT, 5.0, volume_remain=100),
        sell_order(11, TRIT, 6.0, volume_remain=50),      # second order, same item
        sell_order(12, PYERITE, 9.0, volume_remain=7, location_id=OTHER_STRUCTURE),
        {"order_id": 13, "type_id": MEXALLON, "price": 1.0, "volume_remain": 40,
         "location_id": STRUCTURE, "is_buy_order": True},
    ]})
    assert own_orders.fetch_own_sell_orders(1, "seller", client, cfg) == {TRIT: 150}


# ------------------------------------------------------------- undercut
def test_undercut_flags_only_beaten_orders_and_sorts_by_difference(cfg):
    client = StubClient(
        orders={1: [sell_order(10, TRIT, 100.0), sell_order(11, PYERITE, 50.0),
                    sell_order(12, MEXALLON, 20.0)]},
        book=[sell_order(10, TRIT, 100.0), sell_order(11, PYERITE, 50.0),
              sell_order(12, MEXALLON, 20.0),
              sell_order(90, TRIT, 90.0),        # undercuts by 10
              sell_order(91, PYERITE, 50.0),     # exactly equal - not undercut
              sell_order(92, MEXALLON, 5.0),     # undercuts by 15
              sell_order(93, 99999, 1.0)],       # item I don't list at all
    )
    rows = own_orders.check_undercut(1, "seller", client, cfg)
    assert [r["type_id"] for r in rows] == [MEXALLON, TRIT]
    assert rows[1] == {"type_id": TRIT, "my_price": 100.0, "competitor_price": 90.0,
                       "difference": 10.0}


def test_undercut_ignores_orders_outside_the_structure(cfg):
    """A cheaper order elsewhere in the book's payload isn't competition for
    my listing here - my own side is location-filtered before comparison."""
    client = StubClient(
        orders={1: [sell_order(10, TRIT, 100.0, location_id=OTHER_STRUCTURE)]},
        book=[sell_order(90, TRIT, 1.0)],
    )
    assert own_orders.check_undercut(1, "seller", client, cfg) == []


def test_undercut_ignores_buy_orders_in_the_book(cfg):
    book_buy = dict(sell_order(90, TRIT, 1.0), is_buy_order=True)
    client = StubClient(orders={1: [sell_order(10, TRIT, 100.0)]},
                        book=[sell_order(10, TRIT, 100.0), book_buy])
    assert own_orders.check_undercut(1, "seller", client, cfg) == []


def test_undercut_pooled_excludes_every_own_seller_not_just_the_checked_one(cfg):
    """Issue #46: a cheaper order belonging to another of *my* seller
    characters must never count as being undercut - order_id is the only
    thing that identifies it, since the structure book has no owner field."""
    mine_a = sell_order(10, TRIT, 100.0)
    mine_b = sell_order(11, TRIT, 80.0)
    client = StubClient(orders={1: [mine_a], 2: [mine_b]}, book=[mine_a, mine_b])
    assert own_orders.check_undercut_pooled([(1, "seller"), (2, "seller2")], client, cfg) == []
    # Checked alone, character 1 *is* beaten by character 2's own order -
    # which is exactly the false positive pooling exists to prevent.
    assert own_orders.check_undercut(1, "seller", client, cfg) != []


def test_undercut_uses_my_cheapest_order_as_the_reference_price(cfg):
    client = StubClient(orders={1: [sell_order(10, TRIT, 100.0), sell_order(11, TRIT, 70.0)]},
                        book=[sell_order(10, TRIT, 100.0), sell_order(11, TRIT, 70.0),
                              sell_order(90, TRIT, 80.0)])
    assert own_orders.check_undercut(1, "seller", client, cfg) == []


def test_undercut_skips_the_book_fetch_when_nothing_is_listed(cfg):
    client = StubClient(orders={1: []}, book=[sell_order(90, TRIT, 1.0)])
    assert own_orders.check_undercut(1, "seller", client, cfg) == []
    assert client.book_fetches == 0


# --------------------------------------------------------- unlisted stock
def test_unlisted_stock_flags_only_fully_uncovered_shortlist_items(cfg):
    client = StubClient(
        orders={1: [sell_order(10, PYERITE, 5.0, volume_remain=1)]},
        assets={1: [asset(TRIT, 500),                      # no order at all -> flagged
                    asset(PYERITE, 500),                   # partially listed -> not flagged
                    asset(MEXALLON, 300),                  # not on the shortlist -> ignored
                    asset(99999, 5)]},
    )
    rows = own_orders.fetch_seller_stock_without_order(1, "seller", client, {TRIT, PYERITE}, cfg)
    assert rows == [{"type_id": TRIT, "asset_quantity": 500,
                     "sell_order_remaining": 0.0, "unlisted_quantity": 500}]


def test_unlisted_stock_ignores_assets_elsewhere_and_non_stock_flags(cfg):
    client = StubClient(orders={1: []}, assets={1: [
        asset(TRIT, 100, location_id=OTHER_STRUCTURE),
        asset(PYERITE, 100, location_flag="AssetSafety"),
        asset(MEXALLON, 100, location_flag="Deliveries"),
    ]})
    assert own_orders.fetch_seller_stock_without_order(
        1, "seller", client, {TRIT, PYERITE, MEXALLON}, cfg) == []


def test_unlisted_stock_pools_quantity_and_coverage_across_sellers(cfg):
    """The structure hangar is shared: quantities add up, and one seller's
    order covers stock held by another (issue #46)."""
    client = StubClient(
        orders={1: [], 2: [sell_order(10, PYERITE, 5.0, volume_remain=1)]},
        assets={1: [asset(TRIT, 100), asset(PYERITE, 20)],
                2: [asset(TRIT, 400)]},
    )
    rows = own_orders.fetch_seller_stock_without_order_pooled(
        [(1, "seller"), (2, "seller2")], client, {TRIT, PYERITE}, cfg)
    assert rows == [{"type_id": TRIT, "asset_quantity": 500,
                     "sell_order_remaining": 0.0, "unlisted_quantity": 500}]


# ------------------------------------------------------- buyer coverage
@pytest.fixture
def sde(db):
    """One Jita station and one outside it, so the asset-location check has
    something real to accept and reject."""
    with storage.connect() as conn:
        conn.executemany("INSERT INTO sde_solar_systems VALUES (?,?,?,?)",
                         [(own_orders.JITA_SOLAR_SYSTEM_ID, "Jita", 0.9, JITA_REGION),
                          (30002187, "Amarr", 1.0, 10000043)])
        conn.executemany("INSERT INTO sde_stations VALUES (?,?,?)",
                         [(JITA_STATION, own_orders.JITA_SOLAR_SYSTEM_ID, "Jita IV - Moon 4"),
                          (AMARR_STATION, 30002187, "Amarr VIII")])
    return db


def buy_order(type_id, *, region_id=None, location_id=None):
    return {"order_id": 1, "type_id": type_id, "price": 1.0, "volume_remain": 1,
            "is_buy_order": True, "region_id": region_id, "location_id": location_id}


def test_buyer_already_covered_counts_orders_and_inventory(sde, cfg):
    client = StubClient(
        orders={7: [buy_order(TRIT, region_id=JITA_REGION),
                    buy_order(PYERITE, location_id=STRUCTURE),
                    buy_order(101, region_id=10000043),           # buy order elsewhere
                    sell_order(20, 102, 5.0)]},                   # a sell order isn't coverage
        assets={7: [asset(MEXALLON, 10, location_id=JITA_STATION),
                    asset(103, 10, location_id=STRUCTURE),
                    asset(104, 10, location_id=AMARR_STATION)]},
    )
    assert own_orders.fetch_buyer_already_covered(7, "buyer", client, cfg) == {
        TRIT, PYERITE, MEXALLON, 103}
