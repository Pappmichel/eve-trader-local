package com.pappmichel.evetraderlocal.data.production

import com.pappmichel.evetraderlocal.data.esi.OrderStats

/** Production's whole-catalog buy-vs-build scan - a Kotlin port of the
 * desktop build's `production/engine.discover_build_candidates`, reusing
 * [unitBuildCost]/[marginHome] (`ProductionBuildCost.kt`) per candidate
 * type_id exactly the way that file's own module docstring anticipates
 * ("Nothing here pools demand across multiple targets... or scans the
 * whole catalog for candidates - not ported" - this file is that missing
 * piece, now that a candidate universe (below) exists to feed it).
 *
 * Every simplification [unitBuildCost] itself documents (Manufacturing
 * only, one flat ME level, no structure/rig bonus, no live cost index, EIV
 * approximated from real material prices) applies here too, plus these
 * additional ones specific to a whole-catalog scan:
 *
 * 1. **No stock-target exclusion.** Desktop excludes every item the user
 *    has already configured as a stock target (`storage.load_stock_
 *    targets`) - "an item already deliberately tracked doesn't need to be
 *    independently discovered too". No stock-target table exists on
 *    Android (see `engine.py`'s own module docstring on what the stock-
 *    aware planner needs and SYNC.md), so this scan has nothing to exclude
 *    against: every manufacturable, published type is always a candidate.
 * 2. **No Jita cross-shopping for materials.** Desktop's `_unit_cost` (and
 *    this port's own [unitBuildCost]) freely sources each sub-material from
 *    whichever of home/Jita is cheaper. [scanBuildCandidates] deliberately
 *    passes an *empty* Jita price map instead of a real one: home prices
 *    come from one bulk structure-order-book download regardless of how
 *    many type_ids are involved (`ProductionPricing.homePrices`/
 *    `EsiClient.structureOrderStatsBulk`), but Jita has no equivalent bulk
 *    region endpoint - `EsiClient.regionOrderStatsBulk` is one live ESI call
 *    per type_id (see that method's own docstring). A whole-catalog scan's
 *    material closure can easily run to several hundred distinct type_ids;
 *    firing that many individual Jita calls from a phone on every scan is
 *    not a reasonable trade for a "would Jita have been slightly cheaper
 *    for this one sub-material" refinement. Every result is therefore
 *    priced as if only the home structure's own order book existed - a
 *    real narrowing (some builds that desktop would find cheaper via a
 *    Jita-sourced material show a higher build_cost here, which can only
 *    ever *understate* a margin, never fabricate one that isn't real) but a
 *    safe direction to be wrong in for a "should I build this" scan.
 * 3. **No Goonmetrics daily-movement ranking.** Desktop's real value here
 *    isn't margin alone - it batches a Goonmetrics price-history lookup for
 *    every margin-qualifying candidate and ranks by `potential_daily_profit
 *    = daily_movement x margin x build_cost`, specifically because "a huge
 *    margin on an item nobody actually buys is worthless" (see that
 *    function's own docstring). `GoonmetricsClient` already exists on
 *    Android for Trading's own shortlist, but wiring its production-side
 *    market-slug equivalent and a batched history call is real, separate
 *    scope this port doesn't attempt - [scanBuildCandidates] ranks by
 *    margin alone. A modest-margin, highly-liquid item can therefore rank
 *    below a huge-margin item nobody's actually trading here, the exact
 *    inversion desktop's movement gate exists to prevent; treat this list
 *    as "cheaper to build than to buy right now", not "worth building".
 * 4. **`cfg.minDailyProfit` has no Android equivalent** for the same reason
 *    - nothing here computes a daily-profit figure to gate on. Only
 *    `cfg.minMargin` (`ProductionConfig`) applies.
 */

/** One published, market-relevant type this cache knows has a cached
 * Manufacturing blueprint - the "raw catalog entry" [ProductionCandidateSource
 * .manufacturableTypes] returns, before any pricing/margin math is applied.
 * `volumeM3` is nullable the same way `SdeTypeEntity.volume` is (a type this
 * cache hasn't fully populated, in practice never seen on a real refreshed
 * cache). */
data class CandidateType(
    val typeId: Int,
    val typeName: String,
    val volumeM3: Double?,
    val metaLevel: Int?,
)

/** What [scanBuildCandidates] needs beyond plain [ProductionBomSource] - the
 * whole-catalog candidate list itself. Kept as its own interface (rather
 * than folding `manufacturableTypes` into [ProductionBomSource] directly)
 * so [ProductionBuildCost]'s existing consumers/tests, which only ever need
 * one item's BOM, don't have to fake a whole-catalog method they never
 * call. `SdeRepository` implements both. */
interface ProductionCandidateSource : ProductionBomSource {
    suspend fun manufacturableTypes(): List<CandidateType>
}

/** One scan result: a manufacturable type where building clearly beats
 * buying right now (per `cfg.minMargin`) - mirrors the dict
 * `discover_build_candidates` returns, minus the `daily_movement`/
 * `potential_daily_profit` fields this port doesn't compute (see this
 * file's module docstring, point 3) and `activity`, which is always
 * "Tech I" here (Manufacturing-only, same as `ProductionBuildCost.kt`). */
data class BuildCandidateRow(
    val typeId: Int,
    val typeName: String,
    val buildCost: Double,
    val margin: Double,
    val metaLevel: Int?,
)

/** A margin this high on a *scanned* candidate is far more likely a stale/
 * synthetic price slipping past the home-quote sanity check below than a
 * real opportunity - matches `engine.MAX_PLAUSIBLE_BUILD_MARGIN` exactly
 * (see that constant's own comment for why 300% is a fixed backstop, not a
 * config field). */
const val MAX_PLAUSIBLE_BUILD_MARGIN = 3.0 // 300%

/** Every type_id reachable from `seedTypeIds` through cached Manufacturing
 * blueprint materials, ignoring buy-vs-build economics entirely - mirrors
 * `engine.structural_material_closure` exactly, including its "necessarily
 * a superset of what the priced walk will actually visit" reasoning: a
 * node whose real price makes "Buy" cheaper is still recursed into here,
 * which is correct (a price ready and unused beats an unpriced node
 * mid-recursion), not wasteful. Breadth-first with a visited set so a
 * shared base material is expanded exactly once no matter how many
 * candidates reach it. */
suspend fun structuralMaterialClosure(seedTypeIds: Collection<Int>, bom: ProductionBomSource): Set<Int> {
    val visited = mutableSetOf<Int>()
    var frontier: Set<Int> = seedTypeIds.toSet()
    var depth = 0
    while (frontier.isNotEmpty() && depth < MAX_BOM_DEPTH) {
        visited += frontier
        val next = mutableSetOf<Int>()
        for (typeId in frontier) {
            val bp = bom.blueprintForProduct(typeId) ?: continue
            for ((materialId, _) in bom.blueprintMaterials(bp.blueprintTypeId)) {
                if (materialId !in visited) next += materialId
            }
        }
        frontier = next
        depth++
    }
    return visited
}

/** Production's equivalent of Trading's `CandidateDiscovery` - scans every
 * manufacturable, published SDE item ([ProductionCandidateSource.
 * manufacturableTypes]) and flags the ones where building clearly beats
 * buying right now (`marginHome` clearing `cfg.minMargin`, gated at
 * [MAX_PLAUSIBLE_BUILD_MARGIN] the same way desktop's scan is), ranked
 * margin-descending. See this file's module docstring for the full list of
 * differences from `engine.discover_build_candidates`.
 *
 * `home` must already cover every candidate plus its whole material
 * closure ([structuralMaterialClosure]) - callers should build it with one
 * [homePrices] call over `candidates.map { it.typeId } +
 * structuralMaterialClosure(...)`, mirroring
 * `_scan_build_candidates`'s own "one shared home/Jita price fetch for the
 * whole scan" precedent. A candidate missing from `home` entirely, or with
 * no positive sell quote, is skipped outright - no quote means no margin to
 * judge it by. A quote whose buy and sell percentiles are exactly equal is
 * skipped too: the same "signature of a market with no real order book on
 * one side" synthetic-quote guard `_scan_build_candidates` applies.
 *
 * One shared `cost_memo` for the whole scan (mirroring `_scan_build_
 * candidates`'s own `cost_memo`) - a material common to many candidates (a
 * base mineral, a shared component) is only ever priced once. */
suspend fun scanBuildCandidates(
    candidates: List<CandidateType>,
    home: Map<Int, OrderStats>,
    cfg: ProductionConfig,
    bom: ProductionBomSource,
    topN: Int = 200,
): List<BuildCandidateRow> {
    val jita = emptyMap<Int, OrderStats>() // see module docstring, point 2
    val memo = mutableMapOf<Int, Double?>()
    val results = mutableListOf<BuildCandidateRow>()

    for (candidate in candidates) {
        val quote = home[candidate.typeId] ?: continue
        val sell = quote.sellPercentile
        if (sell == null || sell <= 0.0) continue
        if (quote.buyPercentile != null && quote.buyPercentile == sell) continue // synthetic-quote guard

        val buildCost = unitBuildCost(candidate.typeId, cfg, home, jita, bom, memo)
        val margin = marginHome(candidate.typeId, buildCost, home, cfg)
        if (margin == null || margin < cfg.minMargin || margin > MAX_PLAUSIBLE_BUILD_MARGIN) continue

        results += BuildCandidateRow(
            typeId = candidate.typeId,
            typeName = candidate.typeName,
            buildCost = buildCost!!,
            margin = margin,
            metaLevel = candidate.metaLevel,
        )
    }

    results.sortByDescending { it.margin }
    return results.take(topN)
}
