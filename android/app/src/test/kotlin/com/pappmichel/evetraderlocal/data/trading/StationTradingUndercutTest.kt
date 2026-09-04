package com.pappmichel.evetraderlocal.data.trading

import com.pappmichel.evetraderlocal.data.esi.CharacterOrder
import com.pappmichel.evetraderlocal.data.esi.MarketOrder
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** Ported case-for-case from the desktop build's
 * tests/test_station_trading_undercut.py, exercised directly against the
 * pure `computeUndercuts` core (this port splits the desktop functions'
 * ESI-fetching and comparison halves apart - see
 * StationTradingUndercut.kt's own docstring). */
class StationTradingUndercutTest {
    private val STATION = 60_003_760L
    private val TRITANIUM = 34
    private val PYERITE = 35

    private fun myOrder(orderId: Long, typeId: Int, price: Double, isBuyOrder: Boolean, locationId: Long = STATION) =
        CharacterOrder(orderId = orderId, typeId = typeId, price = price, isBuyOrder = isBuyOrder, locationId = locationId)

    private fun regionOrder(orderId: Long, typeId: Int, price: Double, isBuyOrder: Boolean, locationId: Long = STATION) =
        MarketOrder(orderId = orderId, typeId = typeId, price = price, isBuyOrder = isBuyOrder, locationId = locationId)

    // --------------------------------------------------------------- sell side
    @Test
    fun `no traders means no undercuts`() {
        assertEquals(emptyList<UndercutRow>(), computeUndercuts(emptyList(), emptyMap(), STATION, isBuyOrder = false))
    }

    @Test
    fun `cheaper competitor sell order is flagged`() {
        val my = listOf(myOrder(1, TRITANIUM, 10.0, false))
        val region = mapOf(TRITANIUM to listOf(regionOrder(2, TRITANIUM, 9.0, false)))
        val result = computeUndercuts(my, region, STATION, isBuyOrder = false)
        assertEquals(listOf(UndercutRow(TRITANIUM, 10.0, 9.0, 1.0)), result)
    }

    @Test
    fun `own order never counts as its own competitor`() {
        // Same order_id in both lists - only order_id, never price/type_id,
        // can exclude an order (the region book carries no owning-character
        // field at all).
        val my = listOf(myOrder(1, TRITANIUM, 10.0, false))
        val region = mapOf(TRITANIUM to listOf(regionOrder(1, TRITANIUM, 10.0, false)))
        assertTrue(computeUndercuts(my, region, STATION, isBuyOrder = false).isEmpty())
    }

    @Test
    fun `orders at a different station are ignored`() {
        val my = listOf(myOrder(1, TRITANIUM, 10.0, false, locationId = 999L))
        assertTrue(computeUndercuts(my, emptyMap(), STATION, isBuyOrder = false).isEmpty())
    }

    @Test
    fun `pooled across multiple own characters not flagged as undercut`() {
        val my = listOf(myOrder(1, TRITANIUM, 10.0, false), myOrder(2, TRITANIUM, 9.0, false))
        val region = mapOf(TRITANIUM to listOf(regionOrder(2, TRITANIUM, 9.0, false)))
        assertTrue(computeUndercuts(my, region, STATION, isBuyOrder = false).isEmpty())
    }

    @Test
    fun `equal or higher competitor price is not an undercut`() {
        val my = listOf(myOrder(1, TRITANIUM, 10.0, false))
        val region = mapOf(TRITANIUM to listOf(regionOrder(2, TRITANIUM, 10.0, false)))
        assertTrue(computeUndercuts(my, region, STATION, isBuyOrder = false).isEmpty())
    }

    @Test
    fun `only my best sell price per type is checked`() {
        val my = listOf(myOrder(1, TRITANIUM, 10.0, false), myOrder(2, TRITANIUM, 12.0, false))
        val region = mapOf(TRITANIUM to listOf(regionOrder(3, TRITANIUM, 11.0, false)))
        // 11.0 beats my worse order (12.0) but not my best (10.0) - not flagged.
        assertTrue(computeUndercuts(my, region, STATION, isBuyOrder = false).isEmpty())
    }

    @Test
    fun `results sorted by difference descending`() {
        val my = listOf(myOrder(1, TRITANIUM, 10.0, false), myOrder(2, PYERITE, 10.0, false))
        val region = mapOf(
            TRITANIUM to listOf(regionOrder(3, TRITANIUM, 9.5, false)),
            PYERITE to listOf(regionOrder(4, PYERITE, 5.0, false)),
        )
        val result = computeUndercuts(my, region, STATION, isBuyOrder = false)
        assertEquals(listOf(PYERITE, TRITANIUM), result.map { it.typeId })
    }

    // ---------------------------------------------------------------- buy side
    @Test
    fun `buy side flags a higher competing bid`() {
        val my = listOf(myOrder(1, TRITANIUM, 4.0, true))
        val region = mapOf(TRITANIUM to listOf(regionOrder(2, TRITANIUM, 4.5, true)))
        val result = computeUndercuts(my, region, STATION, isBuyOrder = true)
        assertEquals(listOf(UndercutRow(TRITANIUM, 4.0, 4.5, 0.5)), result)
    }

    @Test
    fun `buy side a lower competing bid is not an outbid`() {
        val my = listOf(myOrder(1, TRITANIUM, 4.0, true))
        val region = mapOf(TRITANIUM to listOf(regionOrder(2, TRITANIUM, 3.5, true)))
        assertTrue(computeUndercuts(my, region, STATION, isBuyOrder = true).isEmpty())
    }

    @Test
    fun `buy side only considers buy orders from the region book`() {
        // A cheap sell order at the same station/type must never be mistaken
        // for a competing buy order.
        val my = listOf(myOrder(1, TRITANIUM, 4.0, true))
        val region = mapOf(TRITANIUM to listOf(regionOrder(2, TRITANIUM, 100.0, false)))
        assertTrue(computeUndercuts(my, region, STATION, isBuyOrder = true).isEmpty())
    }
}
