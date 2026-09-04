package com.pappmichel.evetraderlocal.data.refining

import com.pappmichel.evetraderlocal.data.esi.OrderStats
import com.pappmichel.evetraderlocal.data.trading.TradingConfig
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Ported case-for-case from the desktop build's `tests/
 * test_refining_pricing.py` (`evaluate_ore_item`/`mineral_type_ids_for`) and
 * `tests/test_refining_candidate_discovery.py` (`_family_and_is_ice`). Pure -
 * no real SQLite/Room needed, `portionSize`/`materials` are passed in
 * directly the same way desktop's own tests monkeypatch
 * `storage.get_portion_size`/`get_type_materials`. */
class OreShortlistTest {
    private val TRITANIUM = 35

    private fun candidate(typeId: Int = 34, family: String = "Veldspar", isIce: Boolean = false, volumeM3: Double = 0.01) =
        OreCandidate(typeId = typeId, item = "Compressed Veldspar", family = family, isIce = isIce, volumeM3 = volumeM3)

    private val tradingCfg = TradingConfig(
        jitaBuyBrokerFee = 0.0147, structureSellHaircut = 0.9463, importCostPerM3 = 900.0,
        minProfitThreshold = 0.0, minMarginThreshold = 0.05,
    )
    private val refiningCfg = RefiningConfig(refiningTaxRate = 0.0)

    // ------------------------------------------------- familyAndIsIce

    @Test
    fun `family and is ice ore uses group name`() {
        val (family, isIce) = familyAndIsIce("Compressed Veldspar", "Veldspar")
        assertEquals("Veldspar", family)
        assertEquals(false, isIce)
    }

    @Test
    fun `family and is ice ice uses type name`() {
        val (family, isIce) = familyAndIsIce("Compressed Blue Ice", "Ice")
        assertEquals("Blue Ice", family)
        assertEquals(true, isIce)
    }

    // ------------------------------------------------- evaluateOreItem

    @Test
    fun `evaluate ore item no jita data is no market data`() {
        val row = evaluateOreItem(candidate(), true, 100, emptyList(), null, emptyMap(), tradingCfg, refiningCfg)
        assertEquals(ORE_NO_MARKET_DATA_DECISION, row.decision)
        assertNull(row.landedCost)
        assertNull(row.profitPerUnit)
    }

    @Test
    fun `evaluate ore item no portion size is no market data`() {
        val jita = OrderStats(sellPercentile = 1000.0, sellVolume = 5000.0, buyPercentile = null, buyVolume = 0.0)
        val row = evaluateOreItem(candidate(), true, null, emptyList(), jita, emptyMap(), tradingCfg, refiningCfg)
        assertEquals(ORE_NO_MARKET_DATA_DECISION, row.decision)
    }

    @Test
    fun `evaluate ore item missing mineral price is no market data`() {
        val jita = OrderStats(sellPercentile = 1000.0, sellVolume = 5000.0, buyPercentile = null, buyVolume = 0.0)
        val row = evaluateOreItem(
            candidate(), true, 100, listOf(TRITANIUM to 415.0), jita, emptyMap(), tradingCfg, refiningCfg,
        )
        assertEquals(ORE_NO_MARKET_DATA_DECISION, row.decision)
        assertNull(row.mineralValue)
    }

    @Test
    fun `evaluate ore item profitable is import`() {
        val jita = OrderStats(sellPercentile = 1.0, sellVolume = 5000.0, buyPercentile = null, buyVolume = 0.0)
        val tritanium = OrderStats(sellPercentile = 10.0, sellVolume = 1_000_000.0, buyPercentile = null, buyVolume = 0.0)

        val row = evaluateOreItem(
            candidate(volumeM3 = 0.001), true, 100, listOf(TRITANIUM to 415.0), jita, mapOf(TRITANIUM to tritanium),
            tradingCfg, refiningCfg,
        )

        assertEquals(ORE_IMPORT_DECISION, row.decision)
        assertTrue(row.profitPerUnit != null && row.profitPerUnit!! > 0)
        assertTrue(row.margin != null && row.margin!! > tradingCfg.minMarginThreshold)
    }

    @Test
    fun `evaluate ore item min profit threshold is compared per unit not per portion`() {
        // Same confirmed-real-bug reasoning the desktop build's own test
        // regresses: oreDecision must receive profitPerUnit, not
        // profitPerPortion, or a per-unit threshold is ~portionSize times
        // too lenient.
        val strictCfg = TradingConfig(
            jitaBuyBrokerFee = 0.0147, structureSellHaircut = 0.9463, importCostPerM3 = 900.0,
            minProfitThreshold = 100.0, minMarginThreshold = 0.05,
        )
        val jita = OrderStats(sellPercentile = 1.0, sellVolume = 5000.0, buyPercentile = null, buyVolume = 0.0)
        val tritanium = OrderStats(sellPercentile = 10.0, sellVolume = 1_000_000.0, buyPercentile = null, buyVolume = 0.0)

        val row = evaluateOreItem(
            candidate(volumeM3 = 0.001), true, 100, listOf(TRITANIUM to 415.0), jita, mapOf(TRITANIUM to tritanium),
            strictCfg, refiningCfg,
        )

        assertEquals(22.2160, row.profitPerUnit!!, 0.001)
        assertEquals(ORE_SKIP_DECISION, row.decision)
    }

    @Test
    fun `evaluate ore item unprofitable is skip`() {
        val jita = OrderStats(sellPercentile = 1000.0, sellVolume = 5000.0, buyPercentile = null, buyVolume = 0.0)
        val tritanium = OrderStats(sellPercentile = 10.0, sellVolume = 1_000_000.0, buyPercentile = null, buyVolume = 0.0)

        val row = evaluateOreItem(
            candidate(), true, 100, listOf(TRITANIUM to 415.0), jita, mapOf(TRITANIUM to tritanium),
            tradingCfg, refiningCfg,
        )

        assertEquals(ORE_SKIP_DECISION, row.decision)
    }

    @Test
    fun `evaluate ore item inactive overrides everything`() {
        val jita = OrderStats(sellPercentile = 1.0, sellVolume = 5000.0, buyPercentile = null, buyVolume = 0.0)
        val tritanium = OrderStats(sellPercentile = 10.0, sellVolume = 1_000_000.0, buyPercentile = null, buyVolume = 0.0)

        val row = evaluateOreItem(
            candidate(volumeM3 = 0.001), false, 100, listOf(TRITANIUM to 415.0), jita, mapOf(TRITANIUM to tritanium),
            tradingCfg, refiningCfg,
        )

        assertEquals(ORE_INACTIVE_DECISION, row.decision)
    }

    @Test
    fun `evaluate ore item refining tax reduces net sell`() {
        val jita = OrderStats(sellPercentile = 1.0, sellVolume = 5000.0, buyPercentile = null, buyVolume = 0.0)
        val tritanium = OrderStats(sellPercentile = 10.0, sellVolume = 1_000_000.0, buyPercentile = null, buyVolume = 0.0)

        val noTax = evaluateOreItem(
            candidate(volumeM3 = 0.001), true, 100, listOf(TRITANIUM to 415.0), jita, mapOf(TRITANIUM to tritanium),
            tradingCfg, RefiningConfig(refiningTaxRate = 0.0),
        )
        val withTax = evaluateOreItem(
            candidate(volumeM3 = 0.001), true, 100, listOf(TRITANIUM to 415.0), jita, mapOf(TRITANIUM to tritanium),
            tradingCfg, RefiningConfig(refiningTaxRate = 0.1),
        )

        assertTrue(withTax.netSell!! < noTax.netSell!!)
        assertTrue(withTax.refiningTax!! > 0)
    }

    @Test
    fun `evaluate ore item missing mineral price is never treated as zero`() {
        // An unpriceable mineral must make the whole row "No market data",
        // never silently price it as 0 ISK and understate mineralValue -
        // same "unknown, not free" principle used throughout this port.
        val PYERITE = 36
        val jita = OrderStats(sellPercentile = 1.0, sellVolume = 5000.0, buyPercentile = null, buyVolume = 0.0)
        val tritanium = OrderStats(sellPercentile = 10.0, sellVolume = 1_000_000.0, buyPercentile = null, buyVolume = 0.0)

        val row = evaluateOreItem(
            candidate(volumeM3 = 0.001), true, 100, listOf(TRITANIUM to 415.0, PYERITE to 41.0), jita,
            mapOf(TRITANIUM to tritanium), tradingCfg, refiningCfg,
        )

        assertEquals(ORE_NO_MARKET_DATA_DECISION, row.decision)
        assertNull(row.mineralValue)
        assertNull(row.profitPerUnit)
    }
}
