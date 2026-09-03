"""Tests for engine.discover_ship_margins/_scan_ship_margins - the Margin
page's list view: a pure information browser over every ship, unlike
discover_build_candidates (which is gated by min_margin/min_daily_profit).
Same synthetic-SDE/no-network pattern as test_production_discovery.py.
"""
from __future__ import annotations

import pytest

from eve_trader_local import storage
from eve_trader_local.goonmetrics_client import CurrentPrice
from eve_trader_local.production import engine
from eve_trader_local.production.config import ProductionConfig
from eve_trader_local.production.constants import SHIP_CATEGORY_ID, SPECIAL_EDITION_SHIPS_MARKET_GROUP_ID

MINERAL = 34
GOOD_SHIP = 90201       # a real, profitable ship
NO_QUOTE_SHIP = 90202   # buildable, but no home/Jita quote anywhere
SPECIAL_EDITION_SHIP = 90203  # under the excluded market-group subtree
NOT_A_SHIP = 90204      # same recipe shape, but category != SHIP_CATEGORY_ID
GOOD_BP, NO_QUOTE_BP, SPECIAL_BP, NOT_SHIP_BP = 91201, 91202, 91203, 91204

SHIP_GROUP = 25       # Frigate
OTHER_GROUP = 7       # Module
SHIP_MARKET_GROUP = 400
SPECIAL_MARKET_GROUP = 401  # child of SPECIAL_EDITION_SHIPS_MARKET_GROUP_ID


def _seed():
    storage.replace_sde_data(
        types=[
            (MINERAL, 18, "Tritanium", 0.01, 1, 100, 0, 1, 1),
            (GOOD_SHIP, SHIP_GROUP, "Good Ship", 1.0, 1, SHIP_MARKET_GROUP, 0, 1, 1),
            (NO_QUOTE_SHIP, SHIP_GROUP, "Quoteless Ship", 1.0, 1, SHIP_MARKET_GROUP, 0, 1, 1),
            (SPECIAL_EDITION_SHIP, SHIP_GROUP, "Special Edition Ship", 1.0, 1, SPECIAL_MARKET_GROUP, 0, 1, 1),
            (NOT_A_SHIP, OTHER_GROUP, "Not A Ship", 1.0, 1, SHIP_MARKET_GROUP, 0, 1, 1),
            (GOOD_BP, 9, "Good Ship Blueprint", 0.01, 1, None, 0, None, 1),
            (NO_QUOTE_BP, 9, "Quoteless Ship Blueprint", 0.01, 1, None, 0, None, 1),
            (SPECIAL_BP, 9, "Special Edition Ship Blueprint", 0.01, 1, None, 0, None, 1),
            (NOT_SHIP_BP, 9, "Not A Ship Blueprint", 0.01, 1, None, 0, None, 1),
        ],
        groups=[(18, 4, "Mineral"), (SHIP_GROUP, SHIP_CATEGORY_ID, "Frigate"),
                (OTHER_GROUP, 7, "Module"), (9, 9, "Blueprint")],
        market_groups=[
            (100, None, "Manufacture & Research"),
            (SHIP_MARKET_GROUP, None, "Ships"),
            (SPECIAL_EDITION_SHIPS_MARKET_GROUP_ID, None, "Special Edition Ships"),
            (SPECIAL_MARKET_GROUP, SPECIAL_EDITION_SHIPS_MARKET_GROUP_ID, "Special Edition Frigates"),
        ],
        blueprint_time=[],
        blueprint_materials=[
            (GOOD_BP, 1, MINERAL, 10),
            (NO_QUOTE_BP, 1, MINERAL, 10),
            (SPECIAL_BP, 1, MINERAL, 10),
            (NOT_SHIP_BP, 1, MINERAL, 10),
        ],
        blueprint_products=[
            (GOOD_BP, 1, GOOD_SHIP, 1),
            (NO_QUOTE_BP, 1, NO_QUOTE_SHIP, 1),
            (SPECIAL_BP, 1, SPECIAL_EDITION_SHIP, 1),
            (NOT_SHIP_BP, 1, NOT_A_SHIP, 1),
        ],
        categories=[(4, "Material"), (SHIP_CATEGORY_ID, "Ship"), (7, "Module")],
    )


def _cfg(**overrides) -> ProductionConfig:
    cfg = ProductionConfig(jita_buy_broker_fee=0.0, haul_cost_per_m3=0.0,
                           facility_tax_rate=0.0, market_fees=0.0)
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


# Build cost for every ship above: 10 minerals x 0.9 ME -> ceil(9) = 9 units
# at 5.0 each = 45.0 (no job-cost-index/adjusted price configured, so the
# facility fee term is 0).
HOME = {
    MINERAL: CurrentPrice(type_id=MINERAL, updated="", buy=4.5, sell=5.0),
    GOOD_SHIP: CurrentPrice(type_id=GOOD_SHIP, updated="", buy=90.0, sell=100.0),
    SPECIAL_EDITION_SHIP: CurrentPrice(type_id=SPECIAL_EDITION_SHIP, updated="", buy=900.0, sell=1000.0),
    NOT_A_SHIP: CurrentPrice(type_id=NOT_A_SHIP, updated="", buy=90.0, sell=100.0),
}


@pytest.fixture
def ship_sde(db, monkeypatch):
    _seed()
    monkeypatch.setattr(engine.pricing, "home_prices", lambda type_ids, cfg=None, client=None: HOME)
    monkeypatch.setattr(engine.pricing, "jita_prices", lambda type_ids, client=None, trading_cfg=None: {})

    class _FakeESIClient:
        def __init__(self, *a, **kw):
            pass

        def get_adjusted_prices(self):
            return {}

    import eve_trader_local.esi_client as esi_client_module
    monkeypatch.setattr(esi_client_module, "ESIClient", _FakeESIClient)
    return db


def test_discover_ship_margins_is_not_gated_by_margin_or_profit(ship_sde):
    """Unlike discover_build_candidates, every buildable ship appears
    regardless of margin - including one with no quote anywhere at all."""
    rows = engine.discover_ship_margins(_cfg(min_margin=100.0, min_daily_profit=1e12))
    type_ids = {r["type_id"] for r in rows}
    assert GOOD_SHIP in type_ids
    assert NO_QUOTE_SHIP in type_ids


def test_no_quote_ship_shows_none_prices_not_excluded(ship_sde):
    rows = {r["type_id"]: r for r in engine.discover_ship_margins(_cfg())}
    row = rows[NO_QUOTE_SHIP]
    assert row["home_price"] is None
    assert row["jita_price"] is None
    assert row["margin_home"] is None
    assert row["margin_jita"] is None
    assert row["build_cost"] is not None  # still costed, just unpriceable on the sell side


def test_special_edition_ships_are_excluded(ship_sde):
    rows = {r["type_id"]: r for r in engine.discover_ship_margins(_cfg())}
    assert SPECIAL_EDITION_SHIP not in rows


def test_non_ship_category_is_excluded(ship_sde):
    rows = {r["type_id"]: r for r in engine.discover_ship_margins(_cfg())}
    assert NOT_A_SHIP not in rows


def test_good_ship_margin_and_build_cost(ship_sde):
    rows = {r["type_id"]: r for r in engine.discover_ship_margins(_cfg())}
    row = rows[GOOD_SHIP]
    assert row["build_cost"] == pytest.approx(45.0)
    assert row["home_price"] == pytest.approx(100.0)
    assert row["margin_home"] == pytest.approx((100.0 - 45.0) / 45.0)


def test_do_get_ship_margins_raises_on_empty_sde(db):
    from eve_trader_local.production.actions import do_get_ship_margins
    from eve_trader_local.errors import ActionError

    with pytest.raises(ActionError):
        do_get_ship_margins()
