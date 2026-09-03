"""Cross-cutting Trading + Production portfolio/risk overview.

The two tools stay fully independent everywhere else (separate config,
separate ESI scopes, separate CLI sections) - this is the one place that
looks at both together, and it's deliberately additive: a combined summary
alongside each tool's own dedicated commands, never a replacement for either
(mirrors the parent repo's portfolio.py, which the same reasoning is
confirmed for there).
"""
from __future__ import annotations

from collections import defaultdict
from typing import Optional

from . import storage
from .config import TRADING_CONFIG, TradingConfig


def portfolio_overview(cfg: TradingConfig = TRADING_CONFIG) -> dict:
    """Combines Trading's realized P&L (latest reconciliation run) with
    Production's stock value into one read-only summary, plus a simple
    day-to-day profit volatility signal. Each half degrades independently to
    zero/None if that tool has no data yet, rather than failing the whole
    overview - e.g. a fresh install with Trading data but no Production stock
    targets configured still shows what it has.

    daily_profit_volatility is a plain population standard deviation of
    per-day realized profit (not a formal Value-at-Risk model - this app has
    no options-pricing-grade statistics infrastructure, and a plain stdev of
    realized daily P&L is the standard, well-understood starting point real
    trading tools use for an at-a-glance risk indicator). None below 2 days
    of data - a single day has no meaningful spread.
    """
    trades = storage.latest_realized_trades()
    if not trades:
        trading_realized_profit = 0.0
        trading_average_margin = 0.0
        daily_profit_volatility: Optional[float] = None
        trade_count = 0
    else:
        trading_realized_profit = sum(t.realized_profit for t in trades)
        weighted_denom = sum(t.buy_unit_price * t.matched_qty for t in trades)
        trading_average_margin = (trading_realized_profit / weighted_denom) if weighted_denom else 0.0
        by_day: dict[str, float] = defaultdict(float)
        for t in trades:
            by_day[t.sell_date[:10]] += t.realized_profit
        daily_profit_volatility = _population_stdev(list(by_day.values())) if len(by_day) >= 2 else None
        trade_count = len(trades)

    production_stock_value = 0.0
    stock_targets_configured = bool(storage.load_stock_targets())
    if stock_targets_configured:
        from .production.config import PRODUCTION_CONFIG
        from .production.engine import stock_value
        production_stock_value = stock_value(PRODUCTION_CONFIG)["total_value"]

    return {
        "trading_realized_profit": trading_realized_profit,
        "trading_average_margin": trading_average_margin,
        "trading_daily_profit_volatility": daily_profit_volatility,
        "trading_trade_count": trade_count,
        "production_stock_value": production_stock_value,
        "production_stock_targets_configured": stock_targets_configured,
        "combined_value": trading_realized_profit + production_stock_value,
    }


def _population_stdev(values: list[float]) -> float:
    """Population (ddof=0) standard deviation - matches the parent's pandas
    `.std(ddof=0)` call exactly (pandas defaults to ddof=1/sample stdev, so
    the parent passes ddof=0 explicitly; there's no pandas here, so the
    equivalent plain-Python formula is spelled out instead)."""
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return variance ** 0.5
