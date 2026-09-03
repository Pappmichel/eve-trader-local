"""Ore & Minerals' orchestration layer - the last piece of this tool (see
SYNC.md): wires the already-ported computation modules (candidate_discovery,
pricing, quote, optimizer) to the network clients and persistence they
deliberately don't touch themselves. Mirrors `eve_trader/refining/actions.py`
structurally (same `do_*` names, same thin-orchestration/`ActionError`
boundary), but is a synthesis against this repo's own modules - sync its
*behavior*, not its text (same rule SYNC.md's own "orchestration layer"
section states for the top-level actions.py).

Reuses Trading's own seller character/C-J structure throughout - there is no
Ore-specific login, confirmed by the parent's own module docstring and
carried over unchanged here: Ore & Minerals buys ore at Jita and sells
(refined minerals) at the same structure Trading/Production already use.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Optional

import requests

from .. import storage
from ..auth import TokenManager
from ..config import OAUTH_CONFIG, TRADING_CONFIG, OAuthConfig, TradingConfig
from ..errors import ActionError, ConfigError
from ..esi_client import ESIClient, ESIError
from ..goonmetrics_client import GoonmetricsClient
from ..production.config import PRODUCTION_CONFIG, ProductionConfig
from .candidate_discovery import build_ore_candidate_universe
from .config import REFINING_CONFIG, RefiningConfig, save_config_overrides
from .models import MineralOption, MineralRequirement, OreOption, OreShortlistRow, ShoppingListPlan
from .optimizer import optimize_shopping_list
from .paste_parser import merge_duplicate_stacks, parse_paste
from .pricing import evaluate_ore_shortlist, landed_cost_per_unit, mineral_type_ids_for
from .quote import (REPROCESS_DECISION, ReprocessingQuoteRow, evaluate_reprocessing_line,
                    mineral_type_ids_for_lines, resolve_type_id)
from .reprocessing import apply_reprocessing_yield, ore_ice_yield

log = logging.getLogger("eve_trader_local.refining.actions")


def now_ts() -> str:
    return dt.datetime.utcnow().isoformat(timespec="seconds")


def _seller_role(tm: TokenManager) -> Optional[str]:
    """Any one registered seller with docking access is enough (Trading's own
    multi-character precedent) - same `records[0] if records else None` shape
    _refresh_shortlist_rows already uses in the top-level actions.py."""
    records = tm.list_records("seller")
    return records[0].role if records else None


# --------------------------------------------------------------- Ore Shortlist
def do_add_ore_to_shortlist() -> dict:
    """Adds every candidate from the fixed SDE-derived universe not already on
    the shortlist - unlike Trading's own add step, there's no backtest/
    recommendation gate first (the set of compressed ore/ice types is small
    and stable, every candidate is worth tracking)."""
    candidates = build_ore_candidate_universe()
    if not candidates:
        raise ActionError("No compressed ore/ice types found in the SDE cache - run refresh-sde first.")
    existing_ids = {item_id for item_id, *_ in storage.load_ore_shortlist()}
    new_rows = [(c.type_id, c.item, c.family, c.is_ice, True) for c in candidates if c.type_id not in existing_ids]
    if new_rows:
        storage.upsert_ore_shortlist(new_rows)
    return {"added": len(new_rows), "already_tracked": len(candidates) - len(new_rows)}


def do_refresh_ore_shortlist(trading_cfg: TradingConfig = TRADING_CONFIG,
                              refining_cfg: RefiningConfig = REFINING_CONFIG,
                              oauth_cfg: OAuthConfig = OAUTH_CONFIG) -> dict:
    """Re-fetches live market data for every shortlist item and recomputes
    each one's profit/decision, then saves a new snapshot."""
    candidates = build_ore_candidate_universe()
    shortlist = storage.load_ore_shortlist()
    if not shortlist:
        raise ActionError("Ore Shortlist is empty - run add-ore-to-shortlist first.")
    active_by_id = {item_id: active for item_id, _item, _family, _is_ice, active in shortlist}
    tracked_ids = set(active_by_id)
    tracked_candidates = [c for c in candidates if c.type_id in tracked_ids]

    tm = TokenManager(oauth_cfg)
    seller_role = _seller_role(tm)
    client = ESIClient(trading_cfg, tm)

    ore_type_ids = [c.type_id for c in tracked_candidates]
    jita_stats_by_id = client.region_order_stats_bulk(trading_cfg.jita_region_id, ore_type_ids)

    mineral_ids = mineral_type_ids_for(tracked_candidates)
    try:
        # Falls back to a Goonmetrics current-price snapshot when no seller is
        # logged in or the real call fails - see
        # structure_order_stats_bulk_or_goonmetrics's own docstring.
        mineral_stats_by_id, priced_via_fallback = client.structure_order_stats_bulk_or_goonmetrics(
            trading_cfg.structure_id, mineral_ids, auth_role=seller_role,
            goonmetrics_market_slug=trading_cfg.structure_market_slug)
    except ESIError as e:
        raise ActionError(f"Could not fetch the structure's order book ({e}). "
                           f"Does the seller character still have docking access?") from e

    rows = evaluate_ore_shortlist(tracked_candidates, active_by_id, jita_stats_by_id, mineral_stats_by_id,
                                   trading_cfg, refining_cfg)
    run_ts = now_ts()
    storage.save_ore_shortlist_snapshot([_row_to_tuple(r) for r in rows], run_ts)
    storage.set_esi_sync_time("refining", run_ts)

    import_count = sum(1 for r in rows if r.decision == "Import")
    return {"evaluated": len(rows), "import_candidates": import_count, "priced_via_fallback": priced_via_fallback}


def _row_to_tuple(r: OreShortlistRow) -> tuple:
    return (r.item_id, r.item, r.family, r.is_ice, r.active, r.volume_m3, r.landed_cost, r.yield_pct,
            r.mineral_value, r.refining_tax, r.net_sell, r.sell_listed_qty, r.profit_per_unit, r.margin,
            r.profit_per_m3, r.decision)


def _tuple_to_row(t: tuple) -> OreShortlistRow:
    (item_id, item, family, is_ice, active, volume_m3, landed_cost, yield_pct, mineral_value, refining_tax,
     net_sell, sell_listed_qty, profit_per_unit, margin, profit_per_m3, decision) = t
    return OreShortlistRow(item_id=item_id, item=item, family=family, is_ice=bool(is_ice), active=bool(active),
                           volume_m3=volume_m3, landed_cost=landed_cost, yield_pct=yield_pct,
                           mineral_value=mineral_value, refining_tax=refining_tax, net_sell=net_sell,
                           sell_listed_qty=sell_listed_qty, profit_per_unit=profit_per_unit, margin=margin,
                           profit_per_m3=profit_per_m3, decision=decision)


def do_get_ore_shortlist() -> dict:
    """The last saved evaluation run's rows - a display-only read, no network.
    Not present in the parent's own actions.py (its web frontend reads
    storage.latest_ore_snapshot() directly), added here since this repo's CLI
    needs a do_* to print something after a refresh."""
    return {"rows": [_tuple_to_row(t) for t in storage.latest_ore_shortlist_snapshot()]}


def do_deactivate_ore_shortlist_items(item_ids: list[int]) -> dict:
    storage.deactivate_ore_shortlist_items(item_ids)
    return {"deactivated": len(item_ids)}


def do_activate_ore_shortlist_items(item_ids: list[int]) -> dict:
    storage.activate_ore_shortlist_items(item_ids)
    return {"activated": len(item_ids)}


# ---------------------------------------------------------- Reprocessing quote
def do_quote_reprocessing(paste_text: str, trading_cfg: TradingConfig = TRADING_CONFIG,
                           refining_cfg: RefiningConfig = REFINING_CONFIG,
                           oauth_cfg: OAuthConfig = OAUTH_CONFIG) -> dict:
    """Parses an EVE inventory "Copy As" paste, quotes each item (sell-as-is
    vs. scrapmetal-reprocessed), and returns both the per-item rows and a
    totals summary. Both the item's own sell price and its mineral yield's
    sell price are C-J-only (consistent with the Ore Shortlist and
    Production's own established rule)."""
    if not paste_text or not paste_text.strip():
        raise ActionError("Paste is empty - copy items from an Inventory window's list view first.")

    all_lines = parse_paste(paste_text)
    error_lines = [line for line in all_lines if line.error]
    parsed = merge_duplicate_stacks(all_lines)
    if not parsed and not error_lines:
        raise ActionError("Could not parse any items from the paste.")

    tm = TokenManager(oauth_cfg)
    seller_role = _seller_role(tm)
    client = ESIClient(trading_cfg, tm)

    type_ids = [tid for tid in (resolve_type_id(line.name) for line in parsed) if tid is not None]
    mineral_ids = mineral_type_ids_for_lines(type_ids)
    all_ids = sorted(set(type_ids) | set(mineral_ids))
    try:
        stats_by_id, priced_via_fallback = client.structure_order_stats_bulk_or_goonmetrics(
            trading_cfg.structure_id, all_ids, auth_role=seller_role,
            goonmetrics_market_slug=trading_cfg.structure_market_slug)
    except ESIError as e:
        raise ActionError(f"Could not fetch the structure's order book ({e}). "
                           f"Does the seller character still have docking access?") from e

    rows = [_error_line_to_row(line) for line in error_lines]
    for line in parsed:
        type_id = resolve_type_id(line.name)
        item_stats = stats_by_id.get(type_id) if type_id is not None else None
        rows.append(evaluate_reprocessing_line(line, item_stats, stats_by_id, trading_cfg, refining_cfg))

    reprocess_rows = [r for r in rows if r.decision == REPROCESS_DECISION]
    totals = {
        "reprocess_count": len(reprocess_rows),
        "total_mineral_value": sum(r.mineral_value or 0.0 for r in reprocess_rows),
        "total_refined_value": sum(r.refined_value or 0.0 for r in reprocess_rows),
        "total_sell_as_is_value": sum(r.sell_as_is_value or 0.0 for r in rows if r.sell_as_is_value is not None),
    }
    return {"rows": rows, "totals": totals, "priced_via_fallback": priced_via_fallback}


def _error_line_to_row(line) -> ReprocessingQuoteRow:
    return ReprocessingQuoteRow(name=line.name, quantity=line.quantity, type_id=None, category=line.category,
                                sell_as_is_value=None, refined_value=None, mineral_value=None,
                                refining_tax=None, decision="Unknown item", error=line.error)


# ------------------------------------------------- Mineral Shopping List
def do_list_refinable_minerals() -> list[dict]:
    """Every distinct mineral/ice product the compressed ore/ice universe can
    actually refine into, name-resolved - what a "add a mineral" picker would
    offer. Derived from real SDE material rows, not a hardcoded list of the
    eight classic minerals, so ice products and any future ore material come
    along for free."""
    candidates = build_ore_candidate_universe()
    minerals = []
    for type_id in mineral_type_ids_for(candidates):
        row = storage.get_sde_type(type_id)
        if row:
            minerals.append({"type_id": type_id, "name": row[2]})
    minerals.sort(key=lambda m: m["name"])
    return minerals


def _resolve_type(type_id_or_name: str) -> tuple[int, str]:
    """Accepts either a numeric type_id or an item name (exact match,
    case-insensitive) - same lookup shape production/actions.py's own
    _resolve_type uses for set-stock-target, duplicated rather than imported
    (a private helper, and this repo avoids a refining<->production import
    edge that doesn't otherwise need to exist)."""
    stripped = type_id_or_name.strip()
    if stripped.isdigit():
        type_id = int(stripped)
        sde_type = storage.get_sde_type(type_id)
        if sde_type is None:
            raise ActionError(f"Unknown type_id {type_id} - refresh SDE first?")
        return type_id, sde_type[2]
    matches = storage.search_sde_types(stripped, limit=2)
    exact = [m for m in matches if m[1].lower() == stripped.lower()]
    if not exact:
        if not matches:
            raise ActionError(f"No type found for '{stripped}'. Refresh SDE first?")
        raise ActionError(f"No exact match for '{stripped}'. Did you mean: {matches[0][1]}?")
    return exact[0]


def do_set_mineral_requirement(type_id_or_name: str, required_qty: float) -> dict:
    """Adds or updates one requirement without disturbing the rest of the
    saved list - do_save_mineral_requirements itself always replaces the
    whole list (the parent's web editor submits it wholesale), which doesn't
    fit a one-item-at-a-time CLI command the way set-stock-target needs to
    work; this loads the existing list, upserts one entry, and re-saves it
    through that same replace-all path so both callers share one write."""
    if required_qty <= 0:
        raise ActionError("Required quantity must be greater than 0.")
    type_id, type_name = _resolve_type(type_id_or_name)
    existing = {t: (n, q) for t, n, q in storage.load_mineral_requirements()}
    existing[type_id] = (type_name, required_qty)
    storage.replace_mineral_requirements([(t, n, q) for t, (n, q) in existing.items()])
    return {"type_id": type_id, "type_name": type_name, "required_qty": required_qty}


def do_remove_mineral_requirement(type_id_or_name: str) -> dict:
    type_id, type_name = _resolve_type(type_id_or_name)
    remaining = [(t, n, q) for t, n, q in storage.load_mineral_requirements() if t != type_id]
    storage.replace_mineral_requirements(remaining)
    return {"removed": type_id, "type_name": type_name}


def do_load_mineral_requirements() -> list[dict]:
    return [{"type_id": type_id, "name": name, "required_qty": qty}
            for type_id, name, qty in storage.load_mineral_requirements()]


def do_save_mineral_requirements(requirements: list[dict]) -> dict:
    """Replaces the whole saved requirement list (see storage.
    replace_mineral_requirements for why replace-all, not upsert). Each entry
    needs a `type_id` that really exists in the SDE cache and a positive
    `required_qty`; the name is always re-resolved from the SDE rather than
    trusted from the caller, so a stale/renamed client can't persist a wrong
    label next to a right type_id."""
    rows = []
    seen: set[int] = set()
    for entry in requirements:
        try:
            type_id = int(entry["type_id"])
            qty = float(entry["required_qty"])
        except (KeyError, TypeError, ValueError) as e:
            raise ActionError(f"Each requirement needs a numeric type_id and required_qty ({entry!r}).") from e
        if qty <= 0:
            raise ActionError(f"Required quantity for type {type_id} must be greater than 0.")
        if type_id in seen:
            raise ActionError(f"Type {type_id} is listed twice - each mineral can only have one required quantity.")
        sde_row = storage.get_sde_type(type_id)
        if not sde_row:
            raise ActionError(f"Type {type_id} isn't in the SDE cache - run refresh-sde first.")
        seen.add(type_id)
        rows.append((type_id, sde_row[2], qty))
    storage.replace_mineral_requirements(rows)
    return {"saved": len(rows)}


def _ore_option(candidate, jita_stats, refining_cfg: RefiningConfig,
                 trading_cfg: TradingConfig) -> Optional[OreOption]:
    """Prices one compressed ore/ice candidate and pre-refines one whole
    portion of it, producing a single LP column. Returns None when the type
    can't be used at all (not listed in Jita right now, no SDE portion size,
    or nothing to refine into)."""
    portion_size = storage.get_portion_size(candidate.type_id)
    jita_sell = jita_stats.sell_percentile if jita_stats else None
    unit_cost = landed_cost_per_unit(jita_sell, candidate.volume_m3, trading_cfg)
    if unit_cost is None or not portion_size:
        return None
    # The structure's reprocessing tax is taken out of the refined materials
    # in-game, so it belongs in the yield here, not as a separate ISK fee -
    # see optimizer.py's module docstring (modelling decision 2).
    effective_yield = ore_ice_yield(refining_cfg, candidate.family) * (1 - refining_cfg.refining_tax_rate)
    yield_per_portion = apply_reprocessing_yield(candidate.type_id, portion_size, effective_yield)
    if not yield_per_portion:
        return None
    return OreOption(type_id=candidate.type_id, item=candidate.item, family=candidate.family,
                     is_ice=candidate.is_ice, volume_m3=candidate.volume_m3, portion_size=portion_size,
                     landed_cost_per_unit=unit_cost, yield_per_portion=yield_per_portion)


def do_optimize_mineral_shopping_list(requirements: Optional[list[dict]] = None,
                                       trading_cfg: TradingConfig = TRADING_CONFIG,
                                       refining_cfg: RefiningConfig = REFINING_CONFIG,
                                       production_cfg: ProductionConfig = PRODUCTION_CONFIG,
                                       oauth_cfg: OAuthConfig = OAUTH_CONFIG) -> dict:
    """Solves "cheapest way to acquire these minerals" across every compressed
    ore/ice type at once (see optimizer.py for the LP itself). `requirements`
    defaults to the saved list; passing one solves an ad-hoc list without
    persisting it.

    Needs NO logged-in character: every price it reads is either a *buy*
    price from Jita's public regional order book, or an unauthenticated
    Goonmetrics current-price quote for C-J's own home market - never C-J's
    authenticated structure order book, since nothing is sold in this
    workflow (the minerals are consumed by Production). The ore universe is
    the full SDE-derived one, not the Ore Shortlist's active rows: the
    shortlist is a profit-tracking selection for the import-and-sell
    business, and excluding an ore from it shouldn't quietly make a build
    list more expensive.

    The ore side always sources from Jita only (ore is imported and refined,
    never bought at home). Only the *direct-mineral* alternative compares
    Jita-landed against C-J's home-market price and picks whichever is
    cheaper."""
    entries = requirements if requirements is not None else do_load_mineral_requirements()
    wanted: list[MineralRequirement] = []
    for entry in entries:
        type_id, qty = int(entry["type_id"]), float(entry["required_qty"])
        if qty <= 0:
            continue
        sde_row = storage.get_sde_type(type_id)
        wanted.append(MineralRequirement(type_id=type_id,
                                         name=entry.get("name") or (sde_row[2] if sde_row else str(type_id)),
                                         required_qty=qty))
    if not wanted:
        raise ActionError("No mineral requirements yet - add at least one mineral and quantity first.")

    candidates = build_ore_candidate_universe()
    if not candidates:
        raise ActionError("No compressed ore/ice types found in the SDE cache - run refresh-sde first.")

    client = ESIClient(trading_cfg, TokenManager(oauth_cfg))
    ore_ids = [c.type_id for c in candidates]
    mineral_ids = [r.type_id for r in wanted]
    try:
        stats_by_id = client.region_order_stats_bulk(trading_cfg.jita_region_id, sorted(set(ore_ids + mineral_ids)))
    except (ESIError, requests.RequestException) as e:
        raise ActionError(f"Could not fetch Jita's order book ({e}).") from e

    ore_options = [o for o in (_ore_option(c, stats_by_id.get(c.type_id), refining_cfg, trading_cfg)
                               for c in candidates) if o is not None]

    # Best-effort - a Goonmetrics outage shouldn't break the whole shopping
    # list (Jita-only pricing is still a valid fallback), unlike the ESI
    # order-book fetch above which the function genuinely can't proceed
    # without.
    home_quotes = {}
    if production_cfg.home_market:
        try:
            home_quotes = {p.type_id: p
                          for p in GoonmetricsClient(trading_cfg).current_prices(production_cfg.home_market)}
        except requests.RequestException:
            log.warning("Goonmetrics home-market fetch failed - falling back to Jita-only mineral pricing.")

    mineral_options = {}
    for req in wanted:
        sde_row = storage.get_sde_type(req.type_id)
        volume = sde_row[3] if sde_row and sde_row[3] else 0.0
        stats = stats_by_id.get(req.type_id)
        jita_cost = landed_cost_per_unit(stats.sell_percentile if stats else None, volume, trading_cfg)

        home_quote = home_quotes.get(req.type_id)
        home_cost = (home_quote.sell * (1 + trading_cfg.jita_buy_broker_fee)
                    if home_quote and home_quote.sell > 0 else None)

        if home_cost is not None and (jita_cost is None or home_cost < jita_cost):
            cost, source = home_cost, "Home"
        elif jita_cost is not None:
            cost, source = jita_cost, "Jita"
        else:
            cost, source = None, None

        mineral_options[req.type_id] = MineralOption(
            type_id=req.type_id, name=req.name, landed_cost_per_unit=cost, source=source,
        )

    plan = optimize_shopping_list(wanted, ore_options, mineral_options)
    return _plan_to_dict(plan)


def _plan_to_dict(plan: ShoppingListPlan) -> dict:
    return {
        "ore_purchases": plan.ore_purchases, "direct_purchases": plan.direct_purchases,
        "coverage": plan.coverage, "ore_cost": plan.ore_cost, "direct_cost": plan.direct_cost,
        "total_cost": plan.total_cost, "lp_cost": plan.lp_cost, "all_direct_cost": plan.all_direct_cost,
        "savings_vs_all_direct": plan.savings_vs_all_direct, "total_volume_m3": plan.total_volume_m3,
    }


# ------------------------------------------------------------------ settings
def do_update_settings(updates: dict, cfg: RefiningConfig = REFINING_CONFIG) -> dict:
    """Persists `updates` and applies them to the live REFINING_CONFIG
    immediately - same shape as the top-level/doctrine do_update_settings."""
    try:
        save_config_overrides(updates, cfg)
    except ConfigError as e:
        raise ActionError(str(e)) from e
    return {"updated": list(updates.keys())}
