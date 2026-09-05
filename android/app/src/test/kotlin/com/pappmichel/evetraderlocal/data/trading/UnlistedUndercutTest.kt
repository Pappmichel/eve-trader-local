package com.pappmichel.evetraderlocal.data.trading

import com.pappmichel.evetraderlocal.data.esi.CharacterAsset
import com.pappmichel.evetraderlocal.data.esi.CharacterOrder
import com.pappmichel.evetraderlocal.data.esi.MarketOrder
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** Ports the desktop build's `tests/test_own_orders.py` coverage of
 * `check_undercut`/`fetch_seller_stock_without_order`, including the
 * "_pooled" multi-seller cases (`test_undercut_pooled_excludes_every_own_
 * seller_not_just_the_checked_one`, `test_unlisted_stock_pools_quantity_
 * and_coverage_across_sellers`) - `checkUndercut`/`findUnlistedStock`
 * themselves are already pooled-shaped (flat order/asset lists, not
 * per-character), so a pooled scenario here is exercised by simply passing
 * in the concatenation of what would have been two sellers' own fetches,
 * exactly like `UnlistedUndercutScreen.kt` itself now does. */
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

    // ----------------------------------------------------- pooled: multi-seller
    @Test
    fun `undercut pooled excludes every own seller not just the checked one`() {
        // Issue #46: a cheaper order belonging to another of *my* seller
        // characters must never count as being undercut - order_id is the
        // only thing that identifies it, since the structure book has no
        // owner field. The screen pools by concatenating every seller's own
        // characterOrders() result before calling checkUndercut once, so
        // that pooling is reproduced here by simply passing both sellers'
        // orders together.
        val mineA = sellOrder(10, trit, 100.0)
        val mineB = sellOrder(11, trit, 80.0)
        val pooledMyOrders = listOf(mineA, mineB) // seller 1's + seller 2's own orders
        val book = listOf(mineA, mineB).map { bookOrder(it.orderId, it.typeId, it.price) }
        assertTrue(checkUndercut(pooledMyOrders, book, structure).isEmpty())

        // Checked with only seller 1's own orders (the un-pooled mistake),
        // character 1 *is* beaten by character 2's own order - exactly the
        // false positive pooling exists to prevent.
        assertTrue(checkUndercut(listOf(mineA), book, structure).isNotEmpty())
    }

    @Test
    fun `unlisted stock pools quantity and coverage across sellers`() {
        // The structure hangar is shared: quantities add up across every
        // registered seller, and one seller's sell order covers stock
        // physically held by another (issue #46). The screen builds this by
        // summing ownSellOrderRemaining across every seller's own orders and
        // concatenating every seller's own assets before calling
        // findUnlistedStock once - reproduced directly here.
        val seller1Orders = emptyList<CharacterOrder>()
        val seller2Orders = listOf(sellOrder(10, pyerite, 5.0, volumeRemain = 1.0))
        val pooledOwnSellRemaining = (seller1Orders + seller2Orders)
            .filter { !it.isBuyOrder && it.locationId == structure }
            .groupBy { it.typeId }
            .mapValues { (_, orders) -> orders.sumOf { it.volumeRemain } }

        val seller1Assets = listOf(asset(trit, 100.0), asset(pyerite, 20.0))
        val seller2Assets = listOf(asset(trit, 400.0))
        val pooledAssets = seller1Assets + seller2Assets

        val rows = findUnlistedStock(pooledAssets, pooledOwnSellRemaining, setOf(trit, pyerite), structure)
        assertEquals(listOf(UnlistedStockResult(trit, 500.0, 500.0)), rows)
    }
}
