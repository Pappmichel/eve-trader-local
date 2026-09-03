"""Tests for station_trading/esi_sync.py - character registration only (see
its own docstring for why there's no sync_esi()-style bulk pull here)."""
from __future__ import annotations

from eve_trader_local.station_trading.esi_sync import (
    STATION_TRADING_ROLE_PREFIX,
    STATION_TRADING_SCOPES,
    list_trader_characters,
)


class StubRecord:
    def __init__(self, role, character_id, character_name):
        self.role = role
        self.character_id = character_id
        self.character_name = character_name


class StubTokenManager:
    def __init__(self, records):
        self.records = records

    def list_records(self, prefix=None):
        return [r for r in self.records if r.role.startswith(f"{prefix}:")]


def test_role_prefix_is_trader():
    assert STATION_TRADING_ROLE_PREFIX == "trader"


def test_scopes_match_what_actions_py_actually_calls():
    assert "esi-markets.read_character_orders.v1" in STATION_TRADING_SCOPES
    assert "esi-skills.read_skills.v1" in STATION_TRADING_SCOPES


def test_list_trader_characters_filters_by_role_prefix():
    tm = StubTokenManager([
        StubRecord("trader:111", 111, "A Trader"),
        StubRecord("producer:222", 222, "A Producer"),
    ])
    result = list_trader_characters(tm)
    assert result == [("trader:111", 111, "A Trader")]


def test_no_registered_characters_returns_empty():
    assert list_trader_characters(StubTokenManager([])) == []
