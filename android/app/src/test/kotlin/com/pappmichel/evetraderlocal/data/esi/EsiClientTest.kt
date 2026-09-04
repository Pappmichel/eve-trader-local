package com.pappmichel.evetraderlocal.data.esi

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** Ports the desktop build's `tests/test_esi_client.py` coverage of the
 * pure helpers behind order-book pricing - `percentile`/`summarizeOrders`
 * (esi_client.py's `_percentile`/`_summarize_orders`). Neither makes a
 * network call. */
class EsiClientTest {
    @Test
    fun `percentile is nearest-rank and clamped`() {
        val values = listOf(1.0, 2.0, 3.0, 4.0)
        assertEquals(1.0, percentile(values, 0.0))
        assertEquals(3.0, percentile(values, 0.5))
        assertEquals(4.0, percentile(values, 1.0)) // index 4 clamps to the last element
        assertNull(percentile(emptyList(), 0.05))
    }

    @Test
    fun `summarizeOrders splits sides and sums volume`() {
        val orders = listOf(
            MarketOrder(price = 100.0, isBuyOrder = false, volumeRemain = 5.0),
            MarketOrder(price = 110.0, isBuyOrder = false, volumeRemain = 7.0),
            MarketOrder(price = 90.0, isBuyOrder = true, volumeRemain = 3.0),
            MarketOrder(price = 80.0, isBuyOrder = true, volumeRemain = 2.0),
        )
        val stats = summarizeOrders(orders)
        // sells sorted ascending -> 5th percentile is the cheapest ask;
        // buys sorted descending -> the same percentile picks the highest bid.
        assertEquals(100.0, stats.sellPercentile)
        assertEquals(90.0, stats.buyPercentile)
        assertEquals(12.0, stats.sellVolume, 0.0)
        assertEquals(5.0, stats.buyVolume, 0.0)
    }

    @Test
    fun `summarizeOrders on an empty book`() {
        val stats = summarizeOrders(emptyList())
        assertEquals(OrderStats(null, 0.0, null, 0.0), stats)
    }
}
