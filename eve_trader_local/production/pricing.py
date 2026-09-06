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

`home_prices`/`jita_prices` themselves never call ESI/Goonmetrics anymore -
see esi_update.py's own module docstring for why every market/ESI call in
this app now happens only inside its sync bundles. `refresh_home_prices`/
`refresh_jita_prices` are the real fetch, called only from those bundles;
they write into `storage.order_book_cache`, which the plain `*_prices`
functions then read - so every existing caller (Production's margins/build-
tree/market-status, Doctrine's shopping list) keeps working unchanged, just
against whatever the last relevant Update run cached instead of a live call.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import requests

from .. import storage
from ..auth import TokenManager
from ..config import TRADING_CONFIG, TradingConfig
from ..errors import ActionError
from ..esi_client import ESIClient, ESIError, OrderStats
from ..goonmetrics_client import CurrentPrice, GoonmetricsClient
from .config import PRODUCTION_CONFIG, ProductionConfig
# Defined by esi_sync, which owns producer-character registration - same
# direction as the parent repo, where pricing.py reaches into esi_sync for the
# producer role rather than keeping its own copy of the prefix.
from .esi_sync import PRODUCER_ROLE_PREFIX  # noqa: F401 - re-exported for callers

JITA_MARKET = "jita"


def _home_market_key(cfg: ProductionConfig) -> Optional[str]:
    return f"structure:{cfg.home_location_id}" if cfg.home_location_id is not None else None


def _jita_market_key(trading_cfg: TradingConfig) -> str:
    return f"region:{trading_cfg.jita_region_id}"


def _store_order_stats(market: str, stats: dict[int, OrderStats]) -> None:
    if not stats:
        return
    now = datetime.now(timezone.utc).isoformat()
    rows = {tid: (s.buy_percentile, s.sell_percentile, s.buy_volume, s.sell_volume) for tid, s in stats.items()}
    storage.save_order_book_stats(market, rows, now)


def _store_goonmetrics_prices(market: str, prices: dict[int, CurrentPrice]) -> None:
    if not prices:
        return
    now = datetime.now(timezone.utc).isoformat()
    # No real order-book volume in a Goonmetrics current-price snapshot -
    # same "percentile-only, no real depth" degradation
    # structure_order_stats_bulk_or_goonmetrics already documents for its own
    # synthesized OrderStats.
    rows = {tid: (p.buy, p.sell, 0.0, 0.0) for tid, p in prices.items()}
    storage.save_order_book_stats(market, rows, now)


def _read_cached_prices(market: str, type_ids: list[int]) -> dict[int, CurrentPrice]:
    cached = storage.load_order_book_stats(market, type_ids)
    return {
        tid: CurrentPrice(type_id=tid, updated="", buy=buy_pct or 0.0, sell=sell_pct or 0.0)
        for tid, (buy_pct, sell_pct, _buy_vol, _sell_vol) in cached.items()
    }


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


def refresh_home_prices(type_ids: list[int], cfg: ProductionConfig = PRODUCTION_CONFIG,
                        client: Optional[ESIClient] = None) -> dict[int, CurrentPrice]:
    """The real fetch behind `home_prices` - called only from an
    esi_update.py sync bundle, never from a view/action directly (see this
    module's own docstring).

    Live order book of cfg.home_location_id via any logged-in `producer`
    character (one full-book download regardless of how many type_ids are
    asked for - ESI's structure endpoint has no type filter), falling back
    to a Goonmetrics snapshot of cfg.home_market only when no producer can
    complete the call: none logged in, missing docking access, or an ESI
    outage. Every result is cached into `storage.order_book_cache` before
    being returned, so `home_prices` can read it back with no network call.

    Producer characters are their own role here, not Trading's `seller` - the
    character who can dock and read the industry structure isn't necessarily
    the one running the sell orders.
    """
    if not type_ids:
        return {}
    market = _home_market_key(cfg)
    if market is not None:
        client = client or ESIClient()
        for record in TokenManager().list_records(PRODUCER_ROLE_PREFIX):
            try:
                stats = client.structure_order_stats_bulk(
                    cfg.home_location_id, type_ids, auth_role=record.role)
            # ActionError covers ESIError *and* a token that can no longer be
            # refreshed; a bare RequestException never reaches ESIError at all.
            except (ActionError, requests.RequestException):
                continue
            _store_order_stats(market, stats)
            return _from_order_stats(stats, type_ids)
    if not cfg.home_market:
        return {}
    prices = _goonmetrics_prices(cfg.home_market, type_ids)
    if market is not None:
        _store_goonmetrics_prices(market, prices)
    return prices


def home_prices(type_ids: list[int], cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict[int, CurrentPrice]:
    """Current home-structure quotes for `type_ids`, read from whatever the
    last relevant esi_update.py sync bundle cached (see `refresh_home_prices`
    for the real fetch) - never a live call. A type_id with nothing cached
    yet is simply absent from the result, the same "no price" shape an ESI
    failure already produced before this cache existed."""
    market = _home_market_key(cfg)
    if market is None or not type_ids:
        return {}
    return _read_cached_prices(market, type_ids)


def refresh_jita_prices(type_ids: list[int], client: Optional[ESIClient] = None,
                        trading_cfg: TradingConfig = TRADING_CONFIG) -> dict[int, CurrentPrice]:
    """The real fetch behind `jita_prices` - called only from an
    esi_update.py sync bundle, same contract as `refresh_home_prices`.

    Callers MUST pass a real, bounded `type_ids` (in practice: the materials
    of the things being built). ESI has no bulk region-order endpoint, so this
    is one request per type - "every item Goonmetrics knows about" would be
    tens of thousands of calls.

    Jita's region id comes from TradingConfig, not ProductionConfig: Jita is
    Trading's own concept and there is exactly one of it per install.
    """
    if not type_ids:
        return {}
    market = _jita_market_key(trading_cfg)
    client = client or ESIClient(trading_cfg)
    try:
        stats = client.region_order_stats_bulk(trading_cfg.jita_region_id, type_ids)
    except (ESIError, requests.RequestException):
        prices = _goonmetrics_prices(JITA_MARKET, type_ids, trading_cfg)
        _store_goonmetrics_prices(market, prices)
        return prices
    _store_order_stats(market, stats)
    return _from_order_stats(stats, type_ids)


def jita_prices(type_ids: list[int], trading_cfg: TradingConfig = TRADING_CONFIG) -> dict[int, CurrentPrice]:
    """Same cache-only contract as `home_prices`, for the Jita region (see
    `refresh_jita_prices` for the real fetch)."""
    if not type_ids:
        return {}
    return _read_cached_prices(_jita_market_key(trading_cfg), type_ids)


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
    ACTIVITY_MODS rate in that case rather than guessing an index. The real
    fetch behind `refresh_system_cost_indices` - takes an already-built
    `esi_client` rather than constructing its own, so it stays safe to call
    from a sync bundle that already has one."""
    if system_id is None:
        return {}
    try:
        return esi_client.get_system_cost_indices(system_id, activities=("manufacturing", "reaction"))
    except (ESIError, requests.RequestException):
        return {}


def _cost_indices_cache_key(system_id: int) -> str:
    return f"production:cost_indices:{system_id}"


_ADJUSTED_PRICES_CACHE_KEY = "production:adjusted_prices"


def refresh_system_cost_indices(system_id: Optional[int], client: Optional[ESIClient] = None) -> dict[str, float]:
    """Live fetch + cache write behind `cached_system_cost_indices` - called
    only from an esi_update.py sync bundle."""
    if system_id is None:
        return {}
    indices = system_cost_indices_for(client or ESIClient(), system_id)
    storage.save_result_cache(_cost_indices_cache_key(system_id), indices,
                              datetime.now(timezone.utc).isoformat())
    return indices


def cached_system_cost_indices(system_id: Optional[int]) -> dict[str, float]:
    """Manufacturing/reaction cost indices for `system_id`, read from
    whatever the last Production Update run cached (see
    `refresh_system_cost_indices`) - never a live call. {} when `system_id`
    is unset or nothing has been cached yet."""
    if system_id is None:
        return {}
    cached = storage.load_result_cache(_cost_indices_cache_key(system_id))
    return cached[0] if cached else {}


def refresh_adjusted_prices(client: Optional[ESIClient] = None) -> dict[int, float]:
    """Live fetch + cache write behind `cached_adjusted_prices` - called only
    from an esi_update.py sync bundle."""
    client = client or ESIClient()
    try:
        prices = client.get_adjusted_prices()
    except (ESIError, requests.RequestException):
        return {}
    storage.save_result_cache(_ADJUSTED_PRICES_CACHE_KEY, prices, datetime.now(timezone.utc).isoformat())
    return prices


def cached_adjusted_prices() -> dict[int, float]:
    """Every published type's EIV-adjusted price, read from whatever the
    last Production Update run cached - never a live call. {} before the
    first sync. Keys come back as `int` even though JSON round-trips them as
    strings (result_cache is a plain JSON blob store)."""
    cached = storage.load_result_cache(_ADJUSTED_PRICES_CACHE_KEY)
    if cached is None:
        return {}
    payload, _computed_at = cached
    return {int(type_id): price for type_id, price in payload.items()}
