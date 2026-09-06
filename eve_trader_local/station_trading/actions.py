"""Station Trading's orchestration layer - the last piece of this tool (see
SYNC.md): wires the already-ported computation modules (candidate_discovery,
undercut) to the network clients and persistence they deliberately don't
touch themselves. Mirrors `eve_trader/station_trading/actions.py`
structurally (same `do_*` names, same thin-orchestration/`ActionError`
boundary), but is a synthesis against this repo's own modules - sync its
*behavior*, not its text (same rule SYNC.md's own "orchestration layer"
section states for the top-level actions.py).
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from .. import storage
from ..config import OAUTH_CONFIG, TRADING_CONFIG, OAuthConfig
from ..errors import ActionError, ConfigError
from ..esi_client import ESIClient, ESIError, OrderStats
from ..auth import TokenManager
from . import esi_sync
from .candidate_discovery import confirm_live, discover_candidates
from .config import STATION_TRADING_CONFIG, StationTradingConfig, save_config_overrides
from .constants import SKILL_LABELS, order_slots_from_skills
from .undercut import check_buy_undercut_pooled, check_undercut_pooled

_UNDERCUT_CACHE_KEY = "station_trading:undercut"
_SKILLS_CACHE_KEY = "station_trading:skills"


def _jita_market() -> str:
    # Read fresh, not a module-level constant - TRADING_CONFIG.jita_region_id
    # can change via config.reload() after this module is first imported.
    return f"region:{TRADING_CONFIG.jita_region_id}"


def now_ts() -> str:
    return dt.datetime.utcnow().isoformat(timespec="seconds")


def _item_name(type_id: int) -> str:
    sde_row = storage.get_sde_type(type_id)
    return sde_row[2] if sde_row else str(type_id)


def _category_name(type_id: int, category_names: dict[int, str]) -> str:
    category_id = storage.get_type_category(type_id)
    return category_names.get(category_id, "Unknown") if category_id is not None else "Unknown"


def _profit(live_buy: Optional[float], live_sell: Optional[float],
            cfg: StationTradingConfig) -> tuple[Optional[float], Optional[float]]:
    """profit_per_unit/margin from LIVE prices only (never the Goonmetrics
    discovery-time snapshot the shortlist persists) - broker fee is charged
    on both legs (buy placement and sell placement), sales tax only on the
    sell leg, matching real EVE market mechanics (see StationTradingConfig's
    own field comments). None/None when no live price is available (ESI
    outage, or the item never confirmed) - same "don't fabricate a number"
    convention Trading's own shortlist uses for landed_cost/net_sell."""
    if live_buy is None or live_sell is None or live_buy <= 0:
        return None, None
    buy_cost = live_buy * (1 + cfg.broker_fee_rate)
    sell_net = live_sell * (1 - cfg.broker_fee_rate - cfg.sales_tax_rate)
    profit_per_unit = sell_net - buy_cost
    margin = profit_per_unit / buy_cost
    return profit_per_unit, margin


def _cache_order_book_stats(market: str, stats_by_id: dict[int, OrderStats]) -> None:
    if not stats_by_id:
        return
    cache_rows = {tid: (s.buy_percentile, s.sell_percentile, s.buy_volume, s.sell_volume)
                 for tid, s in stats_by_id.items()}
    storage.save_order_book_stats(market, cache_rows, now_ts())


def _order_stats_from_cache(market: str, type_ids: list[int]) -> dict[int, OrderStats]:
    cached = storage.load_order_book_stats(market, type_ids)
    return {tid: OrderStats(sell_percentile=sell_pct, sell_volume=sell_vol or 0.0,
                            buy_percentile=buy_pct, buy_volume=buy_vol or 0.0)
            for tid, (buy_pct, sell_pct, buy_vol, sell_vol) in cached.items()}


def _build_shortlist_rows(rows: list[tuple[int, float, float, str, bool]],
                           cfg: StationTradingConfig) -> list[dict]:
    """Shared by do_get_shortlist/do_refresh_shortlist - reads every row's
    price from storage.order_book_cache (see do_refresh_shortlist, the only
    place that actually calls confirm_live now) and derives category/profit/
    margin from it, not the persisted discovery-time spread (see _profit's
    own docstring)."""
    type_ids = [type_id for type_id, *_rest in rows]
    live = _order_stats_from_cache(_jita_market(), type_ids)
    category_names = storage.load_sde_category_names()
    result = []
    for type_id, spread_pct, avg_daily_volume, discovered_at, active in rows:
        stats = live.get(type_id)
        live_buy = stats.buy_percentile if stats else None
        live_sell = stats.sell_percentile if stats else None
        profit_per_unit, margin = _profit(live_buy, live_sell, cfg)
        # Whole-market theoretical ceiling (profit_per_unit * a volume
        # figure), deliberately NOT a personal-achievable estimate - same
        # convention as Production's potential_daily_profit/Trading's own
        # "Profit / Day" (see CLAUDE.md's "Theoretical ceiling figures"
        # section). avg_daily_volume here is real Goonmetrics region
        # turnover (not order-book depth), so this doesn't repeat that
        # section's documented sell_volume/depth bug.
        profit_per_day = profit_per_unit * avg_daily_volume if profit_per_unit is not None else None
        result.append({
            "type_id": type_id, "name": _item_name(type_id), "category": _category_name(type_id, category_names),
            "spread_pct": spread_pct, "avg_daily_volume": avg_daily_volume,
            "discovered_at": discovered_at, "active": active,
            "live_buy": live_buy, "live_sell": live_sell,
            "profit_per_unit": profit_per_unit, "margin": margin, "profit_per_day": profit_per_day,
        })
    return result


# ---------------------------------------------------------- trader characters
def do_list_trader_characters() -> list[tuple[str, int, str]]:
    return esi_sync.list_trader_characters()


def do_remove_trader_character(role_key: str) -> dict:
    TokenManager(OAUTH_CONFIG).remove_token(role_key)
    return {"removed": role_key}


# ---------------------------------------------------------------- shortlist
def do_refresh_shortlist(cfg: StationTradingConfig = STATION_TRADING_CONFIG) -> dict:
    """Part of the esi_update.py "Market Prices" scope group. Re-runs
    candidate discovery (Goonmetrics-based, see candidate_discovery.discover_
    candidates) and persists the result - newly-discovered rows all start
    active; a previously-deactivated type_id stays deactivated (see
    storage.upsert_station_trading_shortlist). Live-confirms every row on
    the (now possibly-updated) shortlist via confirm_live and caches the
    result into storage.order_book_cache - the only place this tool still
    calls confirm_live; do_get_shortlist/_build_shortlist_rows only ever read
    that cache back now. Undercut checks and skill summaries are their own
    scope groups (Market Orders/Skills - see _cache_undercut/
    _cache_skill_summary), not part of this function anymore. Returns the
    same live-confirmed, profit-annotated shape do_get_shortlist does, so a
    sync's result can be inspected immediately without a second round-trip."""
    candidates = discover_candidates(cfg)
    run_ts = now_ts()
    storage.upsert_station_trading_shortlist(
        [(c["type_id"], c["spread_pct"], c["avg_daily_volume"], run_ts) for c in candidates]
    )

    all_rows = storage.load_station_trading_shortlist()
    type_ids = [type_id for type_id, *_rest in all_rows]
    _cache_order_book_stats(_jita_market(), confirm_live(type_ids))

    rows = do_get_shortlist(cfg)
    return {"discovered": len(candidates), "rows": rows}


def do_get_shortlist(cfg: StationTradingConfig = STATION_TRADING_CONFIG) -> list[dict]:
    """Persisted shortlist rows, live-price-confirmed and profit-annotated on
    every read (see _build_shortlist_rows) - bounded to whatever
    candidate_discovery.discover_candidates already narrowed the market down
    to, so the live ESI call this makes stays within ESIClient's existing
    30s class-wide order-book cache."""
    rows = storage.load_station_trading_shortlist()
    return _build_shortlist_rows(rows, cfg)


def do_deactivate_shortlist_items(type_ids: list[int]) -> dict:
    storage.deactivate_station_trading_shortlist_items(type_ids)
    return {"deactivated": len(type_ids)}


def do_activate_shortlist_items(type_ids: list[int]) -> dict:
    storage.activate_station_trading_shortlist_items(type_ids)
    return {"activated": len(type_ids)}


# ---------------------------------------------------------------- undercut
def _undercut_row_to_dict(r: dict) -> dict:
    return {"type_id": r["type_id"], "name": _item_name(r["type_id"]), "my_price": r["my_price"],
            "competitor_price": r["competitor_price"], "difference": r["difference"]}


def _cache_undercut(cfg: StationTradingConfig = STATION_TRADING_CONFIG,
                    oauth_cfg: OAuthConfig = OAUTH_CONFIG) -> dict:
    """The real live fetch behind do_check_undercut - called only from
    do_refresh_shortlist. Bidirectional: flags any of the trader's own Jita
    trade-hub orders - buy or sell - that a genuinely different market
    participant now beats. See undercut.py's own docstring for why both
    sides need their own live ESI order-book fetch rather than a Goonmetrics
    snapshot. Caches the raw (pre-name-resolution) rows - do_check_undercut
    resolves names at read time against whatever the SDE cache currently
    has, same as before."""
    tm = TokenManager(oauth_cfg)
    traders = [(character_id, role) for role, character_id, _name in esi_sync.list_trader_characters(tm)]
    if not traders:
        raise ActionError("No trader characters registered yet - run: "
                           "eve-trader-local auth --role trader")
    client = ESIClient(tokens=tm)
    try:
        sell_rows = check_undercut_pooled(traders, client, cfg)
        buy_rows = check_buy_undercut_pooled(traders, client, cfg)
    except ESIError as e:
        raise ActionError(f"Could not fetch order-book data ({e}).") from e
    storage.save_result_cache(_UNDERCUT_CACHE_KEY, {"sell": sell_rows, "buy": buy_rows}, now_ts())
    return {"sell": len(sell_rows), "buy": len(buy_rows)}


def do_check_undercut() -> dict:
    """Last cached undercut check - see _cache_undercut, run only from
    do_refresh_shortlist."""
    cached = storage.load_result_cache(_UNDERCUT_CACHE_KEY)
    if cached is None:
        raise ActionError("No cached undercut check yet - run Update Data for Station Trading first.")
    payload, _computed_at = cached
    return {
        "sell": [_undercut_row_to_dict(r) for r in payload["sell"]],
        "buy": [_undercut_row_to_dict(r) for r in payload["buy"]],
    }


# ------------------------------------------------------------------ skills
def _cache_skill_summary(oauth_cfg: OAuthConfig = OAUTH_CONFIG) -> dict:
    """The real live fetch behind do_get_skill_summary - called only from
    do_refresh_shortlist. Trade-skill levels per registered trader
    character, plus the derived order-slot count - informational only (see
    constants.py's own docstring for why fee/tax discounts are deliberately
    NOT derived from these levels)."""
    tm = TokenManager(oauth_cfg)
    client = ESIClient(tokens=tm)
    summaries = []
    for role, character_id, character_name in esi_sync.list_trader_characters(tm):
        try:
            skills = client.character_skills(character_id, auth_role=role)
        except ESIError as e:
            summaries.append({"character_name": character_name, "error": f"skipped (re-add character? {e})"})
            continue
        levels = {s["skill_id"]: s["active_skill_level"] for s in skills.get("skills", [])}
        summaries.append({
            "character_name": character_name,
            "levels": {label: levels.get(skill_id, 0) for skill_id, label in SKILL_LABELS.items()},
            "order_slots": order_slots_from_skills(levels),
        })
    storage.save_result_cache(_SKILLS_CACHE_KEY, summaries, now_ts())
    return {"count": len(summaries)}


def do_get_skill_summary() -> list[dict]:
    """Last cached skill summary - see _cache_skill_summary, run only from
    do_refresh_shortlist."""
    cached = storage.load_result_cache(_SKILLS_CACHE_KEY)
    if cached is None:
        raise ActionError("No cached skill summary yet - run Update Data for Station Trading first.")
    payload, _computed_at = cached
    return payload


# ----------------------------------------------------------------- settings
def do_update_settings(updates: dict, cfg: StationTradingConfig = STATION_TRADING_CONFIG) -> dict:
    """Persists `updates` and applies them to the live STATION_TRADING_CONFIG
    immediately - same shape as the top-level/doctrine/refining
    do_update_settings."""
    try:
        save_config_overrides(updates, cfg)
    except ConfigError as e:
        raise ActionError(str(e)) from e
    return {"updated": list(updates.keys())}
