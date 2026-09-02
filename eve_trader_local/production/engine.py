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

Deliberately not ported yet (each needs machinery this repo doesn't have -
see SYNC.md): the stock-aware planner (`plan_production`/`_expand_all`/
`_base_runs`, which net demand against ESI-derived assets and manual stock),
build-candidate discovery, the logistics/distribution helpers, and the bought-
blueprint-copy cost term `_unit_cost` folds in from a manual BPC cost table.
"""
from __future__ import annotations

import math
from typing import Iterable, Optional

from .. import storage
from . import invention, pricing
from .config import PRODUCTION_CONFIG, ProductionConfig
from .constants import (
    ACTIVITY_MODS, ACTIVITY_REACTION, ADVANCED_COMPONENT_GROUP_IDS, CAPITAL_COMPONENT_GROUP_IDS,
    CHARGE_CATEGORY_ID, COMPONENT_GROUP_IDS, DEADSPACE_META_GROUP_ID, DECRYPTORS, DRONE_CATEGORY_ID,
    FACTION_META_GROUP_ID, FIGHTER_CATEGORY_ID, MODULE_CATEGORY_ID, OFFICER_META_GROUP_ID,
    SCC_SURCHARGE_RATE, SHIP_SIZE_GROUP_IDS, STORYLINE_META_GROUP_ID, SUBSYSTEM_GROUP_IDS,
    rig_security_multiplier, structure_rig_multiplier,
)
from .models import InventionResult

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
