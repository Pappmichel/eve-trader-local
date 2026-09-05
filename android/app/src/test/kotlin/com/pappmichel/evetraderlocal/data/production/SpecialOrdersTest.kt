package com.pappmichel.evetraderlocal.data.production

import com.pappmichel.evetraderlocal.data.esi.OrderStats
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Pure-logic coverage for [computeSpecialOrderPlan] - the only part of
 * `production/actions.py`'s Special Orders quartet that involves any
 * arithmetic at all; `SpecialOrderRepository`'s CRUD is exercised only via
 * the Android instrumented/Robolectric surface (it needs a real
 * `AppDatabase`), same split every other settings-blob repository on this
 * platform already follows. See `SpecialOrders.kt`'s own module docstring
 * for why this is a deliberately smaller substitute for desktop's
 * `engine.plan_special_order`, not a narrowed port of it. */
class SpecialOrdersTest {
    private val TRITANIUM = 34
    private val COMPONENT = 100
    private val WIDGET = 200
    private val BLUEPRINT_ID = 900

    private fun stats(sell: Double): OrderStats =
        OrderStats(sellPercentile = sell, sellVolume = 1.0, buyPercentile = null, buyVolume = 0.0)

    private fun cfg() = ProductionConfig(jitaBuyBrokerFee = 0.0)

    private class FakeBom(
        private val products: Map<Int, BlueprintForProduct>,
        private val materials: Map<Int, List<Pair<Int, Double>>>,
    ) : ProductionBomSource {
        override suspend fun blueprintForProduct(productTypeId: Int): BlueprintForProduct? = products[productTypeId]
        override suspend fun blueprintMaterials(blueprintTypeId: Int): List<Pair<Int, Double>> =
            materials[blueprintTypeId] ?: emptyList()
        override suspend fun volumeOf(typeId: Int): Double? = null
    }

    @Test
    fun `a raw material line item is priced at quantity times buy price`() = runBlocking {
        val bom = FakeBom(products = emptyMap(), materials = emptyMap())
        val jita = mapOf(TRITANIUM to stats(5.0))
        val items = listOf(SpecialOrderLineItem(TRITANIUM, "Tritanium", quantity = 1000.0))

        val plan = computeSpecialOrderPlan(items, cfg(), home = emptyMap(), jita = jita, bom = bom)

        assertEquals(1, plan.size)
        assertEquals(5.0, plan[0].unitCost!!, 1e-9)
        assertEquals(5000.0, plan[0].totalCost!!, 1e-9)
        assertEquals(TRITANIUM, plan[0].typeId)
        assertEquals(1000.0, plan[0].quantity, 1e-9)
    }

    @Test
    fun `a buildable line item picks the cheaper of buy or build, multiplied by quantity`() = runBlocking {
        // WIDGET builds 1:1 from 10x COMPONENT (base qty), COMPONENT bought
        // at Jita for 1 ISK each -> pure material cost of 10 ISK/unit, well
        // under WIDGET's own (inflated) buy price, so build wins.
        val bom = FakeBom(
            products = mapOf(WIDGET to BlueprintForProduct(BLUEPRINT_ID, 1.0)),
            materials = mapOf(BLUEPRINT_ID to listOf(COMPONENT to 10.0)),
        )
        val jita = mapOf(COMPONENT to stats(1.0), WIDGET to stats(1000.0))
        val items = listOf(SpecialOrderLineItem(WIDGET, "Widget", quantity = 3.0))

        val plan = computeSpecialOrderPlan(items, cfg().copy(jobCostIndexRate = 0.0, facilityTaxRate = 0.0),
            home = emptyMap(), jita = jita, bom = bom)

        assertTrue(plan[0].unitCost!! < 1000.0)
        assertEquals(plan[0].unitCost!! * 3.0, plan[0].totalCost!!, 1e-9)
    }

    @Test
    fun `an unpriceable line item comes back with null unit and total cost`() = runBlocking {
        val bom = FakeBom(products = emptyMap(), materials = emptyMap())
        val items = listOf(SpecialOrderLineItem(TRITANIUM, "Tritanium", quantity = 10.0))

        val plan = computeSpecialOrderPlan(items, cfg(), home = emptyMap(), jita = emptyMap(), bom = bom)

        assertNull(plan[0].unitCost)
        assertNull(plan[0].totalCost)
    }

    @Test
    fun `multiple line items are each priced independently and share the memo`() = runBlocking {
        val bom = FakeBom(products = emptyMap(), materials = emptyMap())
        val jita = mapOf(TRITANIUM to stats(5.0), COMPONENT to stats(2.0))
        val items = listOf(
            SpecialOrderLineItem(TRITANIUM, "Tritanium", quantity = 100.0),
            SpecialOrderLineItem(COMPONENT, "Component", quantity = 50.0),
        )

        val plan = computeSpecialOrderPlan(items, cfg(), home = emptyMap(), jita = jita, bom = bom)

        assertEquals(2, plan.size)
        assertEquals(500.0, plan[0].totalCost!!, 1e-9)
        assertEquals(100.0, plan[1].totalCost!!, 1e-9)
    }
}
