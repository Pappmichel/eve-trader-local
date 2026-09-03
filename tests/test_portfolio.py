"""portfolio_overview: the one place Trading and Production data are combined.

Each half must degrade independently to zero/None rather than erroring when
its own tool has no data yet - that's the whole point of this module, so
each combination is its own test rather than one big fixture.
"""
from __future__ import annotations

import pytest

from eve_trader_local import storage
from eve_trader_local.models import RealizedTrade
from eve_trader_local.portfolio import portfolio_overview
from eve_trader_local.production import engine as production_engine


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("EVE_TRADER_LOCAL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("EVE_TRADER_LOCAL_CONFIG", raising=False)
    storage.init_db()
    return storage.db_path()


def _trade(sell_date, profit, buy_price=100.0, qty=1) -> RealizedTrade:
    return RealizedTrade(
        type_id=34, item="Tritanium", buy_date="2026-01-01T00:00:00Z", buy_qty=qty,
        buy_unit_price=buy_price, sell_date=sell_date, sell_qty=qty, sell_unit_price=150.0,
        matched_qty=qty, realized_profit=profit, margin=profit / (buy_price * qty),
    )


def test_no_data_in_either_tool(db):
    result = portfolio_overview()

    assert result["trading_realized_profit"] == 0.0
    assert result["trading_average_margin"] == 0.0
    assert result["trading_daily_profit_volatility"] is None
    assert result["trading_trade_count"] == 0
    assert result["production_stock_value"] == 0.0
    assert result["production_stock_targets_configured"] is False
    assert result["combined_value"] == 0.0


def test_trading_only_degrades_production_to_zero(db):
    trades = [
        _trade("2026-01-05T10:00:00Z", 500.0),
        _trade("2026-01-06T10:00:00Z", 300.0),
    ]
    storage.save_realized_trades(trades, run_ts="2026-01-07T00:00:00Z")

    result = portfolio_overview()

    assert result["trading_realized_profit"] == 800.0
    assert result["trading_trade_count"] == 2
    # Two distinct days of realized profit -> a real (non-None) volatility figure.
    assert result["trading_daily_profit_volatility"] == pytest.approx(100.0)
    assert result["production_stock_value"] == 0.0
    assert result["production_stock_targets_configured"] is False
    assert result["combined_value"] == 800.0


def test_production_only_degrades_trading_to_zero(db, monkeypatch):
    storage.upsert_stock_target(35, "Pyerite", 1000.0)
    monkeypatch.setattr(production_engine, "stock_value",
                        lambda cfg=None: {"total_value": 42000.0, "priced_items": 1, "unpriced_items": 0})

    result = portfolio_overview()

    assert result["trading_realized_profit"] == 0.0
    assert result["trading_average_margin"] == 0.0
    assert result["trading_daily_profit_volatility"] is None
    assert result["trading_trade_count"] == 0
    assert result["production_stock_value"] == 42000.0
    assert result["production_stock_targets_configured"] is True
    assert result["combined_value"] == 42000.0


def test_single_day_of_trades_has_no_volatility(db):
    trades = [_trade("2026-01-05T10:00:00Z", 500.0), _trade("2026-01-05T11:00:00Z", 300.0)]
    storage.save_realized_trades(trades, run_ts="2026-01-07T00:00:00Z")

    result = portfolio_overview()

    assert result["trading_realized_profit"] == 800.0
    assert result["trading_daily_profit_volatility"] is None


def test_both_tools_have_data(db, monkeypatch):
    storage.save_realized_trades([_trade("2026-01-05T10:00:00Z", 200.0),
                                  _trade("2026-01-06T10:00:00Z", 600.0)],
                                 run_ts="2026-01-07T00:00:00Z")
    storage.upsert_stock_target(35, "Pyerite", 1000.0)
    monkeypatch.setattr(production_engine, "stock_value",
                        lambda cfg=None: {"total_value": 10000.0, "priced_items": 1, "unpriced_items": 0})

    result = portfolio_overview()

    assert result["trading_realized_profit"] == 800.0
    assert result["trading_trade_count"] == 2
    assert result["production_stock_value"] == 10000.0
    assert result["production_stock_targets_configured"] is True
    assert result["combined_value"] == 10800.0
