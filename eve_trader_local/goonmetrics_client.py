"""Client for the Goonmetrics current-price API (appraise.gnf.lt).

"Goonmetrics" is a naming legacy from the original third-party tool - the
endpoint this client actually calls is gnf.lt's rehosting of it. Kept under
the same name as in the parent eve-trader repo (see SYNC.md), where every
caller and every doc already says "the Goonmetrics client".

Scope note: this is specifically the *current price quotes* source - best
bid/best ask right now for every type in one market, used as the failsafe
behind `ESIClient.structure_order_stats_bulk_or_goonmetrics` when the real
structure order book is unreachable. It is not the same thing as the
region price *history* endpoint the parent uses for candidate
backtesting/discovery (`goonmetrics.apps.gnf.lt/api/price_history/`, its
`price_history`/`price_history_chunked`); those serve
history_backtest/shortlist/production, none of which are ported here yet -
port them together with whichever of those arrives first.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Optional

import requests

from .config import TRADING_CONFIG, TradingConfig

USER_AGENT = "eve-trader-local"

# Module-level (not per-instance, and not class-level) TTL cache, keyed by
# `market`. A GoonmetricsClient is constructed fresh at each call site, so a
# per-instance cache would never actually hit; module scope is what survives
# those instantiations. (esi_client.py's caches are class-level for the same
# "fresh client per call" reason - either scope works there; here there is no
# state on the class worth hanging it off, so plain module dicts are the
# simpler shape. sde.py caches nothing at all: its fetches are explicit,
# user-initiated refreshes, not something a request path re-enters.)
#
# The payload is the full market as one multi-megabyte JSON dump (~11MB for
# Jita, measured over 30s on the parent repo's connection) - re-downloading
# it twice in the same minute because the user clicked two actions in a row
# is the exact cost this exists to remove. 60s keeps it fresh enough for a
# real buy/sell decision.
#
# Locked per market, not with one shared lock: the lock is held for the whole
# (multi-second) fetch so two callers racing on a cold cache serialize rather
# than both paying the download - but a single shared lock would also make an
# in-flight Jita fetch block a concurrent home-market fetch, which shares no
# cache entry with it at all.
_PRICES_CACHE_TTL = 60  # seconds
_prices_cache: dict[str, list["CurrentPrice"]] = {}
_prices_cache_at: dict[str, float] = {}
_prices_locks: dict[str, threading.Lock] = {}
_prices_locks_guard = threading.Lock()  # guards creation of a new per-market lock only, never held during a fetch


def _lock_for_market(market: str) -> threading.Lock:
    with _prices_locks_guard:
        if market not in _prices_locks:
            _prices_locks[market] = threading.Lock()
        return _prices_locks[market]


def clear_prices_cache() -> None:
    """Forces the next current_prices call (for every market) to re-fetch.
    Module-level state doesn't reset itself between tests - anything
    exercising the real cache path should call this rather than depend on
    test ordering."""
    with _prices_locks_guard:
        _prices_cache.clear()
        _prices_cache_at.clear()


@dataclass(frozen=True)
class CurrentPrice:
    """Current best buy/sell for one type in a market, as served by
    appraise.gnf.lt."""
    type_id: int
    updated: str
    buy: float
    sell: float


class GoonmetricsClient:
    def __init__(self, cfg: TradingConfig = TRADING_CONFIG):
        self.cfg = cfg
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    def current_prices(self, market: str) -> list[CurrentPrice]:
        """Current best buy (max)/sell (min) for every type in `market` (e.g.
        "jita", or a player structure's market slug), sorted by type_id.

        Retries with backoff before giving up: this is a third-party, no-SLA
        server serving a multi-megabyte response, and normal response-time
        variance alone (not an outage) was enough to kill every caller of a
        bare single-attempt fetch in the parent repo.

        Cached module-wide for _PRICES_CACHE_TTL seconds - see that
        constant's own comment.
        """
        with _lock_for_market(market):
            cached_at = _prices_cache_at.get(market, 0.0)
            if market in _prices_cache and (time.time() - cached_at) < _PRICES_CACHE_TTL:
                return list(_prices_cache[market])

            url = f"{self.cfg.goonmetrics_appraise_base}/market/{market}/prices.json"
            last_exc: Optional[requests.RequestException] = None
            for attempt in range(1, 4):
                try:
                    resp = self.session.get(url, timeout=60)
                    resp.raise_for_status()
                    break
                except requests.RequestException as e:
                    last_exc = e
                    if attempt < 3:
                        time.sleep(attempt * 2)
            else:
                raise last_exc
            prices = [
                CurrentPrice(
                    type_id=item["typeID"],
                    updated=item["prices"]["updated"],
                    buy=item["prices"]["buy"]["max"],
                    sell=item["prices"]["sell"]["min"],
                )
                for item in resp.json()
            ]
            prices.sort(key=lambda p: p.type_id)
            _prices_cache[market] = prices
            _prices_cache_at[market] = time.time()
            return list(prices)
