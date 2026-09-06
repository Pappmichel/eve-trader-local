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
from ..errors import ActionError, ConfigError
from ..esi_client import ESIClient, ESIError
from . import engine, esi_sync, invention, jobs, pricing
from .config import PRODUCTION_CONFIG, ProductionConfig, save_config_overrides
from .constants import DECRYPTORS, JOB_CATEGORIES
from .models import AssetLocationRow, BuildCandidate, ShipMarginRow, SpecialOrder

SYNC_SCOPE = "production"


def do_update_settings(updates: dict, cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Persists `updates` and applies them to the live PRODUCTION_CONFIG
    immediately - same shape as the top-level/doctrine/refining/station
    trading do_update_settings (this tool was simply missing its own copy
    until now, see the GUI Settings dialog task that added it)."""
    try:
        save_config_overrides(updates, cfg)
    except ConfigError as e:
        raise ActionError(str(e)) from e
    # min_margin/min_daily_profit/build-cost fields all feed
    # discover_build_candidates' result set - a stale cached scan would
    # otherwise keep showing pre-save numbers for up to the full TTL.
    engine.invalidate_discover_cache()
    return {"updated": list(updates.keys())}


def do_list_producer_characters(oauth_cfg: OAuthConfig = OAUTH_CONFIG) -> list[tuple[str, int, str]]:
    return esi_sync.list_producer_characters(TokenManager(oauth_cfg))


def do_remove_producer_character(role_key: str, oauth_cfg: OAuthConfig = OAUTH_CONFIG) -> dict:
    """Only drops the stored token. The last-synced assets/blueprints/jobs stay
    exactly as they are until the next do_sync_esi, which is when that
    character's rows actually disappear - removing a character is not itself a
    statement about what anyone owns."""
    TokenManager(oauth_cfg).remove_token(role_key)
    return {"removed": role_key}


def _sync_assets_jobs_blueprints(oauth_cfg: OAuthConfig = OAUTH_CONFIG) -> dict:
    """Every producer character's (and their corps') assets, blueprints and
    industry jobs. Backs all three of esi_update.py's Assets/Industry Jobs/
    Blueprints scope groups - ESI has no per-scope fetch for these that's
    cheaper than the one combined per-character pass esi_sync.sync_esi
    already does, so checking any one of the three in the "Update Data"
    dialog refreshes all three together (a deliberate, documented
    simplification - independent *timers* for these three specifically
    aren't worth a 3x-slower sync).

    This is what puts real owned-BPO ME/TE behind engine._owned_bpo_mods, so it
    covers every case that can change a Tech I build cost: a newly registered
    character's first sync, a BPO finishing research, or a blueprint changing
    hands. Registering or removing a character changes no stored blueprint data
    by itself - only this does."""
    return esi_sync.sync_esi(oauth_cfg)


def _sync_cost_indices(cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Part of the esi_update.py "Cost Indices / Adjusted Prices" scope
    group: system cost indices and adjusted (EIV) prices job-cost modeling
    needs."""
    return {
        "component": pricing.refresh_system_cost_indices(cfg.component_system_id) or None,
        "manufacturing": pricing.refresh_system_cost_indices(cfg.manufacturing_system_id) or None,
        "adjusted_prices": len(pricing.refresh_adjusted_prices()),
    }


def _sync_market_prices(cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Part of the esi_update.py "Market Prices" scope group: home/Jita
    price refresh for engine.production_price_universe() (every ship + stock
    target, material-closure expanded) - see that function's own docstring
    for why margins/build-tree/stock-value read prices from cache instead of
    a live ESI call now."""
    priced_type_ids = engine.production_price_universe()
    return {"home_prices": len(pricing.refresh_home_prices(priced_type_ids, cfg)),
            "jita_prices": len(pricing.refresh_jita_prices(priced_type_ids))}


def do_sync_esi(oauth_cfg: OAuthConfig = OAUTH_CONFIG, cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """The CLI's own "do everything Production needs, in one command"
    convenience wrapper - unrelated to esi_update.py's per-scope-group
    "Update Data" dialog (see that module's own docstring), which triggers
    _sync_assets_jobs_blueprints/_sync_cost_indices/_sync_market_prices
    independently instead."""
    result = _sync_assets_jobs_blueprints(oauth_cfg)
    result["cost_indices"] = _sync_cost_indices(cfg)
    result["market_prices"] = _sync_market_prices(cfg)
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
        raise ActionError(
            "SDE cache is empty. Refresh it via App > Refresh Static Data (SDE)... "
            "in the GUI (or `eve-trader-local refresh-sde` on the CLI)."
        )
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


def do_update_stock_target(type_id_or_name: str, quantity: float | None = None,
                           jita_target: bool | None = None) -> dict:
    """Edits an *existing* stock target's quantity/jita_target in place
    (parent's GitHub issue #16 - "should be able to change the targets
    directly in the table, like the targets on the doctrine table"). Unlike
    do_add_stock_target, this requires the row to already exist - raises
    ActionError otherwise, rather than silently creating one (that's what
    do_add_stock_target is for).

    Each field is genuinely optional: storage.upsert_stock_target here
    always overwrites both columns (unlike the parent's own COALESCE-based
    partial-update version - this repo's stock_targets table only has the
    two, simpler fields, see storage.py's own schema comment), so a field
    left as None is filled in from the row's current value before the
    upsert, rather than silently resetting it."""
    type_id, type_name = _resolve_type(type_id_or_name)
    existing = {t[0]: t for t in storage.load_stock_targets()}
    current = existing.get(type_id)
    if current is None:
        raise ActionError(f"No stock target for '{type_name}' - use set-stock-target to create one.")
    _current_type_id, _current_name, current_quantity, current_jita_target = current
    new_quantity = current_quantity if quantity is None else quantity
    new_jita_target = current_jita_target if jita_target is None else jita_target
    if new_quantity < 0:
        raise ActionError("Stock target quantity cannot be negative.")
    storage.upsert_stock_target(type_id, type_name, new_quantity, new_jita_target)
    return {"type_id": type_id, "type_name": type_name, "quantity": new_quantity, "jita_target": new_jita_target}


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
        raise ActionError(
            "SDE cache is empty. Refresh it via App > Refresh Static Data (SDE)... "
            "in the GUI (or `eve-trader-local refresh-sde` on the CLI)."
        )
    if not storage.load_stock_targets():
        raise ActionError("No stock targets configured. Run: eve-trader-local set-stock-target")
    return engine.plan_production(cfg)


def do_plan_asset_optimized(cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Runs the readiness-focused planner (engine.plan_asset_optimized) -
    same preconditions as do_plan_production."""
    if not storage.sde_row_counts().get("sde_types"):
        raise ActionError(
            "SDE cache is empty. Refresh it via App > Refresh Static Data (SDE)... "
            "in the GUI (or `eve-trader-local refresh-sde` on the CLI)."
        )
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
        raise ActionError(
            "SDE cache is empty. Refresh it via App > Refresh Static Data (SDE)... "
            "in the GUI (or `eve-trader-local refresh-sde` on the CLI)."
        )
    rows = engine.discover_ship_margins(cfg)
    return {"rows": [ShipMarginRow(**r) for r in rows]}


def do_get_item_margin(type_id_or_name: str, cfg: ProductionConfig = PRODUCTION_CONFIG) -> ShipMarginRow:
    """Margin page's search - resolves `type_id_or_name` (same lookup
    do_add_stock_target/do_build_material_tree/do_search_item_locations use)
    and returns its current home/Jita price, build cost, and both margins for
    any category, not just ships. See engine.item_margin_detail - unlike
    discover_ship_margins' whole-catalog scan, this needs the SDE cache
    populated only insofar as the resolved type_id has to exist in it, so no
    separate sde_row_counts precondition is enforced here (an unknown
    type_id/name already raises via _resolve_type)."""
    type_id, type_name = _resolve_type(type_id_or_name)
    return ShipMarginRow(**engine.item_margin_detail(type_id, type_name, cfg))


def do_build_material_tree(type_id_or_name: str, quantity: float = 1.0,
                           cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Resolves `type_id_or_name` (same lookup do_add_stock_target uses - not
    just invented T2/T3 items like do_estimate_invention, any manufacturable-
    or-not item) and returns its full recursive material tree. See
    engine.build_material_tree."""
    if quantity <= 0:
        raise ActionError("Quantity must be positive.")
    type_id, _type_name = _resolve_type(type_id_or_name)
    type_ids = list(engine.structural_material_closure([type_id]))
    home = pricing.home_prices(type_ids, cfg)
    jita = pricing.jita_prices(type_ids)
    selected_decryptors: dict[int, str] = {}  # no manual-decryptor table exists here - see SYNC.md
    t2_memo: dict = {}
    return engine.build_material_tree(type_id, quantity, cfg, home, jita, selected_decryptors, t2_memo)


def do_search_item_locations(type_id_or_name: str) -> dict:
    """Resolves `type_id_or_name` (same lookup do_add_stock_target/
    do_build_material_tree use) and returns every station/structure it's
    currently sitting at, who owns it (character or corp), and how much -
    see storage.search_item_stock_locations. Reflects the last "Sync ESI
    Data" run, not a live ESI call."""
    type_id, type_name = _resolve_type(type_id_or_name)
    rows = storage.search_item_stock_locations(type_id)
    locations = [
        AssetLocationRow(location_id=location_id, location_name=location_name,
                         owner_name=owner_name, quantity=quantity)
        for location_id, location_name, owner_name, quantity in rows
    ]
    return {"type_id": type_id, "type_name": type_name, "locations": locations}


def do_get_system_cost_indices(cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Cached cost indices for the configured component/manufacturing
    systems (see esi_sync.sync_esi, which now also refreshes these) - a
    display-only hint for Settings' manual override fields (so "what's a
    sane value here" doesn't require guessing). Never raises -
    pricing.cached_system_cost_indices already degrades to {} on an unset
    system_id or nothing cached yet; {} is normalized to None here so a
    caller can treat "no data" as one falsy value."""
    return {
        "component": pricing.cached_system_cost_indices(cfg.component_system_id) or None,
        "manufacturing": pricing.cached_system_cost_indices(cfg.manufacturing_system_id) or None,
    }


def do_estimate_invention(product_name: str, decryptor_name: str | None = None,
                          cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """`product_name` is the T2/T3 blueprint you want (e.g. "Small Shield
    Booster II Blueprint"), not the T1 base blueprint (or, for Tech III, the
    relic). If `decryptor_name` is None, compares every (grade, decryptor)
    combination (invention.compare_recipes_and_decryptors - for Tech III,
    "grade" means the Intact/Malfunctioning/Wrecked relic tier, not just the
    decryptor: see that function's own docstring); otherwise estimates just
    that one decryptor, still auto-picking the cheapest grade for it
    (invention.best_recipe_for_decryptor).

    Deliberately resolves `product_name` to its product_type_id only (never
    the arbitrary, non-deterministic t1_blueprint_type_id the same storage
    lookup also returns) before calling either comparison, matching the
    parent's own confirmed fix (2026-08-30) for a real bug: taking that
    arbitrary blueprint_type_id and running every decryptor comparison
    against just that one grade could show, and the "single decryptor" mode
    could estimate against, a different relic grade on every call, never
    letting the user compare or deliberately pick a grade at all."""
    recipe = storage.find_invention_recipe_by_product_name(product_name.strip())
    if recipe is None:
        raise ActionError(
            f"No invention recipe found for '{product_name}'. Exact name? Refresh SDE first?"
        )
    _, product_blueprint_id = recipe

    type_ids = list(engine.structural_material_closure([product_blueprint_id]))
    home = pricing.home_prices(type_ids, cfg)
    jita = pricing.jita_prices(type_ids)
    # activity 1 = Manufacturing: the invented BPC's *own* build materials,
    # used to weigh ME savings the same way the planner does (see engine.py).
    reducible_cost = invention.reducible_material_cost(product_blueprint_id, 1, home, jita, cfg)

    if decryptor_name is None:
        results = invention.compare_recipes_and_decryptors(product_blueprint_id, home, jita, cfg, reducible_cost)
    else:
        if decryptor_name not in DECRYPTORS:
            raise ActionError(f"Unknown decryptor '{decryptor_name}'. Options: {', '.join(DECRYPTORS)}")
        chosen = invention.best_recipe_for_decryptor(product_blueprint_id, decryptor_name, home, jita, cfg,
                                                     reducible_cost)
        results = [chosen] if chosen is not None else []

    return {"results": results}


def do_resolve_structure_name(location_id: int, force: bool = False,
                              oauth_cfg: OAuthConfig = OAUTH_CONFIG) -> dict:
    """Resolves `location_id` to its structure name (and solar_system_id) via
    ESI, cached indefinitely (storage.get/set_cached_structure_name) unless
    `force`.

    Tries two paths, in order:
    1. Each registered character's corp's structure list
       (ESIClient.corporation_structures - esi-corporations.read_structures.v1
       + Station_Manager role) - covers every structure that corp owns, with
       no dependency on any one character having personally docked there.
       Each distinct corporation is only queried once.
    2. Per-character docking history (ESIClient.get_structure_name -
       esi-universe.read_structures.v1) - tries every registered producer
       character in turn until one can actually "see" the structure (needs
       docking rights/to have visited it). Only reached if path 1 didn't
       resolve it.

    A character added before esi-universe.read_structures.v1/
    esi-corporations.read_structures.v1 existed needs to be re-added (remove
    + add again, `producer remove`/`producer add`) before either path works
    for it."""
    if not force:
        was_cached, cached_name = storage.get_cached_structure_name(location_id)
        if was_cached:
            return {"location_id": location_id, "name": cached_name, "cached": True}

    tm = TokenManager(oauth_cfg)
    characters = esi_sync.list_producer_characters(tm)
    if not characters:
        raise ActionError("No producer character logged in yet.")

    client = ESIClient(tokens=tm)
    name = None
    solar_system_id = None

    tried_corporations: set[int] = set()
    for role, character_id, _character_name in characters:
        try:
            corporation_id = client.character_public_info(character_id)["corporation_id"]
        except ESIError:
            continue
        if corporation_id in tried_corporations:
            continue
        tried_corporations.add(corporation_id)
        try:
            structures = client.corporation_structures(corporation_id, auth_role=role)
        except ESIError:
            continue  # this character lacks Station_Manager (or the scope) - try the next one
        for structure in structures:
            if structure.get("structure_id") == location_id:
                name = structure.get("name")
                solar_system_id = structure.get("solar_system_id")
                break
        if name:
            break

    if name is None:
        for role, _character_id, _character_name in characters:
            try:
                info = client.get_structure_name(location_id, auth_role=role)
                name = info.get("name")
                solar_system_id = info.get("solar_system_id")
                break
            except ESIError:
                continue  # this character can't see it - try the next one

    storage.set_cached_structure_name(location_id, name, solar_system_id)
    return {"location_id": location_id, "name": name, "cached": False}


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


# ------------------------------------------------------- manual Build/Buy override
def do_set_manual_build_buy(type_id_or_name: str, decision: str) -> dict:
    """Forces `type_id_or_name` to always Build or always Buy, regardless of
    what the modeled unit cost would otherwise decide - see storage.py's
    manual_build_buy table comment, and engine._buy_or_build_decision/
    _base_runs/_expand_all (plan_production/plan_asset_optimized/
    plan_special_order all consult this via storage.load_manual_build_buy())
    for where it's actually read."""
    if decision not in ("Build", "Buy"):
        raise ActionError(f"Unknown decision {decision!r} - must be 'Build' or 'Buy'.")
    type_id, type_name = _resolve_type(type_id_or_name)
    storage.upsert_manual_build_buy(type_id, decision)
    return {"type_id": type_id, "type_name": type_name, "decision": decision}


def do_clear_manual_build_buy(type_id_or_name: str) -> dict:
    type_id, type_name = _resolve_type(type_id_or_name)
    storage.delete_manual_build_buy(type_id)
    return {"type_id": type_id, "type_name": type_name, "decision": "Auto"}


def do_list_manual_build_buy() -> dict:
    return {"rows": storage.list_manual_build_buy()}


# --------------------------------------------------------------- industry jobs
def do_list_current_jobs(cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Every active/paused/ready character + corp industry job, for the
    Industry Jobs view. See jobs.list_current_jobs."""
    return {"rows": jobs.list_current_jobs(cfg)}


def do_character_slot_overview() -> dict:
    """Per-character, per-category count of currently-running industry jobs -
    a deliberate reduction of the parent's total/free/excluded_from_planning
    Character Slots tab (see jobs.character_slot_overview's own docstring
    and SYNC.md for why: no ESI character-skills scope is synced here to
    derive a real total slot count from)."""
    return {"rows": jobs.character_slot_overview()}


# ------------------------------------------------------------ owned blueprints
def do_list_owned_blueprints() -> dict:
    """Every owned blueprint (character + corp), aggregated across identical
    (type_id, is_original, ME, TE, runs) groups - see engine.
    list_owned_blueprints. A BPO is identified by runs == -1 (ESI's
    convention for "original")."""
    return {"rows": engine.list_owned_blueprints()}


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
        raise ActionError(
            "SDE cache is empty. Refresh it via App > Refresh Static Data (SDE)... "
            "in the GUI (or `eve-trader-local refresh-sde` on the CLI)."
        )
    row = storage.get_special_order(order_id)
    if row is None:
        raise ActionError(f"Special order {order_id} not found.")
    _order_id, _note, net_against_stock, _status, _created_at = row
    items = storage.list_special_order_items(order_id)
    return engine.plan_special_order(items, cfg, net_against_stock=net_against_stock)
