package com.pappmichel.evetraderlocal.data.production

import com.pappmichel.evetraderlocal.data.esi.OrderStats
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Ported (in spirit, not case-for-case - see this file's own notes on where
 * it diverges) from the desktop build's tests/test_production_engine.py
 * build-cost section (`_material_qty`, `_unit_cost`/`unit_cost_detail`,
 * `margin_home`/`margin_jita`). Exact case-for-case porting isn't possible:
 * this Android slice models Manufacturing only, one flat ME level, no
 * structure/rig bonus, and an EIV approximated from real material prices
 * rather than ESI adjusted prices (see ProductionBuildCost.kt's own module
 * docstring, points 1-5) - so the desktop fixtures' Tech II/Reaction/
 * structure-bonus cases have no equivalent here at all. What *is* ported is
 * the underlying math each surviving case actually exercises: batch-ME
 * rounding, buy-vs-build picking the cheaper side at every level, an
 * unpriceable material collapsing the whole build, product quantity
 * dividing the run cost, and the margin formulas' fee/haul handling. */
class ProductionBuildCostTest {
    private val TRITANIUM = 34
    private val COMPONENT = 100
    private val WIDGET = 200
    private val BLUEPRINT_ID = 900 // arbitrary - this fake source ignores blueprint ids entirely

    private fun stats(sell: Double): OrderStats =
        OrderStats(sellPercentile = sell, sellVolume = 1.0, buyPercentile = null, buyVolume = 0.0)

    private fun cfg(
        me: Int = 10,
        jobCostIndexRate: Double = 0.077742,
        facilityTaxRate: Double = 0.0025,
        marketFeesRate: Double = 0.0537,
        haulCostPerM3: Double = 900.0,
    ) = ProductionConfig(
        materialEfficiency = me,
        jobCostIndexRate = jobCostIndexRate,
        facilityTaxRate = facilityTaxRate,
        marketFeesRate = marketFeesRate,
        haulCostPerM3 = haulCostPerM3,
    )

    /** A tiny in-memory BOM: `products` maps a produced type to
     * (blueprintTypeId, productQty); `materials` maps a blueprintTypeId to
     * its [(materialTypeId, baseQty)] list at ME 0. No I/O, no Room - the
     * whole reason [unitBuildCost] takes a [ProductionBomSource] interface
     * rather than a concrete `SdeRepository`. */
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

    // ------------------------------------------------------------- material_qty
    // Mirrors test_material_qty_rounds_up_per_batch_and_floors_at_runs exactly
    // - this rounding rule is untouched by every other simplification.
    @Test
    fun `material qty rounds up per batch and floors at runs`() {
        // 1 * 0.9 * 10 = 9 exactly, but a base_qty=1 material can never drop
        // below one unit per run - floored up to runs (10), not left at 9.
        assertEquals(10.0, materialQty(1.0, 0.9, 10.0), 1e-9)
    }

    @Test
    fun `material qty never drops below the run count`() {
        // A base_qty=1 material at a steep ME reduction: 1 * 0.1 * 5 = 0.5,
        // ceil'd to 1, then floored up to the run count of 5 - ME can shrink
        // a material arbitrarily, but never below one unit per run.
        assertEquals(5.0, materialQty(1.0, 0.1, 5.0), 1e-9)
    }

    @Test
    fun `material qty rounds the batch up once, not per unit`() {
        // 100 * 0.9 * 3 = 270 exactly - no rounding needed, sanity check for
        // the "no accidental extra unit from float noise" 2-decimal round.
        assertEquals(270.0, materialQty(100.0, 0.9, 3.0), 1e-9)
        // 7 * 0.9 * 3 = 18.9 -> ceil to 19.
        assertEquals(19.0, materialQty(7.0, 0.9, 3.0), 1e-9)
    }

    // ------------------------------------------------------------------ unit cost
    @Test
    fun `a raw material with no blueprint is costed at its buy price`() = runBlocking {
        val bom = FakeBom(products = emptyMap(), materials = emptyMap())
        val home = emptyMap<Int, OrderStats>()
        val jita = mapOf(TRITANIUM to stats(100.0))
        val cost = unitBuildCost(TRITANIUM, cfg(), home, jita, bom)
        assertEquals(100.0 * (1 + cfg().jitaBuyBrokerFee), cost!!, 1e-9)
    }

    @Test
    fun `unit cost expands the whole tree and prices the job fee off eiv`() = runBlocking {
        // WIDGET is built from 10x COMPONENT (base qty), COMPONENT is a raw
        // material bought at Jita. No home quotes at all, so buy_price
        // reduces to the plain Jita landed price.
        val bom = FakeBom(
            products = mapOf(WIDGET to BlueprintForProduct(BLUEPRINT_ID, 1.0)),
            materials = mapOf(BLUEPRINT_ID to listOf(COMPONENT to 10.0)),
        )
        val home = emptyMap<Int, OrderStats>()
        val jita = mapOf(COMPONENT to stats(10.0)) // no broker fee configured below (0.0)
        val c = cfg(jobCostIndexRate = 0.05, facilityTaxRate = 0.0, marketFeesRate = 0.0)
            .copy(jitaBuyBrokerFee = 0.0)
        val cost = unitBuildCost(WIDGET, c, home, jita, bom)!!

        val materialMult = 1.0 - c.materialEfficiency / 100.0 // ME10 -> 0.90
        val qty = materialQty(10.0, materialMult, 1.0)         // 10 * 0.9 = 9
        val componentCost = 10.0 // Jita buy price, no fees configured
        val materialCost = qty * componentCost
        val eiv = 10.0 * componentCost // EIV uses the *base* (ME0) qty, not the ME-reduced one
        val jobCostRate = c.jobCostIndexRate + c.facilityTaxRate + SCC_SURCHARGE_RATE
        val expected = materialCost + eiv * jobCostRate

        assertEquals(expected, cost, 1e-6)
    }

    @Test
    fun `unit cost takes the cheaper of buy and build at every level`() = runBlocking {
        // WIDGET can be built for far more than its own listed buy price -
        // the cheaper buy price must win.
        val bom = FakeBom(
            products = mapOf(WIDGET to BlueprintForProduct(BLUEPRINT_ID, 1.0)),
            materials = mapOf(BLUEPRINT_ID to listOf(COMPONENT to 1.0)),
        )
        val home = emptyMap<Int, OrderStats>()
        val jita = mapOf(WIDGET to stats(5.0), COMPONENT to stats(10_000.0))
        val c = cfg().copy(jitaBuyBrokerFee = 0.0)
        val cost = unitBuildCost(WIDGET, c, home, jita, bom)!!
        assertEquals(5.0, cost, 1e-9) // the buy price, not the wildly expensive build
    }

    @Test
    fun `product quantity per run divides the run cost`() = runBlocking {
        // Producing 2 units per run halves the per-unit share of both the
        // material cost and the job fee.
        val bomOne = FakeBom(
            products = mapOf(WIDGET to BlueprintForProduct(BLUEPRINT_ID, 1.0)),
            materials = mapOf(BLUEPRINT_ID to listOf(COMPONENT to 10.0)),
        )
        val bomTwo = FakeBom(
            products = mapOf(WIDGET to BlueprintForProduct(BLUEPRINT_ID, 2.0)),
            materials = mapOf(BLUEPRINT_ID to listOf(COMPONENT to 10.0)),
        )
        val home = emptyMap<Int, OrderStats>()
        val jita = mapOf(COMPONENT to stats(10.0))
        val c = cfg().copy(jitaBuyBrokerFee = 0.0)
        val costOne = unitBuildCost(WIDGET, c, home, jita, bomOne)!!
        val costTwo = unitBuildCost(WIDGET, c, home, jita, bomTwo)!!
        assertEquals(costOne / 2.0, costTwo, 1e-6)
    }

    @Test
    fun `an unpriceable material collapses the build to the buy price`() = runBlocking {
        // COMPONENT has neither a sell order nor a blueprint anywhere - the
        // whole WIDGET build collapses to WIDGET's own buy price rather than
        // treating the missing input as free.
        val bom = FakeBom(
            products = mapOf(WIDGET to BlueprintForProduct(BLUEPRINT_ID, 1.0)),
            materials = mapOf(BLUEPRINT_ID to listOf(COMPONENT to 1.0)),
        )
        val home = emptyMap<Int, OrderStats>()
        val jita = mapOf(WIDGET to stats(42.0)) // COMPONENT: no quote anywhere
        val cost = unitBuildCost(WIDGET, cfg(), home, jita, bom)
        assertEquals(42.0 * (1 + cfg().jitaBuyBrokerFee), cost!!, 1e-9)
    }

    @Test
    fun `nothing priceable anywhere is null, not zero`() = runBlocking {
        val bom = FakeBom(products = emptyMap(), materials = emptyMap())
        val cost = unitBuildCost(TRITANIUM, cfg(), emptyMap(), emptyMap(), bom)
        assertNull(cost)
    }

    @Test
    fun `a cycle in the bom degrades to the buy price instead of overflowing`() = runBlocking {
        // Defensive: WIDGET's blueprint lists WIDGET itself as a material -
        // should never happen in a real SDE, but the cycle guard (memo
        // written before recursing) must still terminate.
        val bom = FakeBom(
            products = mapOf(WIDGET to BlueprintForProduct(BLUEPRINT_ID, 1.0)),
            materials = mapOf(BLUEPRINT_ID to listOf(WIDGET to 1.0)),
        )
        val jita = mapOf(WIDGET to stats(50.0))
        val cost = unitBuildCost(WIDGET, cfg(), emptyMap(), jita, bom)
        assertTrue(cost != null && cost > 0.0)
    }

    // ---------------------------------------------------------------- margins
    @Test
    fun `margin home is net of market fees`() {
        val home = mapOf(WIDGET to stats(100.0))
        val c = cfg(marketFeesRate = 0.05)
        // (100 * 0.95 - 50) / 50 = 0.9
        assertEquals(0.9, marginHome(WIDGET, 50.0, home, c)!!, 1e-9)
    }

    @Test
    fun `margin jita also subtracts the export haul cost`() {
        val jita = mapOf(WIDGET to stats(100.0))
        val c = cfg(marketFeesRate = 0.0, haulCostPerM3 = 10.0)
        // 100 - (10 * 2 m3) = 80; (80 - 50) / 50 = 0.6
        assertEquals(0.6, marginJita(WIDGET, 50.0, jita, 2.0, c)!!, 1e-9)
    }

    @Test
    fun `margin is none without a quote or a build cost`() {
        assertNull(marginHome(WIDGET, 50.0, emptyMap(), cfg()))
        assertNull(marginHome(WIDGET, null, mapOf(WIDGET to stats(100.0)), cfg()))
        assertNull(marginHome(WIDGET, 0.0, mapOf(WIDGET to stats(100.0)), cfg()))
        assertNull(marginJita(WIDGET, 50.0, emptyMap(), 1.0, cfg()))
    }

    @Test
    fun `a zero sell price is not a real quote`() {
        val home = mapOf(WIDGET to stats(0.0))
        assertNull(marginHome(WIDGET, 50.0, home, cfg()))
    }
}
