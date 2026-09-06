package com.pappmichel.evetraderlocal.data.production

import kotlin.math.max
import kotlin.math.min

/**
 * Production's Logistics tab - a Kotlin port of `engine.logistics_status`/
 * `distribution_recommendations` (GitHub issue #4, the multi-structure
 * "where is this category's build short, and where should I pull it from"
 * pair), now that [planProduction] (`ProductionEngine.kt`) exists to feed
 * them a real `BuildJobEntry` list.
 *
 * ## What's genuinely portable now, and what still isn't
 *
 * **[logisticsStatus]/[distributionRecommendations]: real ports, not
 * substitutes.** Both are pure functions over an already-computed build
 * list plus per-category assigned locations - exactly the shape
 * `plan_production`'s new [BuildJobEntry] output and [CategoryLocationRepository]
 * (Room-backed, mirroring desktop's `job_category_locations`/
 * `category_location_options` tables) now supply. The one real gap this
 * required closing along the way was `BuildJobEntry` having no
 * `job_category` field at all (`ProductionEngine.kt`'s own module docstring
 * called this out as dropped) - [categorizeBuildList] below closes it for
 * real, using [jobCategory] (`JobCategory.kt`), not a placeholder: reading
 * `engine.job_category` in full showed it needs nothing this platform's SDE
 * cache is missing (see that file's own module docstring for the full
 * finding), so this is a genuine port, not a workaround.
 *
 * **Location-scoped stock is a second, separate gap this task closes.**
 * `engine.esi_stock_at_location(type_id, location_id)` nets demand against
 * stock at *one specific structure*, not "owned anywhere" the way
 * [StockSource] (`ProductionEngine.kt`) answers for `plan_production`'s own
 * corp-wide "do I own this at all" question - [StockSource] has no
 * `locationId` parameter at all and cannot answer this. [LocationStockSource]
 * is the new, narrower seam this pair actually needs; [LiveLocationStockSource]
 * (`LiveStockSource.kt`) is its real implementation, reading the same
 * character-assets-only ESI data [LiveStockSource] does (see that class's
 * own docstring for the identical "no corp assets yet" gap - both inherit
 * it from the same missing `EsiClient` endpoint) filtered to one
 * `locationId` and excluding the same non-stock location flags
 * (`storage.NON_STOCK_LOCATION_FLAGS`) desktop's own query excludes.
 *
 * **Category-location config is real Room storage, not a stub.**
 * [CategoryLocationRepository] (`CategoryLocations.kt`) mirrors desktop's
 * `job_category_locations` (one active location per category) and
 * `category_location_options` (a saved quick-switch list per category)
 * tables one-for-one, the same "real Room entity, not a settings-blob"
 * shape `StockPlan.kt`'s three tables already established for exactly this
 * kind of "meant to grow, per-row CRUD" data.
 *
 * **`cfg.distributionSourceLocationId` is a genuinely new config field**
 * ([ProductionConfig.distributionSourceLocationId]) - `distribution_
 * recommendations`' own warehouse-source setting, falling back to
 * [ProductionConfig.homeLocationId] exactly like desktop's own
 * `distribution_source_location_id or home_location_id`.
 *
 * ## What's still genuinely blocked, unchanged from before this task
 *
 * `invention_logistics`/`t1_bpc_invention_needs` are NOT ported here and
 * are not attempted - both need `plan_production`'s `invention_list`
 * (`recommended_invention_runs`/`decryptor`/`t1_blueprint_type_id` per
 * Tech II/III stock target), which [PlanProductionResult] deliberately has
 * no field for at all (`ProductionEngine.kt`'s own module docstring, first
 * bullet: needs `classify_activity`'s Reaction/Tech II/Faction distinction
 * and `invention.py`'s decryptor-selection machinery, neither of which
 * exists on a Manufacturing-only SDE cache). Nothing changed about that
 * finding in this task - forcing a port of either report here would mean
 * inventing a fake `invention_list` from data this platform genuinely does
 * not have, not a real port. "Resolve Structure Name" likewise stays out of
 * scope: `EsiClient.kt` still has no `corporation_structures`/
 * `get_structure_name`-equivalent endpoint (checked fresh for this task,
 * `grep -i "structure\|corporation"` over that file turns up only the
 * structure-*order-book* endpoints `ProductionPricing.kt` already uses).
 */

// --------------------------------------------------------- categorization

/** One build job, already stamped with its [jobCategory] bucket - the
 * Kotlin counterpart of desktop's `BuildJobEntry.job_category` field, kept
 * as its own small type rather than added onto [BuildJobEntry] itself so
 * `ProductionEngine.kt`'s own "no job_category column" scope decision
 * (see that file's module docstring) doesn't have to be revisited or
 * threaded through every one of that file's existing callers/tests just to
 * serve this one, later-added feature. */
data class LogisticsBuildJob(
    val typeId: Int,
    val blueprintTypeId: Int,
    val jobRuns: Int,
    val category: String,
)

/** Stamps a category onto every [BuildJobEntry] plan_production produced,
 * dropping anything [jobCategory] can't place (mirrors
 * `_category_material_demand`'s own `category is None ... continue` skip -
 * desktop computes `job_category` once per row and stores it directly on
 * `BuildJobEntry`; this port computes it lazily here instead, since
 * `BuildJobEntry` itself carries no such field, see this file's module
 * docstring). Every entry in [PlanProductionResult.buildList] has a real
 * cached blueprint by construction (it wouldn't be a build job otherwise),
 * so `hasBlueprint = true` for all of them here. */
suspend fun categorizeBuildList(
    buildList: List<BuildJobEntry>,
    groupIdOf: suspend (Int) -> Int?,
    categoryIdOf: suspend (Int) -> Int?,
): List<LogisticsBuildJob> = buildList.mapNotNull { entry ->
    val category = jobCategory(hasBlueprint = true, groupId = groupIdOf(entry.typeId), categoryId = categoryIdOf(entry.typeId))
        ?: return@mapNotNull null
    LogisticsBuildJob(entry.typeId, entry.blueprintTypeId, entry.jobRuns, category)
}

// -------------------------------------------------------- location stock

/** Owned quantity of `typeId` at one specific `locationId` - the seam
 * [logisticsStatus]/[distributionRecommendations] need that [StockSource]
 * (corp-wide, no location filter) cannot answer. See this file's module
 * docstring for why this is a genuinely separate interface, not a missing
 * overload on [StockSource]. */
interface LocationStockSource {
    suspend fun stockAt(typeId: Int, locationId: Long): Double
}

// ------------------------------------------------------------- material demand

/** Direct-material demand per (category, materialId), one BOM level deep
 * only (not the full recursive chain [expandAll] walks) - mirrors
 * `engine._category_material_demand` exactly, including skipping any
 * category with no assigned location at all (nothing to net against
 * either way). */
suspend fun categoryMaterialDemand(
    buildList: List<LogisticsBuildJob>,
    categoryLocations: Map<String, Long>,
    cfg: ProductionConfig,
    bom: ProductionBomSource,
): Map<Pair<String, Int>, Double> {
    val demand = mutableMapOf<Pair<String, Int>, Double>()
    val materialMult = 1.0 - cfg.materialEfficiency / 100.0
    for (entry in buildList) {
        if (entry.category !in categoryLocations) continue
        for ((materialId, baseQty) in bom.blueprintMaterials(entry.blueprintTypeId)) {
            val qty = materialQty(baseQty, materialMult, entry.jobRuns.toDouble())
            if (qty > 0.0) {
                val key = entry.category to materialId
                demand[key] = (demand[key] ?: 0.0) + qty
            }
        }
    }
    return demand
}

// ------------------------------------------------------------------ rows

/** One row of [logisticsStatus] - mirrors `models.LogisticsRow` (the
 * multi-structure half only; `invention_logistics`'s single-location reuse
 * of the same desktop row shape isn't ported, see this file's module
 * docstring). `pullFromLocationId`/`pullFromAvailable` are the GitHub
 * issue #4 "where to pull a shortfall from" hint - purely informational,
 * independent per row, unlike [DistributionRow]'s own committed quantities. */
data class LogisticsRow(
    val category: String,
    val locationId: Long,
    val typeId: Int,
    val typeName: String,
    val needed: Double,
    val available: Double,
    val missing: Double,
    val pullFromLocationId: Long? = null,
    val pullFromAvailable: Double? = null,
)

/** One row of [distributionRecommendations] - mirrors `models.
 * DistributionRow` exactly. */
data class DistributionRow(
    val typeId: Int,
    val typeName: String,
    val fromLocationId: Long,
    val toCategory: String,
    val toLocationId: Long,
    val quantity: Double,
)

// --------------------------------------------------------- logistics status

/** Per job_category, how much of each direct material the currently-
 * planned jobs in that category need at the structure it's assigned to,
 * netted against what's actually sitting there - mirrors
 * `engine.logistics_status` exactly, including its "pull from" hint: the
 * configured warehouse (`cfg.distributionSourceLocationId`, falling back to
 * `cfg.homeLocationId`) if it has any stock, else whichever *other*
 * configured category location has surplus (stock beyond its own demand),
 * largest surplus first. Rows sorted by (category, -missing), same as
 * desktop. */
suspend fun logisticsStatus(
    buildList: List<LogisticsBuildJob>,
    categoryLocations: Map<String, Long>,
    cfg: ProductionConfig,
    bom: ProductionBomSource,
    stock: LocationStockSource,
    typeName: suspend (Int) -> String,
): List<LogisticsRow> {
    val demand = categoryMaterialDemand(buildList, categoryLocations, cfg, bom)
    val warehouseLocationId = cfg.distributionSourceLocationId ?: cfg.homeLocationId
    val otherLocationsByCategory: Map<String, List<Long>> = categoryLocations.keys.associateWith { category ->
        categoryLocations.filterKeys { it != category }.values.toSet().sorted()
    }

    val rows = mutableListOf<LogisticsRow>()
    for ((key, needed) in demand) {
        val (category, materialId) = key
        val locationId = categoryLocations.getValue(category)
        val available = stock.stockAt(materialId, locationId)
        val missing = max(0.0, needed - available)
        val name = typeName(materialId)

        var pullFromLocationId: Long? = null
        var pullFromAvailable: Double? = null
        if (missing > 0.0) {
            if (warehouseLocationId != null && warehouseLocationId != locationId) {
                val warehouseStock = stock.stockAt(materialId, warehouseLocationId)
                if (warehouseStock > 0.0) {
                    pullFromLocationId = warehouseLocationId
                    pullFromAvailable = warehouseStock
                }
            }
            if (pullFromLocationId == null) {
                for (otherLocationId in otherLocationsByCategory[category].orEmpty()) {
                    if (otherLocationId == warehouseLocationId) continue
                    val otherCategory = categoryLocations.entries.first { it.value == otherLocationId }.key
                    val otherDemand = demand[otherCategory to materialId] ?: 0.0
                    val otherStock = stock.stockAt(materialId, otherLocationId)
                    val surplus = max(0.0, otherStock - otherDemand)
                    if (surplus > 0.0 && (pullFromAvailable == null || surplus > pullFromAvailable!!)) {
                        pullFromLocationId = otherLocationId
                        pullFromAvailable = surplus
                    }
                }
            }
        }

        rows += LogisticsRow(
            category = category, locationId = locationId, typeId = materialId, typeName = name,
            needed = needed, available = available, missing = missing,
            pullFromLocationId = pullFromLocationId, pullFromAvailable = pullFromAvailable,
        )
    }
    return rows.sortedWith(compareBy<LogisticsRow> { it.category }.thenByDescending { it.missing })
}

// -------------------------------------------------- distribution recommendations

/** What to move from the configured distribution source to whichever
 * category stations are currently short of it - mirrors `engine.
 * distribution_recommendations` exactly, including its two-phase
 * commitment (warehouse first, largest shortfall first; whatever's left
 * pulled from other categories' surplus, largest surplus first, tracked via
 * a shared `surplusRemaining` ledger so two categories short of the same
 * material can never be recommended more than a shared surplus location
 * actually has - the confirmed real bug this file's own comment, matching
 * desktop's, calls out). Returns an empty list when no distribution source
 * is configured at all. Rows sorted by quantity desc. */
suspend fun distributionRecommendations(
    buildList: List<LogisticsBuildJob>,
    categoryLocations: Map<String, Long>,
    cfg: ProductionConfig,
    bom: ProductionBomSource,
    stock: LocationStockSource,
    typeName: suspend (Int) -> String,
): List<DistributionRow> {
    val sourceLocationId = cfg.distributionSourceLocationId ?: cfg.homeLocationId ?: return emptyList()

    val demand = categoryMaterialDemand(buildList, categoryLocations, cfg, bom)

    val shortfallsByMaterial = mutableMapOf<Int, MutableList<Pair<String, Double>>>()
    for ((key, needed) in demand) {
        val (category, materialId) = key
        val locationId = categoryLocations.getValue(category)
        if (locationId == sourceLocationId) continue // already sourced locally, nothing to move
        val available = stock.stockAt(materialId, locationId)
        val missing = max(0.0, needed - available)
        if (missing > 0.0) {
            shortfallsByMaterial.getOrPut(materialId) { mutableListOf() } += category to missing
        }
    }

    val rows = mutableListOf<DistributionRow>()
    for ((materialId, shortfalls) in shortfallsByMaterial) {
        var remainingFromWarehouse = stock.stockAt(materialId, sourceLocationId)
        val name = typeName(materialId)

        // Phase 1: cover as much as possible from the warehouse, largest
        // shortfall first.
        val stillNeeded = mutableMapOf<String, Double>()
        for ((category, missing) in shortfalls.sortedByDescending { it.second }) {
            val moveQty = if (remainingFromWarehouse > 0.0) min(missing, remainingFromWarehouse) else 0.0
            if (moveQty > 0.0) {
                remainingFromWarehouse -= moveQty
                rows += DistributionRow(
                    typeId = materialId, typeName = name, fromLocationId = sourceLocationId,
                    toCategory = category, toLocationId = categoryLocations.getValue(category), quantity = moveQty,
                )
            }
            val left = missing - moveQty
            if (left > 0.0) stillNeeded[category] = left
        }

        // Phase 2: whatever the warehouse couldn't cover, pull from other
        // category locations' surplus - largest remaining shortfall first,
        // largest remaining surplus first within that. surplusRemaining is
        // shared across every category processed in this phase so the same
        // physical surplus is never committed twice.
        val surplusRemaining = mutableMapOf<Long, Double>()
        for ((category, initialNeeded) in stillNeeded.entries.sortedByDescending { it.value }) {
            var needed = initialNeeded
            val candidateIds = mutableListOf<Long>()
            for ((otherCategory, otherLocationId) in categoryLocations) {
                if (otherLocationId == sourceLocationId || otherLocationId == categoryLocations.getValue(category)) continue
                if (otherLocationId !in surplusRemaining) {
                    val otherDemand = demand[otherCategory to materialId] ?: 0.0
                    val otherStock = stock.stockAt(materialId, otherLocationId)
                    surplusRemaining[otherLocationId] = max(0.0, otherStock - otherDemand)
                }
                candidateIds += otherLocationId
            }
            for (locId in candidateIds.sortedByDescending { surplusRemaining.getValue(it) }) {
                if (needed <= 0.0) break
                val avail = surplusRemaining.getValue(locId)
                if (avail <= 0.0) continue
                val moveQty = min(needed, avail)
                surplusRemaining[locId] = avail - moveQty
                needed -= moveQty
                rows += DistributionRow(
                    typeId = materialId, typeName = name, fromLocationId = locId,
                    toCategory = category, toLocationId = categoryLocations.getValue(category), quantity = moveQty,
                )
            }
        }
    }

    return rows.sortedByDescending { it.quantity }
}
