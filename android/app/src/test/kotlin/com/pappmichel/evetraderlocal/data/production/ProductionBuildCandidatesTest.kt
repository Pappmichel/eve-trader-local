package com.pappmichel.evetraderlocal.data.production

import com.pappmichel.evetraderlocal.data.esi.OrderStats
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** Ported (in spirit, not case-for-case) from the desktop build's
 * tests/test_production_discovery.py - the movement/Goonmetrics-ranking
 * cases (`test_below_min_daily_profit_...`,
 * `test_potential_daily_profit_is_movement_times_margin_times_build_cost`)
 * and the stock-target exclusion case
 * (`test_existing_stock_targets_are_excluded_from_discovery`) have no
 * equivalent here at all - see `ProductionBuildCandidates.kt`'s own module
 * docstring, points 1 and 3, for why. What *is* ported is every case that
 * exercises the surviving margin/synthetic-quote/ranking math, adapted to a
 * [ProductionBomSource] fake the same way `ProductionBuildCostTest` already
 * does rather than desktop's `storage.replace_sde_data` fixture. */
class ProductionBuildCandidatesTest {
    private val MINERAL = 34
    private val GOOD_ITEM = 90101
    private val LOW_MARGIN_ITEM = 90102
    private val BAD_QUOTE_ITEM = 90104
    private val GOOD_BP = 91101
    private val LOW_MARGIN_BP = 91102
    private val BAD_QUOTE_BP = 91104

    // Same "flat 10% ME reduces 10 minerals to 9 at 5.0 each = 45.0, no job
    // cost index configured" arithmetic the desktop fixture's own comment
    // documents - build_cost is 45.0 for every widget below regardless of
    // its home quote.
    private fun cfg(minMargin: Double = 0.15) = ProductionConfig(
        jitaBuyBrokerFee = 0.0,
        haulCostPerM3 = 0.0,
        facilityTaxRate = 0.0,
        marketFeesRate = 0.0,
        jobCostIndexRate = 0.0,
        materialEfficiency = 10,
        minMargin = minMargin,
    )

    private class FakeBom(
        private val products: Map<Int, BlueprintForProduct>,
        private val materials: Map<Int, List<Pair<Int, Double>>>,
    ) : ProductionBomSource {
        override suspend fun blueprintForProduct(productTypeId: Int): BlueprintForProduct? = products[productTypeId]
        override suspend fun blueprintMaterials(blueprintTypeId: Int): List<Pair<Int, Double>> =
            materials[blueprintTypeId] ?: emptyList()
        override suspend fun volumeOf(typeId: Int): Double? = null
    }

    private val bom = FakeBom(
        products = mapOf(
            GOOD_ITEM to BlueprintForProduct(GOOD_BP, 1.0),
            LOW_MARGIN_ITEM to BlueprintForProduct(LOW_MARGIN_BP, 1.0),
            BAD_QUOTE_ITEM to BlueprintForProduct(BAD_QUOTE_BP, 1.0),
        ),
        materials = mapOf(
            GOOD_BP to listOf(MINERAL to 10.0),
            LOW_MARGIN_BP to listOf(MINERAL to 10.0),
            BAD_QUOTE_BP to listOf(MINERAL to 10.0),
        ),
    )

    private val candidates = listOf(
        CandidateType(GOOD_ITEM, "Good Widget", 1.0, null),
        CandidateType(LOW_MARGIN_ITEM, "Thin-Margin Widget", 1.0, null),
        CandidateType(BAD_QUOTE_ITEM, "Suspicious Widget", 1.0, null),
    )

    private fun stats(sell: Double, buy: Double? = null): OrderStats =
        OrderStats(sellPercentile = sell, sellVolume = 1.0, buyPercentile = buy, buyVolume = 1.0)

    // build_cost 45.0, home sell 100.0 -> margin ~122%, clears 15%.
    private val home = mapOf(
        MINERAL to stats(5.0, 4.5),
        GOOD_ITEM to stats(100.0, 90.0),
        // build_cost 45.0, home sell 51.0 -> margin ~13% < 15%.
        LOW_MARGIN_ITEM to stats(51.0, 50.0),
        // buy == sell exactly - synthetic/fallback quote, must be skipped.
        BAD_QUOTE_ITEM to stats(100.0, 100.0),
    )

    @Test
    fun `only the margin-qualifying item is returned`() = runBlocking {
        val results = scanBuildCandidates(candidates, home, cfg(), bom)

        assertEquals(setOf(GOOD_ITEM), results.map { it.typeId }.toSet())
    }

    @Test
    fun `below-min-margin item is excluded`() = runBlocking {
        val results = scanBuildCandidates(candidates, home, cfg(), bom)

        assertTrue(results.none { it.typeId == LOW_MARGIN_ITEM })
    }

    @Test
    fun `a buy-equals-sell home quote is treated as synthetic and skipped`() = runBlocking {
        val results = scanBuildCandidates(candidates, home, cfg(minMargin = 0.0), bom)

        assertTrue(results.none { it.typeId == BAD_QUOTE_ITEM })
    }

    @Test
    fun `build cost and margin are computed correctly for a qualifying candidate`() = runBlocking {
        val results = scanBuildCandidates(candidates, home, cfg(), bom)

        // material_cost = ceil(10 * 0.9) units at 5.0 each = 45.0; eiv (this
        // port's real-material-price approximation, see ProductionBuildCost
        // .kt's own module docstring point 5) = 10 * 5.0 = 50.0; job_cost_rate
        // is SCC_SURCHARGE_RATE alone (0.04) since cfg zeroes out the index
        // and facility-tax terms - SCC_SURCHARGE_RATE is a fixed constant,
        // not a ProductionConfig field, so it always applies. build_cost =
        // 45.0 + 50.0 * 0.04 = 47.0.
        val good = results.first { it.typeId == GOOD_ITEM }
        assertEquals(47.0, good.buildCost, 1e-9)
        assertEquals((100.0 - 47.0) / 47.0, good.margin, 1e-9)
    }

    @Test
    fun `top N slices the already-ranked results`() = runBlocking {
        val results = scanBuildCandidates(candidates, home, cfg(minMargin = 0.0), bom, topN = 1)

        assertEquals(1, results.size)
        assertEquals(GOOD_ITEM, results[0].typeId) // highest margin
    }

    @Test
    fun `a margin above the plausibility ceiling is excluded`() = runBlocking {
        // 10000.0 sell vs 45.0 build_cost is a ~22000% margin, far past
        // MAX_PLAUSIBLE_BUILD_MARGIN (300%) - almost certainly a stale/
        // synthetic quote, not a real opportunity.
        val extremeHome = home + (GOOD_ITEM to stats(10_000.0, 9_000.0))
        val results = scanBuildCandidates(candidates, extremeHome, cfg(minMargin = 0.0), bom)

        assertTrue(results.none { it.typeId == GOOD_ITEM })
    }

    @Test
    fun `structural material closure walks the full BOM breadth-first`() = runBlocking {
        // Mirrors engine.structural_material_closure's own "visited |= frontier"
        // shape: the seeds themselves are part of the closure, not just what's
        // reachable below them - a superset of what the priced walk visits, by
        // design (see this function's own docstring).
        val closure = structuralMaterialClosure(listOf(GOOD_ITEM, LOW_MARGIN_ITEM), bom)

        assertEquals(setOf(GOOD_ITEM, LOW_MARGIN_ITEM, MINERAL), closure)
    }
}
