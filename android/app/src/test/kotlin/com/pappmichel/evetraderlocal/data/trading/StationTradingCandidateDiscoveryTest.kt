package com.pappmichel.evetraderlocal.data.trading

import com.pappmichel.evetraderlocal.data.history.CurrentPrice
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** Ported case-for-case from the desktop build's
 * tests/test_station_trading_candidate_discovery.py's `discover_candidates`
 * cases - exercised here directly against the pure `rankCandidates`
 * function (this port splits the desktop function's Goonmetrics-fetching
 * and ranking halves apart, the same pure/IO split the rest of
 * `data/trading/` already uses - see StationTradingCandidateDiscovery.kt's
 * own docstring). `confirmLive`'s desktop test cases are not ported: they
 * only exercise a thin one-line delegation to `EsiClient.
 * regionOrderStatsBulk`, which is not pure/network-free logic. */
class StationTradingCandidateDiscoveryTest {
    private val TRITANIUM = 34
    private val PYERITE = 35
    private val MEGACYTE = 40

    private fun cfg(
        minSpreadThreshold: Double = 0.08,
        minDailyVolume: Double = 1000.0,
        enforceShortlistCap: Boolean = false,
        maxActiveShortlistItems: Int = 300,
    ) = StationTradingConfig(
        minSpreadThreshold = minSpreadThreshold, minDailyVolume = minDailyVolume,
        enforceShortlistCap = enforceShortlistCap, maxActiveShortlistItems = maxActiveShortlistItems,
    )

    private fun price(typeId: Int, buy: Double, sell: Double) = CurrentPrice(typeId, "2026-09-01", buy, sell)

    @Test
    fun `item below spread threshold is excluded`() {
        assertEquals(emptyList<StationTradingCandidate>(), rankCandidates(listOf(price(TRITANIUM, 4.9, 5.0)), emptyMap(), cfg()))
    }

    @Test
    fun `item clearing spread but below volume is excluded`() {
        val result = rankCandidates(
            listOf(price(TRITANIUM, 4.0, 5.0)), // 20% spread
            mapOf(TRITANIUM to listOf(10.0, 20.0)), // avg 15, well under 1000
            cfg(),
        )
        assertEquals(emptyList<StationTradingCandidate>(), result)
    }

    @Test
    fun `item clearing both gates is returned`() {
        val result = rankCandidates(
            listOf(price(TRITANIUM, 4.0, 5.0)),
            mapOf(TRITANIUM to listOf(2000.0, 4000.0)), // avg 3000
            cfg(),
        )
        assertEquals(1, result.size)
        val row = result[0]
        assertEquals(TRITANIUM, row.typeId)
        assertEquals(4.0, row.buy, 1e-9)
        assertEquals(5.0, row.sell, 1e-9)
        assertEquals(0.2, row.spreadPct, 1e-9)
        assertEquals(3000.0, row.avgDailyVolume, 1e-9)
    }

    @Test
    fun `no history at all means zero volume and is excluded`() {
        val result = rankCandidates(listOf(price(TRITANIUM, 4.0, 5.0)), emptyMap(), cfg(minDailyVolume = 0.0))
        assertEquals(0.0, result[0].avgDailyVolume, 1e-9)
    }

    @Test
    fun `synthetic or degenerate quotes are skipped`() {
        val prices = listOf(
            price(TRITANIUM, 0.0, 5.0), // buy <= 0
            price(PYERITE, 5.0, 0.0),   // sell <= 0
            price(MEGACYTE, 5.0, 4.0),  // buy >= sell, inverted quote
        )
        assertEquals(emptyList<StationTradingCandidate>(), rankCandidates(prices, emptyMap(), cfg()))
    }

    @Test
    fun `results are sorted by spread times volume richest first`() {
        val prices = listOf(price(TRITANIUM, 4.0, 5.0), price(PYERITE, 8.0, 10.0)) // both 20% spread
        val movement = mapOf(TRITANIUM to listOf(1000.0), PYERITE to listOf(10000.0))
        val result = rankCandidates(prices, movement, cfg())
        assertEquals(listOf(PYERITE, TRITANIUM), result.map { it.typeId })
    }

    @Test
    fun `topN caps when explicitly passed`() {
        val prices = listOf(price(TRITANIUM, 4.0, 5.0), price(PYERITE, 4.0, 5.0))
        val movement = mapOf(TRITANIUM to listOf(5000.0), PYERITE to listOf(5000.0))
        assertEquals(1, rankCandidates(prices, movement, cfg(), topN = 1).size)
    }

    @Test
    fun `no cap by default even with a large candidate set`() {
        val prices = (1000..1049).map { price(it, 4.0, 5.0) }
        val movement = (1000..1049).associateWith { listOf(5000.0) }
        assertEquals(50, rankCandidates(prices, movement, cfg()).size)
    }

    @Test
    fun `enforce shortlist cap applies max active shortlist items`() {
        val prices = (1000..1049).map { price(it, 4.0, 5.0) }
        val movement = (1000..1049).associateWith { listOf(5000.0) }
        val result = rankCandidates(prices, movement, cfg(enforceShortlistCap = true, maxActiveShortlistItems = 5))
        assertEquals(5, result.size)
    }

    @Test
    fun `empty market returns empty`() {
        assertTrue(rankCandidates(emptyList(), emptyMap(), cfg()).isEmpty())
    }
}
