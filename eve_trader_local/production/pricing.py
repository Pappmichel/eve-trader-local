"""What one unit of an item costs to acquire right now, and where from.

This is the "buy" half of Production's buy-vs-build decision (the "build"
half - bill-of-materials traversal, job costs, margins - isn't ported yet,
see SYNC.md). Two markets are compared per item: the home structure's own
order book, and Jita plus the haul cost of getting it home.

Price sources, deliberately different from Trading's:
- live ESI order-book percentiles first, both for the home structure (needs a
  logged-in `producer` character with docking access) and for the Jita region;
- a Goonmetrics current-price snapshot (appraise.gnf.lt) only as a fallback,
  fetched lazily on an actual ESI failure - never eagerly alongside it, so the
  expected-common success path doesn't pay for a multi-megabyte download it
  will throw away.
"""
from __future__ import annotations

from typing import Optional

import requests

from ..auth import TokenManager
from ..config import TRADING_CONFIG, TradingConfig
from ..errors import ActionError
from ..esi_client import ESIClient, ESIError, OrderStats
from ..goonmetrics_client import CurrentPrice, GoonmetricsClient
from .config import PRODUCTION_CONFIG, ProductionConfig

JITA_MARKET = "jita"
PRODUCER_ROLE_PREFIX = "producer"


def _goonmetrics_prices(market: str, type_ids: list[int],
                        trading_cfg: TradingConfig = TRADING_CONFIG) -> dict[int, CurrentPrice]:
    if not market or not type_ids:
        return {}
    wanted = set(type_ids)
    return {p.type_id: p for p in GoonmetricsClient(trading_cfg).current_prices(market)
            if p.type_id in wanted}


def _from_order_stats(stats: dict[int, OrderStats], type_ids: list[int]) -> dict[int, CurrentPrice]:
    # updated="": a live order-book read has no "last updated" concept of its
    # own - it *is* the book right now - and nothing in this repo reads
    # CurrentPrice.updated.
    return {
        tid: CurrentPrice(
            type_id=tid, updated="",
            buy=stats[tid].buy_percentile or 0.0,
            sell=stats[tid].sell_percentile or 0.0,
        )
        for tid in type_ids if tid in stats
    }


def home_prices(type_ids: list[int], cfg: ProductionConfig = PRODUCTION_CONFIG,
                client: Optional[ESIClient] = None) -> dict[int, CurrentPrice]:
    """Current home-structure quotes for `type_ids`, from the live order book
    of cfg.home_location_id via any logged-in `producer` character (one
    full-book download regardless of how many type_ids are asked for - ESI's
    structure endpoint has no type filter), falling back to a Goonmetrics
    snapshot of cfg.home_market only when no producer can complete the call:
    none logged in, missing docking access, or an ESI outage.

    Producer characters are their own role here, not Trading's `seller` - the
    character who can dock and read the industry structure isn't necessarily
    the one running the sell orders.
    """
    if not type_ids:
        return {}
    if cfg.home_location_id is not None:
        client = client or ESIClient()
        for record in TokenManager().list_records(PRODUCER_ROLE_PREFIX):
            try:
                stats = client.structure_order_stats_bulk(
                    cfg.home_location_id, type_ids, auth_role=record.role)
            # ActionError covers ESIError *and* a token that can no longer be
            # refreshed; a bare RequestException never reaches ESIError at all.
            except (ActionError, requests.RequestException):
                continue
            return _from_order_stats(stats, type_ids)
    return _goonmetrics_prices(cfg.home_market, type_ids) if cfg.home_market else {}


def jita_prices(type_ids: list[int], client: Optional[ESIClient] = None,
                trading_cfg: TradingConfig = TRADING_CONFIG) -> dict[int, CurrentPrice]:
    """Same ESI-first/Goonmetrics-fallback shape as home_prices, but for the
    public Jita region order book (no auth needed).

    Callers MUST pass a real, bounded `type_ids` (in practice: the materials
    of the things being built). ESI has no bulk region-order endpoint, so this
    is one request per type - "every item Goonmetrics knows about" would be
    tens of thousands of calls.

    Jita's region id comes from TradingConfig, not ProductionConfig: Jita is
    Trading's own concept and there is exactly one of it per install.
    """
    if not type_ids:
        return {}
    client = client or ESIClient(trading_cfg)
    try:
        stats = client.region_order_stats_bulk(trading_cfg.jita_region_id, type_ids)
    except (ESIError, requests.RequestException):
        return _goonmetrics_prices(JITA_MARKET, type_ids, trading_cfg)
    return _from_order_stats(stats, type_ids)


def _candidate_prices(type_id: int, home: dict[int, CurrentPrice], jita: dict[int, CurrentPrice],
                      volume_m3: Optional[float], cfg: ProductionConfig) -> dict[str, float]:
    """Landed unit price per source, for whichever sources actually have a
    sell order listed. Both are inflated by the buy-side broker fee (same
    character, same rate, wherever they buy); Jita's additionally carries the
    haul cost of moving the item home, which a home purchase doesn't need by
    definition."""
    candidates: dict[str, float] = {}
    home_quote = home.get(type_id)
    if home_quote and home_quote.sell > 0:
        candidates["home"] = home_quote.sell * (1 + cfg.jita_buy_broker_fee)
    jita_quote = jita.get(type_id)
    if jita_quote and jita_quote.sell > 0:
        candidates["jita"] = (jita_quote.sell * (1 + cfg.jita_buy_broker_fee)
                              + cfg.haul_cost_per_m3 * (volume_m3 or 0))
    return candidates


def buy_source(type_id: int, home: dict[int, CurrentPrice], jita: dict[int, CurrentPrice],
               volume_m3: Optional[float] = None,
               cfg: ProductionConfig = PRODUCTION_CONFIG) -> Optional[str]:
    """Which market buy_price() sources `type_id` from - "home" or "jita",
    whichever is cheaper landed, or whichever of the two has a sell order at
    all, else None. buy_price uses this same helper internally, so the two can
    never disagree about the chosen source."""
    candidates = _candidate_prices(type_id, home, jita, volume_m3, cfg)
    if not candidates:
        return None
    return min(candidates, key=candidates.get)


def buy_price(type_id: int, home: dict[int, CurrentPrice], jita: dict[int, CurrentPrice],
              volume_m3: Optional[float] = None,
              cfg: ProductionConfig = PRODUCTION_CONFIG) -> Optional[float]:
    """Cheapest way to acquire one unit of `type_id` right now: the home sell
    price, or the Jita sell price plus haul cost, whichever is lower. None if
    neither market has a sell order for it."""
    candidates = _candidate_prices(type_id, home, jita, volume_m3, cfg)
    if not candidates:
        return None
    return min(candidates.values())


def system_cost_indices_for(esi_client: ESIClient, system_id: Optional[int]) -> dict[str, float]:
    """Manufacturing/reaction cost indices for `system_id`, or {} when it's
    unset or ESI is having a moment - job-cost modeling falls back to the flat
    ACTIVITY_MODS rate in that case rather than guessing an index."""
    if system_id is None:
        return {}
    try:
        return esi_client.get_system_cost_indices(system_id, activities=("manufacturing", "reaction"))
    except (ESIError, requests.RequestException):
        return {}
