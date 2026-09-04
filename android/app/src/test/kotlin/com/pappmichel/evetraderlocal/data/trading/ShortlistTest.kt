package com.pappmichel.evetraderlocal.data.trading

import com.pappmichel.evetraderlocal.data.esi.OrderStats
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.abs

/** Ports the desktop build's `tests/test_shortlist.py` coverage of the
 * formula/decision-precedence rules `evaluateShortlistItem`/
 * `evaluateShortlist` share with `shortlist.py` (see that module's own
 * docstring) - JVM unit tests, no Android instrumentation needed, since
 * this business logic makes no network call and opens no database, same
 * as the Python original. Not ported: Profit/Day and Goonmetrics-history
 * tests, since that half of shortlist.py isn't ported to Kotlin yet (see
 * ROADMAP.md's Android section). */
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
}
