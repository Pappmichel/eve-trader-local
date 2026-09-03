"""Tests for station_trading/actions.py - the Station Trading orchestration
layer. Everything that would touch ESI/Goonmetrics/EVE SSO is replaced at the
module boundary (actions.ESIClient / .TokenManager / .discover_candidates /
.confirm_live), same "no network" pattern tests/test_refining_actions.py and
tests/test_production_esi_sync.py use. SDE reads (item name/category) run
against a real synthetic SDE cache via storage.replace_sde_data.
"""
from __future__ import annotations

import pytest

from eve_trader_local import storage
from eve_trader_local.config import TradingConfig
from eve_trader_local.errors import ActionError, ConfigError
from eve_trader_local.esi_client import ESIError, OrderStats
from eve_trader_local.station_trading import actions
from eve_trader_local.station_trading.config import StationTradingConfig

TRITANIUM = 34
PYERITE = 35
STATION = 60003760


def _seed_sde():
    storage.replace_sde_data(
        types=[(TRITANIUM, 18, "Tritanium", 0.01, 1, 4, None, None, 1),
              (PYERITE, 18, "Pyerite", 0.01, 1, 4, None, None, 1)],
        groups=[(18, 4, "Mineral")],
        market_groups=[],
        blueprint_time=[], blueprint_materials=[], blueprint_products=[],
        categories=[(4, "Material")],
    )


@pytest.fixture
def cfg() -> StationTradingConfig:
    return StationTradingConfig(station_id=STATION, broker_fee_rate=0.05, sales_tax_rate=0.075)


@pytest.fixture(autouse=True)
def _seed(db):
    _seed_sde()


class StubRecord:
    def __init__(self, role, character_id, character_name):
        self.role = role
        self.character_id = character_id
        self.character_name = character_name


class StubTokenManager:
    def __init__(self, records=()):
        self.records = list(records)
        self.removed = []

    def list_records(self, prefix=None):
        if prefix is None:
            return list(self.records)
        return [r for r in self.records if r.role.startswith(f"{prefix}:")]

    def remove_token(self, role):
        self.removed.append(role)


def stats(sell=None, buy=None) -> OrderStats:
    return OrderStats(sell_percentile=sell, sell_volume=0.0, buy_percentile=buy, buy_volume=0.0)


# --------------------------------------------------------------- shortlist
def test_refresh_shortlist_persists_and_returns_annotated_rows(cfg, monkeypatch):
    monkeypatch.setattr(actions, "discover_candidates",
                        lambda cfg, **kw: [{"type_id": TRITANIUM, "buy": 4.0, "sell": 5.0,
                                             "spread_pct": 0.2, "avg_daily_volume": 1000.0}])
    monkeypatch.setattr(actions, "confirm_live", lambda type_ids, **kw: {TRITANIUM: stats(sell=5.0, buy=4.0)})

    result = actions.do_refresh_shortlist(cfg)

    assert result["discovered"] == 1
    row = result["rows"][0]
    assert row["type_id"] == TRITANIUM
    assert row["name"] == "Tritanium"
    assert row["category"] == "Material"
    assert row["active"] is True
    # buy_cost = 5.0 * 1.05, sell_net = 4.0 wait: live_sell drives sell leg -
    # confirm: buy leg is live_buy, sell leg is live_sell (see _profit).
    assert row["live_buy"] == 4.0 and row["live_sell"] == 5.0
    assert row["profit_per_unit"] is not None
    assert row["profit_per_day"] == pytest.approx(row["profit_per_unit"] * 1000.0)

    persisted = storage.load_station_trading_shortlist()
    assert persisted == [(TRITANIUM, 0.2, 1000.0, persisted[0][3], True)]


def test_a_previously_deactivated_item_stays_deactivated_after_refresh(cfg, monkeypatch):
    monkeypatch.setattr(actions, "discover_candidates",
                        lambda cfg, **kw: [{"type_id": TRITANIUM, "buy": 4.0, "sell": 5.0,
                                             "spread_pct": 0.2, "avg_daily_volume": 1000.0}])
    monkeypatch.setattr(actions, "confirm_live", lambda type_ids, **kw: {})

    actions.do_refresh_shortlist(cfg)
    actions.do_deactivate_shortlist_items([TRITANIUM])
    actions.do_refresh_shortlist(cfg)

    rows = storage.load_station_trading_shortlist()
    assert rows[0][4] is False  # still inactive


def test_reactivating_an_item_makes_it_active_again(cfg, monkeypatch):
    monkeypatch.setattr(actions, "discover_candidates",
                        lambda cfg, **kw: [{"type_id": TRITANIUM, "buy": 4.0, "sell": 5.0,
                                             "spread_pct": 0.2, "avg_daily_volume": 1000.0}])
    monkeypatch.setattr(actions, "confirm_live", lambda type_ids, **kw: {})
    actions.do_refresh_shortlist(cfg)
    actions.do_deactivate_shortlist_items([TRITANIUM])

    actions.do_activate_shortlist_items([TRITANIUM])

    assert storage.load_station_trading_shortlist()[0][4] is True


def test_no_live_price_gives_none_profit_not_a_fabricated_number(cfg, monkeypatch):
    monkeypatch.setattr(actions, "discover_candidates",
                        lambda cfg, **kw: [{"type_id": TRITANIUM, "buy": 4.0, "sell": 5.0,
                                             "spread_pct": 0.2, "avg_daily_volume": 1000.0}])
    monkeypatch.setattr(actions, "confirm_live", lambda type_ids, **kw: {})  # ESI has nothing

    result = actions.do_refresh_shortlist(cfg)

    row = result["rows"][0]
    assert row["live_buy"] is None and row["live_sell"] is None
    assert row["profit_per_unit"] is None
    assert row["margin"] is None
    assert row["profit_per_day"] is None


def test_get_shortlist_on_empty_shortlist_returns_empty(cfg):
    assert actions.do_get_shortlist(cfg) == []


# ------------------------------------------------------------------ profit
def test_profit_charges_broker_fee_on_both_legs_and_tax_only_on_sell():
    cfg = StationTradingConfig(broker_fee_rate=0.05, sales_tax_rate=0.075)
    profit, margin = actions._profit(4.0, 5.0, cfg)
    buy_cost = 4.0 * 1.05
    sell_net = 5.0 * (1 - 0.05 - 0.075)
    assert profit == pytest.approx(sell_net - buy_cost)
    assert margin == pytest.approx((sell_net - buy_cost) / buy_cost)


def test_profit_is_none_when_a_live_price_is_missing():
    cfg = StationTradingConfig()
    assert actions._profit(None, 5.0, cfg) == (None, None)
    assert actions._profit(4.0, None, cfg) == (None, None)
    assert actions._profit(0.0, 5.0, cfg) == (None, None)


# ---------------------------------------------------------------- undercut
class StubUndercutClient:
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


def test_check_undercut_reports_both_sell_and_buy_undercuts(cfg, monkeypatch):
    tm = StubTokenManager([StubRecord("trader:111", 111, "A Trader")])
    monkeypatch.setattr(actions, "TokenManager", lambda cfg: tm)
    client = StubUndercutClient(
        orders_by_character={111: [_order(1, TRITANIUM, 10.0, False), _order(2, PYERITE, 4.0, True)]},
        region_orders={TRITANIUM: [_order(3, TRITANIUM, 9.0, False)],
                      PYERITE: [_order(4, PYERITE, 4.5, True)]},
    )
    monkeypatch.setattr(actions, "ESIClient", lambda tokens: client)

    result = actions.do_check_undercut(cfg)

    assert result["sell"] == [{"type_id": TRITANIUM, "name": "Tritanium",
                               "my_price": 10.0, "competitor_price": 9.0, "difference": 1.0}]
    assert result["buy"] == [{"type_id": PYERITE, "name": "Pyerite",
                              "my_price": 4.0, "competitor_price": 4.5, "difference": 0.5}]


def test_check_undercut_with_no_traders_raises(cfg, monkeypatch):
    monkeypatch.setattr(actions, "TokenManager", lambda cfg: StubTokenManager([]))
    with pytest.raises(ActionError, match="No trader characters"):
        actions.do_check_undercut(cfg)


def test_check_undercut_converts_esi_error_to_action_error(cfg, monkeypatch):
    tm = StubTokenManager([StubRecord("trader:111", 111, "A Trader")])
    monkeypatch.setattr(actions, "TokenManager", lambda cfg: tm)

    class FailingClient:
        def character_orders(self, character_id, auth_role):
            raise ESIError("503 Service Unavailable")

    monkeypatch.setattr(actions, "ESIClient", lambda tokens: FailingClient())

    with pytest.raises(ActionError, match="Could not fetch order-book data"):
        actions.do_check_undercut(cfg)


# ------------------------------------------------------------------ skills
class StubSkillClient:
    def __init__(self, skills_by_character=None, error=None):
        self.skills_by_character = skills_by_character or {}
        self.error = error

    def character_skills(self, character_id, auth_role):
        if self.error:
            raise self.error
        return self.skills_by_character.get(character_id, {"skills": []})


def test_skill_summary_reports_levels_and_derived_order_slots(monkeypatch):
    from eve_trader_local.station_trading.constants import SKILL_TRADE

    tm = StubTokenManager([StubRecord("trader:111", 111, "A Trader")])
    monkeypatch.setattr(actions, "TokenManager", lambda cfg: tm)
    client = StubSkillClient({111: {"skills": [{"skill_id": SKILL_TRADE, "active_skill_level": 5}]}})
    monkeypatch.setattr(actions, "ESIClient", lambda tokens: client)

    result = actions.do_get_skill_summary()

    assert result[0]["character_name"] == "A Trader"
    assert result[0]["levels"]["Trade"] == 5
    assert result[0]["order_slots"] == 5 + 4 * 5


def test_skill_summary_reports_a_failed_character_without_raising(monkeypatch):
    tm = StubTokenManager([StubRecord("trader:111", 111, "A Trader")])
    monkeypatch.setattr(actions, "TokenManager", lambda cfg: tm)
    monkeypatch.setattr(actions, "ESIClient", lambda tokens: StubSkillClient(error=ESIError("403")))

    result = actions.do_get_skill_summary()

    assert "error" in result[0]
    assert result[0]["character_name"] == "A Trader"


def test_skill_summary_with_no_characters_is_empty(monkeypatch):
    monkeypatch.setattr(actions, "TokenManager", lambda cfg: StubTokenManager([]))
    monkeypatch.setattr(actions, "ESIClient", lambda tokens: StubSkillClient())
    assert actions.do_get_skill_summary() == []


# -------------------------------------------------------------- characters
def test_list_trader_characters_delegates_to_esi_sync(monkeypatch):
    tm = StubTokenManager([StubRecord("trader:111", 111, "A Trader")])
    monkeypatch.setattr(actions.esi_sync, "TokenManager", lambda cfg: tm)

    assert actions.do_list_trader_characters() == [("trader:111", 111, "A Trader")]


def test_remove_trader_character_removes_the_token(monkeypatch):
    tm = StubTokenManager()
    monkeypatch.setattr(actions, "TokenManager", lambda cfg: tm)

    result = actions.do_remove_trader_character("trader:111")

    assert result == {"removed": "trader:111"}
    assert tm.removed == ["trader:111"]


# --------------------------------------------------------------- settings
def test_update_settings_persists_and_applies(db):
    cfg = StationTradingConfig()
    result = actions.do_update_settings({"min_daily_volume": 5000}, cfg)
    assert result == {"updated": ["min_daily_volume"]}
    assert cfg.min_daily_volume == 5000
    assert storage.load_settings("station_trading") == {"min_daily_volume": 5000}


def test_update_settings_rejects_bad_values(db):
    cfg = StationTradingConfig()
    with pytest.raises(ActionError):
        actions.do_update_settings({"broker_fee_rate": 5}, cfg)
    assert storage.load_settings("station_trading") == {}
