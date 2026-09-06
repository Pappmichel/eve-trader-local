"""Central registry for every ESI/Goonmetrics-calling "sync bundle" in this
app, and the single gateway the "Update Data" dialog
(gui/dialogs/esi_update_dialog.py) uses to run them.

Every network call this app makes against ESI or Goonmetrics for game data
(market orders, wallet, assets, contracts, industry jobs, skills, market
prices, ...) now happens *only* inside one of the nine scope-group bundle
functions wired up in `SCOPES` below. Every other `do_*`/view in the app
reads back whatever the relevant bundle last cached (`storage.
order_book_cache`, `storage.result_cache`, or each tool's own snapshot
tables) and never constructs an `ESIClient`/`GoonmetricsClient` of its own.

Grouping is by *scope* (the kind of data - Assets, Contracts, Skills, ...),
not by tool: several tools can share one scope-group's cache (e.g. Trading's
shortlist, Refining's ore/mineral prices, Station Trading's candidate
discovery and Doctrine's/Production's own price universes are all "Market
Prices"; Production's and Doctrine's asset syncs are both "Assets"), and one
tool's needs can be spread across several scope-groups (e.g. Trading's
shortlist evaluation reads Market Orders + Assets + Market Prices, three
independently-triggerable/independently-timed caches). This mirrors
jEveAssets' own "Update" window - confirmed with the user over an earlier,
coarser "one row per tool" grouping, specifically so scopes that genuinely
don't share a cache-freshness need (e.g. slow-changing Blueprints vs.
fast-changing Market Prices) aren't forced onto one shared timer.

Two consequences worth knowing about:

- Production's Assets/Industry Jobs/Blueprints scopes all call the *same*
  underlying `production.actions._sync_assets_jobs_blueprints` (which wraps
  `production.esi_sync.sync_esi`) - ESI has no cheaper way to fetch just one
  of the three per character, so checking any one of these three rows in the
  dialog refreshes all three together. A deliberate, documented
  simplification: independent *timers* for these three specifically aren't
  worth a 3x-slower sync.
- An item outside a scope's own known type-id universe (e.g. a Reprocessing
  Quote paste of something never priced by any Ore & Minerals/Trading sync)
  simply has no cached price until some sync happens to cover it - an
  accepted limitation of caching everything, not a bug (ESI's regional-orders
  endpoint has no whole-market fetch, only per-type_id, so nothing can
  pre-cache a truly arbitrary future lookup). Goonmetrics-sourced market data
  (candidate discovery, region price history) isn't an ESI scope at all, but
  lives under the "Market Prices" group too, since it's the same kind of
  external market-data fetch."""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Callable, Optional

from . import actions, storage
from .doctrine import esi_sync as doctrine_esi_sync
from .errors import ActionError
from .production import actions as production_actions
from .refining import actions as refining_actions
from .station_trading import actions as station_trading_actions

log = logging.getLogger("eve_trader_local.esi_update")

_INTERVALS_SETTINGS_SCOPE = "esi_update_intervals"


@dataclass
class UpdateScope:
    key: str
    label: str
    run: Callable[[], dict]
    default_interval_seconds: int


# ------------------------------------------------------------- scope bundles
# Each bundle below either delegates straight to one already-isolated tool
# function, or - where a scope spans more than one tool - isolates each
# tool's own contribution in its own try/except, the same "one failure must
# not block the others" reasoning every tool's own bundle already used
# before this module existed.
def _sync_market_orders() -> dict:
    """Trading's own seller sell-orders (feeds shortlist evaluation) plus
    both tools' undercut/unlisted-stock checks."""
    result: dict = {}
    try:
        result["own_sell_orders"] = actions._cache_own_sell_orders()
    except ActionError as e:
        result["own_sell_orders"] = {"error": str(e)}
    result.update(actions._cache_unlisted_stock_and_undercut())
    try:
        result["station_trading_undercut"] = station_trading_actions._cache_undercut()
    except ActionError as e:
        result["station_trading_undercut"] = {"error": str(e)}
    return result


def _sync_wallet() -> dict:
    result: dict = {}
    try:
        result["balances"] = actions._cache_wallet_balances()
    except ActionError as e:
        result["balances"] = {"error": str(e)}
    try:
        result["transactions"] = actions._cache_wallet_transactions()
    except ActionError as e:
        result["transactions"] = {"error": str(e)}
    try:
        result["reconcile_trades"] = actions._cache_reconcile_trades()
    except ActionError as e:
        result["reconcile_trades"] = {"error": str(e)}
    return result


def _sync_assets() -> dict:
    """Trading's buyer-side "already covered" check, plus Production's and
    Doctrine's own character/corp asset syncs - three independent token
    sets, all fetching the same *kind* of ESI data."""
    result: dict = {}
    try:
        result["trading_buyer_covered"] = actions._cache_buyer_already_covered()
    except ActionError as e:
        result["trading_buyer_covered"] = {"error": str(e)}
    try:
        result["production"] = production_actions._sync_assets_jobs_blueprints()
    except ActionError as e:
        result["production"] = {"error": str(e)}
    try:
        result["doctrine"] = doctrine_esi_sync.sync_assets()
    except ActionError as e:
        result["doctrine"] = {"error": str(e)}
    return result


def _sync_industry_jobs() -> dict:
    return production_actions._sync_assets_jobs_blueprints()


def _sync_blueprints() -> dict:
    return production_actions._sync_assets_jobs_blueprints()


def _sync_contracts() -> dict:
    return doctrine_esi_sync.sync_contracts()


def _sync_skills() -> dict:
    return station_trading_actions._cache_skill_summary()


def _sync_market_prices() -> dict:
    """The big shared one: every tool's structure/Jita/Goonmetrics price
    need, all writing into the same storage.order_book_cache table (see that
    table's own docstring) - one fetch benefits every reader regardless of
    which tool triggered it."""
    result: dict = {}
    try:
        result["trading_shortlist"] = actions._cache_shortlist_market_prices()
    except ActionError as e:
        result["trading_shortlist"] = {"error": str(e)}
    try:
        result["trading_candidates"] = actions.do_find_new_candidates(safe=True)
    except ActionError as e:
        result["trading_candidates"] = {"error": str(e)}
    try:
        result["refining"] = refining_actions._cache_ore_mineral_prices()
    except ActionError as e:
        result["refining"] = {"error": str(e)}
    try:
        result["station_trading"] = station_trading_actions.do_refresh_shortlist()
    except ActionError as e:
        result["station_trading"] = {"error": str(e)}
    try:
        result["production"] = production_actions._sync_market_prices()
    except ActionError as e:
        result["production"] = {"error": str(e)}
    try:
        result["doctrine"] = doctrine_esi_sync.refresh_shopping_list_prices()
    except ActionError as e:
        result["doctrine"] = {"error": str(e)}
    return result


def _sync_cost_indices() -> dict:
    return production_actions._sync_cost_indices()


# Interval defaults: market-driven scopes (Market Orders, Market Prices) get
# a short window since prices/orders move fast; Wallet is checked about as
# often for the same reason (a balance/recent-transaction view a user
# actually cares about being current). Character-data scopes (Assets,
# Industry Jobs, Contracts) get a medium window; Blueprints and Skills get
# the longest since they change the least often. Any of these is
# overridable per scope via set_interval_seconds (Settings), not a fixed
# rule.
SCOPES: list[UpdateScope] = [
    UpdateScope("market_orders", "Market Orders", _sync_market_orders, 300),
    UpdateScope("wallet", "Wallet", _sync_wallet, 300),
    UpdateScope("assets", "Assets", _sync_assets, 1800),
    UpdateScope("industry_jobs", "Industry Jobs", _sync_industry_jobs, 900),
    UpdateScope("blueprints", "Blueprints", _sync_blueprints, 1800),
    UpdateScope("contracts", "Contracts", _sync_contracts, 900),
    UpdateScope("skills", "Skills", _sync_skills, 3600),
    UpdateScope("market_prices", "Market Prices", _sync_market_prices, 300),
    UpdateScope("cost_indices", "Cost Indices / Adjusted Prices", _sync_cost_indices, 1800),
]

_SCOPES_BY_KEY = {s.key: s for s in SCOPES}


def interval_seconds(key: str) -> int:
    """The effective cache interval for `key` - a user override saved via
    set_interval_seconds if one exists, else the scope's own default."""
    scope = _SCOPES_BY_KEY[key]
    overrides = storage.load_settings(_INTERVALS_SETTINGS_SCOPE)
    return int(overrides.get(key, scope.default_interval_seconds))


def set_interval_seconds(key: str, seconds: int) -> None:
    if key not in _SCOPES_BY_KEY:
        raise ActionError(f"Unknown update scope '{key}'.")
    if seconds <= 0:
        raise ActionError("Update interval must be a positive number of seconds.")
    storage.save_settings(_INTERVALS_SETTINGS_SCOPE, {key: seconds})


def last_synced_at(key: str) -> Optional[dt.datetime]:
    """Naive UTC datetime, regardless of whether the stored ISO string was
    itself naive or tz-aware - not every writer stamps the same way, so this
    normalizes both to the same shape rather than let a comparison against
    `now` below crash on a naive/aware mismatch."""
    raw = storage.get_esi_sync_time(key)
    if raw is None:
        return None
    parsed = dt.datetime.fromisoformat(raw)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return parsed


def next_allowed_at(key: str) -> Optional[dt.datetime]:
    """When `key` can be refreshed again, or None if it has never been
    synced (i.e. it's allowed right now)."""
    last = last_synced_at(key)
    if last is None:
        return None
    return last + dt.timedelta(seconds=interval_seconds(key))


def is_due(key: str, now: Optional[dt.datetime] = None) -> bool:
    allowed_at = next_allowed_at(key)
    if allowed_at is None:
        return True
    return (now or dt.datetime.utcnow()) >= allowed_at


@dataclass
class ScopeStatus:
    key: str
    label: str
    last_synced_at: Optional[dt.datetime]
    next_allowed_at: Optional[dt.datetime]
    due: bool


def all_status() -> list[ScopeStatus]:
    """One row per SCOPES entry, in order - what gui/dialogs/
    esi_update_dialog.py's table renders directly."""
    return [
        ScopeStatus(key=s.key, label=s.label, last_synced_at=last_synced_at(s.key),
                    next_allowed_at=next_allowed_at(s.key), due=is_due(s.key))
        for s in SCOPES
    ]


def _describe_status(status: ScopeStatus, *, prefix_label: bool) -> str:
    label_prefix = f"{status.label} data" if prefix_label else "Data"
    if status.last_synced_at is None:
        prefix = f"{status.label}: never" if prefix_label else "Never"
        return f"{prefix} synced - use App > Update Data... to fetch it."
    when = status.last_synced_at.strftime("%Y-%m-%d %H:%M UTC")
    if status.due:
        return f"{label_prefix} as of {when} (can update again now via App > Update Data...)."
    remaining = max(dt.timedelta(0), status.next_allowed_at - dt.datetime.utcnow())
    total_seconds = int(remaining.total_seconds())
    return f"{label_prefix} as of {when} (updates again in {total_seconds // 60}m {total_seconds % 60}s)."


def describe(key: str) -> str:
    """One-line human status for `key` - what every view's small staleness
    label (gui/views/base.py's `_add_staleness_label`) and this app's
    "Update Data..." dialog both build off of, so the wording stays
    consistent wherever a view tells the user how fresh its data is and
    where to refresh it."""
    status = next((s for s in all_status() if s.key == key), None)
    if status is None:
        return "Never synced - use App > Update Data... to fetch it."
    return _describe_status(status, prefix_label=False)


def describe_many(keys: list[str]) -> str:
    """Same as describe(), for a view whose data spans more than one scope
    (e.g. Trading's Shortlist reads Market Orders + Assets + Market Prices) -
    reports whichever of `keys` is least fresh, since that's what's actually
    bounding the view's own staleness. A single key behaves exactly like
    describe()."""
    if len(keys) == 1:
        return describe(keys[0])
    statuses = {s.key: s for s in all_status() if s.key in keys}
    never_synced = [s for s in statuses.values() if s.last_synced_at is None]
    if never_synced:
        labels = ", ".join(s.label for s in never_synced)
        return f"Never synced ({labels}) - use App > Update Data... to fetch it."
    oldest = min(statuses.values(), key=lambda s: s.last_synced_at)
    return _describe_status(oldest, prefix_label=True)


def run_selected(keys: list[str], force: bool = False) -> dict[str, dict]:
    """Runs every selected scope's bundle, isolating failures the same way
    each bundle already isolates its own internal steps: one scope's failure
    never blocks the others. A scope not yet due is skipped (not re-fetched)
    unless `force` - re-querying before its own cache window elapses would
    just re-read the same ESI data anyway, the same reasoning the "Update
    Data" dialog uses to grey out its checkbox. A scope is only stamped as
    just-synced when its bundle returns without raising - a bundle that
    spans several tools (e.g. Assets) isolates each tool's own failure
    internally, so it still counts as "synced" even if one of several
    tools inside it failed; a single-tool scope that raises outright (e.g.
    Contracts with no structure configured) is not stamped at all."""
    results: dict[str, dict] = {}
    for key in keys:
        scope = _SCOPES_BY_KEY.get(key)
        if scope is None:
            results[key] = {"error": f"Unknown update scope '{key}'."}
            continue
        if not force and not is_due(key):
            results[key] = {"skipped": "not due yet"}
            continue
        try:
            results[key] = scope.run()
            storage.set_esi_sync_time(key, dt.datetime.utcnow().isoformat(timespec="seconds"))
        except ActionError as e:
            results[key] = {"error": str(e)}
        except Exception as e:  # noqa: BLE001 - one scope's bug must not block the others
            log.exception("Update scope '%s' failed unexpectedly", key)
            results[key] = {"error": str(e)}
    return results
