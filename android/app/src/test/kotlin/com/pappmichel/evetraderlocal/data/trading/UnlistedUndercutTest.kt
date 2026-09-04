package com.pappmichel.evetraderlocal.data.trading

import com.pappmichel.evetraderlocal.data.esi.CharacterAsset
import com.pappmichel.evetraderlocal.data.esi.CharacterOrder
import com.pappmichel.evetraderlocal.data.esi.MarketOrder
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** Ports the desktop build's `tests/test_own_orders.py` coverage of
 * `check_undercut`/`fetch_seller_stock_without_order` (single-seller only
 * - see `UnlistedUndercut.kt`'s own docstring for why the "_pooled"
 * multi-seller variants aren't ported). */
class UnlistedUndercutTest {
    private val structure = 1234567890123L
    private val otherStructure = 9999999999999L
    private val trit = 34
    private val pyerite = 35
    private val mexallon = 36

    private fun sellOrder(orderId: Long, typeId: Int, price: Double, volumeRemain: Double = 10.0, locationId: Long = structure) =
        CharacterOrder(orderId = orderId, typeId = typeId, price = price, isBuyOrder = false, volumeRemain = volumeRemain, locationId = locationId)

    private fun bookOrder(orderId: Long, typeId: Int, price: Double, isBuyOrder: Boolean = false) =
        MarketOrder(orderId = orderId, typeId = typeId, price = price, isBuyOrder = isBuyOrder, volumeRemain = 10.0)

    private fun asset(typeId: Int, quantity: Double, locationId: Long = structure, locationFlag: String = "Hangar") =
        CharacterAsset(typeId = typeId, locationId = locationId, quantity = quantity, locationFlag = locationFlag)

    @Test
    fun `undercut flags only beaten orders and sorts by difference`() {
        val myOrders = listOf(sellOrder(10, trit, 100.0), sellOrder(11, pyerite, 50.0), sellOrder(12, mexallon, 20.0))
        val book = listOf(
            bookOrder(10, trit, 100.0), bookOrder(11, pyerite, 50.0), bookOrder(12, mexallon, 20.0),
            bookOrder(90, trit, 90.0),     // undercuts by 10
            bookOrder(91, pyerite, 50.0),  // exactly equal - not undercut
            bookOrder(92, mexallon, 5.0),  // undercuts by 15
            bookOrder(93, 99999, 1.0),     // item I don't list at all
        )
        val rows = checkUndercut(myOrders, book, structure)
        assertEquals(listOf(mexallon, trit), rows.map { it.typeId })
        assertEquals(UndercutResult(trit, 100.0, 90.0, 10.0), rows[1])
    }

    @Test
    fun `undercut ignores orders outside the structure`() {
        val myOrders = listOf(sellOrder(10, trit, 100.0, locationId = otherStructure))
        val book = listOf(bookOrder(90, trit, 1.0))
        assertTrue(checkUndercut(myOrders, book, structure).isEmpty())
    }

    @Test
    fun `undercut ignores buy orders in the book`() {
        val myOrders = listOf(sellOrder(10, trit, 100.0))
        val book = listOf(bookOrder(10, trit, 100.0), bookOrder(90, trit, 1.0, isBuyOrder = true))
        assertTrue(checkUndercut(myOrders, book, structure).isEmpty())
    }

    @Test
    fun `undercut excludes my own order id from the competitor pool`() {
        // The false positive check_undercut_pooled's own-order-id exclusion
        // exists to prevent - here reproduced by a "competing" order that
        // is actually mine (same order_id, so it must not count).
        val myOrders = listOf(sellOrder(10, trit, 100.0), sellOrder(11, trit, 80.0))
        val book = listOf(bookOrder(10, trit, 100.0), bookOrder(11, trit, 80.0))
        assertTrue(checkUndercut(myOrders, book, structure).isEmpty())
    }

    @Test
    fun `undercut uses my cheapest order as the reference price`() {
        val myOrders = listOf(sellOrder(10, trit, 100.0), sellOrder(11, trit, 70.0))
        val book = listOf(bookOrder(10, trit, 100.0), bookOrder(11, trit, 70.0), bookOrder(90, trit, 80.0))
        assertTrue(checkUndercut(myOrders, book, structure).isEmpty())
    }

    @Test
    fun `unlisted stock flags only fully uncovered shortlist items`() {
        val ownSellRemaining = mapOf(pyerite to 1.0) // partially listed
        val assets = listOf(
            asset(trit, 500.0),      // no order at all -> flagged
            asset(pyerite, 500.0),   // partially listed -> not flagged
            asset(mexallon, 300.0),  // not on the shortlist -> ignored
            asset(99999, 5.0),
        )
        val rows = findUnlistedStock(assets, ownSellRemaining, setOf(trit, pyerite), structure)
        assertEquals(listOf(UnlistedStockResult(trit, 500.0, 500.0)), rows)
    }

    @Test
    fun `unlisted stock ignores assets elsewhere and non-stock flags`() {
        val assets = listOf(
            asset(trit, 100.0, locationId = otherStructure),
            asset(pyerite, 100.0, locationFlag = "AssetSafety"),
            asset(mexallon, 100.0, locationFlag = "Deliveries"),
        )
        val rows = findUnlistedStock(assets, emptyMap(), setOf(trit, pyerite, mexallon), structure)
        assertTrue(rows.isEmpty())
    }
}
