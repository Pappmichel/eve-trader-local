"""Thin wrapper around EVE Online's ESI API.

Covers market structure/order-book stats, character/corp orders, wallet,
assets, industry jobs, blueprints, contracts, skills, name resolution,
adjusted prices and system cost indices - the generic HTTP layer every
feature ported into this repo later will sit on top of.

Ported near-verbatim from the parent eve-trader repo's `eve_trader/
esi_client.py` (see SYNC.md). The networking mechanics - retry/backoff,
ESI's error-limit budget, pagination - are storage- and tenant-agnostic and
carry over unchanged; what was dropped is the parent's tenant plumbing
(`storage.with_current_tenant` around every thread-pool submission, needed
there only because a worker thread doesn't inherit the ambient tenant
contextvar - there is no ambient tenant here).
"""
from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Optional

import requests

from . import storage
from .auth import TokenManager
from .config import TRADING_CONFIG, TradingConfig
from .errors import ActionError

_contact = os.getenv("EVE_CONTACT_EMAIL", "").strip()
USER_AGENT = (
    f"eve-trader-local (contact: {_contact})" if _contact
    else "eve-trader-local (contact: set EVE_CONTACT_EMAIL in .env)"
)

METALEVEL_ATTRIBUTE_ID = 633  # EVE SDE dogma attribute "metaLevel" (0=Tech I, 5=Tech II, ...)

# location_flag values that share a hangar's location_id in ESI's asset model
# but are NOT usable stock sitting in that hangar:
# - AssetSafety: recovered after a structure was unanchored/lost - requires a
#   separate retrieval trip/fee, not physically at this location right now.
# - Deliveries / CorpDeliveries: tied up in a pending contract, not free stock.
# - CorpMarket: listed on the corp's buyback/market system, not hangar stock.
# Confirmed as a real, previously-hit bug class for ESI asset tools (jeveassets
# changelog: "Asset safety is not an unknown location" needed its own fix).
# Without this filter anything counting "stock at a location" silently
# over-counts by including quantities that aren't actually available.
# Lives here rather than in storage.py (where the parent repo keeps it): it
# describes ESI's asset payload, and this repo has no persisted asset table
# for it to sit next to - see SYNC.md.
NON_STOCK_LOCATION_FLAGS = ("AssetSafety", "Deliveries", "CorpDeliveries", "CorpMarket")


class ESIError(ActionError):
    """An ESI call failed in a way the user should be told about (HTTP error,
    retries exhausted, no cost indices for a system). Subclasses ActionError -
    unlike the parent repo, where the two were deliberately kept separate to
    avoid a circular import between config.py and actions.py; this repo's
    errors.py exists precisely so every layer can raise the user-facing type
    without that problem (same reasoning ConfigError already follows)."""


def extract_meta_level(type_info: dict) -> Optional[int]:
    """Pulls the metaLevel dogma attribute out of a /universe/types/{id}/
    response, if present (unpublished/no-attribute types return None)."""
    for attr in type_info.get("dogma_attributes") or []:
        if attr.get("attribute_id") == METALEVEL_ATTRIBUTE_ID:
            try:
                return int(attr["value"])
            except (KeyError, TypeError, ValueError):
                return None
    return None


# SDE category_id constants for the packaged-volume quirk below.
_SHIP_CATEGORY_ID = 6
_MODULE_CATEGORY_ID = 7


def resolve_effective_volume(type_id: int, sde_volume: Optional[float],
                             category_id: Optional[int] = None) -> Optional[float]:
    """The volume to actually use for freight/haul-cost math - the *packaged*
    volume for ships and capital-sized modules, which can be drastically
    smaller than the flight/assembled volume in sde_types.volume.

    There's no clean SDE-only signal for exactly which modules this applies
    to (confirmed live via ESI in the parent repo: Capital Shield Booster I
    lists volume=4000 but packaged_volume=1000, while an ordinary Large
    Shield Booster I or a Capital Trimark Armor Pump rig comes back with
    packaged == flight) - so every Ship/Module-category type_id goes through
    the same ESI lookup+cache path, cached once in
    storage.type_packaged_volume since it's effectively a static game
    constant. Every other category returns its plain SDE volume unchanged.

    `category_id` is optional - pass it when the caller already has it (e.g.
    straight out of its own sde_types tuple) to skip storage.
    get_type_category's extra lookup."""
    if sde_volume is None:
        return None
    if category_id is None:
        category_id = storage.get_type_category(type_id)
    if category_id not in (_SHIP_CATEGORY_ID, _MODULE_CATEGORY_ID):
        return sde_volume
    cached = storage.get_cached_packaged_volume(type_id)
    if cached is not None:
        return cached
    try:
        packaged = ESIClient().get_packaged_volume(type_id)
    except Exception:  # noqa: BLE001 - best-effort; a transient ESI hiccup shouldn't block callers
        packaged = None
    if packaged is not None:
        # Only cache a real ESI answer, never the sde_volume fallback below -
        # caching a transient-failure fallback as if it were the real
        # packaged volume would permanently poison it.
        storage.set_cached_packaged_volume(type_id, packaged)
        return packaged
    return sde_volume


def resolve_effective_volume_bulk(items: list[tuple[int, Optional[float], Optional[int]]],
                                  max_workers: int = 10) -> dict[int, Optional[float]]:
    """Same as resolve_effective_volume, but for many (type_id, sde_volume,
    category_id) triples concurrently - on a machine whose
    type_packaged_volume cache hasn't been filled yet, resolving a few
    hundred Ship/Module types one at a time is several hundred sequential
    ESI round-trips (a confirmed multi-minute stall in the parent repo).
    The work is pure network latency, so a small thread pool cuts wall-clock
    time roughly by max_workers."""
    results: dict[int, Optional[float]] = {}
    sde_volume_by_id: dict[int, Optional[float]] = {}
    to_fetch: list[int] = []
    for type_id, sde_volume, category_id in items:
        sde_volume_by_id[type_id] = sde_volume
        if sde_volume is None:
            results[type_id] = None
            continue
        if category_id is None:
            category_id = storage.get_type_category(type_id)
        if category_id not in (_SHIP_CATEGORY_ID, _MODULE_CATEGORY_ID):
            results[type_id] = sde_volume
            continue
        cached = storage.get_cached_packaged_volume(type_id)
        if cached is not None:
            results[type_id] = cached
            continue
        to_fetch.append(type_id)

    if not to_fetch:
        return results

    client = ESIClient()

    def _fetch(type_id: int) -> Optional[float]:
        try:
            return client.get_packaged_volume(type_id)
        except Exception:  # noqa: BLE001 - best-effort; one hiccup shouldn't fail the whole batch
            return None

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_fetch, tid): tid for tid in to_fetch}
        for future in as_completed(futures):
            tid = futures[future]
            packaged = future.result()
            if packaged is not None:
                storage.set_cached_packaged_volume(tid, packaged)
                results[tid] = packaged
            else:
                results[tid] = sde_volume_by_id[tid]
    return results


@dataclass
class OrderStats:
    """Summary stats for one side (buy/sell) of an order book - a robust
    price percentile plus total listed volume, see _summarize_orders."""
    sell_percentile: Optional[float]
    sell_volume: float
    buy_percentile: Optional[float]
    buy_volume: float


class ESIClient:
    # Class-level (not per-instance): ESI's error limit is enforced per
    # calling IP by CCP, not per ESIClient object, and callers construct a
    # fresh ESIClient() freely - an instance-level counter wouldn't see
    # requests made through a different instance. ESI's own best-practices
    # doc says a well-behaved client should watch X-Esi-Error-Limit-Remain
    # and back off *before* it runs out, since "once you reach the error
    # limit, all your requests are automatically discarded until the end of
    # the time frame" - reacting to a 420 after the fact is too late.
    _error_limit_lock = threading.Lock()
    _error_limit_remain: int = 100
    _error_limit_reset_at: float = 0.0  # unix timestamp the current window ends

    # Class-level, same reasoning: a fresh ESIClient() per call site means an
    # instance-level cache would never actually hit, and these two endpoints
    # each return the *entire* catalog (every published type's adjusted
    # price; every solar system's cost indices) in one large response.
    _prices_indices_lock = threading.Lock()
    _adjusted_prices_cache: Optional[dict[int, float]] = None
    _adjusted_prices_cache_at: float = 0.0
    _cost_indices_cache: Optional[dict[int, dict[str, float]]] = None
    _cost_indices_cache_at: float = 0.0

    # Short-TTL order-book caches, so two near-simultaneous reads of the same
    # region/structure book (e.g. a shortlist refresh immediately followed by
    # an undercut check, which independently re-downloads the whole structure
    # book) don't each pay a full live fetch. Per-key lock rather than one
    # shared lock: two callers racing on a cold cache should serialize on
    # *that key* only, never block an unrelated key's fetch. 30s, not the
    # 60s a market-wide price snapshot would get - an order book moves
    # faster, and this only needs to collapse back-to-back reads, not serve
    # stale numbers into a real buy/sell decision.
    _ORDER_BOOK_CACHE_TTL = 30  # seconds
    _region_order_stats_cache: dict[tuple[int, int], list[dict]] = {}
    _region_order_stats_cache_at: dict[tuple[int, int], float] = {}
    _region_order_stats_locks: dict[tuple[int, int], threading.Lock] = {}
    _structure_book_cache: dict[int, list[dict]] = {}
    _structure_book_cache_at: dict[int, float] = {}
    _structure_book_locks: dict[int, threading.Lock] = {}
    _order_book_locks_guard = threading.Lock()  # protects creation of a new per-key lock only, never held during a fetch

    def __init__(self, cfg: TradingConfig = TRADING_CONFIG, tokens: Optional[TokenManager] = None):
        self.cfg = cfg
        self.tokens = tokens or TokenManager()
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    @classmethod
    def clear_price_caches(cls) -> None:
        """Forces the next get_adjusted_prices/get_system_cost_indices call
        (on *any* instance - these caches are class-wide) to re-fetch."""
        with cls._prices_indices_lock:
            cls._adjusted_prices_cache = None
            cls._adjusted_prices_cache_at = 0.0
            cls._cost_indices_cache = None
            cls._cost_indices_cache_at = 0.0

    @classmethod
    def clear_order_book_caches(cls) -> None:
        """Forces the next region_orders_raw/structure_orders_raw call (for
        every region_id/structure_id - class-wide) to re-fetch."""
        with cls._order_book_locks_guard:
            cls._region_order_stats_cache.clear()
            cls._region_order_stats_cache_at.clear()
            cls._structure_book_cache.clear()
            cls._structure_book_cache_at.clear()

    @classmethod
    def _lock_for_key(cls, locks: dict, key) -> threading.Lock:
        with cls._order_book_locks_guard:
            if key not in locks:
                locks[key] = threading.Lock()
            return locks[key]

    # ------------------------------------------------------------- low level
    @classmethod
    def _await_error_budget(cls) -> None:
        """Proactively sleeps if the last-observed error-limit window is
        nearly exhausted, instead of waiting to get 420'd first."""
        with cls._error_limit_lock:
            remaining_window = cls._error_limit_reset_at - time.time()
            if cls._error_limit_remain <= 2 and remaining_window > 0:
                wait = remaining_window
            else:
                wait = 0.0
        if wait > 0:
            time.sleep(wait)

    @classmethod
    def _record_error_budget(cls, resp: requests.Response) -> None:
        remain = resp.headers.get("X-Esi-Error-Limit-Remain")
        reset = resp.headers.get("X-Esi-Error-Limit-Reset")
        if remain is None or reset is None:
            return
        with cls._error_limit_lock:
            cls._error_limit_remain = int(remain)
            cls._error_limit_reset_at = time.time() + int(reset)

    def _get_response(self, path_or_url: str, params: Optional[dict] = None,
                      auth_role: Optional[str] = None, retries: int = 3) -> requests.Response:
        """Shared retry/backoff core for both _get and _get_all_pages - returns
        the raw Response (status 200 guaranteed) so callers needing headers
        (X-Pages, for pagination) aren't stuck re-implementing retry logic
        themselves."""
        url = path_or_url if path_or_url.startswith("http") else f"{self.cfg.esi_base}{path_or_url}"
        headers = {}
        if auth_role:
            headers.update(self.tokens.auth_header(auth_role))
        for attempt in range(1, retries + 1):
            self._await_error_budget()
            resp = self.session.get(url, params=params, headers=headers, timeout=30)
            self._record_error_budget(resp)
            if resp.status_code == 200:
                return resp
            if resp.status_code in (420, 429):  # ESI error-limited (420) or plain HTTP rate-limited (429)
                time.sleep(self._retry_after_seconds(resp, attempt))
                continue
            if resp.status_code in (500, 502, 503, 504) and attempt < retries:
                time.sleep(attempt * 1.5)
                continue
            raise ESIError(f"HTTP {resp.status_code} for {url}: {resp.text[:300]}")
        raise ESIError(f"Exhausted retries for {url}")

    @staticmethod
    def _retry_after_seconds(resp: requests.Response, attempt: int) -> float:
        """How long to back off before retrying a 420/429 - prefers the
        response's own Retry-After header (present on real 429s: a burst-rate
        limit distinct from ESI's own 420 error-limit mechanism, seen on the
        market-order endpoint group under concurrent load) over the same
        blind exponential backoff used when it's absent (420 responses don't
        reliably send one)."""
        retry_after = resp.headers.get("Retry-After")
        if retry_after is not None:
            try:
                return float(retry_after)
            except ValueError:
                pass
        return min(2 ** attempt, 20)

    def _get(self, path_or_url: str, params: Optional[dict] = None,
             auth_role: Optional[str] = None, retries: int = 3) -> Any:
        return self._get_response(path_or_url, params, auth_role, retries).json()

    def _post_response(self, path: str, json_body: Any, params: Optional[dict] = None,
                       retries: int = 3) -> requests.Response:
        """POST counterpart to _get_response - same retry/backoff behavior
        (420/429 rate-limited, 500/502/503/504) and same ESIError on
        exhaustion, so a transient error while resolving names doesn't
        silently drop a whole chunk of up to 1000 ids."""
        url = f"{self.cfg.esi_base}{path}"
        for attempt in range(1, retries + 1):
            self._await_error_budget()
            resp = self.session.post(url, json=json_body, params=params, timeout=30)
            self._record_error_budget(resp)
            if resp.status_code == 200:
                return resp
            if resp.status_code in (420, 429):
                time.sleep(self._retry_after_seconds(resp, attempt))
                continue
            if resp.status_code in (500, 502, 503, 504) and attempt < retries:
                time.sleep(attempt * 1.5)
                continue
            raise ESIError(f"HTTP {resp.status_code} for {url}: {resp.text[:300]}")
        raise ESIError(f"Exhausted retries for {url}")

    def _get_all_pages(self, path: str, params: Optional[dict] = None, auth_role: Optional[str] = None,
                       max_workers: int = 6) -> list:
        """Page 1 has to be fetched alone first (its X-Pages header is the only
        way to learn how many pages exist); pages 2..N are then fetched
        concurrently, since nothing about page N depends on page N-1's
        *content* - only on that count. A character with 9k+ assets pages at
        ESI's standard 1000-items/page into ~10 round-trips for that one call
        alone, and a full sync does this for assets/industry-jobs/blueprints.
        Results are reassembled in page order, matching a sequential fetch."""
        base_params = dict(params or {})
        first_params = dict(base_params, page=1)
        resp = self._get_response(path, first_params, auth_role)
        first_chunk = resp.json()
        if not first_chunk:
            return []
        total_pages = int(resp.headers.get("X-Pages", "1"))
        if total_pages <= 1:
            return first_chunk

        def _fetch_page(page: int) -> list:
            page_params = dict(base_params, page=page)
            return self._get_response(path, page_params, auth_role).json()

        pages: dict[int, list] = {1: first_chunk}
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_fetch_page, page): page
                       for page in range(2, total_pages + 1)}
            for future in as_completed(futures):
                pages[futures[future]] = future.result()

        out: list = []
        for page in range(1, total_pages + 1):
            out.extend(pages[page])
        return out

    # -------------------------------------------------- market groups / types
    def list_market_group_ids(self) -> list[int]:
        return self._get("/markets/groups/", params={"datasource": "tranquility"})

    def get_market_group(self, group_id: int) -> dict:
        return self._get(f"/markets/groups/{group_id}/",
                         params={"datasource": "tranquility", "language": "en"})

    def get_type_info(self, type_id: int) -> dict:
        return self._get(f"/universe/types/{type_id}/",
                         params={"datasource": "tranquility", "language": "en"})

    def get_meta_level(self, type_id: int) -> Optional[int]:
        """Fetches just the metaLevel for a type (extra request - prefer
        extract_meta_level() on a get_type_info() response you already have)."""
        return extract_meta_level(self.get_type_info(type_id))

    def get_packaged_volume(self, type_id: int) -> Optional[float]:
        """Packaged (repackaged/cargo) volume for `type_id`, mainly relevant
        for ships - not in Fuzzwork's SDE CSVs, only ESI exposes it per type,
        and only for types where it differs from the flight volume (absent
        otherwise, callers should fall back to the SDE `volume` field)."""
        info = self.get_type_info(type_id)
        value = info.get("packaged_volume")
        return float(value) if value is not None else None

    def corporation_structures(self, corporation_id: int, auth_role: str) -> list[dict]:
        """Lists every Upwell structure the corp owns (name included directly
        in the response) - requires esi-corporations.read_structures.v1 and
        the Station_Manager role in that corporation, but crucially does NOT
        need the querying character to have personally docked at/visited any
        of them, unlike get_structure_name below. Prefer this as the primary
        structure-name-resolution path: it resolves every corp-owned
        structure in one call instead of depending on one character's docking
        history per structure."""
        return self._get_all_pages(f"/corporations/{corporation_id}/structures/",
                                   params={"datasource": "tranquility"}, auth_role=auth_role)

    def get_structure_name(self, structure_id: int, auth_role: str) -> dict:
        """Resolves a player-owned Upwell structure's info (never in the SDE,
        unlike NPC stations) - requires esi-universe.read_structures.v1 and a
        character that can actually "see" the structure (has docking rights/
        has been there), so a 403 from a character without access isn't
        necessarily fatal - a caller should retry with a different character
        before giving up. Returns the raw ESI dict (at least `name`, usually
        also `solar_system_id`) rather than unpacking it here, so a caller
        needing more of the response later doesn't need another round trip."""
        return self._get(f"/universe/structures/{structure_id}/",
                         params={"datasource": "tranquility"}, auth_role=auth_role)

    # -------------------------------------------------------------- markets
    def region_orders_raw(self, region_id: int, type_id: int) -> list[dict]:
        """The region's full, unsummarized order book for one type_id - every
        order (both is_buy_order true/false), with location_id so a caller
        can further scope down to one specific station (knowing *which*
        competing order beat a given own order, not just the best price).

        Uses _get_all_pages defensively: /markets/{region_id}/orders/ is a
        genuinely paginated endpoint per the ESI spec even with a type_id
        filter applied - a single type_id in a normal region is very unlikely
        to exceed one page in practice, but nothing in the spec guarantees
        that.

        Cached class-wide for _ORDER_BOOK_CACHE_TTL seconds, keyed by
        (region_id, type_id) - see that constant's own comment."""
        key = (region_id, type_id)
        with self._lock_for_key(self._region_order_stats_locks, key):
            cached_at = self._region_order_stats_cache_at.get(key, 0.0)
            if key in self._region_order_stats_cache and (time.time() - cached_at) < self._ORDER_BOOK_CACHE_TTL:
                return self._region_order_stats_cache[key]

            orders = self._get_all_pages(
                f"/markets/{region_id}/orders/",
                params={"datasource": "tranquility", "type_id": type_id, "order_type": "all"})
            self._region_order_stats_cache[key] = orders
            self._region_order_stats_cache_at[key] = time.time()
            return orders

    def region_order_stats(self, region_id: int, type_id: int) -> OrderStats:
        """Regional order-book stats for one type_id - summarizes
        region_orders_raw (see that method's own docstring for the
        pagination/caching details, shared by both)."""
        return _summarize_orders(self.region_orders_raw(region_id, type_id))

    def region_order_stats_bulk(self, region_id: int, type_ids: list[int],
                                max_workers: int = 10) -> dict[int, OrderStats]:
        """Same as region_order_stats, but for many type_ids concurrently. ESI
        has no multi-type_id batch endpoint for regional orders (each type_id
        needs its own call), but the calls are independent and each already
        server-side filtered (small payload) - running them on a small thread
        pool cuts wall-clock time roughly by max_workers vs. one-by-one, since
        the cost here is almost entirely network round-trip latency. A failed
        lookup for one type_id doesn't affect the others (falls back to an
        empty OrderStats)."""
        results: dict[int, OrderStats] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(self.region_order_stats, region_id, tid): tid
                       for tid in type_ids}
            for future in as_completed(futures):
                tid = futures[future]
                try:
                    results[tid] = future.result()
                except ESIError:
                    results[tid] = OrderStats(None, 0.0, None, 0.0)
        return results

    def structure_order_stats(self, structure_id: int, type_id: int, auth_role: str) -> OrderStats:
        """Player-structure order-book stats for one type_id.
        Requires a token with esi-markets.structure_markets.v1 for a character
        docked at / with access to that structure. Prefer
        structure_order_stats_bulk() when you need this for many type_ids -
        ESI's structure-orders endpoint has no type_id filter, so this method
        downloads the *entire* structure order book on every call."""
        orders = self._get_all_pages(f"/markets/structures/{structure_id}/",
                                     params={"datasource": "tranquility"}, auth_role=auth_role)
        orders = [o for o in orders if o.get("type_id") == type_id]
        return _summarize_orders(orders)

    def structure_orders_raw(self, structure_id: int, auth_role: str) -> list[dict]:
        """The structure's full, unsummarized order book (every order, every
        type_id) - the shared raw fetch behind structure_order_stats_bulk and
        any undercut check, both of which need the whole book anyway (ESI's
        /markets/structures/{id}/ endpoint has no type_id filter) and would
        otherwise each re-download it separately.

        Cached class-wide for _ORDER_BOOK_CACHE_TTL seconds, keyed by
        structure_id - the book is the same real data regardless of which
        authorized character's token fetched it."""
        with self._lock_for_key(self._structure_book_locks, structure_id):
            cached_at = self._structure_book_cache_at.get(structure_id, 0.0)
            if structure_id in self._structure_book_cache and (time.time() - cached_at) < self._ORDER_BOOK_CACHE_TTL:
                return list(self._structure_book_cache[structure_id])

            orders = self._get_all_pages(f"/markets/structures/{structure_id}/",
                                         params={"datasource": "tranquility"}, auth_role=auth_role)
            self._structure_book_cache[structure_id] = orders
            self._structure_book_cache_at[structure_id] = time.time()
            return list(orders)

    def structure_order_stats_bulk(self, structure_id: int, type_ids: list[int],
                                   auth_role: str) -> dict[int, OrderStats]:
        """Downloads the structure's full order book exactly once (ESI's
        /markets/structures/{id}/ endpoint has no type_id filter, unlike
        regional orders), then groups locally per type_id - instead of one
        full-book re-download per item of interest."""
        orders = self.structure_orders_raw(structure_id, auth_role)
        by_type: dict[int, list[dict]] = {}
        for o in orders:
            by_type.setdefault(o.get("type_id"), []).append(o)
        return {tid: _summarize_orders(by_type.get(tid, [])) for tid in type_ids}

    def structure_order_stats_bulk_or_goonmetrics(
        self, structure_id: int, type_ids: list[int], auth_role: Optional[str],
        goonmetrics_market_slug: Optional[str],
    ) -> tuple[dict[int, OrderStats], bool]:
        """Failsafe wrapper around structure_order_stats_bulk: tries the real
        structure order book first when a seller/producer character
        (`auth_role`) is logged in, falling back to a Goonmetrics
        current-price snapshot whenever that's unavailable - no character
        logged in at all, or the ESI call itself failing (lost docking
        access, ESI outage). Returns (stats_by_id, used_fallback) so callers
        can surface the degraded-precision warning to the user.

        The synthesized OrderStats only ever has sell_percentile/
        buy_percentile populated, from Goonmetrics' best-ask/best-bid
        snapshot - not a real percentile over actual orders - and
        sell_volume/buy_volume are always 0.0, since the current-price
        endpoint carries no order-book depth at all. Good enough for
        "roughly what's this worth"; deliberately NOT for anything comparing
        against real competing orders (an undercut check), where a fallback
        could silently report "not undercut" when a real order says
        otherwise - worse than a hard failure.

        Raises ESIError (the same type the real call raises, so callers need
        no extra handling) only when BOTH sources are unavailable."""
        last_error: Optional[Exception] = None
        if auth_role is not None:
            try:
                return self.structure_order_stats_bulk(structure_id, type_ids, auth_role=auth_role), False
            except ESIError as e:
                last_error = e
        if goonmetrics_market_slug:
            from .goonmetrics_client import GoonmetricsClient  # local import: keeps esi_client<->goonmetrics_client uncoupled for callers that never hit this fallback
            try:
                prices = GoonmetricsClient(self.cfg).current_prices(goonmetrics_market_slug)
            except requests.RequestException as e:
                last_error = last_error or e
            else:
                wanted = set(type_ids)
                return {
                    p.type_id: OrderStats(sell_percentile=p.sell or None, sell_volume=0.0,
                                          buy_percentile=p.buy or None, buy_volume=0.0)
                    for p in prices if p.type_id in wanted
                }, True
        if auth_role is None:
            raise ESIError("No seller/producer character logged in, and no Goonmetrics fallback "
                           "market configured (structure_market_slug).")
        raise ESIError(f"{last_error} (No Goonmetrics fallback market configured either.)")

    def region_market_history(self, region_id: int, type_id: int) -> list[dict]:
        """Official ESI daily history for one type in one region."""
        return self._get(f"/markets/{region_id}/history/",
                         params={"datasource": "tranquility", "type_id": type_id})

    # ------------------------------------------------------------ industry
    def get_adjusted_prices(self, type_ids: Optional[list[int]] = None,
                            cache_seconds: float = 3600) -> dict[int, float]:
        """ESI has no per-type lookup, it returns every published type's
        adjusted price in one call - so fetch the full list (cached
        class-wide for `cache_seconds`, see _adjusted_prices_cache's own
        comment) and filter locally."""
        with ESIClient._prices_indices_lock:
            now = time.time()
            if (ESIClient._adjusted_prices_cache is None
                    or (now - ESIClient._adjusted_prices_cache_at) > cache_seconds):
                rows = self._get("/markets/prices/", params={"datasource": "tranquility"})
                ESIClient._adjusted_prices_cache = {
                    row["type_id"]: row["adjusted_price"] for row in rows if "adjusted_price" in row
                }
                ESIClient._adjusted_prices_cache_at = now
            cache = ESIClient._adjusted_prices_cache
        if type_ids is None:
            return dict(cache)
        return {t: cache.get(t, 0) for t in type_ids}

    def get_system_cost_indices(self, system_id: int,
                                activities: tuple = ("manufacturing", "reaction", "invention"),
                                cache_seconds: float = 21600) -> dict[str, float]:
        """ESI returns every solar system's cost indices in one call - fetch
        once (cached class-wide for `cache_seconds`), then look up `system_id`
        and filter to `activities`. Missing activities are omitted."""
        with ESIClient._prices_indices_lock:
            now = time.time()
            if ESIClient._cost_indices_cache is None or (now - ESIClient._cost_indices_cache_at) > cache_seconds:
                rows = self._get("/industry/systems/", params={"datasource": "tranquility"})
                ESIClient._cost_indices_cache = {
                    row["solar_system_id"]: {
                        idx["activity"]: idx["cost_index"] for idx in row.get("cost_indices", [])
                    }
                    for row in rows
                }
                ESIClient._cost_indices_cache_at = now
            system_indices = ESIClient._cost_indices_cache.get(system_id)
        if system_indices is None:
            raise ESIError(f"No cost indices found for solar system {system_id}.")
        return {a: system_indices[a] for a in activities if a in system_indices}

    # ------------------------------------------------------------ characters
    def character_search(self, name: str) -> Optional[int]:
        """Resolves a character name to its ID via a strict (exact, case-
        insensitive) match against /universe/ids/."""
        result = self._post_universe_ids([name])
        chars = result.get("characters") or []
        for c in chars:
            if c["name"].lower() == name.lower():
                return c["id"]
        return chars[0]["id"] if chars else None

    def resolve_system_id(self, name: str) -> Optional[int]:
        """Resolves a solar system name (e.g. "Jita", "Amarr") to its ID via
        the same /universe/ids/ endpoint character_search uses. Solar system
        names are unique and never change, so callers are expected to resolve
        once (e.g. when a setting is saved) and store the ID, not re-resolve
        on every calculation."""
        result = self._post_universe_ids([name])
        systems = result.get("systems") or []
        for s in systems:
            if s["name"].lower() == name.strip().lower():
                return s["id"]
        return None

    def _post_universe_ids(self, names: list[str]) -> dict:
        return self._post_response("/universe/ids/", names, params={"datasource": "tranquility"}).json()

    def character_orders(self, character_id: int, auth_role: str) -> list[dict]:
        """This character's open market orders (buy and sell)."""
        return self._get(f"/characters/{character_id}/orders/",
                         params={"datasource": "tranquility"}, auth_role=auth_role)

    def corporation_orders(self, corporation_id: int, auth_role: str) -> list[dict]:
        """Requires esi-markets.read_corporation_orders.v1 + the character
        holding the Accountant or Trader role in that corporation. Stock
        sitting in a corp hangar (see corporation_assets) is often listed for
        sale via a *corp* order funded by the corp wallet, not a personal one
        - without this, such stock looks unlisted even when it's for sale."""
        return self._get_all_pages(f"/corporations/{corporation_id}/orders/",
                                   params={"datasource": "tranquility"}, auth_role=auth_role)

    def character_public_info(self, character_id: int) -> dict:
        """Public endpoint, no auth - used to resolve corporation_id for corp-level calls."""
        return self._get(f"/characters/{character_id}/", params={"datasource": "tranquility"})

    def corporation_public_info(self, corporation_id: int) -> dict:
        """Public endpoint, no auth - used to resolve a corp's name for display."""
        return self._get(f"/corporations/{corporation_id}/", params={"datasource": "tranquility"})

    # ------------------------------------------------- assets / industry / BPs
    def character_assets(self, character_id: int, auth_role: str) -> list[dict]:
        """Requires esi-assets.read_assets.v1."""
        return self._get_all_pages(f"/characters/{character_id}/assets/",
                                   params={"datasource": "tranquility"}, auth_role=auth_role)

    def corporation_assets(self, corporation_id: int, auth_role: str) -> list[dict]:
        """Requires esi-assets.read_corporation_assets.v1 + the character holding
        the Director role in that corporation."""
        return self._get_all_pages(f"/corporations/{corporation_id}/assets/",
                                   params={"datasource": "tranquility"}, auth_role=auth_role)

    def character_industry_jobs(self, character_id: int, auth_role: str) -> list[dict]:
        """Requires esi-industry.read_character_jobs.v1. Confirmed against the
        ESI spec: unlike the corporation variant below, this endpoint has no
        `page` param / X-Pages header at all - plain _get (not _get_all_pages,
        which would still work by accident since it defaults to one page, but
        implies pagination capability this endpoint doesn't have)."""
        return self._get(f"/characters/{character_id}/industry/jobs/",
                         params={"datasource": "tranquility", "include_completed": "false"},
                         auth_role=auth_role)

    def corporation_industry_jobs(self, corporation_id: int, auth_role: str) -> list[dict]:
        """Requires esi-industry.read_corporation_jobs.v1 + Director role."""
        return self._get_all_pages(f"/corporations/{corporation_id}/industry/jobs/",
                                   params={"datasource": "tranquility", "include_completed": "false"},
                                   auth_role=auth_role)

    def character_blueprints(self, character_id: int, auth_role: str) -> list[dict]:
        """Requires esi-characters.read_blueprints.v1."""
        return self._get_all_pages(f"/characters/{character_id}/blueprints/",
                                   params={"datasource": "tranquility"}, auth_role=auth_role)

    def corporation_blueprints(self, corporation_id: int, auth_role: str) -> list[dict]:
        """Requires esi-corporations.read_blueprints.v1 + Director role."""
        return self._get_all_pages(f"/corporations/{corporation_id}/blueprints/",
                                   params={"datasource": "tranquility"}, auth_role=auth_role)

    # ------------------------------------------------------------- contracts
    def character_contracts(self, character_id: int, auth_role: str) -> list[dict]:
        """Requires esi-contracts.read_character_contracts.v1. Every contract
        the character is issuer, acceptor, or assignee of, only up to 30 days
        old or still `in_progress`/`outstanding` (ESI's own retention rule,
        not filtered here)."""
        return self._get_all_pages(f"/characters/{character_id}/contracts/",
                                   params={"datasource": "tranquility"}, auth_role=auth_role)

    def corporation_contracts(self, corporation_id: int, auth_role: str) -> list[dict]:
        """Requires esi-contracts.read_corporation_contracts.v1. Same
        retention/shape as character_contracts, corp-wide."""
        return self._get_all_pages(f"/corporations/{corporation_id}/contracts/",
                                   params={"datasource": "tranquility"}, auth_role=auth_role)

    def character_contract_items(self, character_id: int, contract_id: int, auth_role: str) -> list[dict]:
        """Requires esi-contracts.read_character_contracts.v1 (same scope as
        character_contracts, not a separate one). A contract's item list never
        changes after creation (ESI guarantee), so a caller only ever needs to
        fetch items once per contract_id."""
        return self._get(f"/characters/{character_id}/contracts/{contract_id}/items/",
                         params={"datasource": "tranquility"}, auth_role=auth_role)

    def corporation_contract_items(self, corporation_id: int, contract_id: int, auth_role: str) -> list[dict]:
        """Requires esi-contracts.read_corporation_contracts.v1."""
        return self._get(f"/corporations/{corporation_id}/contracts/{contract_id}/items/",
                         params={"datasource": "tranquility"}, auth_role=auth_role)

    def character_wallet_transactions(self, character_id: int, auth_role: str,
                                      from_id: Optional[int] = None) -> list[dict]:
        """This character's wallet transaction history. Returns up to 2500
        transactions (ESI's fixed per-call cap for this endpoint), most recent
        first. This endpoint uses cursor pagination via `from_id` (pass the
        oldest transaction_id from a previous call to page further back in
        time) rather than the page/X-Pages scheme _get_all_pages handles, so
        it can't use that helper - a caller loops it until it has covered the
        lookback window it wants."""
        params = {"datasource": "tranquility"}
        if from_id is not None:
            params["from_id"] = from_id
        return self._get(f"/characters/{character_id}/wallet/transactions/",
                         params=params, auth_role=auth_role)

    def character_wallet_balance(self, character_id: int, auth_role: str) -> float:
        """This character's current ISK wallet balance (a bare float, not a
        list/object - ESI's own response shape for this endpoint). Same
        esi-wallet.read_character_wallet.v1 scope character_wallet_
        transactions already needs, no new grant required."""
        return self._get(f"/characters/{character_id}/wallet/",
                         params={"datasource": "tranquility"}, auth_role=auth_role)

    def character_wallet_journal(self, character_id: int, auth_role: str) -> list[dict]:
        """This character's wallet journal (every ISK-moving event, not just
        market trades - contract payments, bounties, taxes, ...). Standard
        page/X-Pages pagination (_get_all_pages), unlike character_wallet_
        transactions' from_id cursor scheme above - confirmed against ESI's
        own OpenAPI spec, not assumed. The point of it for trade
        reconciliation: a wallet/transactions entry's own `journal_ref_id`
        links 1:1 to the journal entry recording that same sale (`ref_type:
        "market_transaction"`), whose `amount` is the real ISK credited
        *after* sales tax. Requires esi-wallet.read_character_wallet.v1 - the
        same scope character_wallet_transactions already needs."""
        return self._get_all_pages(f"/characters/{character_id}/wallet/journal/",
                                   params={"datasource": "tranquility"}, auth_role=auth_role)

    def character_skills(self, character_id: int, auth_role: str) -> dict:
        """Requires esi-skills.read_skills.v1. Returns {"skills": [{"skill_id",
        "active_skill_level", ...}], "total_sp", ...} - used to derive industry
        job-slot counts."""
        return self._get(f"/characters/{character_id}/skills/",
                         params={"datasource": "tranquility"}, auth_role=auth_role)

    def resolve_names(self, ids: list[int]) -> dict[int, str]:
        """Batch id->name resolution via POST /universe/names/ (public, no
        auth, up to 1000 ids/call). Unknown/invalid ids are silently omitted
        (ESI itself omits them from the response body, not this method's
        doing). A transient error retries via _post_response rather than
        silently dropping a whole chunk of up to 1000 names."""
        if not ids:
            return {}
        out: dict[int, str] = {}
        unique_ids = list(dict.fromkeys(ids))
        for i in range(0, len(unique_ids), 1000):
            chunk = unique_ids[i:i + 1000]
            resp = self._post_response("/universe/names/", chunk, params={"datasource": "tranquility"})
            for entry in resp.json():
                out[entry["id"]] = entry["name"]
        return out


def _summarize_orders(orders: list[dict]) -> OrderStats:
    """percentile = 5th percentile sell price / 95th percentile buy price (a
    robust proxy for "realistic" best price, ignoring outlier orders), volume
    = sum of volume_remain across all orders on that side."""
    sells = sorted((o["price"] for o in orders if not o["is_buy_order"]))
    buys = sorted((o["price"] for o in orders if o["is_buy_order"]), reverse=True)
    sell_vol = sum(o["volume_remain"] for o in orders if not o["is_buy_order"])
    buy_vol = sum(o["volume_remain"] for o in orders if o["is_buy_order"])
    return OrderStats(
        sell_percentile=_percentile(sells, 0.05),
        sell_volume=sell_vol,
        buy_percentile=_percentile(buys, 0.05),
        buy_volume=buy_vol,
    )


def _percentile(sorted_values: list[float], pct: float) -> Optional[float]:
    """`sorted_values` must already be sorted ascending (both call sites in
    _summarize_orders sort before calling) - not re-sorted here since both
    callers already have the sort order they need for their own side (sells
    ascending for a low percentile, buys descending so the same `pct` picks
    from the top). Nearest-rank, not interpolated: index = pct fraction into
    the list, clamped to the last element."""
    if not sorted_values:
        return None
    idx = min(int(len(sorted_values) * pct), len(sorted_values) - 1)
    return sorted_values[idx]
