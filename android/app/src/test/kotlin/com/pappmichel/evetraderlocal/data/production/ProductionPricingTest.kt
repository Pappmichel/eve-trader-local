package com.pappmichel.evetraderlocal.data.production

import com.pappmichel.evetraderlocal.data.esi.OrderStats
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** Ported case-for-case from the desktop build's tests/test_production_
 * pricing.py - just its pure buy_price/buy_source cases (the
 * "-------- buy price / source" section of that file). The sourcing tests
 * in that file (home_prices/jita_prices's ESI-fetch-then-fallback
 * behavior, including the Goonmetrics fallback) aren't ported: this
 * Android port has no Goonmetrics fallback at all (a documented
 * simplification, see ProductionPricing.kt's own docstring) and its IO
 * helpers (homePrices/jitaPrices) are thin enough - one try/catch loop
 * over EsiClient calls already covered by EsiClient's own tests - that a
 * hand-built fake EsiClient/TokenManager pair would mostly be re-testing
 * EsiClient itself, the same reasoning StationTradingConfigRepository's
 * own test coverage skips its IO half. */
class ProductionPricingTest {
    private val TRITANIUM = 34

    private fun stats(sell: Double): OrderStats =
        OrderStats(sellPercentile = sell, sellVolume = 1.0, buyPercentile = null, buyVolume = 0.0)

    private fun cfg() = ProductionConfig(jitaBuyBrokerFee = 0.10, haulCostPerM3 = 100.0)

    // ----------------------------------------------------------- buy price / source
    @Test
    fun `home wins when cheaper after haul`() {
        val home = mapOf(TRITANIUM to stats(100.0))
        val jita = mapOf(TRITANIUM to stats(95.0))
        // home: 100 * 1.10 = 110. jita: 95 * 1.10 + 100 * 1.0 m3 = 204.5.
        assertEquals(110.0, buyPrice(TRITANIUM, home, jita, 1.0, cfg())!!, 1e-9)
        assertEquals("home", buySource(TRITANIUM, home, jita, 1.0, cfg()))
    }

    @Test
    fun `jita wins when home is expensive`() {
        val home = mapOf(TRITANIUM to stats(500.0))
        val jita = mapOf(TRITANIUM to stats(100.0))
        // home: 550. jita: 100 * 1.10 + 100 * 0.01 = 111.
        assertEquals(111.0, buyPrice(TRITANIUM, home, jita, 0.01, cfg())!!, 1e-9)
        assertEquals("jita", buySource(TRITANIUM, home, jita, 0.01, cfg()))
    }

    @Test
    fun `haul cost scales with volume`() {
        val jita = mapOf(TRITANIUM to stats(100.0))
        val cheap = buyPrice(TRITANIUM, emptyMap(), jita, 1.0, cfg())!!
        val bulky = buyPrice(TRITANIUM, emptyMap(), jita, 10.0, cfg())!!
        assertEquals(cfg().haulCostPerM3 * 9, bulky - cheap, 1e-9)
    }

    @Test
    fun `missing volume charges no haul`() {
        val jita = mapOf(TRITANIUM to stats(100.0))
        assertEquals(110.0, buyPrice(TRITANIUM, emptyMap(), jita, null, cfg())!!, 1e-9)
    }

    @Test
    fun `home price is never hauled`() {
        val home = mapOf(TRITANIUM to stats(100.0))
        assertEquals(110.0, buyPrice(TRITANIUM, home, emptyMap(), 1000.0, cfg())!!, 1e-9)
    }

    @Test
    fun `only listed sources are considered`() {
        // A zero sell price means "nothing listed", not "free".
        val home = mapOf(TRITANIUM to stats(0.0))
        val jita = mapOf(TRITANIUM to stats(100.0))
        assertEquals("jita", buySource(TRITANIUM, home, jita, 0.0, cfg()))
    }

    @Test
    fun `no source anywhere is none`() {
        assertNull(buyPrice(TRITANIUM, emptyMap(), emptyMap(), 1.0, cfg()))
        assertNull(buySource(TRITANIUM, emptyMap(), emptyMap(), 1.0, cfg()))
        assertNull(buyPrice(TRITANIUM, mapOf(TRITANIUM to stats(0.0)), emptyMap(), 1.0, cfg()))
    }

    @Test
    fun `source and price always agree`() {
        val home = mapOf(TRITANIUM to stats(100.0))
        val jita = mapOf(TRITANIUM to stats(90.0))
        val chosen = buySource(TRITANIUM, home, jita, 0.05, cfg())
        val expected = if (chosen == "home") 110.0 else 90 * 1.10 + 100 * 0.05
        assertEquals(expected, buyPrice(TRITANIUM, home, jita, 0.05, cfg())!!, 1e-9)
    }

    // --------------------------------------------------------------- unlisted
    @Test
    fun `unlisted types have no quote rather than a zero one`() {
        // OrderStats reports a missing item as sellPercentile == null (never
        // fetched a real percentile) - this must read as "no sell order",
        // never as a free item. A summarized-but-empty book (sellPercentile
        // 0.0) must behave the same way.
        val jita = mapOf(TRITANIUM to stats(0.0))
        assertNull(buyPrice(TRITANIUM, emptyMap(), jita, 1.0, ProductionConfig()))
    }
}
