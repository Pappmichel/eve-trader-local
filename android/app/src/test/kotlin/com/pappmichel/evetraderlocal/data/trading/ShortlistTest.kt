package com.pappmichel.evetraderlocal.data.trading

import com.pappmichel.evetraderlocal.data.esi.OrderStats
import com.pappmichel.evetraderlocal.data.history.HistoryPoint
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.abs

/** Ports the desktop build's `tests/test_shortlist.py` coverage of the
 * formula/decision-precedence rules `evaluateShortlistItem`/
 * `evaluateShortlist` share with `shortlist.py` (see that module's own
 * docstring), plus the Profit/Day (`averageMarketDailyVolume`,
 * `topImportsByDailyProfit`) cases from the same file (issues #51, #100) -
 * JVM unit tests, no Android instrumentation needed, since this business
 * logic makes no network call and opens no database, same as the Python
 * original. */
class ShortlistTest {
    // Round numbers so every expected figure can be worked out by hand:
    // no broker fee, no haircut, no import cost by default - landed cost
    // is exactly the Jita ask, net sell exactly the structure ask.
    private val cfg = TradingConfig(
        jitaBuyBrokerFee = 0.0, structureSellHaircut = 1.0, importCostPerM3 = 0.0,
        minProfitThreshold = 0.0, minMarginThreshold = 0.05,
    )

    private fun stats(sellPercentile: Double?, sellVolume: Double = 100.0) =
        OrderStats(sellPercentile = sellPercentile, sellVolume = sellVolume, buyPercentile = null, buyVolume = 0.0)

    private fun item(itemId: Int = 34, volumeM3: Double = 1.0, active: Boolean = true) =
        ShortlistItem(item = "Tritanium", itemId = itemId, category = "Material", volumeM3 = volumeM3, active = active)

    private fun approx(actual: Double?, expected: Double, epsilon: Double = 1e-9) {
        assertNotEquals(null, actual)
        assertTrue("expected $expected, was $actual", abs(actual!! - expected) < epsilon)
    }

    @Test
    fun `landed cost, net sell and margin follow the formula`() {
        val c = cfg.copy(jitaBuyBrokerFee = 0.01, importCostPerM3 = 10.0, structureSellHaircut = 0.95)
        val row = evaluateShortlistItem(item(volumeM3 = 2.0), 0.0, stats(100.0), stats(200.0), c)
        approx(row.landedCost, 121.0)   // 100 x 1.01 + (2 m3 x 10 ISK)
        approx(row.netSell, 190.0)      // 200 x 0.95
        approx(row.profitPerUnit, 69.0)
        approx(row.margin, 69.0 / 121.0)
        approx(row.profitPerM3, 34.5)
    }

    @Test
    fun `missing item id is unpriceable`() {
        val row = evaluateShortlistItem(item(itemId = 0), 0.0, stats(100.0), stats(200.0), cfg)
        assertEquals("Missing ID", row.decision)
        assertNull(row.landedCost)
        assertNull(row.netSell)
        assertNull(row.margin)
    }

    @Test
    fun `inactive item is still fully priced`() {
        // Only the decision short-circuits - the numbers stay visible, so a
        // deactivated item's economics can be seen to have recovered.
        val row = evaluateShortlistItem(item(active = false), 0.0, stats(100.0), stats(200.0), cfg)
        assertEquals("Inactive", row.decision)
        approx(row.margin, 1.0)
    }

    @Test
    fun `decision precedence - import`() {
        val row = evaluateShortlistItem(item(), 0.0, stats(100.0), stats(200.0), cfg, buyerAlreadyCovered = false)
        assertEquals("Import", row.decision)
    }

    @Test
    fun `decision precedence - already ordered via own orders`() {
        val row = evaluateShortlistItem(item(), 5.0, stats(100.0), stats(200.0), cfg, buyerAlreadyCovered = false)
        assertEquals("Already ordered", row.decision)
    }

    @Test
    fun `decision precedence - already ordered via buyer coverage`() {
        val row = evaluateShortlistItem(item(), 0.0, stats(100.0), stats(200.0), cfg, buyerAlreadyCovered = true)
        assertEquals("Already ordered", row.decision)
    }

    @Test
    fun `decision precedence - skip when priced but under the margin bar`() {
        val row = evaluateShortlistItem(item(), 0.0, stats(100.0), stats(101.0), cfg, buyerAlreadyCovered = false)
        assertEquals("Skip", row.decision)
    }

    @Test
    fun `decision precedence - no market data when never priced`() {
        val row = evaluateShortlistItem(item(), 0.0, stats(100.0), null, cfg, buyerAlreadyCovered = false)
        assertEquals(NO_MARKET_DATA_DECISION, row.decision)
    }

    @Test
    fun `no market data is distinct from skip`() {
        assertNotEquals(NO_MARKET_DATA_DECISION, SKIP_DECISION)
    }

    @Test
    fun `nothing listed is not an import`() {
        // sellVolume is order-book depth, and its only legitimate use is
        // this gate: with nothing listed at the structure there is no ask
        // to price against.
        val row = evaluateShortlistItem(item(), 0.0, stats(100.0), stats(200.0, sellVolume = 0.0), cfg)
        assertEquals("Skip", row.decision)
    }

    @Test
    fun `evaluateShortlist maps each item to its own inputs`() {
        val items = listOf(item(itemId = 34), item(itemId = 35))
        val rows = evaluateShortlist(
            items = items,
            jitaStatsByItem = mapOf(34 to stats(100.0), 35 to stats(100.0)),
            structureStatsByItem = mapOf(34 to stats(200.0), 35 to stats(200.0)),
            cfg = cfg,
            ownOrdersByItem = mapOf(35 to 7.0),
        )
        assertEquals(listOf("Import", "Already ordered"), rows.map { it.decision })
    }

    @Test
    fun `evaluateShortlist honors buyer-covered ids independently per item`() {
        val items = listOf(item(itemId = 34), item(itemId = 35))
        val rows = evaluateShortlist(
            items = items,
            jitaStatsByItem = mapOf(34 to stats(100.0), 35 to stats(100.0)),
            structureStatsByItem = mapOf(34 to stats(200.0), 35 to stats(200.0)),
            cfg = cfg,
            buyerAlreadyCoveredIds = setOf(35),
        )
        assertEquals(listOf("Import", "Already ordered"), rows.map { it.decision })
    }

    @Test
    fun `summaryCounts averages only positive margins`() {
        val items = listOf(item(itemId = 34), item(itemId = 35))
        val rows = evaluateShortlist(
            items = items,
            jitaStatsByItem = mapOf(34 to stats(100.0), 35 to stats(100.0)),
            structureStatsByItem = mapOf(34 to stats(200.0), 35 to stats(50.0)),
            cfg = cfg,
        )
        val summary = summaryCounts(rows)
        assertEquals(1, summary.importCandidates)
        assertEquals(1, summary.skipped)
        assertEquals(1, summary.positiveMargin)
    }

    // ---------------------------------- averageMarketDailyVolume (issue #100)
    private val referenceRegionId = 10000009

    private fun history(typeId: Int, movements: List<Double>, regionId: Int = referenceRegionId) =
        movements.mapIndexed { d, m ->
            HistoryPoint(
                regionId = regionId, typeId = typeId, date = "2026-08-%02d".format(d + 1),
                minPrice = 1.0, maxPrice = 2.0, avgPrice = 1.5, movement = m, numOrders = 3,
            )
        }

    @Test
    fun `averageMarketDailyVolume averages movement per type`() {
        val points = history(34, listOf(10.0, 20.0, 30.0)) + history(35, listOf(5.0))
        assertEquals(mapOf(34 to 20.0, 35 to 5.0), averageMarketDailyVolume(points))
    }

    @Test
    fun `averageMarketDailyVolume omits types without history`() {
        // A type Goonmetrics returned nothing for is simply absent - it must
        // never be estimated from order-book depth or anything else.
        val volumes = averageMarketDailyVolume(history(34, listOf(10.0)))
        assertFalse(35 in volumes)
        assertTrue(averageMarketDailyVolume(emptyList()).isEmpty())
    }

    @Test
    fun `row without history has no avg daily volume`() {
        // Even with a deep order book (sellVolume=999), no Goonmetrics
        // history means avgDailyVolume stays null rather than falling back
        // to it.
        val row = evaluateShortlistItem(
            item(), 0.0, stats(100.0), stats(200.0, sellVolume = 999.0), cfg, avgDailyVolume = null,
        )
        assertEquals(999.0, row.sellVolume!!, 1e-9)
        assertNull(row.avgDailyVolume)
    }

    // ---------------------------------------------- Profit / Day (#51, #100)
    private fun row(itemId: Int = 34, profit: Double = 10.0, avgDailyVolume: Double? = null, sellVolume: Double? = null) =
        ShortlistRow(
            item = "Item $itemId", category = "Material", landedCost = 20.0, netSell = 30.0,
            sellVolume = sellVolume, ownOrdersRemaining = 0.0, profitPerUnit = profit, margin = 0.5,
            profitPerM3 = profit, decision = "Import", active = true, avgDailyVolume = avgDailyVolume,
        )

    @Test
    fun `profit per day uses region history volume not order book depth`() {
        // The exact shape of the parent's GitHub issue #51: one item with a
        // huge parked sell order (order-book depth 100000) but almost no
        // real trading, another with a modest book but genuine turnover.
        // Ranking by listed quantity would put the parked one first.
        val parked = row(itemId = 34, profit = 10.0, sellVolume = 100_000.0, avgDailyVolume = 1.0)
        val liquid = row(itemId = 35, profit = 10.0, sellVolume = 5.0, avgDailyVolume = 500.0)
        val top = topImportsByDailyProfit(listOf(parked, liquid))
        assertEquals(listOf("Item 35", "Item 34"), top.map { it.item })
        assertEquals(5000.0, top[0].maxProfitPerDay, 1e-9)
        assertEquals(10.0, top[1].maxProfitPerDay, 1e-9)
    }

    @Test
    fun `profit per day excludes items without history`() {
        // Issue #100's other half: a candidate with no history is dropped
        // from the ranking, not backfilled from sellVolume.
        val top = topImportsByDailyProfit(
            listOf(
                row(itemId = 34, avgDailyVolume = null, sellVolume = 100_000.0),
                row(itemId = 35, avgDailyVolume = 2.0),
            ),
        )
        assertEquals(listOf("Item 35"), top.map { it.item })
    }

    @Test
    fun `top imports honours topN`() {
        val rows = (1..5).map { row(itemId = it, profit = it.toDouble(), avgDailyVolume = 1.0) }
        assertEquals(2, topImportsByDailyProfit(rows, topN = 2).size)
    }

    @Test
    fun `profit per day survives a full evaluate pass`() {
        // End to end from raw history points through evaluateShortlist into
        // the ranking, so the volume figure can't quietly become something
        // else in between.
        val volumes = averageMarketDailyVolume(history(34, listOf(40.0, 60.0)))
        val rows = evaluateShortlist(
            items = listOf(item(itemId = 34)),
            jitaStatsByItem = mapOf(34 to stats(100.0)),
            structureStatsByItem = mapOf(34 to stats(200.0, sellVolume = 9999.0)),
            cfg = cfg,
            avgDailyVolumeByItem = volumes,
        )
        val top = topImportsByDailyProfit(rows)
        assertEquals(50.0, top[0].avgDailyVolume, 1e-9)
        assertEquals(100.0 * 50.0, top[0].maxProfitPerDay, 1e-9)
    }
}
