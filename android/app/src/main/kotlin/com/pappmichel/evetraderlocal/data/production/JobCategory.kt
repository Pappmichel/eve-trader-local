package com.pappmichel.evetraderlocal.data.production

/**
 * Port of `production/constants.py`'s job-category section plus
 * `engine.job_category` itself - the "where do I start this job" display
 * grouping Logistics' category-location config and both of its portable
 * reports (`logisticsStatus`/`distributionRecommendations` in
 * `LogisticsEngine.kt`) key off. Kept in its own file rather than folded
 * into `ProductionEngine.kt` since job_category is genuinely a separate
 * concept from that file's buy/build math - `plan_production` itself never
 * calls `job_category` (only `_build_build_list`, which stamps it onto
 * `BuildJobEntry`, does - and `BuildJobEntry` here has no such field at all,
 * see `ProductionEngine.kt`'s own module docstring for why).
 *
 * Why this file exists now when `ProductionEngine.kt`'s own docstring said
 * `job_category` was out of scope: that docstring's reasoning was narrower
 * than "cannot be computed" - it said the *ship-size/component-group id
 * constants* weren't ported, not that the underlying SDE data was missing.
 * Reading `engine.job_category` in full (`eve_trader_local/production/
 * engine.py`) shows it needs nothing invention/Reaction-specific at all for
 * a Manufacturing-only cache - it's a pure `groupId`/`categoryId` lookup
 * already-published `SdeGroupEntity`/`SdeTypeEntity` rows carry
 * (`SdeRepository.type(typeId)?.groupId`/`categoryIdOf(typeId)`, both
 * already public). The one branch this port genuinely cannot reach is the
 * "Reactions" bucket (`classify_activity` returning "Reaction" - Reaction
 * activity doesn't exist in this Manufacturing-only SDE cache at all, see
 * `ProductionBuildCost.kt`'s own module docstring point 1) - [jobCategory]
 * below takes `hasBlueprint` instead of a full activity classification and
 * simply never produces "Reactions", exactly what a Manufacturing-only
 * `classify_activity` would always return for anything with a cached
 * blueprint. [JOB_CATEGORIES] still lists "Reactions" (a user may configure
 * a location for it - harmless, matching desktop's own always-9-category
 * list); it is just never a category [jobCategory] itself returns here.
 */

// COMPONENT_GROUP_IDS split - see constants.py's own comment for exactly
// which ESI item groups these are and why they split this way (which of the
// two ME rigs, "Advanced Component" vs. "Basic Capital Component", covers
// each group - group 873 alone is the capital-rig one).
val CAPITAL_COMPONENT_GROUP_IDS: Set<Int> = setOf(873)
val ADVANCED_COMPONENT_GROUP_IDS: Set<Int> = setOf(332, 334, 716, 913, 964)

/** Every value [jobCategory] can return, in display order, plus "Reactions"
 * (never actually returned here - see this file's module docstring) so the
 * category-location config panel still offers the full 9-bucket set a
 * desktop-parity setup expects. */
val JOB_CATEGORIES: List<String> = listOf(
    "Reactions",
    "Advanced Components",
    "Capital Components",
    "Equipment",
    "Drones & Ammunition",
    "Small Ships",
    "Medium Ships",
    "Large Ships",
    "Capital Ship",
)

/** T3 Strategic Cruiser subsystems count as Medium Ships, not Equipment -
 * matches constants.py's SUBSYSTEM_GROUP_IDS exactly. */
val SUBSYSTEM_GROUP_IDS: Set<Int> = setOf(958, 954, 956, 957)

/** Ship hull size tiers by SDE invGroup - verbatim port of constants.py's
 * SHIP_SIZE_GROUP_IDS (every published category_id=6 group as of that
 * file's own writing). */
val SHIP_SIZE_GROUP_IDS: Map<String, Set<Int>> = mapOf(
    "Small Ships" to setOf(324, 1534, 237, 830, 420, 893, 1283, 25, 831, 541, 1527, 1022, 31, 834, 1305, 29),
    "Medium Ships" to setOf(1201, 1202, 419, 906, 540, 26, 380, 543, 4902, 1972, 833, 28, 358, 894, 832, 463, 963),
    "Large Ships" to setOf(27, 898, 513, 941, 902, 900),
    "Capital Ship" to setOf(883, 547, 5120, 485, 1538, 4594, 659, 30),
)

// SDE category_ids for the "Drones & Ammunition" and "Equipment" buckets -
// matches constants.py's CHARGE_CATEGORY_ID/DRONE_CATEGORY_ID/
// FIGHTER_CATEGORY_ID/MODULE_CATEGORY_ID exactly.
private val DRONE_AMMO_CATEGORY_IDS: Set<Int> = setOf(8, 18, 87) // Charge, Drone, Fighter
private const val MODULE_CATEGORY_ID = 7

/** Which "where do I start this job" bucket `typeId` belongs to - mirrors
 * `engine.job_category` minus the unreachable "Reactions" branch (see this
 * file's module docstring). `hasBlueprint` stands in for desktop's
 * `bp is None` check (`classify_activity`'s second return value) - a
 * caller here already knows this from [ProductionBomSource.blueprintForProduct]
 * before ever needing a category label, so it's passed in rather than
 * re-derived. `groupId`/`categoryId` come from `SdeRepository.type(typeId)?.
 * groupId`/`categoryIdOf(typeId)` respectively. Returns null for anything
 * with no blueprint (nothing to "start") or that falls into no bucket at
 * all - exactly desktop's own fallthrough. */
fun jobCategory(hasBlueprint: Boolean, groupId: Int?, categoryId: Int?): String? {
    if (!hasBlueprint) return null
    if (groupId != null && groupId in CAPITAL_COMPONENT_GROUP_IDS) return "Capital Components"
    if (groupId != null && groupId in ADVANCED_COMPONENT_GROUP_IDS) return "Advanced Components"
    if (groupId != null && groupId in SUBSYSTEM_GROUP_IDS) return "Medium Ships"
    for ((size, groupIds) in SHIP_SIZE_GROUP_IDS) {
        if (groupId != null && groupId in groupIds) return size
    }
    if (categoryId != null && categoryId in DRONE_AMMO_CATEGORY_IDS) return "Drones & Ammunition"
    if (categoryId == MODULE_CATEGORY_ID) return "Equipment"
    return null
}
