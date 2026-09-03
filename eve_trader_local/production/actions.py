"""Production's orchestration layer - the Production-side counterpart to the
top-level actions.py, kept separate for the same reason the parent repo keeps
them apart: nothing here is useful to Trading and vice versa.

Same rule as everywhere else: no real logic lives here. Each do_* function
calls one module and returns a plain dict, so the CLI and a future GUI drive
identical code paths.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .. import storage
from ..auth import TokenManager
from ..config import OAUTH_CONFIG, OAuthConfig
from ..errors import ActionError
from . import engine, esi_sync
from .config import PRODUCTION_CONFIG, ProductionConfig
from .constants import JOB_CATEGORIES
from .models import BuildCandidate, ShipMarginRow, SpecialOrder

SYNC_SCOPE = "production"


def do_list_producer_characters(oauth_cfg: OAuthConfig = OAUTH_CONFIG) -> list[tuple[str, int, str]]:
    return esi_sync.list_producer_characters(TokenManager(oauth_cfg))


def do_remove_producer_character(role_key: str, oauth_cfg: OAuthConfig = OAUTH_CONFIG) -> dict:
    """Only drops the stored token. The last-synced assets/blueprints/jobs stay
    exactly as they are until the next do_sync_esi, which is when that
    character's rows actually disappear - removing a character is not itself a
    statement about what anyone owns."""
    TokenManager(oauth_cfg).remove_token(role_key)
    return {"removed": role_key}


def do_sync_esi(oauth_cfg: OAuthConfig = OAUTH_CONFIG) -> dict:
    """Refreshes every producer character's (and their corps') assets,
    blueprints and industry jobs from ESI.

    This is what puts real owned-BPO ME/TE behind engine._owned_bpo_mods, so it
    covers every case that can change a Tech I build cost: a newly registered
    character's first sync, a BPO finishing research, or a blueprint changing
    hands. Registering or removing a character changes no stored blueprint data
    by itself - only this does."""
    result = esi_sync.sync_esi(oauth_cfg)
    storage.set_esi_sync_time(SYNC_SCOPE, datetime.now(timezone.utc).isoformat())
    return result


def do_get_esi_sync_time() -> dict:
    return {"synced_at": storage.get_esi_sync_time(SYNC_SCOPE)}


def do_discover_build_candidates(top_n: int = 200, cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Scans every manufacturable, market-listed SDE item for ones where
    building clearly beats buying right now - see engine.
    discover_build_candidates. Needs the SDE cache populated (refresh-sde)
    to find anything at all."""
    if not storage.sde_row_counts().get("sde_types"):
        raise ActionError("SDE cache is empty. Run: eve-trader-local refresh-sde")
    candidates = engine.discover_build_candidates(cfg, top_n=top_n)
    return {"rows": [BuildCandidate(**c) for c in candidates]}


def _resolve_type(type_id_or_name: str) -> tuple[int, str]:
    """Accepts either a numeric type_id or an item name (exact match,
    case-insensitive) - same lookup shape the parent's do_add_stock_target
    uses, so `set-stock-target 34 100` and `set-stock-target Tritanium 100`
    both work from the CLI."""
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


def do_add_stock_target(type_id_or_name: str, quantity: float, jita_target: bool = False) -> dict:
    if quantity < 0:
        raise ActionError("Stock target quantity cannot be negative.")
    type_id, type_name = _resolve_type(type_id_or_name)
    storage.upsert_stock_target(type_id, type_name, quantity, jita_target)
    return {"type_id": type_id, "type_name": type_name, "quantity": quantity, "jita_target": jita_target}


def do_remove_stock_target(type_id_or_name: str) -> dict:
    type_id, type_name = _resolve_type(type_id_or_name)
    storage.delete_stock_target(type_id)
    return {"removed": type_id, "type_name": type_name}


def do_list_stock_targets() -> dict:
    return {"rows": storage.load_stock_targets()}


def do_plan_production(cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Runs the stock-aware planner (engine.plan_production) - see that
    function's docstring for what it computes and what it deliberately
    doesn't yet."""
    if not storage.sde_row_counts().get("sde_types"):
        raise ActionError("SDE cache is empty. Run: eve-trader-local refresh-sde")
    if not storage.load_stock_targets():
        raise ActionError("No stock targets configured. Run: eve-trader-local set-stock-target")
    return engine.plan_production(cfg)


def do_plan_asset_optimized(cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Runs the readiness-focused planner (engine.plan_asset_optimized) -
    same preconditions as do_plan_production."""
    if not storage.sde_row_counts().get("sde_types"):
        raise ActionError("SDE cache is empty. Run: eve-trader-local refresh-sde")
    if not storage.load_stock_targets():
        raise ActionError("No stock targets configured. Run: eve-trader-local set-stock-target")
    return engine.plan_asset_optimized(cfg)


def do_market_status(cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Cheap target-vs-current-stock read (engine.market_status), no live
    pricing needed."""
    if not storage.load_stock_targets():
        raise ActionError("No stock targets configured. Run: eve-trader-local set-stock-target")
    return {"rows": engine.market_status(cfg)}


def do_stock_value(cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Total ISK value of current stock (engine.stock_value)."""
    if not storage.load_stock_targets():
        raise ActionError("No stock targets configured. Run: eve-trader-local set-stock-target")
    return engine.stock_value(cfg)


def do_get_ship_margins(cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Margin page's list view - every ship's current home/Jita price, build
    cost, and both margins (engine.discover_ship_margins)."""
    if not storage.sde_row_counts().get("sde_types"):
        raise ActionError("SDE cache is empty. Run: eve-trader-local refresh-sde")
    rows = engine.discover_ship_margins(cfg)
    return {"rows": [ShipMarginRow(**r) for r in rows]}


def do_invention_logistics(cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Datacores/decryptors/T1 BPC (or relic) runs needed vs. what's at
    cfg.invention_location_id, for the invention_list plan_production's most
    recent run would produce - re-runs plan_production to get that list
    (no cached "last plan" table exists here yet)."""
    if cfg.invention_location_id is None:
        raise ActionError("No invention_location_id configured.")
    plan = do_plan_production(cfg)
    return {"rows": engine.invention_logistics(plan["invention_list"], cfg)}


def do_t1_bpc_invention_needs(cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """T1-blueprint(-or-relic)-only slice of do_invention_logistics - see
    engine.t1_bpc_invention_needs."""
    if cfg.invention_location_id is None:
        raise ActionError("No invention_location_id configured.")
    plan = do_plan_production(cfg)
    return {"rows": engine.t1_bpc_invention_needs(plan["invention_list"], cfg)}


# --------------------------------------------------- multi-structure logistics
# GitHub issue #4, ported from the parent's Logistik tab - see storage.py's
# job_category_locations table comment and engine.py's logistics_status/
# distribution_recommendations docstrings for the underlying computation.

def do_get_logistics_status(cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Per job_category, how much of each direct material the currently-
    planned jobs in that category need at the structure it's assigned to,
    netted against what's actually there, plus a "pull from" hint where
    short. Re-runs plan_production to get the current build_list (no cached
    "last plan" table exists here yet, same shape as do_invention_logistics
    above)."""
    plan = do_plan_production(cfg)
    return {"rows": engine.logistics_status(plan["build_list"], cfg)}


def do_get_distribution_recommendations(cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """What to move from the configured distribution source to whichever
    category stations are currently short, for the currently-planned jobs.
    Same re-run-the-planner shape as do_get_logistics_status above."""
    plan = do_plan_production(cfg)
    return {"rows": engine.distribution_recommendations(plan["build_list"], cfg)}


def do_set_category_location(category: str, location_id: int) -> dict:
    """Assigns `category` (one of constants.JOB_CATEGORIES) to build its jobs
    at `location_id`. Also remembers it as a quick-switch option - setting a
    category active shouldn't require a separate "save as option" step too
    (matches the parent's own do_set_category_location)."""
    if category not in JOB_CATEGORIES:
        raise ActionError(f"Unknown category '{category}'. Options: {', '.join(JOB_CATEGORIES)}")
    storage.upsert_category_location(category, location_id)
    storage.add_category_location_option(category, location_id)
    return {"category": category, "location_id": location_id}


def do_clear_category_location(category: str) -> dict:
    storage.delete_category_location(category)
    return {"category": category}


def do_add_category_location_option(category: str, location_id: int) -> dict:
    if category not in JOB_CATEGORIES:
        raise ActionError(f"Unknown category '{category}'. Options: {', '.join(JOB_CATEGORIES)}")
    storage.add_category_location_option(category, location_id)
    return {"category": category, "location_id": location_id}


def do_remove_category_location_option(category: str, location_id: int) -> dict:
    storage.delete_category_location_option(category, location_id)
    return {"category": category, "location_id": location_id}


def do_list_category_locations() -> dict:
    """Every job_category's currently-assigned location plus its saved
    quick-switch options (storage.category_location_options) - no parent
    equivalent action exists (the parent's frontend reads both tables via
    two separate endpoints), but this repo's CLI wants one combined listing."""
    assigned = storage.load_category_locations()
    options = storage.load_category_location_options()
    return {"assigned": assigned, "options": options}


# ---------------------------------------------------------------- manual stock
def do_set_manual_stock(type_id_or_name: str, count: float) -> dict:
    """See storage.py's manual_stock table comment for why this is a genuinely
    separate signal from ESI-synced assets, not a simplification of them."""
    if count < 0:
        raise ActionError("Manual stock count cannot be negative.")
    type_id, type_name = _resolve_type(type_id_or_name)
    storage.upsert_manual_stock(type_id, type_name, count)
    return {"type_id": type_id, "type_name": type_name, "count": count}


def do_remove_manual_stock(type_id_or_name: str) -> dict:
    type_id, type_name = _resolve_type(type_id_or_name)
    storage.delete_manual_stock(type_id)
    return {"removed": type_id, "type_name": type_name}


def do_list_manual_stock() -> dict:
    return {"rows": storage.list_manual_stock()}


# ------------------------------------------------------------- Special Orders
# One-off build orders, tracked separately from the permanent stock_targets
# list - see engine.plan_special_order's own docstring for the two
# deliberate differences from plan_production (no margin gate,
# net_against_stock replacing stock-target-driven netting).

def _special_order_to_model(row: tuple, item_count: int) -> SpecialOrder:
    order_id, note, net_against_stock, status, created_at = row
    return SpecialOrder(order_id=order_id, note=note, net_against_stock=net_against_stock,
                        status=status, created_at=created_at, item_count=item_count)


def do_create_special_order(items: list[dict], note: str | None = None,
                            net_against_stock: bool = False) -> dict:
    """`items`: [{"type_id": int, "quantity": float}, ...] - at least one,
    each validated the same way do_add_stock_target validates an existing
    type_id (storage.get_sde_type(type_id) is not None). Raises on the first
    invalid item rather than silently skipping it."""
    if not items:
        raise ActionError("A special order needs at least one item.")
    resolved: list[tuple[int, str, float]] = []
    for item in items:
        type_id = item["type_id"]
        quantity = item["quantity"]
        if quantity <= 0:
            raise ActionError(f"Quantity for type_id {type_id} must be positive.")
        sde_type = storage.get_sde_type(type_id)
        if sde_type is None:
            raise ActionError(f"Unknown type_id {type_id} - refresh SDE first?")
        resolved.append((type_id, sde_type[2], quantity))

    order_id = storage.create_special_order(note, net_against_stock)
    for type_id, type_name, quantity in resolved:
        storage.upsert_special_order_item(order_id, type_id, type_name, quantity)
    return {"order_id": order_id}


def do_list_special_orders() -> list[SpecialOrder]:
    return [_special_order_to_model(row, len(storage.list_special_order_items(row[0])))
            for row in storage.list_special_orders()]


def do_get_special_order(order_id: str) -> dict:
    row = storage.get_special_order(order_id)
    if row is None:
        raise ActionError(f"Special order {order_id} not found.")
    items = storage.list_special_order_items(order_id)
    return {
        "order": _special_order_to_model(row, len(items)),
        "items": [{"type_id": t, "type_name": n, "quantity": q} for t, n, q in items],
    }


def do_update_special_order(order_id: str, status: str | None = None, note: str | None = None) -> dict:
    """Partial update - same dynamic-dict shape as doctrine/actions.py's
    do_update_fitting/storage.update_doctrine. Used for both "mark complete"
    (status="done") and "reopen" (status="open")."""
    if storage.get_special_order(order_id) is None:
        raise ActionError(f"Special order {order_id} not found.")
    if status is not None and status not in ("open", "done"):
        raise ActionError(f"Unknown status {status!r} - must be 'open' or 'done'.")
    updates = {}
    if status is not None:
        updates["status"] = status
    if note is not None:
        updates["note"] = note
    storage.update_special_order(order_id, updates)
    return do_get_special_order(order_id)


def do_remove_special_order(order_id: str) -> dict:
    storage.delete_special_order(order_id)
    return {"removed": order_id}


def do_compute_special_order(order_id: str, cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Runs engine.plan_special_order for one order's current line items.
    Raises under the same SDE-cache-empty precondition as do_plan_production
    - a special order has no stock_targets-equivalent precondition (it
    always has >=1 item, enforced at creation)."""
    if not storage.sde_row_counts().get("sde_types"):
        raise ActionError("SDE cache is empty. Run: eve-trader-local refresh-sde")
    row = storage.get_special_order(order_id)
    if row is None:
        raise ActionError(f"Special order {order_id} not found.")
    _order_id, _note, net_against_stock, _status, _created_at = row
    items = storage.list_special_order_items(order_id)
    return engine.plan_special_order(items, cfg, net_against_stock=net_against_stock)
