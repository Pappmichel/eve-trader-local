package com.pappmichel.evetraderlocal.data.doctrine

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Ported (with the "Build" leg removed - see [ShoppingList]'s own
 * docstring) from the shape of the desktop build's
 * `tests/test_doctrine_shopping_list.py`: same three findings (cheapest
 * source correctly identified either way, and an unpriceable item never
 * silently reads as 0 ISK), restated as a two-way (C-J/Jita) comparison. */
class ShoppingListTest {
    private fun aggregatedRow(typeId: Int, name: String, shortfall: Double) = StockpileStatus.AggregatedRow(
        typeId = typeId, typeName = name, requiredTotal = shortfall, available = 0.0,
        shortfall = shortfall, severity = "critical", fittingCount = 1,
    )

    @Test
    fun build_cjWinsWhenCheaper() {
        val rows = listOf(aggregatedRow(1, "Damage Control II", shortfall = 5.0))
        val result = ShoppingList.build(
            aggregatedRows = rows, home = mapOf(1 to 100.0), jita = mapOf(1 to 150.0),
            volumeByType = mapOf(1 to 10.0), importCostPerM3 = 900.0, jitaBuyBrokerFee = 0.0147,
        )
        val row = result.single()
        val expectedCj = 100.0 * 1.0147
        val expectedJita = 150.0 * 1.0147 + 900.0 * 10.0
        assertEquals(expectedCj, row.cjPrice!!, 1e-6)
        assertEquals(expectedJita, row.jitaLandedPrice!!, 1e-6)
        assertEquals("C-J", row.recommendedSource)
        assertEquals(expectedCj * 5.0, row.totalCost!!, 1e-6)
    }

    @Test
    fun build_jitaWinsWhenLandedIsCheaper() {
        val rows = listOf(aggregatedRow(2, "Cheap Mineral", shortfall = 10.0))
        val result = ShoppingList.build(
            aggregatedRows = rows, home = mapOf(2 to 500.0), jita = mapOf(2 to 10.0),
            volumeByType = mapOf(2 to 10.0), importCostPerM3 = 1.0, jitaBuyBrokerFee = 0.0147,
        )
        val row = result.single()
        assertEquals("Jita", row.recommendedSource)
        assertEquals(row.jitaLandedPrice!! * 10.0, row.totalCost!!, 1e-6)
    }

    @Test
    fun build_unpriceableItemIsNeverZeroIsk() {
        val rows = listOf(aggregatedRow(3, "Obscure Faction Widget", shortfall = 3.0))
        val result = ShoppingList.build(
            aggregatedRows = rows, home = emptyMap(), jita = emptyMap(),
            volumeByType = emptyMap(), importCostPerM3 = 900.0, jitaBuyBrokerFee = 0.0147,
        )
        val row = result.single()
        assertNull(row.cjPrice)
        assertNull(row.jitaLandedPrice)
        assertNull(row.recommendedSource)
        assertNull(row.totalCost)
    }

    @Test
    fun build_skipsItemsWithNoRealShortfall() {
        val rows = listOf(aggregatedRow(4, "Fully Stocked Item", shortfall = 0.0))
        val result = ShoppingList.build(
            aggregatedRows = rows, home = mapOf(4 to 100.0), jita = mapOf(4 to 100.0),
            volumeByType = emptyMap(), importCostPerM3 = 900.0, jitaBuyBrokerFee = 0.0147,
        )
        assertTrue(result.isEmpty())
    }

    @Test
    fun fetchHomePrices_blankSlugReturnsEmptyMap() = kotlinx.coroutines.runBlocking {
        val prices = ShoppingList.fetchHomePrices(
            com.pappmichel.evetraderlocal.data.history.GoonmetricsClient(), null,
        )
        assertTrue(prices.isEmpty())
    }
}
