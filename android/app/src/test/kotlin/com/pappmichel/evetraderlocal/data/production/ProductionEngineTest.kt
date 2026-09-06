package com.pappmichel.evetraderlocal.data.production

import com.pappmichel.evetraderlocal.data.esi.OrderStats
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Tests for [planProduction] and its own building blocks
 * ([currentStock]/[baseRuns]/[expandAll]/[buyOrBuildDecision]) - a Kotlin
 * port, case for case where this platform's Manufacturing-only, flat-ME
 * scope allows it, of desktop's `tests/test_production_planner.py` (the
 * real `engine.plan_production` suite - not `test_production_engine.py`,
 * which mostly covers `classify_activity`/structure-rig/decryptor machinery
 * this Manufacturing-only port never reaches, see `ProductionEngine.kt`'s
 * own module docstring). Same BOM shape as desktop's own fixture:
 *
 *     FINISHED_A --(2x)--> COMPONENT --(10x)--> MINERAL (Input, buy only)
 *     FINISHED_B --(3x)-->/
 *
 * so a reader who knows the desktop suite's fixture immediately recognizes
 * this one - only MINERAL has a home quote in [HOME], matching desktop's
 * own fixture exactly (COMPONENT/FINISHED_A/FINISHED_B are never listed, so
 * [buyPrice] returns null for them and [buyOrBuildDecision] always picks
 * "Build" for anything with a real recipe - a null buy price can never beat
 * a real build cost). */
class ProductionEngineTest {
    private val MINERAL = 34
    private val COMPONENT = 91201
    private val FINISHED_A = 91202
    private val FINISHED_B = 91203
    private val COMPONENT_BP = 92201
    private val FINISHED_A_BP = 92202
    private val FINISHED_B_BP = 92203

    private fun stats(sell: Double): OrderStats =
        OrderStats(sellPercentile = sell, sellVolume = 1.0, buyPercentile = null, buyVolume = 0.0)

    private val HOME = mapOf(MINERAL to stats(5.0))
    private val JITA = emptyMap<Int, OrderStats>()

    /** Matches desktop's own `_cfg()` fixture helper: every friction term
     * zeroed out except `materialEfficiency`, which stays at
     * [ProductionConfig]'s real default (ME10 -> a 0.9 material multiplier -
     * desktop's own fixture leaves it untouched too, hence "2*0.9*10=18" in
     * its own comments), so the pooled-demand math below matches desktop's
     * own worked numbers exactly. */
    private fun cfg(componentOverbuild: Double = 0.0, minMargin: Double = 0.0) = ProductionConfig(
        jitaBuyBrokerFee = 0.0, haulCostPerM3 = 0.0, facilityTaxRate = 0.0, marketFeesRate = 0.0,
        componentOverbuild = componentOverbuild, minMargin = minMargin,
    )

    private fun bom() = FakeBom(
        products = mapOf(
            COMPONENT to BlueprintForProduct(COMPONENT_BP, 1.0),
            FINISHED_A to BlueprintForProduct(FINISHED_A_BP, 1.0),
            FINISHED_B to BlueprintForProduct(FINISHED_B_BP, 1.0),
        ),
        materials = mapOf(
            COMPONENT_BP to listOf(MINERAL to 10.0),
            FINISHED_A_BP to listOf(COMPONENT to 2.0),
            FINISHED_B_BP to listOf(COMPONENT to 3.0),
        ),
    )

    private class FakeBom(
        private val products: Map<Int, BlueprintForProduct>,
        private val materials: Map<Int, List<Pair<Int, Double>>>,
        private val volumes: Map<Int, Double> = emptyMap(),
    ) : ProductionBomSource {
        override suspend fun blueprintForProduct(productTypeId: Int): BlueprintForProduct? = products[productTypeId]
        override suspend fun blueprintMaterials(blueprintTypeId: Int): List<Pair<Int, Double>> =
            materials[blueprintTypeId] ?: emptyList()
        override suspend fun volumeOf(typeId: Int): Double? = volumes[typeId]
    }

    private class FakeStockSource(
        private val owned: Map<Int, Double> = emptyMap(),
        private val incomingRuns: Map<Int, Double> = emptyMap(),
    ) : StockSource {
        override suspend fun ownedQuantity(typeId: Int): Double = owned[typeId] ?: 0.0
        override suspend fun incomingIndustryRuns(typeId: Int): Double = incomingRuns[typeId] ?: 0.0
    }

    private suspend fun name(typeId: Int): String = typeId.toString()

    // -------------------------------------------------------- current stock
    @Test
    fun `current stock nets owned assets and incoming industry jobs`() = runBlocking {
        // 3 physically owned + 2 incoming (2 runs x product_qty 1) = 5.
        val stock = FakeStockSource(owned = mapOf(FINISHED_A to 3.0), incomingRuns = mapOf(FINISHED_A to 2.0))
        val current = currentStock(FINISHED_A, manualStock = emptyMap(), stock = stock, bom = bom())
        assertEquals(5.0, current, 1e-9)
    }

    @Test
    fun `manual stock adds to ESI-derived current stock`() = runBlocking {
        val stock = FakeStockSource(owned = mapOf(FINISHED_A to 3.0))
        val current = currentStock(FINISHED_A, manualStock = mapOf(FINISHED_A to 7.0), stock = stock, bom = bom())
        assertEquals(10.0, current, 1e-9) // 3 ESI + 7 manual
    }

    @Test
    fun `includeIncoming false excludes incoming industry jobs (stock on hand)`() = runBlocking {
        val stock = FakeStockSource(owned = mapOf(FINISHED_A to 3.0), incomingRuns = mapOf(FINISHED_A to 2.0))
        val onHand = currentStock(FINISHED_A, emptyMap(), stock, bom(), includeIncoming = false)
        assertEquals(3.0, onHand, 1e-9)
    }

    // -------------------------------------------------- inventory + missing
    @Test
    fun `inventory reports target vs current stock for every configured target`() = runBlocking {
        val stock = FakeStockSource(owned = mapOf(FINISHED_A to 5.0))
        val targets = listOf(StockTarget(FINISHED_A, "Finished Widget A", 10.0, jitaTarget = false))
        val plan = planProduction(cfg(), targets, emptyMap(), emptyMap(), HOME, JITA, bom(), stock, ::name)

        val invA = plan.inventory.single { it.typeId == FINISHED_A }
        assertEquals(5.0, invA.currentStock, 1e-9)
        assertEquals(5.0, invA.totalMissing, 1e-9)
        assertEquals("Tech I", invA.activity)
    }

    @Test
    fun `an item with no cached blueprint is labeled Input`() = runBlocking {
        val targets = listOf(StockTarget(MINERAL, "Tritanium", 100.0, jitaTarget = false))
        val plan = planProduction(cfg(), targets, emptyMap(), emptyMap(), HOME, JITA, bom(), NoStockSource, ::name)
        assertEquals("Input", plan.inventory.single().activity)
    }

    // ------------------------------------------------- shared-demand pooling
    @Test
    fun `shared component demand pools across stock targets rather than double counting`() = runBlocking {
        // FINISHED_A (target 10) and FINISHED_B (target 5) both consume
        // COMPONENT - its build_runs entry must reflect their combined
        // demand, not two separate (and double-counted) entries. Matches
        // desktop's own worked numbers exactly (see this file's own
        // docstring for the shared fixture).
        val targets = listOf(
            StockTarget(FINISHED_A, "Finished Widget A", 10.0, jitaTarget = false),
            StockTarget(FINISHED_B, "Finished Widget B", 5.0, jitaTarget = false),
        )
        val plan = planProduction(cfg(), targets, emptyMap(), emptyMap(), HOME, JITA, bom(), NoStockSource, ::name)

        val buildByType = plan.buildList.associateBy { it.typeId }
        assertEquals(10, buildByType.getValue(FINISHED_A).jobRuns)
        assertEquals(5, buildByType.getValue(FINISHED_B).jobRuns)
        // COMPONENT: ceil(2*0.9*10)=18 for A + ceil(3*0.9*5)=14 for B = 32 runs (product_qty=1).
        assertEquals(32, buildByType.getValue(COMPONENT).jobRuns)

        // MINERAL is never buildable - the pooled COMPONENT runs' own
        // material demand nets into the Buy List instead of a build entry.
        val buyByType = plan.buyList.associateBy { it.typeId }
        assertTrue(MINERAL in buyByType)
        assertFalse(COMPONENT in buyByType)
        assertEquals(max(32.0, 10 * 0.9 * 32), buyByType.getValue(MINERAL).quantity, 1e-9)
    }

    @Test
    fun `stock netted against one target is not counted again for a second target sharing the same material`() =
        runBlocking {
            // Both targets need COMPONENT; 20 units already on hand should be
            // consumed by pooled demand exactly once (not once per target).
            val stock = FakeStockSource(owned = mapOf(COMPONENT to 20.0))
            val targets = listOf(
                StockTarget(FINISHED_A, "Finished Widget A", 10.0, jitaTarget = false),
                StockTarget(FINISHED_B, "Finished Widget B", 5.0, jitaTarget = false),
            )
            val plan = planProduction(cfg(), targets, emptyMap(), emptyMap(), HOME, JITA, bom(), stock, ::name)

            // Pooled COMPONENT demand is 18 + 14 = 32; 20 already on hand
            // nets it down to 12 runs, not (32 - 20*2) or any double count.
            val buildByType = plan.buildList.associateBy { it.typeId }
            assertEquals(12, buildByType.getValue(COMPONENT).jobRuns)
        }

    // ------------------------------------------------------------ overbuild
    @Test
    fun `overbuild buffer is sized off the parent's stock-oblivious base run count`() = runBlocking {
        val targets = listOf(StockTarget(FINISHED_A, "Finished Widget A", 10.0, jitaTarget = false))
        val plan = planProduction(cfg(componentOverbuild = 0.5), targets, emptyMap(), emptyMap(), HOME, JITA,
            bom(), NoStockSource, ::name)

        // Bare COMPONENT demand: ceil(2*0.9*10) = 18. Buffer: 0.5 * 2 * 0.9 *
        // baseRuns[FINISHED_A]=10 = 9. Target before stock netting: 27; no
        // stock at all, so all 27 becomes real build demand -> 27 runs.
        val component = plan.buildList.single { it.typeId == COMPONENT }
        assertEquals(27, component.jobRuns)
    }

    // ----------------------------------------------------------- margin gate
    @Test
    fun `a stock target that fails the margin gate is dropped from buy and build lists`() = runBlocking {
        // No home/jita quote at all for FINISHED_A -> buildMargin is null,
        // which does NOT fail the gate (matches engine.plan_production: only
        // a real sub-min_margin number skips it, a null margin does not).
        // Force a real, failing margin instead: MINERAL is listed and cheap,
        // COMPONENT is not listed and must be built from it, but FINISHED_A
        // is also listed at a price below its own build cost, and marked as
        // a Jita target so marginJita applies.
        val cheapFinishedAJita = mapOf(FINISHED_A to stats(0.01))
        val targets = listOf(StockTarget(FINISHED_A, "Finished Widget A", 10.0, jitaTarget = true))
        val plan = planProduction(
            cfg(minMargin = 0.15), targets, emptyMap(), emptyMap(), HOME, cheapFinishedAJita, bom(),
            NoStockSource, ::name,
        )

        assertTrue(plan.buyList.none { it.typeId == FINISHED_A } && plan.buildList.none { it.typeId == FINISHED_A })
        // The shortfall is still visible on the inventory row.
        assertEquals(10.0, plan.inventory.single().totalMissing, 1e-9)
    }

    @Test
    fun `a manual override bypasses the margin gate entirely`() = runBlocking {
        val cheapFinishedAJita = mapOf(FINISHED_A to stats(0.01))
        val targets = listOf(StockTarget(FINISHED_A, "Finished Widget A", 10.0, jitaTarget = true))
        val overrides = mapOf(FINISHED_A to BuildOrBuyDecision.BUILD)
        val plan = planProduction(
            cfg(minMargin = 0.15), targets, emptyMap(), overrides, HOME, cheapFinishedAJita, bom(),
            NoStockSource, ::name,
        )

        assertTrue(plan.buildList.any { it.typeId == FINISHED_A })
    }

    // ------------------------------------------------------ buy vs. build
    @Test
    fun `buyOrBuildDecision picks Buy when there is no cached blueprint`() = runBlocking {
        val decision = buyOrBuildDecision(
            MINERAL, cfg(), HOME, JITA, bom(), manualOverrides = emptyMap(), costMemo = emptyMap(),
            hasBlueprint = false,
        )
        assertEquals(BuildOrBuyDecision.BUY, decision)
    }

    @Test
    fun `a manual override always wins over the modeled cost`() = runBlocking {
        val costMemo = mapOf(FINISHED_A to 999999.0) // a deliberately terrible build cost
        val decision = buyOrBuildDecision(
            FINISHED_A, cfg(), HOME, JITA, bom(),
            manualOverrides = mapOf(FINISHED_A to BuildOrBuyDecision.BUILD), costMemo = costMemo, hasBlueprint = true,
        )
        assertEquals(BuildOrBuyDecision.BUILD, decision)
    }

    @Test
    fun `unconfigured items are simply absent rather than zero rows`() = runBlocking {
        val plan = planProduction(cfg(), emptyList(), emptyMap(), emptyMap(), HOME, JITA, bom(), NoStockSource, ::name)
        assertTrue(plan.inventory.isEmpty())
        assertTrue(plan.buyList.isEmpty())
        assertTrue(plan.buildList.isEmpty())
    }

    // --------------------------------------------------------------- cycles
    @Test
    fun `a cycle in the bom terminates at MAX_BOM_DEPTH instead of recursing forever`() = runBlocking {
        val cyclicBom = FakeBom(
            products = mapOf(FINISHED_A to BlueprintForProduct(FINISHED_A_BP, 1.0)),
            materials = mapOf(FINISHED_A_BP to listOf(FINISHED_A to 1.0)),
        )
        val targets = listOf(StockTarget(FINISHED_A, "Finished Widget A", 1.0, jitaTarget = false))
        // Must return, not hang or stack overflow.
        val plan = planProduction(cfg(), targets, emptyMap(), emptyMap(), emptyMap(), emptyMap(), cyclicBom,
            NoStockSource, ::name)
        assertTrue(plan.inventory.isNotEmpty())
    }

    @Test
    fun `unitBuildCost is null for an item with no price and no blueprint`() = runBlocking {
        assertNull(buyPrice(999_999, HOME, JITA, null, cfg()))
    }

    private fun max(a: Double, b: Double) = if (a >= b) a else b
}
