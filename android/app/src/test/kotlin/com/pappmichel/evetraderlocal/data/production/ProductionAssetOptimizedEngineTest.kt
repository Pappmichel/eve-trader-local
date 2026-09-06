package com.pappmichel.evetraderlocal.data.production

import com.pappmichel.evetraderlocal.data.esi.OrderStats
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * JUnit4 port, case for case, of the relevant scenarios in the desktop
 * build's tests/test_production_planner.py for engine.plan_asset_optimized:
 * - test_plan_asset_optimized_splits_ready_now_from_blocked
 * - test_plan_asset_optimized_stock_on_hand_excludes_incoming_jobs
 * - test_plan_asset_optimized_respects_min_margin_gate
 *
 * Same synthetic BOM as the desktop test file's own module docstring:
 *   FINISHED_A --(2x)--> COMPONENT --(10x)--> MINERAL (Input, buy only)
 * Only MINERAL has a home quote (COMPONENT/FINISHED_A are never listed at
 * home), so buyPrice() returns null for them and buyOrBuildDecision always
 * picks "Build" for anything with a real recipe - matching the desktop
 * fixture's own comment.
 */
class ProductionAssetOptimizedEngineTest {
    private val mineral = 34
    private val component = 91201
    private val finishedA = 91202
    private val componentBp = 92201
    private val finishedABp = 92202

    private val bom = object : ProductionBomSource {
        override suspend fun blueprintForProduct(productTypeId: Int): BlueprintForProduct? = when (productTypeId) {
            component -> BlueprintForProduct(componentBp, 1.0)
            finishedA -> BlueprintForProduct(finishedABp, 1.0)
            else -> null
        }

        override suspend fun blueprintMaterials(blueprintTypeId: Int): List<Pair<Int, Double>> = when (blueprintTypeId) {
            componentBp -> listOf(mineral to 10.0)
            finishedABp -> listOf(component to 2.0)
            else -> emptyList()
        }

        override suspend fun volumeOf(typeId: Int): Double? = null
    }

    private val home = mapOf(mineral to OrderStats(sellPercentile = 5.0, sellVolume = 0.0, buyPercentile = 4.5, buyVolume = 0.0))
    private val jita = emptyMap<Int, OrderStats>()

    private fun cfg(minMargin: Double = 0.0) = ProductionConfig(
        jitaBuyBrokerFee = 0.0,
        haulCostPerM3 = 0.0,
        facilityTaxRate = 0.0,
        marketFeesRate = 0.0,
        componentOverbuild = 0.0,
        minMargin = minMargin,
    )

    private class FakeStock(
        private val owned: Map<Int, Double> = emptyMap(),
        private val incoming: Map<Int, Double> = emptyMap(),
    ) : StockSource {
        override suspend fun ownedQuantity(typeId: Int): Double = owned[typeId] ?: 0.0
        override suspend fun incomingIndustryRuns(typeId: Int): Double = incoming[typeId] ?: 0.0
    }

    @Test
    fun `splits ready now from blocked`() = runBlocking {
        // FINISHED_A needs 18 units of COMPONENT for its 10 runs (2 x 10) -
        // with only 9 physically on hand, exactly half of FINISHED_A's runs
        // are startable right now, and the remaining COMPONENT shortfall (9
        // units) becomes its own job, itself entirely blocked since no
        // MINERAL is on hand to build it.
        val stockTargets = listOf(StockTarget(finishedA, "Finished Widget A", 10.0, false))
        val stock = FakeStock(owned = mapOf(component to 9.0))

        val result = planAssetOptimized(
            cfg(), stockTargets, emptyMap(), emptyMap(), home, jita, bom, stock,
        ) { it.toString() }

        val jobsByType = result.jobs.associateBy { it.typeId }
        val fa = jobsByType.getValue(finishedA)
        assertEquals(10, fa.jobRuns)
        assertEquals(5, fa.runsReadyNow)
        assertEquals(0.0, fa.stockCoverage!!, 1e-9) // 0 FINISHED_A units on hand / target 10

        val comp = jobsByType.getValue(component)
        assertEquals(9, comp.jobRuns) // 18 needed - 9 on hand
        assertEquals(0, comp.runsReadyNow) // no MINERAL on hand to build any of it
        assertEquals(0.5, comp.stockCoverage!!, 1e-9) // 9 available / 18 buffered demand

        // MINERAL has no blueprint at all - it never becomes a job here.
        assertTrue(mineral !in jobsByType)
    }

    @Test
    fun `stock on hand excludes incoming jobs`() = runBlocking {
        // 18 units "as good as in stock" via an incoming job, but 0
        // physically on hand.
        val stockTargets = listOf(StockTarget(finishedA, "Finished Widget A", 10.0, false))
        val stock = FakeStock(incoming = mapOf(component to 18.0))

        val result = planAssetOptimized(
            cfg(), stockTargets, emptyMap(), emptyMap(), home, jita, bom, stock,
        ) { it.toString() }

        val jobsByType = result.jobs.associateBy { it.typeId }
        // Sizing nets the full 18 incoming units against the 18 needed, so
        // no further COMPONENT job/shortfall exists at all...
        assertTrue(component !in jobsByType)
        // ...but readiness sees 0 units physically on hand, so nothing is
        // ready now.
        assertEquals(0, jobsByType.getValue(finishedA).runsReadyNow)
        assertEquals(10, jobsByType.getValue(finishedA).jobRuns)
    }

    @Test
    fun `respects min margin gate`() = runBlocking {
        // Near-zero margin at home for FINISHED_A - an unprofitable stock
        // target produces no job at all.
        val homeWithFinishedA = home + (finishedA to OrderStats(1.0, 0.0, 1.0, 0.0))
        val stockTargets = listOf(StockTarget(finishedA, "Finished Widget A", 10.0, false))
        val stock = FakeStock()

        val result = planAssetOptimized(
            cfg(minMargin = 0.5), stockTargets, emptyMap(), emptyMap(), homeWithFinishedA, jita, bom, stock,
        ) { it.toString() }

        assertTrue(result.jobs.isEmpty())
    }

    @Test
    fun `allocateScarceStock fills smallest claims first`() {
        val covered = allocateScarceStock(listOf(10 to 5.0, 20 to 2.0, 30 to 4.0), available = 6.0)
        // Smallest (20 -> 2.0) fully covered first, then next smallest
        // (30 -> 4.0) exactly exhausts the remaining 4.0, leaving 10
        // entirely uncovered.
        assertEquals(2.0, covered.getValue(20), 1e-9)
        assertEquals(4.0, covered.getValue(30), 1e-9)
        assertEquals(0.0, covered.getValue(10), 1e-9)
    }
}
