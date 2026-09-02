"""Client for the Goonmetrics APIs (gnf.lt).

"Goonmetrics" is a naming legacy from the original third-party tool - the
endpoints this client actually calls are gnf.lt's rehosting of it. Kept under
the same name as in the parent eve-trader repo (see SYNC.md), where every
caller and every doc already says "the Goonmetrics client".

Two separate endpoints live here, answering two different questions - don't
treat a number from one as interchangeable with a same-sounding number from
the other:

* `current_prices` (appraise.gnf.lt) - best bid/best ask *right now* for
  every type in one market. Used as the failsafe behind
  `ESIClient.structure_order_stats_bulk_or_goonmetrics` when the real
  structure order book is unreachable.
* `price_history`/`price_history_chunked` (goonmetrics.apps.gnf.lt) - daily
  *region* history (avg/min/max price and units moved) for a batch of
  type_ids. This is what answers "is a newly discovered candidate
  historically worth importing at all", asked against
  `TradingConfig.reference_region_id` - a region-wide average smooths out
  the noise a live order-book snapshot is full of.

The history endpoint returns XML like:
    <evec_api><result><rowset name="history">
      <type id="...">
        <history date="..." avgPrice="..." maxPrice="..." minPrice="..."
                 movement="..." numOrders="..."/>
      </type>
    </rowset></result></evec_api>
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Iterable, Optional
from xml.etree import ElementTree as ET

import requests

from .config import TRADING_CONFIG, TradingConfig

log = logging.getLogger(__name__)

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
class HistoryPoint:
    region_id: int
    type_id: int
    date: str
    min_price: float
    max_price: float
    avg_price: float
    movement: float
    num_orders: int


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

    def price_history(self, region_id: int, type_ids: Iterable[int]) -> list[HistoryPoint]:
        """Daily history for a batch of type_ids in one region. Never raises
        on a Goonmetrics failure - silently falls back to the slower
        per-type_id ESI history endpoint instead, so callers don't need their
        own fallback handling."""
        type_ids = list(type_ids)
        ids = ",".join(str(t) for t in type_ids)
        url = f"{self.cfg.goonmetrics_history_base}?region_id={region_id}&type_id={ids}"
        try:
            resp = self.session.get(url, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            # Goonmetrics is a third-party community API with no SLA - fall
            # back to ESI's own (slower, one call per type_id, but
            # always-available) daily history rather than letting a
            # Goonmetrics outage take down candidate discovery entirely.
            log.warning("Goonmetrics price history unavailable (%s) - falling back to ESI per-type history.", e)
            return self._esi_price_history_fallback(region_id, type_ids)
        return _parse_history_xml(resp.text, region_id)

    def _esi_price_history_fallback(self, region_id: int, type_ids: list[int]) -> list[HistoryPoint]:
        """Refetches via ESI's /markets/{region_id}/history/ (esi_client.
        region_market_history), one call per type_id since ESI has no batch
        endpoint for this.

        Maps ESI's schema onto HistoryPoint: `movement` is ESI's own `volume`
        field (units traded that day, *not* an ISK value - confirmed live in
        the parent repo against ESI directly), so it passes straight through.
        That multiplication by average_price used to happen here and silently
        mixed unit-count and ISK-value figures in the same
        HistoryPoint.movement field depending on which source served the
        request."""
        from .esi_client import ESIClient, ESIError  # local import: keeps callers that never hit this fallback free of an esi_client<->goonmetrics_client coupling

        esi = ESIClient(self.cfg)
        points: list[HistoryPoint] = []
        for type_id in type_ids:
            try:
                rows = esi.region_market_history(region_id, type_id)
            except ESIError as e:
                log.warning("ESI price history fallback also failed for type_id %d (%s) - skipping it.", type_id, e)
                continue
            for r in rows:
                points.append(HistoryPoint(
                    region_id=region_id, type_id=type_id, date=r["date"],
                    min_price=r["lowest"], max_price=r["highest"], avg_price=r["average"],
                    movement=r["volume"], num_orders=r["order_count"],
                ))
        return points

    def price_history_chunked(self, region_id: int, type_ids: list[int],
                              chunk_size: int | None = None, max_workers: int = 6) -> list[HistoryPoint]:
        """Chunks are independent requests (each already batches chunk_size
        type_ids into one call) - fetching them concurrently cuts wall-clock
        time roughly by max_workers, since this is network-latency-bound.

        Each chunk is isolated in its own try/except (on top of
        price_history's own Goonmetrics->ESI fallback for plain request
        failures) so a single chunk hitting something neither of those
        handles - a malformed response, a parsing bug - just loses that one
        chunk's points instead of aborting the whole call and everything
        after it (matters most for a full candidate search spanning many
        chunks over several minutes)."""
        chunk_size = chunk_size or self.cfg.chunk_size
        chunks = [type_ids[i:i + chunk_size] for i in range(0, len(type_ids), chunk_size)]
        if not chunks:
            return []

        def _fetch(chunk: list[int]) -> list[HistoryPoint]:
            try:
                return self.price_history(region_id, chunk)
            except Exception:  # noqa: BLE001 - one bad chunk must not lose the rest
                log.exception("Price history chunk failed for region %d (%d ids) - skipping it.",
                              region_id, len(chunk))
                return []

        out: list[HistoryPoint] = []
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for points in pool.map(_fetch, chunks):
                out.extend(points)
        return out


def _parse_history_xml(xml_text: str, region_id: int) -> list[HistoryPoint]:
    root = ET.fromstring(xml_text)
    points: list[HistoryPoint] = []
    for type_el in root.iter("type"):
        type_id = int(type_el.attrib["id"])
        for hist_el in type_el.findall("history"):
            points.append(HistoryPoint(
                region_id=region_id,
                type_id=type_id,
                date=hist_el.attrib["date"],
                min_price=float(hist_el.attrib["minPrice"]),
                max_price=float(hist_el.attrib["maxPrice"]),
                avg_price=float(hist_el.attrib["avgPrice"]),
                movement=float(hist_el.attrib["movement"]),
                num_orders=int(hist_el.attrib["numOrders"]),
            ))
    return points
