"""Production's SDE-driven classification and buy-vs-build cost math: what one
unit of an item really costs to *build* (recursively, since every material of
a built item goes through the same buy-vs-build decision), and what margin
selling it would leave.

ME/TE stacks in two independently-stacking layers (see constants.py):
1. The blueprint/BPC's own research level:
   - Tech I: your *actual* owned BPO's researched ME/TE where you own the
     blueprint (storage.get_owned_bpo_best_me_te via _owned_bpo_mods, best
     across every owned Original - a BPC's ME/TE was fixed by whoever copied
     it, so copies don't count), falling back to a flat "perfect BPO research"
     (ME10/TE20, constants.ACTIVITY_MODS) baseline when you don't own it or
     haven't run production/esi_sync.py yet.
   - Reaction: flat base (constants.ACTIVITY_MODS), no research at all - a real
     EVE mechanic (reactions have no BPO research), not a simplification.
   - Faction/Storyline/Officer/Deadspace: flat 1.00/1.00, also a real mechanic -
     those blueprints are fixed at ME0/TE0 and cannot be researched at all.
   - Tech II/III: the resulting BPC's ME/TE depends on which decryptor it was
     invented with (see invention.py) - resolved per item, either from a
     caller-supplied manual override or by picking whichever decryptor
     minimizes *net* cost per build run (invention cost amortized over runs,
     minus the ME material savings that run gives you). Falls back to the flat
     Tech II ACTIVITY_MODS entry when no invention recipe is found.
2. Your structure + installed rig (constants.STRUCTURE_TYPES / RIG_TIERS),
   applied on top of #1 - EVE always stacks these multiplicatively regardless
   of blueprint research level. Which structure/rig setting applies is split by
   build profile (see _structure_profile): reactions use the reaction profile
   (typically a Refinery), the item groups covered by the two capital/advanced-
   component ME rigs (constants.COMPONENT_GROUP_IDS) use the component profile,
   everything else the manufacturing profile - you may well run three different
   structures. Rig bonuses additionally scale with the security status of the
   system the structure sits in (real EVE mechanic), read from the local SDE
   cache, and Engineering Complex rigs use a different security table than
   Refinery/reactor rigs (see constants.rig_security_multiplier's is_reaction).

Once the material multiplier from those two layers is known, every actual
material-quantity computation must go through `_material_qty` rather than a
plain `base_qty * material_mult * runs`: real EVE rounds a job's material
requirement UP once for the whole batch of runs, and never below `runs` itself.

The job cost (facility/installation fee) is priced off a *live* system cost
index, split by what's being built - not by Tech I/II/Reaction: reactions and
the rig-covered component groups use ProductionConfig.component_system_id,
everything else manufacturing_system_id (see _job_cost_rate). Note that split
is 2-way, one level coarser than the 3-way structure/rig split above, since
reactions and rig-covered components are commonly hosted in the same low-index
system even when built in different structures. Falls back to the flat
ACTIVITY_MODS job_cost_rate when the system isn't configured or ESI can't be
reached - a system's cost index only ever affects job installation *cost*,
never ME/TE. Confirmed real EVE formula (wiki.eveuniversity.org/Manufacturing):
`job_cost_rate = system_index x structure_cost_bonus + facility_tax_rate +
SCC_SURCHARGE_RATE` - the tax/surcharge terms are additive, not folded into the
index multiplication. The ISK fee is then `job_cost_rate x EIV`, where EIV
("Estimated Item Value") sums each material's *base* (ME 0, unreduced) quantity
times its ESI-published adjusted_price (a CCP-regulated valuation, decoupled
from real market prices) - never the ME-reduced quantity at market prices.

The stock-aware planner (`plan_production`, below) nets each configured
stock target (storage.stock_targets, a genuinely new local concept - see
SYNC.md) against ESI-derived owned assets/incoming industry jobs
(`_current_stock`), then expands the whole set of targets' bills of
materials together (`_expand_all`/`_base_runs`), pooling demand for any
component shared across targets rather than sizing each target's tree in
isolation. Deliberately simpler than the parent's version in two ways
(documented where each applies below): no manual-stock override table (ESI
sync is the only stock source), and stock targets carry one target quantity
plus a home-vs-Jita sell flag, not the parent's three-way backup/home-
market/Jita-market split with its own live sell-order-listing nets.
Still not ported: the logistics/distribution helpers and the bought-
blueprint-copy cost term `_unit_cost` folds in from a manual BPC cost table.

`discover_build_candidates` (below) needs none of the stock-target
machinery above, only the cost/margin core this module already has - it now
excludes every configured stock target from its scan (`existing_target_ids`),
matching the parent: an item already deliberately tracked doesn't need to be
independently "discovered" too.
"""
from __future__ import annotations

import math
import threading
import time
from typing import Iterable, Optional

from .. import storage
from ..config import TRADING_CONFIG
from . import invention, pricing
from .config import PRODUCTION_CONFIG, ProductionConfig
from .constants import (
    ACTIVITY_MODS, ACTIVITY_REACTION, ADVANCED_COMPONENT_GROUP_IDS, ANCIENT_RELIC_CATEGORY_ID,
    CAPITAL_COMPONENT_GROUP_IDS, CHARGE_CATEGORY_ID, COMPONENT_GROUP_IDS, DEADSPACE_META_GROUP_ID, DECRYPTORS,
    DRONE_CATEGORY_ID, FACTION_META_GROUP_ID, FIGHTER_CATEGORY_ID, MODULE_CATEGORY_ID, OFFICER_META_GROUP_ID,
    SCC_SURCHARGE_RATE, SHIP_CATEGORY_ID, SHIP_SIZE_GROUP_IDS, SPECIAL_EDITION_SHIPS_MARKET_GROUP_ID,
    STORYLINE_META_GROUP_ID, SUBSYSTEM_GROUP_IDS, rig_security_multiplier, structure_rig_multiplier,
)
from .models import (
    AssetPlanJob, BuildJobEntry, BuyListEntry, DistributionRow, InventionNeedRow, InventionResult, InventoryRow,
    LogisticsRow, MarketStatusRow, OwnedBlueprintRow, ShipMarginRow, SpecialOrderLineItem, StockOverlapWarningRow,
    T1BpcInventionNeedRow,
)

# Guards against an unexpected SDE cycle (a blueprint whose materials
# transitively include its own product): every recursive walk here stops here.
# A real bill of materials never legitimately goes this deep.
MAX_DEPTH = 10

CostIndices = dict[str, dict[str, float]]
# {"component"|"manufacturing": {"manufacturing"|"reaction": rate}} - one
# pricing.system_cost_indices_for() result per configured system, keyed by
# build profile. See _job_cost_rate.


def classify_activity(type_id: int) -> tuple[str, Optional[tuple[int, int, float]]]:
    """Returns (activity_label, blueprint_info). blueprint_info is
    (blueprint_type_id, activity_id, product_qty_per_run), or None if `type_id`
    has no known Manufacturing/Reaction formula (must be bought).

    "Tech II" (meaning: decryptor-invented) is decided *solely* by whether a
    real invention recipe exists for the blueprint (storage.
    find_invention_recipe_candidates_by_product_type_id) - never by metaLevel.
    This used to be "metaLevel>=2 OR has an invention recipe" to catch Tech III
    hulls/subsystems, whose metaLevel doesn't reliably track "genuinely
    invented" (confirmed against real SDE data: a Loki hull is metaLevel 5, but
    its "Loki Core - Augmented Nuclear Reactor" subsystem is metaLevel 1, even
    though it's genuinely invented via a real T1-equivalent blueprint) - but
    that metaLevel>=2 fallback turned out to be both unnecessary
    (find_invention_recipe_candidates_by_product_type_id already correctly
    catches the Loki Core case directly, no metaLevel needed) and actively
    wrong elsewhere: confirmed live (2026-07-16) that metaLevel>=2 also covers
    every Faction/Pirate ship (Machariel, Nestor: metaLevel 8),
    Officer/Deadspace module, and faction booster/drug - none of which are
    actually decryptor-invented (the invention-recipe lookup correctly returns
    an empty tuple for all of them; they're built from their own real,
    directly-researchable blueprint, same as a Tech I item, just not obtainable
    from an NPC seller). The metaLevel branch alone misclassified 854 of 4208
    scanned manufacturable items as "Tech II" in a live scan - each one then
    got priced off the flat Tech II ACTIVITY_MODS ME/TE baseline (and shown as
    "Tech II" in the UI) instead of the correct Tech I-style baseline (owned
    BPO ME/TE if you have it, else the flat "perfect BPO" assumption).

    Tech III uses the exact same activity_id=8 Invention this tool already
    models for Tech II (CCP removed the old relic-based "Reverse Engineering"
    mechanic years ago - confirmed empirically, the current SDE has zero rows
    for activity_id 7) for probability/runs/materials - but its *input* is a
    Sleeper relic (Intact/Malfunctioning/Wrecked, one of 3 grades), never a
    real T1 blueprint, which DOES need its own separate handling elsewhere
    (buy-list/logistics routing, cost pricing, grade optimization - see
    constants.ANCIENT_RELIC_CATEGORY_ID; none of that is ported here yet).

    Non-invented items get a second check against the SDE's real metaGroupID
    (storage.get_sde_type, populated from Fuzzwork's invMetaTypes.csv - see
    sde.py): "Faction" (confirmed with the user, 2026-07-16 - "Faction items
    are de facto T1 items, but add a separate category for them"), extended
    2026-07-17 to "Storyline" and "Officer" the same way (both confirmed to
    have real, blueprint-backed, market-priceable products in the parent app's
    own cached SDE - not just SDE rows that happen to exist but are never
    reachable here) and "Deadspace" for completeness (currently unreachable -
    zero blueprint-backed Deadspace products exist in the SDE, deadspace
    modules are drop-only - but costs nothing to check for a future SDE that
    might add one). Falls back to plain "Tech I" if metaGroupID matches none of
    these. This is a *label* distinction for the user's own tracking/filtering,
    not a purely cosmetic one: all four of these blueprint types are fixed at
    ME0/TE0 and can never be researched or changed at all (Faction confirmed
    with the user 2026-07-16; Storyline/Officer share the same
    non-researchable treatment by the same reasoning - see
    constants.ACTIVITY_MODS) - unlike a genuine Tech I BPO, which CAN be
    researched up to ME10/TE20 - so none of them get Tech I's "assumes perfect
    research, or your real owned BPO's ME/TE if better" treatment."""
    bp = storage.get_blueprint_for_product(type_id)
    if bp is None:
        return "Input", None
    blueprint_id, activity_id, _ = bp
    if activity_id == ACTIVITY_REACTION:
        return "Reaction", bp
    if storage.find_invention_recipe_candidates_by_product_type_id(blueprint_id):
        return "Tech II", bp
    sde_type = storage.get_sde_type(type_id)
    meta_group_id = sde_type[7] if sde_type else None
    meta_group_labels = {
        FACTION_META_GROUP_ID: "Faction",
        STORYLINE_META_GROUP_ID: "Storyline",
        OFFICER_META_GROUP_ID: "Officer",
        DEADSPACE_META_GROUP_ID: "Deadspace",
    }
    return meta_group_labels.get(meta_group_id, "Tech I"), bp


def _material_qty(base_qty: float, material_mult: float, runs: float) -> float:
    """Real EVE material-quantity rounding (wiki.eveuniversity.org Blueprint
    research page): a job's material requirement is rounded UP once for the
    whole batch of `runs`, and can never drop below `runs` itself - ME never
    reduces a material below 1 unit/run, no matter how large the reduction.
    Every quantity computation in this module goes through this rather than a
    plain `base_qty * material_mult * runs`, or a base_qty=1 material (common
    in blueprints) gets silently under-counted by its full ME reduction
    instead of being floored at `runs`."""
    return max(runs, math.ceil(round(base_qty * material_mult * runs, 2)))


# ------------------------------------------------ structure/job-cost profiles
def _is_component(type_id: int) -> bool:
    """True if `type_id` is in one of the item groups covered by the two
    capital/advanced-component ME rigs (constants.COMPONENT_GROUP_IDS)."""
    sde_type = storage.get_sde_type(type_id)
    group_id = sde_type[1] if sde_type else None
    return group_id in COMPONENT_GROUP_IDS


def job_category(type_id: int) -> Optional[str]:
    """Which "where do I start this job" bucket `type_id` belongs to -
    Reactions / Advanced Components / Capital Components / Equipment / Drones &
    Ammunition / Small|Medium|Large Ships / Capital Ship (see constants.py's
    job-category section). Purely a display/grouping label: it never feeds
    ME/TE or job cost, which stay on the coarser 3-way reaction/component/
    manufacturing split (_structure_profile). None for anything with no
    blueprint (nothing to "start") or that falls into no bucket at all
    (blueprints themselves, skillbooks, PI output)."""
    activity, bp = classify_activity(type_id)
    if activity == "Reaction":
        return "Reactions"
    if bp is None:
        return None
    sde_type = storage.get_sde_type(type_id)
    group_id = sde_type[1] if sde_type else None
    if group_id in CAPITAL_COMPONENT_GROUP_IDS:
        return "Capital Components"
    if group_id in ADVANCED_COMPONENT_GROUP_IDS:
        return "Advanced Components"
    if group_id in SUBSYSTEM_GROUP_IDS:
        return "Medium Ships"
    for size, group_ids in SHIP_SIZE_GROUP_IDS.items():
        if group_id in group_ids:
            return size
    category_id = storage.get_type_category(type_id)
    if category_id in (CHARGE_CATEGORY_ID, DRONE_CATEGORY_ID, FIGHTER_CATEGORY_ID):
        return "Drones & Ammunition"
    if category_id == MODULE_CATEGORY_ID:
        return "Equipment"
    return None


def _structure_profile(activity: str, type_id: int) -> str:
    """Which structure/rig setting applies: reactions use the reaction profile
    (typically a Refinery), rig-covered component groups the component profile
    (an Engineering Complex with the component ME rigs), everything else the
    manufacturing profile - each independently configurable, since you may well
    build in three different structures."""
    if activity == "Reaction":
        return "reaction"
    return "component" if _is_component(type_id) else "manufacturing"


def _structure_rig(profile: str, cfg: ProductionConfig) -> tuple[str, str]:
    if profile == "reaction":
        return cfg.reaction_structure_type, cfg.reaction_rig_tier
    if profile == "component":
        return cfg.component_structure_type, cfg.component_rig_tier
    return cfg.manufacturing_structure_type, cfg.manufacturing_rig_tier


def _security_multiplier_for(structure_profile: str, cfg: ProductionConfig) -> float:
    """Rig bonuses scale with the security status of the system the structure
    sits in (constants.rig_security_multiplier - reaction/refinery rigs use a
    different table than Engineering Complex rigs, hence is_reaction below).
    Reaction and component structures use the component system's security,
    manufacturing structures the manufacturing system's - the same 2-way system
    mapping the cost index uses (see _job_cost_rate), since that is the only
    per-category system information configured at all. Read from the local SDE
    cache (static data), never ESI."""
    system_id = (cfg.component_system_id if structure_profile in ("reaction", "component")
                 else cfg.manufacturing_system_id)
    return rig_security_multiplier(storage.get_system_security(system_id),
                                   is_reaction=(structure_profile == "reaction"))


def _job_cost_rate(activity: str, type_id: int, cfg: ProductionConfig,
                   cost_indices: CostIndices) -> float:
    """Live system-cost-index-derived job cost rate, split by build profile
    (see module docstring). Falls back to the flat ACTIVITY_MODS rate when that
    profile's system isn't configured or its index is missing.

    Confirmed real EVE formula (wiki.eveuniversity.org/Manufacturing):
    `Total job cost = EIV * ((system cost index * structure bonus) + facility
    tax + SCC surcharge)` - the facility tax and SCC surcharge are flat
    *additive* terms, not folded into the cost-index multiplier. An earlier
    version of the parent repo folded them in, which understated every job fee
    by at least ~4.25% of EIV at the NPC defaults - a confirmed real bug, and
    the reason the two terms are added separately below rather than being
    treated as part of the index. The SCC surcharge is a fixed CCP-wide
    rate (unlike facility_tax_rate, which a structure's owner can change).

    cfg.reaction_/component_/manufacturing_cost_index_override outrank the
    looked-up index entirely when set - a manual value always wins, so it works
    with no system configured at all."""
    structure_type, rig_tier = _structure_rig(_structure_profile(activity, type_id), cfg)
    cost_mult, _, _ = structure_rig_multiplier(structure_type, rig_tier)
    esi_activity = "reaction" if activity == "Reaction" else "manufacturing"
    is_component_profile = activity == "Reaction" or _is_component(type_id)

    if activity == "Reaction":
        override = cfg.reaction_cost_index_override
    elif is_component_profile:
        override = cfg.component_cost_index_override
    else:
        override = cfg.manufacturing_cost_index_override

    if override is not None:
        index = override
    else:
        system_profile = "component" if is_component_profile else "manufacturing"
        index = cost_indices.get(system_profile, {}).get(esi_activity)

    index_rate = ACTIVITY_MODS[activity].job_cost_rate if index is None else index
    return index_rate * cost_mult + cfg.facility_tax_rate + SCC_SURCHARGE_RATE


# ------------------------------------------------------------ ME/TE stacking
def _owned_bpo_mods(blueprint_id: Optional[int]) -> Optional[tuple[float, float]]:
    """Real (material_multiplier, time_multiplier) from an owned BPO's actual
    researched ME/TE (storage.get_owned_bpo_best_me_te, populated by
    production/esi_sync.py), or None when you don't own `blueprint_id` - in
    which case _activity_mods falls back to the flat perfect-research baseline.

    Only meaningful for Tech I: Reaction has no BPO research at all, and Tech
    II/III take their ME/TE from the decryptor the BPC was invented with
    (_tech_ii_mods), never from a BPO's own stat."""
    if blueprint_id is None:
        return None
    best = storage.get_owned_bpo_best_me_te(blueprint_id)
    if best is None:
        return None
    me, te = best
    return (1 - me / 100, 1 - te / 100)


def _activity_mods(activity: str, type_id: int, cfg: ProductionConfig,
                   cost_indices: CostIndices,
                   blueprint_id: Optional[int] = None) -> tuple[float, float, float]:
    """(material_multiplier, time_multiplier, job_cost_rate) for every activity
    except Tech II/III, with your structure/rig bonus stacked on top of the
    blueprint's own research baseline and a live system-cost-index-derived job
    cost rate. Tech II doesn't use this for ME/TE - see _tech_ii_mods, which
    applies the same structure/rig multiplier to its per-item decryptor result.

    For Tech I this prefers your actual owned BPO's researched ME/TE
    (_owned_bpo_mods, which needs `blueprint_id`) over the flat perfect-
    research (ME10/TE20) baseline in ACTIVITY_MODS - a caller holding a
    blueprint_id should always pass it; it stays optional only for the odd call
    site that has none to hand.

    Reaction and the four meta-group activities never consult owned-BPO data at
    all: reactions have no BPO research, and Faction/Storyline/Officer/
    Deadspace blueprints are fixed at ME0/TE0 and cannot be researched - their
    flat ACTIVITY_MODS entry is the one correct value, not a fallback to
    override with owned data the way Tech I's baseline is."""
    base = ACTIVITY_MODS[activity]
    structure_profile = _structure_profile(activity, type_id)
    structure_type, rig_tier = _structure_rig(structure_profile, cfg)
    security_multiplier = _security_multiplier_for(structure_profile, cfg)
    _, me_mult, te_mult = structure_rig_multiplier(structure_type, rig_tier, security_multiplier)
    job_cost_rate = _job_cost_rate(activity, type_id, cfg, cost_indices)

    base_material_mult, base_time_mult = base.material_multiplier, base.time_multiplier
    if activity == "Tech I":
        owned = _owned_bpo_mods(blueprint_id)
        if owned is not None:
            base_material_mult, base_time_mult = owned

    return base_material_mult * me_mult, base_time_mult * te_mult, job_cost_rate


T2Mods = tuple[float, float, Optional[str], Optional[InventionResult]]


def _tech_ii_mods(type_id: int, blueprint_id: int, activity_id: int, cfg: ProductionConfig,
                  home: dict, jita: dict, selected_decryptors: dict[int, str],
                  memo: dict[int, T2Mods]) -> T2Mods:
    """(material_multiplier, time_multiplier, decryptor_name, chosen) for a Tech
    II *or* Tech III item: the invented BPC's own ME/TE (from whichever
    decryptor is cheapest in net terms, see invention.py) with your structure/
    rig bonus stacked on top, exactly like every other activity. `chosen` is the
    full InventionResult those multipliers came from (None in both fallback
    branches), so a caller that needs more than the multipliers can reuse it
    instead of re-estimating. Memoized per type_id: picking a decryptor prices
    every (source x decryptor) combination, far too expensive to repeat for
    every parent that reaches the same component.

    Tech II and Tech III use the exact same activity_id=8 Invention - what
    differs is how many *sources* storage.find_invention_recipe_candidates_by_
    product_type_id returns: exactly one (a real T1 blueprint) for Tech II, up
    to three (Intact/Malfunctioning/Wrecked relic grades) for Tech III.
    invention.best_recipe_and_decryptor explores every candidate x decryptor,
    which for Tech II reduces to a plain "best decryptor" search.

    The `elif` branch below is a defensive fallback for the rare case where no
    invention recipe can be found at all (missing/stale SDE) but the caller has
    still manually picked a decryptor: its ME/TE applies rather than being
    silently ignored. Falls back further to the flat ACTIVITY_MODS["Tech II"]
    entry when there's neither a recipe nor a manual decryptor."""
    if type_id in memo:
        return memo[type_id]

    structure_profile = _structure_profile("Tech II", type_id)
    structure_type, rig_tier = _structure_rig(structure_profile, cfg)
    security_multiplier = _security_multiplier_for(structure_profile, cfg)
    _, me_mult, te_mult = structure_rig_multiplier(structure_type, rig_tier, security_multiplier)
    fallback = ACTIVITY_MODS["Tech II"]
    override = selected_decryptors.get(type_id)
    candidates = storage.find_invention_recipe_candidates_by_product_type_id(blueprint_id)

    chosen: Optional[InventionResult] = None
    if candidates:
        reducible_cost = invention.reducible_material_cost(blueprint_id, activity_id, home, jita, cfg)
        if override:
            # Decryptor fixed by the caller - the invention source (Tech III's
            # relic grades) is still optimized against that fixed decryptor.
            chosen = invention.best_recipe_for_decryptor(blueprint_id, override, home, jita,
                                                         cfg, reducible_cost)
        else:
            chosen = invention.best_recipe_and_decryptor(blueprint_id, home, jita, cfg, reducible_cost)
    elif override and override in DECRYPTORS:
        decryptor = DECRYPTORS[override]
        result = ((1 - decryptor.me_bonus / 100) * me_mult,
                  (1 - decryptor.te_bonus / 100) * te_mult, override, None)
        memo[type_id] = result
        return result

    if chosen is None:
        result = (fallback.material_multiplier * me_mult, fallback.time_multiplier * te_mult, None, None)
    else:
        result = ((1 - chosen.me / 100) * me_mult, (1 - chosen.te / 100) * te_mult,
                  chosen.decryptor, chosen)
    memo[type_id] = result
    return result


def _material_mult_for(type_id: int, activity: str, bp: tuple[int, int, float],
                       cfg: ProductionConfig, home: dict, jita: dict,
                       selected_decryptors: dict[int, str], t2_memo: dict[int, T2Mods],
                       cost_indices: CostIndices) -> tuple[float, float, Optional[str]]:
    """(material_multiplier, job_cost_rate, decryptor_name) for `type_id`,
    dispatching between the two ME/TE sources above. Exists so no caller can
    accidentally take the Tech I path for an invented item (or forget that Tech
    II's job cost rate has to be looked up separately, since _tech_ii_mods only
    answers the ME/TE half)."""
    blueprint_id, activity_id, _product_qty = bp
    if activity == "Tech II":
        material_mult, _, decryptor_name, _ = _tech_ii_mods(
            type_id, blueprint_id, activity_id, cfg, home, jita, selected_decryptors, t2_memo)
        return material_mult, _job_cost_rate("Tech II", type_id, cfg, cost_indices), decryptor_name
    material_mult, _, job_cost_rate = _activity_mods(activity, type_id, cfg, cost_indices,
                                                     blueprint_id)
    return material_mult, job_cost_rate, None


# ------------------------------------------------------------- build cost
def _haul_volume(type_id: int, cfg: ProductionConfig) -> Optional[float]:
    """Volume to charge haul cost on (cfg.haul_cost_per_m3 x this) - the
    *packaged* volume for ships and capital-sized modules, which can be far
    smaller than the flight/assembled volume in sde_types.volume. Thin wrapper
    around esi_client.resolve_effective_volume, which owns the lookup/cache/
    fallback behavior so Trading's candidate discovery and this share one
    implementation rather than two that can drift."""
    sde_type = storage.get_sde_type(type_id)
    if sde_type is None:
        return None
    from ..esi_client import resolve_effective_volume  # local import: rare, lazy call
    return resolve_effective_volume(type_id, sde_type[3])


def _unit_cost(type_id: int, cfg: ProductionConfig, home: dict, jita: dict,
               memo: dict[int, Optional[float]], selected_decryptors: dict[int, str],
               t2_memo: dict[int, T2Mods], cost_indices: CostIndices,
               adjusted_prices: dict[int, float], depth: int = 0) -> Optional[float]:
    """Pure (no side effects) recursive best-of-buy-or-build unit cost estimate,
    used to decide whether building beats buying. Memoized per type_id in
    `memo`, which the caller owns and can reuse across items.

    job_cost is *not* a percentage markup on the real material cost: real EVE
    computes it as EIV x job_cost_rate, where EIV sums each material's *base*
    (ME 0, unreduced) quantity times its ESI-published adjusted_price, a
    CCP-regulated valuation decoupled from live market prices (community
    reverse-engineered - CCP has never published the formula). material_cost,
    what you actually spend sourcing materials, still correctly uses the
    ME-reduced quantity at real market prices; EIV is a separate, parallel
    calculation that exists solely to price the facility fee.

    Returns None only if it can neither be bought nor costed - a material with
    no sell order anywhere and no blueprint. A sub-material priced at None
    collapses the whole build to its own buy price rather than silently
    treating the missing input as free."""
    if type_id in memo:
        return memo[type_id]
    memo[type_id] = None  # break cycles defensively; refined below once computed
    buy = pricing.buy_price(type_id, home, jita, _haul_volume(type_id, cfg), cfg)

    if depth >= MAX_DEPTH:
        memo[type_id] = buy
        return buy

    activity, bp = classify_activity(type_id)
    if bp is None:
        memo[type_id] = buy
        return buy

    _blueprint_id, _activity_id, product_qty = bp
    material_cost, eiv, job_cost_rate = _material_cost_and_eiv(
        type_id, activity, bp, cfg, home, jita, memo, selected_decryptors, t2_memo,
        cost_indices, adjusted_prices, depth + 1)
    if material_cost is None:
        memo[type_id] = buy
        return buy

    build_cost = (material_cost + eiv * job_cost_rate) / product_qty
    best = build_cost if buy is None else min(buy, build_cost)
    memo[type_id] = best
    return best


def _material_cost_and_eiv(type_id: int, activity: str, bp: tuple[int, int, float],
                           cfg: ProductionConfig, home: dict, jita: dict,
                           memo: dict[int, Optional[float]], selected_decryptors: dict[int, str],
                           t2_memo: dict[int, T2Mods], cost_indices: CostIndices,
                           adjusted_prices: dict[int, float], depth: int,
                           ) -> tuple[Optional[float], float, float]:
    """One run's (material_cost, EIV, job_cost_rate) for `type_id` - the shared
    body of _unit_cost and unit_cost_detail, which price a build identically and
    differ only in what they return. material_cost is None if any sub-material
    couldn't be costed at all, which collapses the whole build (see _unit_cost);
    both callers discard the other two values in that case."""
    blueprint_id, activity_id, _product_qty = bp
    material_mult, job_cost_rate, _decryptor = _material_mult_for(
        type_id, activity, bp, cfg, home, jita, selected_decryptors, t2_memo, cost_indices)

    material_cost = 0.0
    eiv = 0.0
    for material_id, base_qty in storage.get_blueprint_materials(blueprint_id, activity_id):
        m_cost = _unit_cost(material_id, cfg, home, jita, memo, selected_decryptors, t2_memo,
                            cost_indices, adjusted_prices, depth)
        if m_cost is None:
            return None, eiv, job_cost_rate
        material_cost += _material_qty(base_qty, material_mult, 1) * m_cost
        eiv += base_qty * adjusted_prices.get(material_id, 0.0)
    return material_cost, eiv, job_cost_rate


def unit_cost_detail(type_id: int, cfg: ProductionConfig, home: dict, jita: dict,
                     memo: dict[int, Optional[float]], selected_decryptors: dict[int, str],
                     t2_memo: dict[int, T2Mods], cost_indices: CostIndices,
                     adjusted_prices: dict[int, float],
                     ) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """Like _unit_cost, but for callers that need Buy vs Build side by side
    rather than only the cheaper one - returns (best, build_cost, buy_price).
    Sub-materials still recurse through _unit_cost, so each of *them* is still
    sourced whichever way is cheaper; only this top-level item's own two
    numbers are exposed separately."""
    buy = pricing.buy_price(type_id, home, jita, _haul_volume(type_id, cfg), cfg)
    activity, bp = classify_activity(type_id)
    if bp is None:
        return buy, None, buy
    _blueprint_id, _activity_id, product_qty = bp
    material_cost, eiv, job_cost_rate = _material_cost_and_eiv(
        type_id, activity, bp, cfg, home, jita, memo, selected_decryptors, t2_memo,
        cost_indices, adjusted_prices, depth=1)
    if material_cost is None:
        return buy, None, buy
    build_cost = (material_cost + eiv * job_cost_rate) / product_qty
    best = build_cost if buy is None else min(buy, build_cost)
    return best, build_cost, buy


def _buy_or_build_decision(type_id: int, cfg: ProductionConfig, home: dict, jita: dict,
                           manual_overrides: dict[int, str], cost_memo: dict[int, Optional[float]],
                           bp: Optional[tuple[int, int, float]], depth: int = 0) -> str:
    """How to source `type_id`: a manual override if the caller set one, else
    whichever the modeled unit cost (`cost_memo`, populated by _unit_cost) says
    is cheaper - "Buy" when there is no blueprint or MAX_DEPTH was reached."""
    override = manual_overrides.get(type_id)
    if override is not None:
        return override
    if bp is None or depth >= MAX_DEPTH:
        return "Buy"
    buy = pricing.buy_price(type_id, home, jita, _haul_volume(type_id, cfg), cfg)
    build_cost = cost_memo.get(type_id)
    return "Build" if (build_cost is not None and (buy is None or build_cost < buy)) else "Buy"


# ----------------------------------------------------------------- margins
def _sell_margin(sell_price: float, build_cost: Optional[float]) -> Optional[float]:
    """Shared core of margin_home/margin_jita - (sell_price - build_cost) /
    build_cost, or None if build_cost isn't a real positive cost."""
    if build_cost is None or build_cost <= 0:
        return None
    return (sell_price - build_cost) / build_cost


def margin_home(type_id: int, build_cost: Optional[float], home: dict,
                cfg: ProductionConfig = PRODUCTION_CONFIG) -> Optional[float]:
    """Margin from building one unit of `type_id` and selling it at the home
    structure's current sell quote, net of cfg.market_fees - its real
    opportunity-cost value even for something consumed internally rather than
    resold. None if there's no home sell quote to compare against."""
    home_quote = home.get(type_id)
    if not home_quote or home_quote.sell <= 0:
        return None
    return _sell_margin(home_quote.sell * (1 - cfg.market_fees), build_cost)


def margin_jita(type_id: int, build_cost: Optional[float], jita: dict,
                cfg: ProductionConfig = PRODUCTION_CONFIG) -> Optional[float]:
    """Margin from building one unit of `type_id`, shipping it to Jita and
    selling it there, net of cfg.market_fees and the export haul cost (the same
    haul_cost_per_m3 rate as the reverse Jita->home import, just subtracted
    instead of added). None if there's no Jita sell quote."""
    jita_quote = jita.get(type_id)
    if not jita_quote or jita_quote.sell <= 0:
        return None
    export_cost = cfg.haul_cost_per_m3 * (_haul_volume(type_id, cfg) or 0)
    return _sell_margin(jita_quote.sell * (1 - cfg.market_fees) - export_cost, build_cost)


def _build_margin(type_id: int, build_cost: Optional[float], jita_target: bool,
                  home: dict, jita: dict, cfg: ProductionConfig) -> Optional[float]:
    """Margin if you built and sold one unit of `type_id` right now - gates
    whether a *stock target* is worth building at all, separate from the
    plain build-cheaper-than-buy sourcing comparison _buy_or_build_decision
    makes for every BOM node (that's about minimizing cost for something
    you're building anyway; this is "should you even bother"). A stock
    target flagged jita_target uses margin_jita (sell at Jita); everything
    else uses margin_home. Restored (2026-09-03) now that storage.
    stock_targets exists to feed it - see SYNC.md; was deferred with the
    planner that owns stock targets until then."""
    if jita_target:
        return margin_jita(type_id, build_cost, jita, cfg)
    return margin_home(type_id, build_cost, home, cfg)


# --------------------------------------------------- bill-of-materials walks
def structural_material_closure(seed_type_ids: Iterable[int]) -> set[int]:
    """Every type_id reachable from `seed_type_ids` through blueprint materials,
    ignoring buy-vs-build economics entirely - prices aren't known at this
    point, which is exactly why this exists: it gives pricing.home_prices/
    jita_prices a bounded universe to bulk-price up front, instead of needing a
    price for "everything" before the walk can start.

    Necessarily a superset of what the priced walk will actually visit (a node
    whose real price makes "Buy" cheaper is still recursed into here) - that's
    correct, not wasteful: far better to have a price ready and unused than to
    hit an unpriced node mid-recursion. Breadth-first with a visited set, so a
    shared base material is expanded exactly once no matter how many parents
    reach it, and never re-queued at a deeper level."""
    visited: set[int] = set()
    frontier: set[int] = set(seed_type_ids)
    depth = 0
    while frontier and depth < MAX_DEPTH:
        visited |= frontier
        next_frontier: set[int] = set()
        for type_id in frontier:
            _activity, bp = classify_activity(type_id)
            if bp is None:
                continue
            blueprint_id, activity_id, _product_qty = bp
            for material_id, _base_qty in storage.get_blueprint_materials(blueprint_id, activity_id):
                if material_id not in visited:
                    next_frontier.add(material_id)
        frontier = next_frontier
        depth += 1
    return visited


def build_material_tree(type_id: int, quantity: float, cfg: ProductionConfig, home: dict, jita: dict,
                        selected_decryptors: dict[int, str], t2_memo: dict[int, T2Mods],
                        depth: int = 0) -> dict:
    """Recursive, hierarchical material tree for one root item: the FULL recipe
    (invented BPC -> components -> ... -> base minerals) exactly as the
    blueprints define it, regardless of whether building any given node would
    currently be economical - the point is exploring a recipe, not planning a
    shopping list (that's the stock-aware planner, not ported here).

    Uses the same ME/decryptor logic as everything else (_activity_mods/
    _tech_ii_mods) so the quantities shown can never drift from what the cost
    math would compute for the same item. Stops at a leaf with no blueprint, or
    at MAX_DEPTH."""
    sde_type = storage.get_sde_type(type_id)
    type_name = sde_type[2] if sde_type else str(type_id)
    activity, bp = classify_activity(type_id)

    node = {
        "type_id": type_id, "type_name": type_name, "quantity": quantity,
        "activity": activity if bp else "Buy", "decryptor": None, "children": [],
    }
    if bp is None or depth >= MAX_DEPTH:
        return node

    blueprint_id, activity_id, product_qty = bp
    # {} for cost_indices: only material_mult drives sub-material quantities
    # here, so the job cost rate this discards must not cost a live lookup.
    material_mult, _job_cost_rate, decryptor_name = _material_mult_for(
        type_id, activity, bp, cfg, home, jita, selected_decryptors, t2_memo, {})
    node["decryptor"] = decryptor_name

    runs = math.ceil(quantity / product_qty) if product_qty > 0 else 0
    for material_id, base_qty in storage.get_blueprint_materials(blueprint_id, activity_id):
        node["children"].append(build_material_tree(
            material_id, _material_qty(base_qty, material_mult, runs), cfg, home, jita,
            selected_decryptors, t2_memo, depth + 1))
    return node


# --------------------------------------------------------- build-candidate scan
# A margin this high on a *scanned* candidate (as opposed to one you've
# deliberately configured as a stock target) is far more likely a stale/
# synthetic price slipping past the home-quote sanity check below than a real
# opportunity - confirmed against the parent repo's own discovery scan, kept
# here as the same fixed backstop rather than a config field (nobody would
# ever legitimately want to raise it).
MAX_PLAUSIBLE_BUILD_MARGIN = 3.0  # 300%

# Single-user version of the parent's own discover_build_candidates cache
# (production/engine.py there) - a plain time.time()-based TTL, no
# per-tenant keying since there is only ever one user of this database (see
# storage.py's own module docstring). What DOES make an already-cached scan
# wrong isn't concurrency (there is none to protect against here - this was
# the local repo's original, correct reasoning for skipping a cache
# entirely) but simply staleness across repeat calls in one GUI session: the
# GUI's Build Candidates page and the planner re-invoke this on every click,
# and without a cache each one re-walks the whole SDE market-listed universe
# from scratch (a Windows-local performance audit, 2026-09-06, traced the
# local Windows install's "planner takes forever" complaint to exactly this
# - the online repo's own TTL cache was never ported over). A Settings save
# (min_margin/min_daily_profit/build costs) or an SDE refresh changes the
# result set, so both call invalidate_discover_cache() explicitly rather
# than waiting out the TTL (see their own call sites).
_DISCOVER_CACHE_TTL = 600  # seconds
_discover_cache: Optional[list[dict]] = None
_discover_cache_at: float = 0.0
# Guards the cache slot and the scan that fills it, held for the whole scan
# (not just the read/write) so two callers racing on a cold cache serialize
# instead of one redundantly re-walking the whole SDE universe a second time.
_discover_cache_lock = threading.Lock()


def invalidate_discover_cache() -> None:
    """Forces the next discover_build_candidates call to re-scan instead of
    reusing a cached result. Call after anything that changes its result set
    - a Settings save (do_update_settings) or an SDE refresh (refresh_sde) -
    same reasoning as the parent repo's own invalidate_discover_cache."""
    global _discover_cache, _discover_cache_at
    with _discover_cache_lock:
        _discover_cache = None
        _discover_cache_at = 0.0


def discover_build_candidates(cfg: ProductionConfig = PRODUCTION_CONFIG, top_n: int = 200,
                              client: Optional["GoonmetricsClient"] = None) -> list[dict]:
    """Production's equivalent of Trading's candidate discovery
    (candidate_discovery.py): scans every published, market-listed SDE item
    with a real Manufacturing/Reaction/Invention recipe (classify_activity)
    and flags the ones where building clearly beats buying right now, gated
    by cfg.min_margin - the same buy-vs-build margin check a configured stock
    target would need to pass (margin_home, since there is no stock-aware
    planner or per-target Jita routing here yet - see SYNC.md). Unlike the
    parent repo, there is no existing-stock-target exclusion: this repo has
    no stock-target table to exclude against yet, so every recipe-backed item
    is a candidate.

    Every candidate is priced from one shared home/Jita price fetch and one
    cost_memo/t2_memo pair for the whole scan - a material common to many
    candidates (a base mineral, a common component) is only ever priced
    once, not once per candidate that uses it.

    Confirmed by the parent repo's own live testing: margin alone isn't a
    useful ranking - a huge margin on an item nobody actually buys is
    worthless, the same "profitability alone isn't enough" reasoning
    Trading's min_avg_movement gate already encodes. So every margin-
    qualifying candidate gets a batched Goonmetrics history lookup
    (TRADING_CONFIG.reference_region_id - the home structure's own region,
    the same constant Trading's shortlist/history_backtest already use for
    "is this worth it at all" questions) for its recent daily "movement" -
    ESI's own daily traded-unit count, not an ISK value (confirmed against
    the parent's own live ESI history check). potential_daily_profit =
    daily_movement (units/day) x profit_per_unit, where profit_per_unit =
    margin x build_cost. Results are ranked by this, not raw margin - a
    modest-margin, liquid item correctly outranks a huge-margin item nobody's
    actually trading, and cfg.min_daily_profit gates out candidates below a
    configured floor (0.0 by default: no floor).

    potential_daily_profit is a theoretical ceiling - "if this item's entire
    day of home-structure market turnover were captured" - not a claim about
    what one builder could personally sell in a day. A tiny-volume,
    huge-per-unit item (a capital hull, a faction module) can still show an
    enormous number; that's the mathematically correct answer to "what's the
    whole market worth", not a bug to cap or filter (see CLAUDE.md's
    "Theoretical ceiling" section in the parent repo, and this repo's own
    Trading Profit/Day column, which follows the identical principle).

    Deliberately home-structure only, never Jita - same reasoning as the
    parent: production here is for the local home market, freighting
    finished goods to Jita to sell isn't part of this tool's business model.

    Cached for _DISCOVER_CACHE_TTL seconds (see that constant's own comment)
    - `top_n` only slices the cached, already-ranked list, so repeat calls
    with a different `top_n` reuse the same scan instead of re-triggering
    it. The scan itself runs under _discover_cache_lock so two callers
    racing on a cold cache serialize instead of redundantly scanning twice."""
    global _discover_cache, _discover_cache_at
    with _discover_cache_lock:
        if _discover_cache is not None and (time.time() - _discover_cache_at) < _DISCOVER_CACHE_TTL:
            return _discover_cache[:top_n]
        results = _scan_build_candidates(cfg, client)
        _discover_cache = results
        _discover_cache_at = time.time()
        return results[:top_n]


def _scan_build_candidates(cfg: ProductionConfig, client: Optional["GoonmetricsClient"]) -> list[dict]:
    """The actual (slow - walks every published, market-listed SDE item) scan
    behind discover_build_candidates - split out so the public function can
    hold _discover_cache_lock across the whole scan without an awkwardly
    deep `with` indentation over this entire body, same split as the
    parent's own function."""
    cost_memo: dict[int, Optional[float]] = {}
    t2_memo: dict[int, T2Mods] = {}
    selected_decryptors: dict[int, str] = {}  # no manual-decryptor table exists yet - see SYNC.md

    existing_target_ids = {t[0] for t in storage.load_stock_targets()}
    sde_rows = storage.load_sde_types_with_market_group()
    buildable = [(type_id, type_name, meta_level, activity, bp)
                 for type_id, type_name, _volume, _market_group_id, meta_level, _category_id in sde_rows
                 if type_id not in existing_target_ids
                 for activity, bp in [classify_activity(type_id)] if bp is not None]

    # Bounded, price-agnostic universe to price up front - see pricing.
    # home_prices/jita_prices' own docstrings for why this can't just be
    # "everything" now that both are ESI-first (Jita has no bulk-region
    # endpoint at all, one call per type_id).
    priced_type_ids = list(structural_material_closure(t[0] for t in buildable))
    home = pricing.home_prices(priced_type_ids, cfg)
    jita = pricing.jita_prices(priced_type_ids)

    cost_indices: CostIndices = {
        "component": pricing.cached_system_cost_indices(cfg.component_system_id),
        "manufacturing": pricing.cached_system_cost_indices(cfg.manufacturing_system_id),
    }
    adjusted_prices = pricing.cached_adjusted_prices()

    results = []
    for type_id, type_name, meta_level, activity, _bp in buildable:
        home_quote = home.get(type_id)
        if home_quote is None or home_quote.buy <= 0 or home_quote.buy == home_quote.sell:
            # buy == sell (exactly) is the signature of a market with no real
            # order book on one side - a synthetic/fallback price, not a
            # genuine live quote (confirmed live in the parent repo: a
            # module showed buy=sell to the ISK, which a real independent
            # buy-side and sell-side order book essentially never produces
            # by coincidence).
            continue
        build_cost = _unit_cost(type_id, cfg, home, jita, cost_memo, selected_decryptors,
                                t2_memo, cost_indices, adjusted_prices)
        margin = margin_home(type_id, build_cost, home, cfg)
        if margin is None or margin < cfg.min_margin or margin > MAX_PLAUSIBLE_BUILD_MARGIN:
            continue
        results.append({
            "type_id": type_id, "type_name": type_name, "activity": activity,
            "build_cost": build_cost, "margin": margin, "meta_level": meta_level,
        })

    if results:
        if client is None:
            # Lazy import, same "keeps this dependency lazy until actually
            # needed" reasoning as the ESIClient import above.
            from ..goonmetrics_client import GoonmetricsClient
            client = GoonmetricsClient()
        movement_by_type: dict[int, list[float]] = {}
        for point in client.price_history_chunked(TRADING_CONFIG.reference_region_id,
                                                    [r["type_id"] for r in results]):
            movement_by_type.setdefault(point.type_id, []).append(point.movement)
        for r in results:
            days = movement_by_type.get(r["type_id"])
            daily_movement = sum(days) / len(days) if days else 0.0
            r["daily_movement"] = daily_movement
            r["potential_daily_profit"] = daily_movement * r["margin"] * r["build_cost"]
        results = [r for r in results if r["potential_daily_profit"] >= cfg.min_daily_profit]

    results.sort(key=lambda r: r.get("potential_daily_profit", 0.0), reverse=True)
    return results


# ------------------------------------------------------------- stock planner
def _current_stock(type_id: int, manual_stock: dict[int, float],
                   bp: Optional[tuple[int, int, float]]) -> float:
    """Owned stock right now: manual entry (storage.manual_stock - a genuinely
    separate, user-maintained count, not a simplification of ESI assets; see
    storage.py's own table comment) + ESI-derived assets everywhere (character
    + corp, every location - see storage.esi_stock_at_location's own docstring
    for why "do I own this anywhere" is corp-wide, not scoped to one curated
    structure) + industry jobs already in progress, whose eventual output
    counts as "as good as in stock" for sizing purposes (how many total runs
    to plan for should net against a batch already cooking)."""
    total = manual_stock.get(type_id, 0.0)
    total += storage.esi_stock_at_location(type_id, None)
    incoming = storage.esi_incoming_industry_qty(type_id)
    if incoming["runs"] and bp is not None:
        _, _, product_qty = bp
        total += incoming["runs"] * product_qty
    return total


def _stock_on_hand(type_id: int, manual_stock: dict[int, float]) -> float:
    """Physically-existing stock right now: manual entry + ESI assets, same
    scope as _current_stock above, but *without* its "industry jobs already
    in progress count as good as in stock" addition - an active/paused job's
    eventual output isn't real inventory yet, so it can't be handed to a
    *different* job you're trying to actually start in EVE right now.

    Ported from the parent's confirmed real bug fix (2026-08-15): plan_asset_
    optimized's readiness split used to reuse the incoming-inclusive
    _current_stock as its "available right now" pool - when several jobs
    shared a scarce material with a batch already cooking, every one of them
    could show as "Ready Now" against that same not-yet-delivered batch, so
    the Bauliste claimed more was startable right now than physically
    existed. _current_stock itself is still correct for job/shortfall
    *sizing* (so as not to recommend building a duplicate batch of something
    already queued) - only the "can I click start right now" readiness
    signal needs this on-hand-only number instead."""
    return manual_stock.get(type_id, 0.0) + storage.esi_stock_at_location(type_id, None)


def _base_runs(cfg: ProductionConfig, home: dict, jita: dict, cost_memo: dict[int, Optional[float]],
               selected_decryptors: dict[int, str], t2_memo: dict[int, T2Mods],
               cost_indices: CostIndices, adjusted_prices: dict[int, float],
               stock_targets: list[tuple[int, str, float, bool]],
               manual_overrides: Optional[dict[int, str]] = None) -> dict[int, float]:
    """Pure structural run count per type_id, completely ignoring current
    stock and never buffered by overbuild: if every stock target's full
    target quantity had to be built from absolute zero, how many runs of each
    item would that take. Used only as a *stable* sizing baseline for
    _expand_all's overbuild buffer - it must not shrink just because some
    stock happens to be on hand right now, and must not itself compound level
    over level (see _expand_all's docstring for why a naive multiplicative
    buffer would).

    `manual_overrides` (storage.load_manual_build_buy - a caller-forced
    "always Build"/"always Buy" per type_id, GitHub issue-style manual
    override) is consulted the same way _expand_all consults it: a manually-
    forced Buy stops the walk here exactly like a cost-based "Buy" decision
    would, so base_runs (the overbuild-buffer baseline) never counts runs for
    something the user has said never to build. None (the default) behaves
    exactly as before this parameter existed - every decision purely cost-
    based."""
    base_runs: dict[int, float] = {}
    overrides = manual_overrides or {}

    def expand(type_id: int, quantity: float, depth: int = 0) -> None:
        """Recurses down the BOM, accumulating each type_id's total run count
        into the enclosing `base_runs` (pooled across every branch that needs
        it, same "pool demand, don't double count" shape _expand_all uses) -
        stops at a bought leaf or MAX_DEPTH."""
        if quantity <= 0 or depth >= MAX_DEPTH:
            return
        activity, bp = classify_activity(type_id)
        _unit_cost(type_id, cfg, home, jita, cost_memo, selected_decryptors, t2_memo, cost_indices, adjusted_prices)
        decision = _buy_or_build_decision(type_id, cfg, home, jita, overrides, cost_memo, bp, depth)
        if decision == "Buy" or bp is None:
            return
        blueprint_id, activity_id, product_qty = bp
        runs = math.ceil(quantity / product_qty)
        base_runs[type_id] = base_runs.get(type_id, 0.0) + runs
        material_mult, _, _ = _material_mult_for(type_id, activity, bp, cfg, home, jita,
                                                  selected_decryptors, t2_memo, {})
        for material_id, base_qty in storage.get_blueprint_materials(blueprint_id, activity_id):
            expand(material_id, _material_qty(base_qty, material_mult, runs), depth + 1)

    for type_id, _type_name, quantity, _jita_target in stock_targets:
        expand(type_id, quantity)
    return base_runs


def _parent_base_runs_for_buffer(type_id: int, base_runs: dict[int, float],
                                 buffered_parents: set[int]) -> float:
    """The overbuild-buffer baseline to use for `type_id`'s materials *this*
    round: base_runs[type_id] (its whole-tree, stock-oblivious aggregate run
    count - see _base_runs) the *first* time type_id is processed as a
    parent across the whole call, 0.0 every later time. Mutates
    `buffered_parents` as a side effect (marks type_id buffered).

    Prevents double-counting: a widely-shared component can legitimately
    reappear as a parent in a *later* round (reached at a different BOM depth
    from a different stock target) - without this guard, that reappearance
    would read the same whole-tree aggregate fresh again and add a second
    full buffer on top of the first."""
    if type_id in buffered_parents:
        return 0.0
    buffered_parents.add(type_id)
    return base_runs.get(type_id, 0.0)


def _expand_all(seed_missing: dict[int, float], cfg: ProductionConfig, home: dict, jita: dict,
                cost_memo: dict[int, Optional[float]], selected_decryptors: dict[int, str],
                t2_memo: dict[int, T2Mods], manual_stock: dict[int, float], stock_used: dict[int, float],
                base_runs: dict[int, float],
                gross_demand: Optional[dict[int, float]] = None,
                ignore_current_stock: bool = False,
                manual_overrides: Optional[dict[int, str]] = None,
                ) -> tuple[dict[int, float], dict[tuple[int, int, int], int]]:
    """Resolves every stock target's `seed_missing` quantity into buy_totals
    ({type_id: qty}) and build_runs ({(blueprint_id, activity_id,
    product_type_id): runs}), using the buy-vs-build decisions already
    computed in cost_memo.

    `gross_demand`, if given, is mutated in place with each material's total
    *pre-stock-netting* pooled demand, accumulated across every round - used
    by plan_production for the Buy List's "on hand %" column. Seed it with
    each top-level stock target's own gross quantity before calling this, so
    a type_id that's *both* a stock target and a shared material downstream
    gets one correctly-combined total.

    Processed breadth-first, level by level (one dict of pooled per-type_id
    quantities per round) rather than depth-first per stock target/edge -
    required, not just a style choice: a shared material (e.g. a reaction
    input feeding a dozen different Tech II components) must be decided on
    and expanded *once*, using its *total* pooled demand, not once per parent
    edge that happens to reach it.

    Each material's target = gross pooled demand (base_qty x material_mult x
    runs, summed across every parent claiming it this round) + an overbuild
    buffer (cfg.component_overbuild x base_qty x material_mult x
    base_runs[parent], summed the same way, deliberately from parents' *base*
    runs rather than their final total runs) - this avoids what a naive
    target = gross_demand * (1 + overbuild) would do: since gross_demand
    already carries the previous level's buffer, multiplying it *again* would
    compound the buffer exponentially with recursion depth.

    `stock_used` is a running ledger (seeded by the caller for top-level
    stock targets, mutated here for everything below them) so a component
    needed by two different branches doesn't have the same physical stock
    counted against both of them.

    `manual_overrides` (storage.load_manual_build_buy) is passed straight
    through to `_buy_or_build_decision` (a manually-forced Build/Buy always
    wins over the modeled cost comparison) and additionally skips the
    overbuild buffer for any material with a manual override of its own -
    matching the parent's own reasoning: a manually-forced decision already
    says exactly how much of that material to source, so padding its target
    with an extra buffer on top second-guesses a choice the user made on
    purpose. None (the default) behaves exactly as before this parameter
    existed - every decision purely cost-based and every material gets the
    full overbuild buffer.

    `ignore_current_stock`, if True, treats every material's on-hand stock as
    0 regardless of what _current_stock would actually report - used by
    plan_special_order's own net_against_stock=False ("from scratch") mode,
    so a one-off order can be priced/planned independently of whatever's
    currently sitting in the hangar. Default False preserves plan_production's
    existing behavior exactly."""
    buy_totals: dict[int, float] = {}
    build_runs: dict[tuple[int, int, int], int] = {}
    buffered_parents: set[int] = set()
    overrides = manual_overrides or {}

    jobs_this_level: dict[int, float] = {tid: q for tid, q in seed_missing.items() if q > 0}
    depth = 0
    while jobs_this_level and depth < MAX_DEPTH:
        next_level: dict[int, float] = {}

        for type_id, quantity in jobs_this_level.items():
            activity, bp = classify_activity(type_id)
            decision = _buy_or_build_decision(type_id, cfg, home, jita, overrides, cost_memo, bp, depth)
            if decision == "Buy" or bp is None:
                buy_totals[type_id] = buy_totals.get(type_id, 0) + quantity
                continue

            blueprint_id, activity_id, product_qty = bp
            runs = math.ceil(quantity / product_qty)
            key = (blueprint_id, activity_id, type_id)
            build_runs[key] = build_runs.get(key, 0) + runs

            material_mult, _, _ = _material_mult_for(type_id, activity, bp, cfg, home, jita,
                                                      selected_decryptors, t2_memo, {})
            parent_base_runs = _parent_base_runs_for_buffer(type_id, base_runs, buffered_parents)
            for material_id, base_qty in storage.get_blueprint_materials(blueprint_id, activity_id):
                gross_needed = _material_qty(base_qty, material_mult, runs)
                if gross_needed <= 0:
                    continue
                overbuild = 0.0 if material_id in overrides else cfg.component_overbuild
                buffer = overbuild * base_qty * material_mult * parent_base_runs
                next_level[material_id] = next_level.get(material_id, 0.0) + gross_needed + buffer

        jobs_this_level = {}
        for material_id, target in next_level.items():
            if target <= 0:
                continue
            if gross_demand is not None:
                gross_demand[material_id] = gross_demand.get(material_id, 0.0) + target
            _, m_bp = classify_activity(material_id)
            if ignore_current_stock:
                available = 0.0
            else:
                available = max(0.0, _current_stock(material_id, manual_stock, m_bp) - stock_used.get(material_id, 0.0))
            consumed = min(target, available)
            stock_used[material_id] = stock_used.get(material_id, 0.0) + consumed
            net_needed = target - consumed
            if net_needed > 0:
                jobs_this_level[material_id] = jobs_this_level.get(material_id, 0.0) + net_needed

        depth += 1

    return buy_totals, build_runs


def _build_buy_list(buy_totals: dict[int, float], gross_demand: dict[int, float],
                    cfg: ProductionConfig, home: dict, jita: dict) -> list[BuyListEntry]:
    """Turns _expand_all's buy_totals into BuyListEntry rows, sorted by total
    price desc - factored out of plan_production so plan_special_order can
    build the exact same shape from its own buy_totals/gross_demand without
    duplicating this loop."""
    buy_list = []
    for type_id, quantity in buy_totals.items():
        sde_type = storage.get_sde_type(type_id)
        name = sde_type[2] if sde_type else str(type_id)
        volume = _haul_volume(type_id, cfg)
        unit_price = pricing.buy_price(type_id, home, jita, volume, cfg)
        gross = gross_demand.get(type_id, quantity)
        on_hand_pct = max(0.0, min(100.0, (gross - quantity) / gross * 100)) if gross > 0 else 0.0
        buy_list.append(BuyListEntry(
            type_id=type_id, type_name=name, quantity=quantity, unit_price=unit_price,
            total_price=(unit_price * quantity) if unit_price is not None else None,
            on_hand_pct=on_hand_pct, buy_from=pricing.buy_source(type_id, home, jita, volume, cfg),
        ))
    buy_list.sort(key=lambda e: e.total_price or 0, reverse=True)
    return buy_list


def _build_build_list(build_runs: dict[tuple[int, int, int], int], cost_memo: dict[int, Optional[float]],
                      t2_memo: dict[int, T2Mods], cfg: ProductionConfig, home: dict) -> list[BuildJobEntry]:
    """Turns _expand_all's build_runs into BuildJobEntry rows, sorted by job
    runs desc - factored out of plan_production, same reasoning as
    _build_buy_list above."""
    build_list = []
    for (blueprint_id, activity_id, product_type_id), runs in build_runs.items():
        sde_type = storage.get_sde_type(product_type_id)
        name = sde_type[2] if sde_type else str(product_type_id)
        activity_label = "Reaction" if activity_id == ACTIVITY_REACTION else "Manufacturing"
        bp = storage.get_blueprint_for_product(product_type_id)
        product_qty = bp[2] if bp else 1
        decryptor_name = t2_memo[product_type_id][2] if product_type_id in t2_memo else None
        unit_cost = cost_memo.get(product_type_id)
        build_list.append(BuildJobEntry(
            type_id=product_type_id, type_name=name, blueprint_type_id=blueprint_id,
            activity=activity_label, quantity=runs * product_qty, job_runs=runs,
            unit_build_cost=unit_cost, decryptor=decryptor_name,
            job_category=job_category(product_type_id),
            # margin_home, not margin_jita - Production sells only at home,
            # never Jita (see CLAUDE.md); this is the real sell-side margin
            # for the item as actually built, independent of any gate that
            # decided *whether* to build at all.
            margin=margin_home(product_type_id, unit_cost, home, cfg),
        ))
    build_list.sort(key=lambda e: e.job_runs, reverse=True)
    return build_list


def plan_production(cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Runs the full Stock Targets -> Inventory -> Buy/Build pipeline. Returns
    a dict with 'inventory' (InventoryRow list), 'buy_list' (BuyListEntry
    list, sorted by total price desc), 'build_list' (BuildJobEntry list,
    sorted by job_runs desc), and 'invention_list' (InventionNeedRow list,
    sorted by recommended invention runs desc - one row per configured Tech
    II/III stock target that's actually invention-sourced, regardless of
    whether it's currently missing or would be bought instead of built right
    now; runs_needed/bpcs_needed/recommended_invention_runs are 0 for a
    target that's already fully stocked).

    A stock target's demand only feeds the Buy List/Build List at all if
    building it clears cfg.min_margin (via _build_margin) - matching the
    parent's confirmed behavior: if building isn't profitable, buying isn't
    assumed to be either, since both ultimately compete on the same sell
    price. InventoryRow.total_missing still reports the real shortfall
    regardless of margin; this gate only controls whether that shortfall
    turns into an actual buy/build recommendation.

    Deliberately not ported (see module docstring/SYNC.md): the per-run
    job_time column on BuildJobEntry (needs storage.get_blueprint_time -
    which now exists, but nothing here has a use for it outside the
    invention-needs list and plan_asset_optimized, unlike the parent's
    BuildJobEntry.job_time_seconds), and the logistics/distribution helpers
    (see engine.py's own module docstring for that decision)."""
    stock_targets = storage.load_stock_targets()
    manual_stock = storage.load_manual_stock()
    manual_overrides = storage.load_manual_build_buy()
    cost_memo: dict[int, Optional[float]] = {}
    t2_memo: dict[int, T2Mods] = {}
    selected_decryptors: dict[int, str] = {}  # no manual-decryptor table exists here - see SYNC.md

    priced_type_ids = list(structural_material_closure(t[0] for t in stock_targets))
    home = pricing.home_prices(priced_type_ids, cfg)
    jita = pricing.jita_prices(priced_type_ids)

    cost_indices: CostIndices = {
        "component": pricing.cached_system_cost_indices(cfg.component_system_id),
        "manufacturing": pricing.cached_system_cost_indices(cfg.manufacturing_system_id),
    }
    adjusted_prices = pricing.cached_adjusted_prices()

    inventory: list[InventoryRow] = []
    invention_list: list[InventionNeedRow] = []
    # Ledger of how much of each type_id's own current stock has already been
    # counted toward some demand this run - threaded through _expand_all (see
    # its docstring) so a component needed by two different branches doesn't
    # have the same physical stock counted against both. Also seeded below
    # for top-level stock targets.
    stock_used: dict[int, float] = {}
    # Stock-oblivious sizing baseline for _expand_all's overbuild buffer,
    # computed once over every stock target's full target quantity, not just
    # what's currently missing.
    base_runs = _base_runs(cfg, home, jita, cost_memo, selected_decryptors, t2_memo,
                           cost_indices, adjusted_prices, stock_targets, manual_overrides)
    seed_missing: dict[int, float] = {}
    gross_demand: dict[int, float] = {}

    for type_id, type_name, quantity, jita_target in stock_targets:
        activity, bp = classify_activity(type_id)
        current_stock = _current_stock(type_id, manual_stock, bp)
        missing = max(0.0, quantity - current_stock)
        stock_used[type_id] = stock_used.get(type_id, 0.0) + min(current_stock, quantity)
        inventory.append(InventoryRow(
            type_id=type_id, type_name=type_name, activity=activity,
            target=quantity, current_stock=current_stock, total_missing=missing,
        ))

        # Every configured Tech II/III stock target gets an invention-needs
        # row, independent of whether it's currently missing or whether the
        # margin gate below would drop it - the point of this list is
        # forward planning ("what would it take to invent enough BPCs"), not
        # just today's Bauliste. Reuses _tech_ii_mods' own chosen
        # InventionResult (grade x decryptor already optimized there)
        # instead of re-resolving the recipe from scratch - see the parent's
        # own confirmed fix for why that duplicate computation matters.
        if activity == "Tech II" and bp is not None:
            blueprint_id, activity_id, product_qty = bp
            _, _, _, chosen = _tech_ii_mods(type_id, blueprint_id, activity_id, cfg, home, jita,
                                            selected_decryptors, t2_memo)
            if chosen is not None and chosen.output_runs > 0 and chosen.probability > 0:
                t2_bpc_owned = int(storage.available_blueprint_copies(blueprint_id, None))
                runs_needed = math.ceil(missing / product_qty) if missing > 0 else 0
                runs_still_needed = max(0, runs_needed - t2_bpc_owned)
                bpcs_needed = math.ceil(runs_still_needed / chosen.output_runs) if runs_still_needed > 0 else 0
                recommended_runs = math.ceil(bpcs_needed / chosen.probability) if bpcs_needed > 0 else 0
                target_stock_runs = math.ceil(quantity / product_qty) if quantity > 0 else 0
                stockpile_pct = (max(0.0, t2_bpc_owned / target_stock_runs * 100)
                                 if target_stock_runs > 0 else 0.0)
                invention_list.append(InventionNeedRow(
                    type_id=type_id, type_name=type_name,
                    t1_blueprint_type_id=chosen.t1_blueprint_type_id, t1_blueprint_name=chosen.t1_blueprint_name,
                    decryptor=chosen.decryptor, probability=chosen.probability, output_runs=chosen.output_runs,
                    runs_needed=runs_needed, bpcs_needed=bpcs_needed,
                    recommended_invention_runs=recommended_runs,
                    t2_bpc_owned=t2_bpc_owned, stockpile_pct=stockpile_pct,
                ))

        if missing <= 0:
            continue

        _unit_cost(type_id, cfg, home, jita, cost_memo, selected_decryptors, t2_memo, cost_indices, adjusted_prices)
        skip_due_to_margin = False
        # A manual override always wins - if the user has said "always Build"
        # (or "always Buy") for this type_id, the min_margin gate below (which
        # only exists to decide whether a modeled Build is worth recommending
        # at all) doesn't apply: the decision is no longer cost-based.
        if bp is not None and type_id not in manual_overrides:
            margin = _build_margin(type_id, cost_memo.get(type_id), jita_target, home, jita, cfg)
            if margin is not None and margin < cfg.min_margin:
                skip_due_to_margin = True
        if skip_due_to_margin:
            continue

        seed_missing[type_id] = missing
        gross_demand[type_id] = gross_demand.get(type_id, 0.0) + quantity

    buy_totals, build_runs = _expand_all(seed_missing, cfg, home, jita, cost_memo, selected_decryptors,
                                         t2_memo, manual_stock, stock_used, base_runs, gross_demand,
                                         manual_overrides=manual_overrides)

    buy_list = _build_buy_list(buy_totals, gross_demand, cfg, home, jita)
    build_list = _build_build_list(build_runs, cost_memo, t2_memo, cfg, home)
    invention_list.sort(key=lambda e: e.recommended_invention_runs, reverse=True)

    return {"inventory": inventory, "buy_list": buy_list, "build_list": build_list,
            "invention_list": invention_list}


def _allocate_scarce_stock(claims: list[tuple[int, float]], available: float) -> dict[int, float]:
    """Splits `available` units of a scarce shared material across competing
    claims (parent_job_type_id, quantity_needed): smallest claim first, each
    filled completely while stock lasts - maximizes the *count* of claims
    that end up fully covered rather than spreading partial coverage across
    everything (ported verbatim from the parent's confirmed allocation rule
    for plan_asset_optimized). Returns {parent_job_type_id: quantity_covered},
    one entry per input claim (0.0 once stock runs out)."""
    remaining = available
    covered: dict[int, float] = {}
    for parent_id, qty in sorted(claims, key=lambda c: c[1]):
        take = min(qty, remaining)
        covered[parent_id] = take
        remaining -= take
    return covered


def plan_asset_optimized(cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Asset-aware Bauliste: the same buy-vs-build calls as plan_production
    (_buy_or_build_decision - the two Baulisten never disagree on *whether*
    to build something), but nets real owned stock (_current_stock) against
    demand at *every* level of the recursive bill of materials, not just the
    top-level stock target the way plan_production's 'missing' does.

    Processed breadth-first, level by level (a "round" per depth), because a
    level's material demand is only known once the level above it has been
    netted against stock. When several jobs need the same scarce
    intermediate component this round, the available stock goes to the
    *smallest* claims first (_allocate_scarce_stock) so as many whole jobs
    as possible become immediately startable. Stock consumption is tracked
    in a running ledger across rounds (stock_used) so a component needed at
    two different tree depths doesn't get the same physical stock counted
    twice.

    Readiness (runs_ready_now) specifically uses _stock_on_hand, not
    _current_stock, with its own parallel ledger (stock_used_on_hand) - see
    _stock_on_hand's own docstring for the real bug this exists to avoid:
    _current_stock counts an in-progress industry job's eventual output as
    available for *sizing* purposes, but that output doesn't physically
    exist yet, so it can't make a *different* job "Ready Now".

    Each material's per-parent claim also carries the same overbuild buffer
    _expand_all applies (cfg.component_overbuild x base_qty x material_mult
    x that parent's whole-tree base_runs, via the shared
    _parent_base_runs_for_buffer helper) - the two Baulisten must agree on
    *how much* to build, not just *whether*.

    Returns {'jobs': list[AssetPlanJob]} sorted by job_runs desc. A job's
    runs_ready_now is bottlenecked by whichever of its own direct materials
    is scarcest right now (the rest of job_runs still needs something
    upstream built or bought first).

    Deliberately narrower than the parent's version, matching the parent's
    own confirmed simplifications this repo has already made elsewhere:
    - No recommended_slots/_free_slots_by_category - that needs live
      character-skill/job-slot ESI data plus a per-character "excluded from
      planning" flag, neither of which this repo syncs (production/
      esi_sync.py's PRODUCTION_SCOPES deliberately doesn't request skill
      scopes - see that module's own SYNC.md row). runs_ready_now/job_runs
      is still a real, useful readiness signal without the slot split on
      top of it.
    - stock_coverage_by_id's "configured stock target" denominator is this
      repo's own single target quantity, not the parent's backup+home+Jita
      sum - matching plan_production's own already-simplified model."""
    stock_targets = storage.load_stock_targets()
    manual_stock = storage.load_manual_stock()
    manual_overrides = storage.load_manual_build_buy()
    cost_memo: dict[int, Optional[float]] = {}
    t2_memo: dict[int, T2Mods] = {}
    selected_decryptors: dict[int, str] = {}  # no manual-decryptor table exists here - see SYNC.md

    priced_type_ids = list(structural_material_closure(t[0] for t in stock_targets))
    home = pricing.home_prices(priced_type_ids, cfg)
    jita = pricing.jita_prices(priced_type_ids)

    cost_indices: CostIndices = {
        "component": pricing.cached_system_cost_indices(cfg.component_system_id),
        "manufacturing": pricing.cached_system_cost_indices(cfg.manufacturing_system_id),
    }
    adjusted_prices = pricing.cached_adjusted_prices()

    jobs: dict[int, AssetPlanJob] = {}
    stock_used: dict[int, float] = {}
    # Parallel ledger, on-hand-only (see _stock_on_hand) - tracks readiness
    # consumption separately from stock_used's incoming-inclusive planning
    # consumption.
    stock_used_on_hand: dict[int, float] = {}
    base_runs = _base_runs(cfg, home, jita, cost_memo, selected_decryptors, t2_memo,
                           cost_indices, adjusted_prices, stock_targets, manual_overrides)
    buffered_parents: set[int] = set()

    # Same margin gate as plan_production: a stock target's demand is
    # dropped entirely (no job here) if building it doesn't clear
    # cfg.min_margin - accumulated across every top-level target first, then
    # reused for the recursive per-material decision below, so a low-margin
    # stock target that's *also* a raw material of another job can't be
    # excluded from plan_production but still "Build" here for the same
    # type_id (confirmed real bug in the parent this guards against).
    margin_excluded: set[int] = set()
    jobs_this_level: dict[int, float] = {}
    # How much of type_id's own current demand is already covered by owned
    # stock (0-1, None if there's nothing to compare against) - purely a
    # display signal, not used in the sourcing/queueing math above.
    stock_coverage_by_id: dict[int, Optional[float]] = {}
    for type_id, type_name, quantity, jita_target in stock_targets:
        activity, bp = classify_activity(type_id)
        if bp is None:
            continue
        current_stock = _current_stock(type_id, manual_stock, bp)
        missing = max(0.0, quantity - current_stock)
        if missing <= 0:
            continue
        _unit_cost(type_id, cfg, home, jita, cost_memo, selected_decryptors, t2_memo, cost_indices, adjusted_prices)

        if type_id not in manual_overrides:
            margin = _build_margin(type_id, cost_memo.get(type_id), jita_target, home, jita, cfg)
            if margin is not None and margin < cfg.min_margin:
                margin_excluded.add(type_id)
        if type_id in margin_excluded:
            continue
        if _buy_or_build_decision(type_id, cfg, home, jita, manual_overrides, cost_memo, bp) != "Build":
            continue
        jobs_this_level[type_id] = jobs_this_level.get(type_id, 0.0) + missing
        stock_coverage_by_id[type_id] = min(1.0, current_stock / quantity) if quantity > 0 else None

    depth = 0
    while jobs_this_level and depth < MAX_DEPTH:
        # Phase A: this round's net production quantity -> runs + raw
        # material claims (not yet netted against stock).
        runs_this_round: dict[int, int] = {}
        job_meta: dict[int, tuple] = {}
        next_level_claims: dict[int, list[tuple[int, float]]] = {}
        buffered_demand: dict[int, float] = {}

        for type_id, quantity in jobs_this_level.items():
            activity, bp = classify_activity(type_id)
            if bp is None:
                continue
            blueprint_id, activity_id, product_qty = bp
            runs = math.ceil(quantity / product_qty)
            runs_this_round[type_id] = runs

            material_mult, _job_cost_rate, decryptor_name = _material_mult_for(
                type_id, activity, bp, cfg, home, jita, selected_decryptors, t2_memo, {})
            activity_label = "Reaction" if activity_id == ACTIVITY_REACTION else "Manufacturing"
            job_meta[type_id] = (blueprint_id, activity_id, product_qty, activity_label, decryptor_name)

            parent_base_runs = _parent_base_runs_for_buffer(type_id, base_runs, buffered_parents)
            for material_id, base_qty in storage.get_blueprint_materials(blueprint_id, activity_id):
                bare_needed = _material_qty(base_qty, material_mult, runs)
                if bare_needed <= 0:
                    continue
                next_level_claims.setdefault(material_id, []).append((type_id, bare_needed))
                overbuild = 0.0 if material_id in manual_overrides else cfg.component_overbuild
                buffer = overbuild * base_qty * material_mult * parent_base_runs
                buffered_demand[material_id] = buffered_demand.get(material_id, 0.0) + bare_needed + buffer

        # Phase B: readiness (min_fraction) is allocated across the *bare*
        # per-job claims, smallest first, against stock physically on hand
        # right now - purely informational. Actual consumption/shortfall
        # sizing uses the *buffered* pooled total against _current_stock's
        # incoming-inclusive availability instead.
        min_fraction: dict[int, float] = {type_id: 1.0 for type_id in runs_this_round}
        jobs_this_level = {}
        for material_id, claims in next_level_claims.items():
            _, m_bp = classify_activity(material_id)
            available = max(0.0, _current_stock(material_id, manual_stock, m_bp) - stock_used.get(material_id, 0.0))
            available_on_hand = max(
                0.0, _stock_on_hand(material_id, manual_stock) - stock_used_on_hand.get(material_id, 0.0))
            covered_by_parent = _allocate_scarce_stock(claims, available_on_hand)
            stock_used_on_hand[material_id] = stock_used_on_hand.get(material_id, 0.0) + sum(covered_by_parent.values())

            for parent_type_id, qty in claims:
                fraction = covered_by_parent.get(parent_type_id, 0.0) / qty if qty > 0 else 1.0
                if fraction < min_fraction[parent_type_id]:
                    min_fraction[parent_type_id] = fraction

            buffered_total = buffered_demand.get(material_id, 0.0)
            consumed = min(buffered_total, available)
            stock_used[material_id] = stock_used.get(material_id, 0.0) + consumed
            shortfall = buffered_total - consumed
            if shortfall <= 0 or m_bp is None:
                continue
            if material_id not in margin_excluded and _buy_or_build_decision(
                    material_id, cfg, home, jita, manual_overrides, cost_memo, m_bp, depth + 1) == "Build":
                jobs_this_level[material_id] = jobs_this_level.get(material_id, 0.0) + shortfall
                if material_id not in stock_coverage_by_id:
                    stock_coverage_by_id[material_id] = available / buffered_total if buffered_total > 0 else None

        # Phase C: materialize/merge this round's AssetPlanJob entries now
        # that readiness is known.
        for type_id, runs in runs_this_round.items():
            blueprint_id, activity_id, product_qty, activity_label, decryptor_name = job_meta[type_id]
            sde_type = storage.get_sde_type(type_id)
            name = sde_type[2] if sde_type else str(type_id)
            ready_increment = math.floor(runs * min_fraction[type_id])

            existing = jobs.get(type_id)
            if existing is None:
                jobs[type_id] = AssetPlanJob(
                    type_id=type_id, type_name=name, blueprint_type_id=blueprint_id,
                    activity=activity_label, quantity=runs * product_qty, job_runs=runs,
                    runs_ready_now=ready_increment,
                    unit_build_cost=cost_memo.get(type_id), decryptor=decryptor_name,
                    job_category=job_category(type_id),
                    stock_coverage=stock_coverage_by_id.get(type_id),
                    margin=margin_home(type_id, cost_memo.get(type_id), home, cfg),
                )
            else:
                # Same item is a job in more than one round (needed at two
                # different tree depths) - merge rather than overwrite, so an
                # earlier round's already-netted readiness isn't lost.
                existing.job_runs += runs
                existing.quantity += runs * product_qty
                existing.runs_ready_now += ready_increment

        depth += 1

    return {"jobs": sorted(jobs.values(), key=lambda j: j.job_runs, reverse=True)}


def plan_special_order(items: list[tuple[int, str, float]], cfg: ProductionConfig,
                       net_against_stock: bool) -> dict:
    """One-off order variant of plan_production - seeded from `items`
    ((type_id, type_name, quantity) tuples, a Special Order's own line items)
    instead of storage.load_stock_targets(). Reuses the exact same buy-vs-
    build/pricing engine (_base_runs, _expand_all, _build_buy_list/
    _build_build_list) so the two Baulisten can never drift apart on *how*
    they price or decide build-vs-buy - only on *what* demand they start from
    and how stock is (or isn't) netted.

    Two deliberate differences from plan_production, matching the parent's
    own confirmed behavior (see SYNC.md):
    - No margin gate: an order must be fulfilled regardless of whether
      building clears cfg.min_margin - margin is still visible on each
      BuildJobEntry, just never used to drop an item from the result.
    - `net_against_stock` (per order, not per line item) picks between two
      whole-order modes: False (default) plans "from scratch", entirely
      ignoring current stock (_expand_all's ignore_current_stock=True, plus
      no top-level stock netting on the line items themselves either); True
      nets against real ESI-synced stock the same way plan_production does.
      True also triggers a stock_overlap_warning: every item that's both
      reachable from this order's own material tree AND from the configured
      stock_targets' own tree AND currently has stock on hand right now - a
      heads-up that the same physical stock might get "claimed" by both this
      order and a separately-computed regular Bauliste, which don't
      otherwise know about each other. This is a structural/currently-in-
      stock signal, not a precise "did the regular plan actually consume it"
      check (that would need re-running plan_production itself).

    Deliberately simpler than the parent's version, matching plan_production's
    own already-documented simplifications: no invention-needs list for a
    one-off order (there's no fixed steady-state target the way a stock
    target's own quantity gives plan_production's list a stockpile_pct
    denominator - see that function's own row in this module)."""
    manual_stock = storage.load_manual_stock()
    manual_overrides = storage.load_manual_build_buy()
    cost_memo: dict[int, Optional[float]] = {}
    t2_memo: dict[int, T2Mods] = {}
    selected_decryptors: dict[int, str] = {}  # no manual-decryptor table exists here - see SYNC.md
    line_items: list[SpecialOrderLineItem] = [
        SpecialOrderLineItem(type_id=type_id, type_name=type_name, quantity=quantity)
        for type_id, type_name, quantity in items
    ]

    # Priced universe is this order's own material closure only - unlike the
    # parent's _PlanContext, this repo's plan_production doesn't share a
    # price fetch with plan_special_order either (see its own inline
    # priced_type_ids), so there's no shared cache to fold this into.
    priced_type_ids = list(structural_material_closure(type_id for type_id, _n, _q in items))
    home = pricing.home_prices(priced_type_ids, cfg)
    jita = pricing.jita_prices(priced_type_ids)

    cost_indices: CostIndices = {
        "component": pricing.cached_system_cost_indices(cfg.component_system_id),
        "manufacturing": pricing.cached_system_cost_indices(cfg.manufacturing_system_id),
    }
    adjusted_prices = pricing.cached_adjusted_prices()

    # Same pooling ledger _expand_all itself uses (see its own docstring) -
    # seeded here for the order's own top-level items the same way
    # plan_production seeds it for top-level stock targets, so a line item
    # that's *also* a shared material downstream doesn't get the same
    # physical stock counted against both.
    stock_used: dict[int, float] = {}
    synthetic_stock_targets = [(type_id, type_name, quantity, False) for type_id, type_name, quantity in items]
    base_runs = _base_runs(cfg, home, jita, cost_memo, selected_decryptors, t2_memo,
                           cost_indices, adjusted_prices, synthetic_stock_targets, manual_overrides)

    seed_missing: dict[int, float] = {}
    gross_demand: dict[int, float] = {}

    for type_id, _type_name, quantity in items:
        _activity, bp = classify_activity(type_id)
        if net_against_stock:
            current_stock = _current_stock(type_id, manual_stock, bp)
            missing = max(0.0, quantity - current_stock)
            stock_used[type_id] = stock_used.get(type_id, 0.0) + min(current_stock, quantity)
        else:
            missing = quantity
        if missing <= 0:
            continue
        _unit_cost(type_id, cfg, home, jita, cost_memo, selected_decryptors, t2_memo, cost_indices, adjusted_prices)
        # No margin gate here (see docstring) - every missing quantity feeds
        # the Buy/Build result regardless of cfg.min_margin.
        seed_missing[type_id] = missing
        gross_demand[type_id] = gross_demand.get(type_id, 0.0) + quantity

    buy_totals, build_runs = _expand_all(seed_missing, cfg, home, jita, cost_memo, selected_decryptors,
                                         t2_memo, manual_stock, stock_used, base_runs, gross_demand,
                                         ignore_current_stock=not net_against_stock,
                                         manual_overrides=manual_overrides)

    buy_list = _build_buy_list(buy_totals, gross_demand, cfg, home, jita)
    build_list = _build_build_list(build_runs, cost_memo, t2_memo, cfg, home)

    stock_overlap_warning: list[StockOverlapWarningRow] = []
    if net_against_stock:
        order_closure = structural_material_closure(type_id for type_id, _n, _q in items)
        targets_closure = structural_material_closure(t[0] for t in storage.load_stock_targets())
        for type_id in sorted(order_closure & targets_closure):
            _, m_bp = classify_activity(type_id)
            stock = _current_stock(type_id, manual_stock, m_bp)
            if stock > 0:
                sde_type = storage.get_sde_type(type_id)
                name = sde_type[2] if sde_type else str(type_id)
                stock_overlap_warning.append(StockOverlapWarningRow(type_id=type_id, type_name=name,
                                                                     current_stock=stock))

    return {
        "line_items": line_items, "buy_list": buy_list, "build_list": build_list,
        "stock_overlap_warning": stock_overlap_warning,
    }


# --------------------------------------------------------------- cheap reads
def market_status(cfg: ProductionConfig = PRODUCTION_CONFIG) -> list[MarketStatusRow]:
    """Cheap stock-target readiness read: target vs. current_stock only, with
    no pricing/BOM traversal - unlike plan_production's InventoryRow (which
    is always computed as a side effect of the full priced Bauliste pass),
    this needs no live home/Jita/cost-index ESI calls at all, just the
    already-synced asset snapshot plus the manual-stock table - useful for a
    quick "am I stocked" glance.

    Simplified from the parent's three-way backup/home-market/Jita-market
    model (see storage.py's stock_targets table comment: this repo's own
    stock target is one target quantity plus jita_target) - there is no
    separate "how much is actually listed for sale" signal to show, since
    there's no market-target column distinct from the stock target itself."""
    manual_stock = storage.load_manual_stock()
    rows = []
    for type_id, type_name, target, jita_target in storage.load_stock_targets():
        _, bp = classify_activity(type_id)
        current = _current_stock(type_id, manual_stock, bp)
        rows.append(MarketStatusRow(
            type_id=type_id, type_name=type_name, target=target, current_stock=current,
            missing=max(0.0, target - current), jita_target=jita_target,
        ))
    return rows


def stock_value(cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Total ISK value of current stock (manual + ESI assets + incoming jobs,
    the same _current_stock every stock target uses), priced at each item's
    home sell quote - its real opportunity-cost value even for stock that's
    actually consumed internally rather than resold - falling back to the
    Jita sell quote if there's no home listing. Returns {'total_value': ISK,
    'priced_items': N, 'unpriced_items': N} - unpriced items (no sell quote
    anywhere) are excluded from total_value rather than counted as 0, so a
    temporary market-data gap doesn't silently understate the total."""
    manual_stock = storage.load_manual_stock()
    stock_targets = storage.load_stock_targets()
    # Only the stock targets' own type_ids need pricing here (this KPI never
    # recurses into materials).
    type_ids = [t[0] for t in stock_targets]
    home = pricing.home_prices(type_ids, cfg)
    jita = pricing.jita_prices(type_ids)

    total_value = 0.0
    priced_items = 0
    unpriced_items = 0
    for type_id, _type_name, _target, _jita_target in stock_targets:
        _, bp = classify_activity(type_id)
        current = _current_stock(type_id, manual_stock, bp)
        if current <= 0:
            continue
        home_quote = home.get(type_id)
        jita_quote = jita.get(type_id)
        if home_quote and home_quote.sell > 0:
            price = home_quote.sell
        elif jita_quote and jita_quote.sell > 0:
            price = jita_quote.sell
        else:
            unpriced_items += 1
            continue
        total_value += current * price
        priced_items += 1
    return {"total_value": total_value, "priced_items": priced_items, "unpriced_items": unpriced_items}


# --------------------------------------------------------------- ship margins
def _descendant_market_group_ids(root_id: int) -> set[int]:
    """`root_id` plus every market_group_id nested under it (any depth) in
    sde_market_groups' parent_group_id tree - lets a caller exclude a whole
    named market-group subtree (e.g. "Special Edition Ships") instead of an
    ad-hoc type_id/name list that would miss anything CCP adds later."""
    children: dict[Optional[int], list[int]] = {}
    for market_group_id, parent_group_id, _name in storage.load_sde_market_groups():
        children.setdefault(parent_group_id, []).append(market_group_id)
    result = {root_id}
    frontier = [root_id]
    while frontier:
        frontier = [child for parent in frontier for child in children.get(parent, [])]
        result.update(frontier)
    return result


def production_price_universe() -> list[int]:
    """The bounded type_id set esi_update.py's Production sync bundle
    refreshes home/Jita prices for: every ship (discover_ship_margins' own
    catalog) plus every configured stock target, both expanded through their
    full material closure - the same bounded universe _scan_ship_margins/
    stock_value already compute for themselves, just fetched once up front
    by the sync bundle instead of live on every view open.

    A build-material-tree/item-margin lookup for something outside this set
    simply has no cached price until it happens to be covered here - the
    same accepted limitation Ore & Minerals' Reprocessing Quote has for
    items outside its own known universe (see esi_update.py's own
    docstring)."""
    excluded_market_groups = _descendant_market_group_ids(SPECIAL_EDITION_SHIPS_MARKET_GROUP_ID)
    ship_ids = [type_id for type_id, _type_name, _volume, market_group_id, _meta_level, category_id
               in storage.load_sde_types_with_market_group()
               if category_id == SHIP_CATEGORY_ID and market_group_id not in excluded_market_groups]
    stock_target_ids = [t[0] for t in storage.load_stock_targets()]
    return list(structural_material_closure(ship_ids + stock_target_ids))


def discover_ship_margins(cfg: ProductionConfig = PRODUCTION_CONFIG,
                          client: Optional["GoonmetricsClient"] = None) -> list[dict]:
    """Production's Margin page (list view): every ship with a real
    Manufacturing/Reaction/Invention recipe (classify_activity), with its
    current home/Jita sell price, build cost, and both margin_home/
    margin_jita - deliberately an information browser, not a candidate
    filter like discover_build_candidates: no existing_target_ids exclusion,
    no min_margin/min_daily_profit gate, no history/movement lookup. A ship
    with no real order book on one or both sides still appears, with None
    for whichever price/margin couldn't be computed.

    Not cached, same reasoning as discover_build_candidates - a single local
    CLI-driven user has no concurrent-request cache to protect against.
    `client` param exists only for parity/tests, same as discover_build_
    candidates' own (unused here - no movement lookup needed for a plain
    margin browser)."""
    return _scan_ship_margins(cfg)


def _scan_ship_margins(cfg: ProductionConfig) -> list[dict]:
    """The actual scan behind discover_ship_margins - split out purely to
    mirror discover_build_candidates/_scan_build_candidates' own split.
    Ships are a much smaller SDE slice than discover_build_candidates' full
    scan, so this is cheap even though it's not gated by margin/daily-profit
    at all."""
    cost_memo: dict[int, Optional[float]] = {}
    t2_memo: dict[int, T2Mods] = {}
    selected_decryptors: dict[int, str] = {}  # no manual-decryptor table exists here - see SYNC.md
    excluded_market_groups = _descendant_market_group_ids(SPECIAL_EDITION_SHIPS_MARKET_GROUP_ID)

    ships = [(type_id, type_name, meta_level)
             for type_id, type_name, _volume, market_group_id, meta_level, category_id
             in storage.load_sde_types_with_market_group()
             if category_id == SHIP_CATEGORY_ID and market_group_id not in excluded_market_groups]

    priced_type_ids = list(structural_material_closure(t[0] for t in ships))
    home = pricing.home_prices(priced_type_ids, cfg)
    jita = pricing.jita_prices(priced_type_ids)

    cost_indices: CostIndices = {
        "component": pricing.cached_system_cost_indices(cfg.component_system_id),
        "manufacturing": pricing.cached_system_cost_indices(cfg.manufacturing_system_id),
    }
    adjusted_prices = pricing.cached_adjusted_prices()

    results = []
    for type_id, type_name, meta_level in ships:
        activity, bp = classify_activity(type_id)
        if bp is None:
            continue
        build_cost = _unit_cost(type_id, cfg, home, jita, cost_memo, selected_decryptors,
                                t2_memo, cost_indices, adjusted_prices)
        home_quote = home.get(type_id)
        jita_quote = jita.get(type_id)
        results.append({
            "type_id": type_id, "type_name": type_name, "activity": activity,
            "home_price": home_quote.sell if home_quote and home_quote.sell > 0 else None,
            "jita_price": jita_quote.sell if jita_quote and jita_quote.sell > 0 else None,
            "build_cost": build_cost,
            "margin_home": margin_home(type_id, build_cost, home, cfg),
            "margin_jita": margin_jita(type_id, build_cost, jita, cfg),
            "meta_level": meta_level,
        })

    results.sort(key=lambda r: r.get("margin_home") or 0.0, reverse=True)
    return results


def item_margin_detail(type_id: int, type_name: str, cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Production Margin page's search: the same row shape discover_ship_
    margins produces, for one arbitrary already-resolved item (any category,
    not just ships - see production/actions.py's do_get_item_margin, which
    resolves a bare type_id/name the same way _resolve_type does before
    calling this). No caching needed (one item per call, same cheap cost
    profile as build_material_tree) - a fresh home/Jita price fetch and
    cost_memo/t2_memo pair every call, unlike discover_ship_margins' whole-
    catalog scan."""
    cost_memo: dict[int, Optional[float]] = {}
    t2_memo: dict[int, T2Mods] = {}
    selected_decryptors: dict[int, str] = {}

    priced_type_ids = list(structural_material_closure([type_id]))
    home = pricing.home_prices(priced_type_ids, cfg)
    jita = pricing.jita_prices(priced_type_ids)

    cost_indices: CostIndices = {
        "component": pricing.cached_system_cost_indices(cfg.component_system_id),
        "manufacturing": pricing.cached_system_cost_indices(cfg.manufacturing_system_id),
    }
    adjusted_prices = pricing.cached_adjusted_prices()

    activity, _bp = classify_activity(type_id)
    build_cost = _unit_cost(type_id, cfg, home, jita, cost_memo, selected_decryptors,
                            t2_memo, cost_indices, adjusted_prices)
    home_quote = home.get(type_id)
    jita_quote = jita.get(type_id)
    sde_type = storage.get_sde_type(type_id)
    return {
        "type_id": type_id, "type_name": type_name, "activity": activity,
        "home_price": home_quote.sell if home_quote and home_quote.sell > 0 else None,
        "jita_price": jita_quote.sell if jita_quote and jita_quote.sell > 0 else None,
        "build_cost": build_cost,
        "margin_home": margin_home(type_id, build_cost, home, cfg),
        "margin_jita": margin_jita(type_id, build_cost, jita, cfg),
        # Same shape as discover_ship_margins' own rows (both feed
        # ShipMarginRow) - fetched here too so do_get_item_margin can build
        # one without a second lookup of its own.
        "meta_level": sde_type[6] if sde_type else None,
    }


# ------------------------------------------------------- multi-structure logistics
def _direct_material_mult(type_id: int, activity: str, decryptor_name: Optional[str],
                          cfg: ProductionConfig, blueprint_id: Optional[int] = None) -> float:
    """Material multiplier (ME) for `type_id`'s own blueprint, for logistics
    purposes - independent of live prices, unlike _tech_ii_mods (which also
    has to pick the best decryptor *economically*): here the decryptor is
    already decided (BuildJobEntry.decryptor, from the last computed plan),
    so this just looks up its ME bonus directly (constants.DECRYPTORS)
    instead of re-running the pricing pipeline for a value that never
    actually depended on it. Falls back to the flat activity baseline
    (_activity_mods, which itself prefers your owned BPO's real ME if
    `blueprint_id` is given and you own it) for Tech I/Reaction, or Tech II
    with no decryptor decided (no invention recipe found - mirrors
    _tech_ii_mods' own fallback)."""
    if activity == "Tech II" and decryptor_name is not None and decryptor_name in DECRYPTORS:
        structure_profile = _structure_profile("Tech II", type_id)
        structure_type, rig_tier = _structure_rig(structure_profile, cfg)
        security_multiplier = _security_multiplier_for(structure_profile, cfg)
        _, me_mult, _ = structure_rig_multiplier(structure_type, rig_tier, security_multiplier)
        return (1 - DECRYPTORS[decryptor_name].me_bonus / 100) * me_mult
    material_mult, _, _ = _activity_mods(activity, type_id, cfg, {}, blueprint_id)
    return material_mult


def _category_material_demand(build_list: list[BuildJobEntry], category_locations: dict[str, int],
                              cfg: ProductionConfig) -> dict[tuple[str, int], float]:
    """Shared by logistics_status and distribution_recommendations: how much
    of each *direct* material (one level only, not the full recursive chain
    _expand_all walks) is needed for every currently-planned job in each
    category that has an assigned location. Categories with no assigned
    location are skipped entirely (nothing to net against either way)."""
    demand: dict[tuple[str, int], float] = {}
    for entry in build_list:
        category = entry.job_category
        if category is None or category not in category_locations:
            continue
        activity, bp = classify_activity(entry.type_id)
        if bp is None:
            continue
        blueprint_id, activity_id, _ = bp
        material_mult = _direct_material_mult(entry.type_id, activity, entry.decryptor, cfg, blueprint_id)
        for material_id, base_qty in storage.get_blueprint_materials(blueprint_id, activity_id):
            qty = _material_qty(base_qty, material_mult, entry.job_runs)
            if qty > 0:
                key = (category, material_id)
                demand[key] = demand.get(key, 0.0) + qty
    return demand


def logistics_status(build_list: list[BuildJobEntry], cfg: ProductionConfig = PRODUCTION_CONFIG) -> list[LogisticsRow]:
    """Per job_category, how much of each direct material is needed for every
    currently-planned job in that category, netted against what's actually
    sitting at the structure the user assigned that category to (storage.
    job_category_locations) - not stock *anywhere* the way _current_stock's
    corp-wide default works, since the whole point here is "is it at *this
    specific* structure". Reads `build_list` from an already-computed plan
    rather than recomputing one - this doesn't need its own ESI/pricing
    round-trip, just the already-synced asset snapshot (storage.
    esi_stock_at_location, a plain DB read) against whatever plan is already
    on screen.

    A row with missing > 0 also gets a "pull from" hint (GitHub issue #4) -
    the configured warehouse (cfg.distribution_source_location_id, falling
    back to home_location_id - "the home market acts as the central
    warehouse" by default) if it has any stock, else whichever *other*
    configured category location currently has surplus (stock beyond its
    own demand), largest surplus first. Purely informational/independent
    per row - unlike distribution_recommendations below, this doesn't need
    to track stock already "claimed" by an earlier row, since it's just a
    hint for the user to read, not a set of recommendations that must sum
    to no more than what's actually there."""
    category_locations = storage.load_category_locations()
    demand = _category_material_demand(build_list, category_locations, cfg)
    warehouse_location_id = cfg.distribution_source_location_id or cfg.home_location_id
    other_locations_by_category = {
        category: sorted({loc for cat, loc in category_locations.items() if cat != category})
        for category in category_locations
    }

    rows = []
    for (category, material_id), needed in demand.items():
        location_id = category_locations[category]
        available = storage.esi_stock_at_location(material_id, location_id)
        missing = max(0.0, needed - available)
        sde_type = storage.get_sde_type(material_id)
        name = sde_type[2] if sde_type else str(material_id)

        pull_from_location_id = None
        pull_from_available = None
        if missing > 0:
            if warehouse_location_id is not None and warehouse_location_id != location_id:
                warehouse_stock = storage.esi_stock_at_location(material_id, warehouse_location_id)
                if warehouse_stock > 0:
                    pull_from_location_id, pull_from_available = warehouse_location_id, warehouse_stock
            if pull_from_location_id is None:
                for other_location_id in other_locations_by_category[category]:
                    if other_location_id == warehouse_location_id:
                        continue
                    other_category = next(c for c, loc in category_locations.items() if loc == other_location_id)
                    other_demand = demand.get((other_category, material_id), 0.0)
                    stock = storage.esi_stock_at_location(material_id, other_location_id)
                    surplus = max(0.0, stock - other_demand)
                    if surplus > 0 and (pull_from_available is None or surplus > pull_from_available):
                        pull_from_location_id, pull_from_available = other_location_id, surplus

        rows.append(LogisticsRow(
            category=category, location_id=location_id, type_id=material_id, type_name=name,
            needed=needed, available=available, missing=missing,
            pull_from_location_id=pull_from_location_id, pull_from_available=pull_from_available,
        ))
    rows.sort(key=lambda r: (r.category, -r.missing))
    return rows


def distribution_recommendations(build_list: list[BuildJobEntry],
                                 cfg: ProductionConfig = PRODUCTION_CONFIG) -> list[DistributionRow]:
    """What to move from the configured distribution source (cfg.
    distribution_source_location_id, falling back to home_location_id - "the
    home market acts as the central warehouse" by default, GitHub issue #4)
    to whichever category stations are currently short of it. When several
    categories are short on the same material and the source can't cover all
    of them, the largest shortfall is covered first (source stock is
    limited) - consistent with logistics_status' own -missing sort.

    Whatever the warehouse still can't cover after that is filled from other
    category locations' surplus (stock beyond their own demand), largest
    surplus first, same "warehouse first, then surplus" priority as
    logistics_status' own pull-from hint. Unlike that hint (informational,
    independent per row), the recommendations here are a commitment - two
    categories short of the same material can't both be recommended to pull
    more than a shared surplus location actually has, so surplus_remaining
    tracks what's already been committed to an earlier category in this same
    material's loop and decrements as it's consumed (confirmed real bug in
    an earlier attempt at this: recomputing each category's candidate
    surplus independently, straight off the unmodified DB stock figure,
    let two categories each get recommended the same units).

    Returns [] if no distribution source is configured at all."""
    source_location_id = cfg.distribution_source_location_id or cfg.home_location_id
    if source_location_id is None:
        return []

    category_locations = storage.load_category_locations()
    demand = _category_material_demand(build_list, category_locations, cfg)

    shortfalls_by_material: dict[int, list[tuple[str, float]]] = {}
    for (category, material_id), needed in demand.items():
        location_id = category_locations[category]
        if location_id == source_location_id:
            continue  # already sourced locally, nothing to move
        available = storage.esi_stock_at_location(material_id, location_id)
        missing = max(0.0, needed - available)
        if missing > 0:
            shortfalls_by_material.setdefault(material_id, []).append((category, missing))

    rows = []
    for material_id, shortfalls in shortfalls_by_material.items():
        remaining_from_warehouse = storage.esi_stock_at_location(material_id, source_location_id)
        sde_type = storage.get_sde_type(material_id)
        name = sde_type[2] if sde_type else str(material_id)

        # Phase 1: cover as much as possible from the warehouse, largest
        # shortfall first. Whatever's left per category (0 if the warehouse
        # fully covered it) carries into phase 2.
        still_needed: dict[str, float] = {}
        for category, missing in sorted(shortfalls, key=lambda x: -x[1]):
            move_qty = min(missing, remaining_from_warehouse) if remaining_from_warehouse > 0 else 0.0
            if move_qty > 0:
                remaining_from_warehouse -= move_qty
                rows.append(DistributionRow(
                    type_id=material_id, type_name=name, from_location_id=source_location_id,
                    to_category=category, to_location_id=category_locations[category], quantity=move_qty,
                ))
            left = missing - move_qty
            if left > 0:
                still_needed[category] = left

        # Phase 2: whatever the warehouse couldn't cover, pull from other
        # category locations' surplus - largest remaining shortfall first,
        # largest remaining surplus first within that.
        surplus_remaining: dict[int, float] = {}
        for category, needed_qty in sorted(still_needed.items(), key=lambda x: -x[1]):
            candidate_ids = []
            for other_category, other_location_id in category_locations.items():
                if other_location_id == source_location_id or other_location_id == category_locations[category]:
                    continue
                if other_location_id not in surplus_remaining:
                    other_demand = demand.get((other_category, material_id), 0.0)
                    other_stock = storage.esi_stock_at_location(material_id, other_location_id)
                    surplus_remaining[other_location_id] = max(0.0, other_stock - other_demand)
                candidate_ids.append(other_location_id)
            for loc_id in sorted(candidate_ids, key=lambda l: -surplus_remaining[l]):
                if needed_qty <= 0:
                    break
                avail = surplus_remaining[loc_id]
                if avail <= 0:
                    continue
                move_qty = min(needed_qty, avail)
                surplus_remaining[loc_id] -= move_qty
                needed_qty -= move_qty
                rows.append(DistributionRow(
                    type_id=material_id, type_name=name, from_location_id=loc_id,
                    to_category=category, to_location_id=category_locations[category], quantity=move_qty,
                ))

    rows.sort(key=lambda r: r.quantity, reverse=True)
    return rows


# --------------------------------------------------------------- invention logistics
def invention_logistics(invention_list: list[InventionNeedRow],
                        cfg: ProductionConfig = PRODUCTION_CONFIG) -> list[LogisticsRow]:
    """Datacores/decryptors/T1 BPC (or Tech III relic) runs needed vs. what's
    sitting at the configured invention station (cfg.invention_location_id)
    - reuses LogisticsRow's "needed vs available at one location" shape.
    Needs recommended_invention_runs (not bpcs_needed) *runs* of the T1
    blueprint itself: one invention attempt consumes exactly one *run* from
    a T1 BPC whether it succeeds or fails (confirmed against
    wiki.eveuniversity.org/Invention), so a max-run copy is worth many
    attempts and a 1-run copy exactly one - bpcs_needed (the number of
    *successful* inventions needed) would undercount real T1-run
    consumption, since every failed attempt burns a run too.

    Availability for a genuine T1 blueprint goes through
    storage.available_blueprint_copies, not the generic esi_stock_at_location
    every other row here uses - a T1 blueprint's BPO and BPC share the exact
    same type_id in EVE's data model, so a plain esi_stock_at_location call
    would count an owned BPO as if it were a usable invention input too.
    A Tech III relic (constants.ANCIENT_RELIC_CATEGORY_ID) is the opposite
    case: it's t1_blueprint_type_id's real value for a Tech III item, but a
    plain purchasable/lootable item, never owned as a blueprint copy - so it
    must go through esi_stock_at_location instead (available_blueprint_copies
    would always return 0 for it, since character_blueprints/corp_blueprints
    never has a row for a non-blueprint type_id).

    No per-category structure-assignment concept exists here (see
    engine.py's own module docstring) - this is deliberately single-location,
    matching the parent's own invention_location_id field one-for-one rather
    than the parent's wider multi-structure Logistik tab."""
    if cfg.invention_location_id is None:
        return []

    demand: dict[int, float] = {}
    t1_blueprint_type_ids: set[int] = set()
    for need in invention_list:
        if need.recommended_invention_runs <= 0:
            continue
        demand[need.t1_blueprint_type_id] = demand.get(need.t1_blueprint_type_id, 0.0) + need.recommended_invention_runs
        t1_blueprint_type_ids.add(need.t1_blueprint_type_id)

        decryptor = DECRYPTORS.get(need.decryptor)
        if decryptor is not None and decryptor.type_id != 0:  # 0 is the "None" sentinel, not a real item
            demand[decryptor.type_id] = demand.get(decryptor.type_id, 0.0) + need.recommended_invention_runs

        recipe = storage.get_invention_recipe(need.t1_blueprint_type_id)
        for datacore_id, datacore_qty in (recipe.get("datacores", []) if recipe else []):
            demand[datacore_id] = demand.get(datacore_id, 0.0) + datacore_qty * need.recommended_invention_runs

    rows = []
    for type_id, needed in demand.items():
        is_real_blueprint = type_id in t1_blueprint_type_ids and storage.get_type_category(type_id) != ANCIENT_RELIC_CATEGORY_ID
        if is_real_blueprint:
            available = storage.available_blueprint_copies(type_id, cfg.invention_location_id)
        else:
            available = storage.esi_stock_at_location(type_id, cfg.invention_location_id)
        sde_type = storage.get_sde_type(type_id)
        name = sde_type[2] if sde_type else str(type_id)
        rows.append(LogisticsRow(
            category="Invention", type_id=type_id, type_name=name, location_id=cfg.invention_location_id,
            needed=needed, available=available, missing=max(0.0, needed - available),
        ))
    rows.sort(key=lambda r: -r.missing)
    return rows


def t1_bpc_invention_needs(invention_list: list[InventionNeedRow],
                           cfg: ProductionConfig = PRODUCTION_CONFIG) -> list[T1BpcInventionNeedRow]:
    """invention_logistics above mixes T1 BPCs, decryptors and datacores into
    one flat LogisticsRow list, which makes "how many BPC runs am I short on
    the thing that actually matters, and do I even own the BPO to print
    more" hard to see. This is the T1-blueprint(-or-relic)-only slice of the
    exact same demand accumulation invention_logistics already does, plus a
    BPO-presence check invention_logistics has no reason to compute.

    Despite this function's own name, `type_id` here can also be a Tech III
    relic - see invention_logistics' own docstring for why that needs
    esi_stock_at_location instead of available_blueprint_copies.
    bpo_present needs no equivalent branch for a relic - storage.
    has_bpo_at_location naturally returns False for one already (a relic
    type_id never has a runs==-1 row, since it was never a blueprint)."""
    if cfg.invention_location_id is None:
        return []

    needed_by_t1: dict[int, int] = {}
    for need in invention_list:
        if need.recommended_invention_runs <= 0:
            continue
        needed_by_t1[need.t1_blueprint_type_id] = (
            needed_by_t1.get(need.t1_blueprint_type_id, 0) + need.recommended_invention_runs)

    rows = []
    for type_id, needed in needed_by_t1.items():
        is_real_blueprint = storage.get_type_category(type_id) != ANCIENT_RELIC_CATEGORY_ID
        available = (storage.available_blueprint_copies(type_id, cfg.invention_location_id) if is_real_blueprint
                     else storage.esi_stock_at_location(type_id, cfg.invention_location_id))
        available = int(available)
        sde_type = storage.get_sde_type(type_id)
        name = sde_type[2] if sde_type else str(type_id)
        bpo_present = is_real_blueprint and storage.has_bpo_at_location(type_id, cfg.invention_location_id)
        rows.append(T1BpcInventionNeedRow(
            type_id=type_id, name=name, needed=needed, available=available,
            missing=max(0, needed - available), bpo_present=bpo_present,
            stockpile_pct=max(0.0, available / needed * 100) if needed > 0 else 0.0,
        ))
    rows.sort(key=lambda r: -r.missing)
    return rows


# ------------------------------------------------------------- owned blueprints
def list_owned_blueprints() -> list[OwnedBlueprintRow]:
    """Every owned blueprint (character + corp), aggregated across identical
    (type_id, is_original, ME, TE, runs) groups - ported from the parent's
    do_list_owned_blueprints (kept in engine.py here rather than actions.py,
    matching this repo's own "no real logic in actions.py" convention: the
    grouping below is the actual logic, do_list_owned_blueprints is a thin
    do_* wrapper around it).

    A BPO is identified by runs == -1 (ESI's convention for "original"), not
    by any explicit is_blueprint_copy flag (that field only exists on the
    *asset* tables, not the blueprint tables - see esi_sync.py's blueprint
    sync)."""
    grouped: dict[tuple, int] = {}
    for type_id, quantity, material_efficiency, time_efficiency, runs in storage.load_owned_blueprints():
        is_original = runs == -1
        key = (type_id, is_original, material_efficiency, time_efficiency, None if is_original else runs)
        # ESI's `quantity` field for a blueprint item is usually a sentinel
        # (-1 original / -2 copy), not an actual stack size - only add it
        # when it looks like a real positive count, else this row is 1 item.
        grouped[key] = grouped.get(key, 0) + (quantity if quantity and quantity > 0 else 1)

    rows = []
    for (type_id, is_original, me, te, runs), qty in grouped.items():
        sde_type = storage.get_sde_type(type_id)
        name = sde_type[2] if sde_type else str(type_id)
        rows.append(OwnedBlueprintRow(
            type_id=type_id, type_name=name, is_original=is_original,
            quantity=qty, material_efficiency=me, time_efficiency=te, runs=runs,
        ))
    rows.sort(key=lambda r: r.type_name)
    return rows
