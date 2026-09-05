package com.pappmichel.evetraderlocal.data.refining

import com.pappmichel.evetraderlocal.data.esi.EsiClient
import com.pappmichel.evetraderlocal.data.esi.OrderStats
import com.pappmichel.evetraderlocal.data.sde.SdeRepository
import com.pappmichel.evetraderlocal.data.trading.TradingConfig
import org.ojalgo.optimisation.ExpressionsBasedModel
import org.ojalgo.optimisation.Variable
import kotlin.math.ceil
import kotlin.math.max
import kotlin.math.roundToInt

/** Ore & Minerals -> Mineral Shopping List's real buy-vs-refine solver: the
 * ore-refining alternative `MineralShoppingList.kt` originally left out,
 * added once this file's own investigation (see below) confirmed a real
 * solver is practical here after all.
 *
 * **The investigation, and why the earlier "impractical" call was wrong.**
 * `MineralShoppingList.kt`'s original docstring reasoned that porting an
 * equivalent of desktop's `scipy.optimize.linprog(method="highs")` MIP
 * solver into a phone-friendly Kotlin/JVM dependency was impractical, and
 * that hand-rolling branch-and-bound was too risky to attempt. Both halves
 * of that were checked properly this time, not assumed:
 *
 * 1. **ojAlgo (`org.ojalgo:ojalgo`) resolves cleanly from Maven Central** -
 *    confirmed live via `curl` against `repo1.maven.org`'s metadata (57.2.0
 *    is current) and by actually pulling it into a standalone Kotlin/JVM
 *    Gradle harness (`gradle test`, not just a metadata check). Its own POM
 *    declares zero runtime dependencies and a Java 11 target - comfortably
 *    under this app's Java 17 `compileOptions`, and Java 11 bytecode runs
 *    fine on minSdk 26 (Android O; O is also the first API level with
 *    native `invokedynamic` support, which some Java 9+ libraries rely on
 *    for string concatenation - ojAlgo's jar was inspected directly
 *    (`javap`) and uses neither `invokedynamic` nor any JNI/native code, so
 *    there was nothing here for D8/R8 to trip over even below API 26). The
 *    jar itself is ~2.7 MB - a real but modest addition, not the "~5MB
 *    dependency for an occasional-use screen" concern the task brief raised
 *    as a plausible reason to say no; see `build.gradle.kts` for where it's
 *    declared.
 * 2. **ojAlgo's `ExpressionsBasedModel` solves genuine small mixed-integer
 *    programs, not just continuous LPs** - `Variable.integer(true)` marks a
 *    column integer exactly like `linprog`'s own `integrality` parameter,
 *    and `ExpressionsBasedModel.minimise()` runs its own branch-and-bound
 *    over the LP relaxation. This was proven, not assumed: every one of
 *    `test_refining_optimizer.py`'s hand-computed cases (including the
 *    `test_greedy_per_mineral_ranking_would_lose_to_the_lp` case that a
 *    per-mineral greedy pick gets wrong) was re-run against ojAlgo in that
 *    same standalone harness and matched the desktop suite's expected
 *    numbers exactly, and the realistic-scale case (60 ore candidates x 8
 *    real EVE minerals, `test_realistic_scale_solves_quickly`'s own inputs)
 *    solved in well under a second - nowhere near desktop's own 5s HiGHS
 *    time-limit safety net. `MineralShoppingListOptimizerTest` in this repo
 *    ports the same cases against the real implementation below.
 *
 * A genuinely hand-rolled branch-and-bound was never actually attempted
 * (and still isn't needed): once ojAlgo confirmed both resolvable and
 * correct, reaching for a general-purpose LP/MIP library already trusted by
 * the desktop build's own reasoning (see `optimizer.py`'s module docstring
 * on why THAT file uses scipy/HiGHS rather than hand-rolling simplex - "the
 * kind of thing that's notoriously easy to get subtly wrong") was the more
 * defensible choice than re-deriving that risk on a second platform for a
 * problem this size (well under ~20 ore types x a handful of minerals -
 * ojAlgo's own branch-and-bound doesn't need the input to be small to stay
 * fast, but this tool's realistic scale never approaches a size where that
 * would matter).
 *
 * **The model itself is a direct, unsimplified port of `optimizer.py`'s
 * LP/MIP** (see that file's own module docstring for the full derivation;
 * summarized here only to the extent Kotlin/ojAlgo specifics matter):
 *
 *     minimize    sum_i  ore_portions_i x landed_cost_per_portion_i
 *               + sum_j  direct_buy_j   x mineral_landed_cost_j
 *     subject to  sum_i  ore_portions_i x yield_ij  +  direct_buy_j  >=  required_j
 *                 for every required mineral j
 *                 ore_portions_i >= 0,  direct_buy_j >= 0,  BOTH marked integer
 *
 * Both variable groups are integer for exactly the reason `optimizer.py`
 * documents (its decision 1) - reprocessing is genuinely discrete (whole
 * portions, each material's output floored - see `ReprocessingYield.kt`'s
 * `applyYield`) and real minerals are bought in whole units too; leaving
 * either group continuous re-opens the same relax-then-round optimality gap
 * that file's own docstring found and fixed (confirmed via randomised
 * brute-force comparison there: ~8% of small cases measurably off, worst
 * case 64% over). The continuous relaxation is still solved once here too,
 * purely to report `ShoppingListPlan.lpCost` as a "theoretical floor" next
 * to the real whole-unit total, exactly mirroring `optimizer.py`'s own
 * `lp_cost`. `_repairShortfalls` below is the same defensive backstop
 * `optimizer.py`'s `_repair_shortfalls` is - normally unreachable once both
 * variable groups solve as true integers, kept only in case a future input
 * feeds a genuinely degenerate case ojAlgo's own branch-and-bound reports
 * feasible-but-under-covered on despite that.
 *
 * Two things this Kotlin port intentionally leaves matching
 * `MineralShoppingList.kt`'s own already-documented scope gaps, not new
 * ones introduced here: mineral unit cost is Jita sell percentile x
 * `(1 + jitaBuyBrokerFee)` only (no haul, no Goonmetrics home-market
 * fallback - same reasoning as that file's module docstring), and ore
 * candidates/landed cost/yield all reuse `OreShortlist.kt`'s own already-
 * ported formulas (`oreLandedCostPerUnit`, `oreIceYield`, `applyYield`)
 * rather than duplicating them. */

/** One ore/ice candidate as a solver column - the Kotlin counterpart of
 * desktop's `OreOption` (`refining/models.py`). `yieldPerPortion` is the
 * whole-portion output (`applyYield(portionSize, materials, portionSize,
 * yieldPct)` - see `ReprocessingYield.kt`), keyed by mineral type id, for
 * ONLY the minerals a given call actually asked for - building it for every
 * mineral an ore yields would just be wasted columns the solver immediately
 * prices at zero weight. */
data class OreOption(
    val typeId: Int,
    val item: String,
    val family: String,
    val isIce: Boolean,
    val volumeM3: Double,
    val portionSize: Int,
    val landedCostPerUnit: Double,
    val yieldPerPortion: Map<Int, Int>,
) {
    val landedCostPerPortion: Double get() = landedCostPerUnit * portionSize
}

/** One ore/ice line in the resolved plan - the Kotlin counterpart of
 * desktop's `OrePurchase`. */
data class OrePurchase(
    val typeId: Int,
    val item: String,
    val family: String,
    val isIce: Boolean,
    val portions: Int,
    val units: Int,
    val volumeM3: Double,
    val landedCostPerUnit: Double,
    val totalCost: Double,
)

/** One directly-bought mineral line - the Kotlin counterpart of desktop's
 * `DirectMineralPurchase`. */
data class DirectMineralPurchase(
    val typeId: Int,
    val name: String,
    val quantity: Int,
    val landedCostPerUnit: Double,
    val totalCost: Double,
)

/** How one required mineral ended up covered - the Kotlin counterpart of
 * desktop's `MineralCoverage`. `surplus` gets no ISK credit in the
 * objective (same reasoning as `optimizer.py`'s decision 3 - crediting it
 * would turn a shopping list into a trading decision, which is what the Ore
 * Shortlist is for). */
data class MineralCoverage(
    val typeId: Int,
    val name: String,
    val required: Double,
    val fromOre: Int,
    val fromDirect: Int,
    val delivered: Int,
    val surplus: Double,
)

/** The full joint-optimized plan - the Kotlin counterpart of desktop's
 * `ShoppingListPlan`. `lpCost` is the continuous relaxation's own optimum
 * (a theoretical floor, not achievable with whole portions/units);
 * `allDirectCost`/`savingsVsAllDirect` are null when at least one required
 * mineral has no direct Jita price at all (same as `optimizer.py`'s own
 * `all_direct_cost` gate). */
data class ShoppingListPlan(
    val orePurchases: List<OrePurchase>,
    val directPurchases: List<DirectMineralPurchase>,
    val coverage: List<MineralCoverage>,
    val oreCost: Double,
    val directCost: Double,
    val totalCost: Double,
    val lpCost: Double,
    val allDirectCost: Double?,
    val savingsVsAllDirect: Double?,
    val totalVolumeM3: Double,
)

/** The Kotlin counterpart of desktop's `ActionError` (raised by
 * `optimize_shopping_list`) - this platform has no shared `ActionError`
 * type to reuse (see `MineralShoppingList.kt`'s own port notes on scope),
 * so a plain message-carrying exception is the whole contract; callers
 * already catch `Exception` and show `.message` (see
 * `MineralShoppingListScreen.kt`). */
class ShoppingListSolveException(message: String) : Exception(message)

/** Solver noise guard - ojAlgo's own branch-and-bound, like HiGHS, can
 * return e.g. `2.9999999999998` for what is really an exact integer 3; this
 * only needs to survive a round()/comparison, never a round-UP, since
 * there's no fractional remainder left once both variable groups are
 * genuinely integer. */
private const val EPS = 1e-6

/** Safety net mirroring `optimizer.py`'s own `_repair_shortfalls` - normally
 * unreachable once both variable groups solve as true integers; only here
 * in case ojAlgo's branch-and-bound reports a feasible-but-under-covered
 * incumbent on some future degenerate input. */
private const val MAX_REPAIR_ROUNDS = 8

private data class SolveOutcome(val objective: Double, val orePortions: DoubleArray, val directUnits: DoubleArray)

/** Builds and solves one `ExpressionsBasedModel` for the given ore/direct
 * columns - called twice by [optimizeShoppingList] (once continuous for
 * `lpCost`, once with `integer = true` for the real plan), exactly
 * mirroring `optimizer.py` solving `linprog` twice for the same reason. */
private fun solve(
    ores: List<OreOption>,
    directIds: List<Int>,
    directPrice: Map<Int, Double>,
    mineralIds: List<Int>,
    required: Map<Int, Double>,
    integer: Boolean,
): SolveOutcome? {
    val model = ExpressionsBasedModel()
    fun column(name: String, cost: Double): Variable {
        val v = model.addVariable(name).lower(0.0).weight(cost)
        return if (integer) v.integer(true) else v
    }
    val oreVars = ores.map { ore -> column("ore${ore.typeId}", ore.landedCostPerPortion) }
    val directVars = directIds.map { id -> column("direct$id", directPrice.getValue(id)) }

    for (mineralId in mineralIds) {
        val expr = model.addExpression("mineral_$mineralId")
        ores.forEachIndexed { i, ore ->
            val y = ore.yieldPerPortion[mineralId] ?: 0
            if (y > 0) expr.set(oreVars[i], y.toDouble())
        }
        val directIdx = directIds.indexOf(mineralId)
        if (directIdx >= 0) expr.set(directVars[directIdx], 1.0)
        expr.lower(required.getValue(mineralId))
    }

    val result = model.minimise()
    if (!result.state.isFeasible) return null
    return SolveOutcome(
        objective = result.value,
        orePortions = DoubleArray(ores.size) { i -> oreVars[i].value.toDouble() },
        directUnits = DoubleArray(directIds.size) { i -> directVars[i].value.toDouble() },
    )
}

/** Pure - every ore/mineral price, yield and portion size is pre-fetched by
 * the caller (see [fetchOreOptionsForMinerals] below), nothing here touches
 * ESI/SDE. `directPriceById` mirrors desktop's `mineral_options`: a missing
 * or null entry just means "can't be bought directly", not an error, as
 * long as some ore yields it - see this file's module docstring for the
 * full LP/MIP this solves and why. */
fun optimizeShoppingList(
    requirements: List<MineralRequirement>,
    oreOptions: List<OreOption>,
    directPriceById: Map<Int, Double?>,
): ShoppingListPlan {
    val wanted = requirements.filter { it.requiredQty > 0 }
    if (wanted.isEmpty()) {
        throw ShoppingListSolveException("No mineral requirements to solve for - add at least one mineral and quantity.")
    }

    val mineralIds = wanted.map { it.typeId }
    val required = wanted.associate { it.typeId to it.requiredQty }
    val names = wanted.associate { it.typeId to it.name }

    // Only ores that actually yield something we asked for are columns in
    // the model - an ore whose entire yield is minerals nobody wants is
    // pure cost, same filter as optimizer.py's own `ores` comprehension.
    val ores = oreOptions.filter { o -> o.portionSize > 0 && mineralIds.any { m -> (o.yieldPerPortion[m] ?: 0) > 0 } }

    val directPrice = mineralIds.mapNotNull { m -> directPriceById[m]?.let { m to it } }.toMap()

    val unreachable = mineralIds.filter { m -> m !in directPrice && ores.none { (it.yieldPerPortion[m] ?: 0) > 0 } }
    if (unreachable.isNotEmpty()) {
        throw ShoppingListSolveException(
            "No way to source " + unreachable.map { names.getValue(it) }.sorted().joinToString(", ") +
                " - no compressed ore/ice on the shortlist refines into it and it isn't listed in Jita right now."
        )
    }

    val directIds = mineralIds.filter { it in directPrice }

    // Solved once, continuous, purely to report `lpCost` as a theoretical
    // floor - the actual plan is never derived from this (see optimizer.py
    // decision 1's docstring for why).
    val relaxed = solve(ores, directIds, directPrice, mineralIds, required, integer = false)
        ?: throw ShoppingListSolveException("Could not solve the shopping list.")
    val lpCost = relaxed.objective

    val result = solve(ores, directIds, directPrice, mineralIds, required, integer = true)
        ?: throw ShoppingListSolveException("Could not solve the shopping list as a whole-unit plan.")

    val portions = IntArray(ores.size) { i -> max(0, result.orePortions[i].roundToInt()) }

    fun deliveredFromOre(): Map<Int, Int> {
        val delivered = mineralIds.associateWith { 0 }.toMutableMap()
        ores.forEachIndexed { i, ore ->
            if (portions[i] <= 0) return@forEachIndexed
            for (m in mineralIds) delivered[m] = delivered.getValue(m) + portions[i] * (ore.yieldPerPortion[m] ?: 0)
        }
        return delivered
    }

    var delivered = deliveredFromOre()

    repeat(MAX_REPAIR_ROUNDS) {
        val gaps = mineralIds.filter { m -> m !in directPrice && delivered.getValue(m) + EPS < required.getValue(m) }
        if (gaps.isEmpty()) return@repeat
        for (mineralId in gaps) {
            val candidates = ores.withIndex().filter { (_, o) -> (o.yieldPerPortion[mineralId] ?: 0) > 0 }
            if (candidates.isEmpty()) {
                throw ShoppingListSolveException("No compressed ore/ice yields ${names.getValue(mineralId)}.")
            }
            val (i, ore) = candidates.minByOrNull { (_, o) -> o.landedCostPerPortion / o.yieldPerPortion.getValue(mineralId) }!!
            val gap = required.getValue(mineralId) - delivered.getValue(mineralId)
            val extra = max(1, ceil(gap / ore.yieldPerPortion.getValue(mineralId) - EPS).toInt())
            portions[i] += extra
            delivered = deliveredFromOre()
        }
    }

    val orePurchases = ores.indices.mapNotNull { i ->
        if (portions[i] <= 0) return@mapNotNull null
        val ore = ores[i]
        val units = portions[i] * ore.portionSize
        OrePurchase(
            typeId = ore.typeId, item = ore.item, family = ore.family, isIce = ore.isIce,
            portions = portions[i], units = units, volumeM3 = units * ore.volumeM3,
            landedCostPerUnit = ore.landedCostPerUnit, totalCost = units * ore.landedCostPerUnit,
        )
    }.sortedByDescending { it.totalCost }

    val directPurchases = mutableListOf<DirectMineralPurchase>()
    val fromDirect = mutableMapOf<Int, Int>()
    for (mineralId in mineralIds) {
        val shortfall = required.getValue(mineralId) - (delivered[mineralId] ?: 0)
        if (shortfall <= EPS || mineralId !in directPrice) continue
        val quantity = ceil(shortfall - EPS).toInt()
        fromDirect[mineralId] = quantity
        directPurchases.add(
            DirectMineralPurchase(
                typeId = mineralId, name = names.getValue(mineralId), quantity = quantity,
                landedCostPerUnit = directPrice.getValue(mineralId), totalCost = quantity * directPrice.getValue(mineralId),
            )
        )
    }
    directPurchases.sortByDescending { it.totalCost }

    val coverage = mineralIds.map { mineralId ->
        val oreQty = delivered[mineralId] ?: 0
        val directQty = fromDirect[mineralId] ?: 0
        val total = oreQty + directQty
        MineralCoverage(
            typeId = mineralId, name = names.getValue(mineralId), required = required.getValue(mineralId),
            fromOre = oreQty, fromDirect = directQty, delivered = total, surplus = total - required.getValue(mineralId),
        )
    }.sortedBy { it.name }

    val short = coverage.filter { it.delivered + EPS < it.required }
    if (short.isNotEmpty()) {
        throw ShoppingListSolveException(
            "Could not build a plan that covers " + short.map { it.name }.sorted().joinToString(", ") + "."
        )
    }

    val oreCost = orePurchases.sumOf { it.totalCost }
    val directCost = directPurchases.sumOf { it.totalCost }
    val allDirectCost = if (directPrice.size == mineralIds.size) {
        mineralIds.sumOf { required.getValue(it) * directPrice.getValue(it) }
    } else null
    val totalCost = oreCost + directCost

    return ShoppingListPlan(
        orePurchases = orePurchases, directPurchases = directPurchases, coverage = coverage,
        oreCost = oreCost, directCost = directCost, totalCost = totalCost, lpCost = lpCost,
        allDirectCost = allDirectCost,
        savingsVsAllDirect = allDirectCost?.let { it - totalCost },
        totalVolumeM3 = orePurchases.sumOf { it.volumeM3 },
    )
}

// ------------------------------------------------------- fetching (impure)

/** Builds the ore/ice candidate columns [optimizeShoppingList] needs, for
 * only the minerals a given requirements list actually asks for - reusing
 * `OreShortlist.kt`'s own candidate universe, landed-cost and yield
 * formulas rather than duplicating them (this file's module docstring).
 * `jitaStatsById` must carry order-book stats for every candidate's own
 * type id (fetched the same way `OreShortlistScreen.kt` does, via
 * `EsiClient.regionOrderStatsBulk`); a candidate with no Jita sell orders,
 * no portion size, or whose whole yield is minerals nobody asked for is
 * silently dropped - not an error, `optimizeShoppingList` itself decides
 * whether the remaining candidates are enough to reach every requirement. */
suspend fun fetchOreOptionsForMinerals(
    sde: SdeRepository,
    jitaStatsById: Map<Int, OrderStats>,
    tradingCfg: TradingConfig,
    refiningCfg: RefiningConfig,
    mineralTypeIds: List<Int>,
): List<OreOption> {
    val candidates = buildOreCandidateUniverse(sde)
    val wanted = mineralTypeIds.toSet()
    val options = mutableListOf<OreOption>()
    for (c in candidates) {
        val jitaSell = jitaStatsById[c.typeId]?.sellPercentile ?: continue
        val portionSize = sde.portionSize(c.typeId) ?: continue
        if (portionSize <= 0) continue
        val materials = sde.typeMaterials(c.typeId)
        val yieldPct = oreIceYield(refiningCfg, c.family)
        val perPortion = applyYield(portionSize, materials, portionSize, yieldPct)
            .filterKeys { it in wanted }
            .filterValues { it > 0 }
        if (perPortion.isEmpty()) continue
        val unitLandedCost = oreLandedCostPerUnit(jitaSell, c.volumeM3, tradingCfg) ?: continue
        options.add(
            OreOption(
                typeId = c.typeId, item = c.item, family = c.family, isIce = c.isIce,
                volumeM3 = c.volumeM3, portionSize = portionSize, landedCostPerUnit = unitLandedCost,
                yieldPerPortion = perPortion,
            )
        )
    }
    return options
}

/** Convenience wrapper matching [fetchMineralShoppingListPrices]'s own
 * shape: fetches Jita order-book stats for every ore/ice candidate type id
 * in one bulk call, the only extra network round-trip this alternative
 * needs beyond what [fetchMineralShoppingListPrices] already fetches for
 * the direct-buy side. */
suspend fun fetchOreCandidateJitaStats(
    esi: EsiClient,
    tradingCfg: TradingConfig,
    sde: SdeRepository,
): Map<Int, OrderStats> {
    val candidates = buildOreCandidateUniverse(sde)
    if (candidates.isEmpty()) return emptyMap()
    return esi.regionOrderStatsBulk(tradingCfg.jitaRegionId, candidates.map { it.typeId }.distinct())
}
