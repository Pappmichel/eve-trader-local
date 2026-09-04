package com.pappmichel.evetraderlocal.data.refining

import com.pappmichel.evetraderlocal.data.esi.EsiClient
import com.pappmichel.evetraderlocal.data.esi.OrderStats
import com.pappmichel.evetraderlocal.data.trading.TradingConfig
import kotlin.math.ceil

/** Ore & Minerals -> Mineral Shopping List, ported from the desktop build's
 * `refining/optimizer.py` + `refining/actions.py`'s
 * `do_optimize_mineral_shopping_list` (parent repo's GitHub issue #93).
 *
 * **Finding on `optimizer.py` (read in full before writing this file): it is
 * a genuine mixed-integer linear program, not a dressed-up greedy pick.** It
 * builds a real cost-minimization LP - one column per candidate ore/ice
 * portion plus one per directly-buyable mineral, one `>=`-required-quantity
 * row per mineral - and solves it with `scipy.optimize.linprog(method="highs")`,
 * with BOTH variable groups marked `integer` (not a continuous relaxation
 * rounded afterwards - the module docstring documents, with numbers, a real
 * optimality-gap bug from an earlier relax-then-round version, confirmed via
 * randomised brute-force comparison: ~8% of small cases landed measurably
 * off, worst case 64% over). `test_refining_optimizer.py` includes a
 * brute-force-vs-solver equivalence test (`test_matches_brute_force_optimum_
 * on_random_small_cases`) that only makes sense against a real solver, plus
 * a hand-constructed case (`test_greedy_per_mineral_ranking_would_lose_to_
 * the_lp`) whose own docstring proves a per-mineral-independent greedy pick
 * gives the WRONG (more expensive) answer on ore that yields two wanted
 * minerals at once. This is not a case of an inflated name over simple logic.
 *
 * **Scope decision made from that finding:** porting an equivalent MIP
 * solver (HiGHS or similar) into a phone-friendly Kotlin/JVM dependency is
 * impractical for one pass - see `build.gradle.kts` before adding any
 * dependency; nothing pure-JVM and lightweight on Maven Central solves
 * general MIPs the way HiGHS does, and hand-rolling a branch-and-bound
 * solver is exactly the "notoriously easy to get subtly wrong" trap
 * `optimizer.py`'s own docstring cites scipy to avoid. So this file
 * deliberately substitutes a documented SIMPLER strategy instead of porting
 * the joint optimization faithfully:
 *
 *     For each required mineral, independently, buy it outright at its
 *     current Jita landed price.
 *
 * This differs from the real behavior in two load-bearing ways:
 * 1. It never considers buying and refining compressed ore/ice at all - the
 *    real tool's whole point is that refining is USUALLY cheaper than buying
 *    minerals outright, and this port cannot show that option yet. Porting
 *    the ore side faithfully needs both a real MIP solver (this decision)
 *    AND the ore/ice candidate universe + `ore_ice_yield` structure/rig/
 *    security/implant/family-skill formula, which - like this file finds for
 *    the optimizer - is a second substantial, not-yet-ported piece: see
 *    `ReprocessingYield.kt`'s own docstring and `ReprocessingQuote.kt`'s
 *    module docstring ("Scope decision" section), both of which already
 *    document that the ore/ice yield path has no ported consumer on this
 *    platform yet. Faking a "buy the cheapest ore and estimate its yield"
 *    fallback without that real formula would produce numbers with no
 *    honest basis, which is worse than the plainly-labeled "direct-buy only"
 *    gap this file documents instead.
 * 2. Even restricted to direct buying, this is per-mineral independent, not
 *    jointly optimized - though with no ore side at all, there is no
 *    cross-mineral tradeoff left for joint optimization to find: buying
 *    each mineral at its own cheapest listed price already IS the cheapest
 *    way to buy a set of unrelated minerals outright (this is exactly the
 *    `all_direct_cost` baseline the desktop optimizer itself reports
 *    `savings_vs_all_direct` against - see `optimizer.py`).
 *
 * A future pass that ports the ore/ice yield engine (needed by Ore Shortlist
 * too - see that view's own scope note) can extend this file with an
 * ore-refining option alongside direct buying; until then this tool tells
 * the truth about being a "what does buying these outright cost right now"
 * calculator, not a refining-vs-buying optimizer.
 *
 * **Further simplification vs. even the direct-buy half of the desktop
 * behavior:** desktop's `landed_cost_per_unit` adds haul cost
 * (`volume_m3 x importCostPerM3`) on top of the Jita sell price x
 * broker fee, and compares that Jita-landed price against a Goonmetrics
 * "home market" quote, taking whichever is cheaper (`MineralOption.source`).
 * Neither piece is ported here: mineral item volume is not yet a column in
 * this app's SDE cache (`SdeRepository.kt` only carries `portionSize`/
 * `typeMaterials`, added for Reprocessing Quote - see that file's own
 * docstring on "port only what's used"), and no Goonmetrics home-market
 * fallback is wired for Ore & Minerals yet (`ReprocessingQuote.kt`
 * documents the identical gap for its own structure-order-book path). This
 * file's unit cost is Jita sell percentile x `(1 + jitaBuyBrokerFee)` only -
 * a slight underestimate of real landed cost (no haul, no cheaper-home-
 * market check), always erring toward showing a lower number than the real
 * shopping trip will cost, never a higher one. */

/** One "I need this many units of this mineral" line - the direct Kotlin
 * counterpart of desktop's `MineralRequirement` (see `models.py`). Entered
 * manually today (this port has no Production buy-list wiring yet, matching
 * desktop's own "populated from Production... from #94 on" note). */
data class MineralRequirement(
    val typeId: Int,
    val name: String,
    val requiredQty: Double,
)

/** One resolved shopping-list line: what it costs (right now, at Jita) to
 * buy `requirement`'s whole quantity outright. `unitCost`/`totalCost` are
 * null when Jita has no sell orders for this mineral at all - not an error,
 * just "can't price this one right now" (see `MineralShoppingListPlan.
 * unpriceable`). */
data class MineralShoppingListLine(
    val typeId: Int,
    val name: String,
    val requiredQty: Double,
    val quantity: Int,
    val unitCost: Double?,
    val totalCost: Double?,
)

/** The full plan: one line per requirement, plus totals across whatever
 * could actually be priced. Mirrors the shape of desktop's
 * `ShoppingListPlan` only where this file's own scope decision (see this
 * file's module docstring) still applies - there is no `ore_purchases`/
 * `lp_cost`/`savings_vs_all_direct` here since there is no ore-refining
 * alternative in this pass to compare against. */
data class MineralShoppingListPlan(
    val lines: List<MineralShoppingListLine>,
    val totalCost: Double,
    val unpriceable: List<MineralShoppingListLine>,
)

/** Pure - `statsById` is pre-fetched by the caller (see
 * `fetchMineralShoppingListPrices` below), nothing here touches ESI. Buys
 * each requirement's quantity rounded UP to a whole unit (real minerals are
 * bought in whole units, same reasoning as `optimizer.py`'s own decision 1
 * for its direct-mineral variables - see that module's docstring), priced at
 * `sellPercentile x (1 + jitaBuyBrokerFee)` (see this file's module
 * docstring for what that omits vs. desktop's real landed-cost formula).
 * Requirements with `requiredQty <= 0` are dropped, matching
 * `optimize_shopping_list`'s own `wanted` filter. */
fun buildMineralShoppingList(
    requirements: List<MineralRequirement>,
    statsById: Map<Int, OrderStats>,
    tradingCfg: TradingConfig,
): MineralShoppingListPlan {
    val lines = requirements.filter { it.requiredQty > 0 }.map { req ->
        val quantity = ceil(req.requiredQty - 1e-9).toInt().coerceAtLeast(0)
        val sell = statsById[req.typeId]?.sellPercentile
        val unitCost = sell?.let { it * (1 + tradingCfg.jitaBuyBrokerFee) }
        val totalCost = unitCost?.let { it * quantity }
        MineralShoppingListLine(
            typeId = req.typeId, name = req.name, requiredQty = req.requiredQty,
            quantity = quantity, unitCost = unitCost, totalCost = totalCost,
        )
    }
    val priceable = lines.filter { it.totalCost != null }
    val unpriceable = lines.filter { it.totalCost == null }
    return MineralShoppingListPlan(
        lines = lines,
        totalCost = priceable.sumOf { it.totalCost ?: 0.0 },
        unpriceable = unpriceable,
    )
}

/** Fetches Jita order-book stats for every requirement's `typeId` in one
 * bulk call - the only network/IO this feature needs, reusing
 * `EsiClient.regionOrderStatsBulk` per the port's own convention of never
 * adding new price-fetch machinery for a view that can reuse what's already
 * there (see `ReprocessingQuoteScreen.kt`, `StationTradingCandidateDiscovery.
 * kt`). */
suspend fun fetchMineralShoppingListPrices(
    esi: EsiClient,
    tradingCfg: TradingConfig,
    requirements: List<MineralRequirement>,
): Map<Int, OrderStats> {
    val typeIds = requirements.filter { it.requiredQty > 0 }.map { it.typeId }.distinct()
    if (typeIds.isEmpty()) return emptyMap()
    return esi.regionOrderStatsBulk(tradingCfg.jitaRegionId, typeIds)
}
