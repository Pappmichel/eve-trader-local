package com.pappmichel.evetraderlocal.data.refining

import com.pappmichel.evetraderlocal.data.esi.OrderStats
import com.pappmichel.evetraderlocal.data.trading.TradingConfig
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Ported case-for-case from the desktop build's
 * `tests/test_refining_quote.py`. Desktop monkeypatches `storage.
 * search_sde_types`/`get_portion_size`/`get_type_materials`; this port has
 * no storage layer to monkeypatch since `evaluateReprocessingLine` takes
 * `typeId`/`portionSize`/`materials` as plain arguments instead (see that
 * function's own docstring) - so each test passes the same fixture values
 * directly rather than stubbing a lookup. `resolveTypeId`/
 * `mineralTypeIdsForLines` themselves are thin `SdeRepository` delegations
 * with no logic of their own (same reasoning
 * `StationTradingCandidateDiscoveryTest` gives for skipping `confirmLive`'s
 * desktop cases) and are not re-tested here. */
class ReprocessingQuoteTest {
    private val tradingCfg = TradingConfig(structureSellHaircut = 0.9463)
    private val refiningCfg = RefiningConfig(scrapmetalProcessingSkillLevel = 5, refiningTaxRate = 0.0) // 55% yield

    private fun line(name: String = "Antimatter Charge S", quantity: Int = 1000) =
        ParsedPasteLine(rawLine = "", name = name, quantity = quantity, category = "Charge", volumeM3 = 0.0025)

    private fun stats(sellPercentile: Double?, sellVolume: Double = 1000.0) =
        OrderStats(sellPercentile = sellPercentile, sellVolume = sellVolume, buyPercentile = null, buyVolume = 0.0)

    @Test
    fun `evaluate line with parse error is unresolved`() {
        val errorLine = ParsedPasteLine(rawLine = "bad", name = "bad", quantity = 0, category = "", volumeM3 = null, error = "Not tab-separated")
        val row = evaluateReprocessingLine(errorLine, null, null, emptyList(), null, emptyMap(), tradingCfg, refiningCfg)
        assertEquals(UNRESOLVED_DECISION, row.decision)
        assertEquals("Not tab-separated", row.error)
    }

    @Test
    fun `evaluate line unresolved item name`() {
        val row = evaluateReprocessingLine(line(), null, null, emptyList(), null, emptyMap(), tradingCfg, refiningCfg)
        assertEquals(UNRESOLVED_DECISION, row.decision)
        assertNull(row.typeId)
    }

    @Test
    fun `evaluate line not reprocessable when no portion size`() {
        val itemStats = stats(sellPercentile = 1.0, sellVolume = 1000.0)
        val row = evaluateReprocessingLine(line(), 100, null, emptyList(), itemStats, emptyMap(), tradingCfg, refiningCfg)
        assertEquals(NOT_REPROCESSABLE_DECISION, row.decision)
        // still priced, even though not reprocessable - 1.0 x 1000 x 0.9463 haircut
        assertEquals(946.3, row.sellAsIsValue!!, 1e-6)
    }

    @Test
    fun `evaluate line no market data when item unpriced`() {
        val row = evaluateReprocessingLine(line(), 100, 1, listOf(35 to 0.1), null, emptyMap(), tradingCfg, refiningCfg)
        assertEquals(NO_MARKET_DATA_DECISION, row.decision)
    }

    @Test
    fun `evaluate line missing mineral price is never treated as zero`() {
        // An input mineral with no stats entry at all must make the whole row
        // 'No market data', never silently 0 ISK - same "unknown, not free"
        // principle used throughout Trading/Production.
        val itemStats = stats(sellPercentile = 0.01, sellVolume = 1000.0)
        val tritanium = stats(sellPercentile = 1000.0, sellVolume = 1_000_000.0)
        // Pyerite (36) has no entry in mineralStatsById at all.
        val row = evaluateReprocessingLine(
            line(quantity = 1000), 100, 1, listOf(35 to 0.01, 36 to 0.02), itemStats, mapOf(35 to tritanium), tradingCfg, refiningCfg,
        )
        assertEquals(NO_MARKET_DATA_DECISION, row.decision)
        assertNull(row.mineralValue)
        assertNull(row.refinedValue)
    }

    @Test
    fun `evaluate line recommends reprocess when refined value higher`() {
        val itemStats = stats(sellPercentile = 0.01, sellVolume = 1000.0)
        val tritanium = stats(sellPercentile = 1000.0, sellVolume = 1_000_000.0)

        val row = evaluateReprocessingLine(
            line(quantity = 1000), 100, 1, listOf(35 to 0.01), itemStats, mapOf(35 to tritanium), tradingCfg, refiningCfg,
        )

        // sell_as_is = 1000 x 0.01 x 0.9463 = 9.463; minerals = floor(1000 x 0.01 x 0.55) = 5;
        // mineral_value = 5 x 1000 x 0.9463 = 4731.5 >> sell_as_is
        assertEquals(REPROCESS_DECISION, row.decision)
        assertTrue(row.refinedValue!! > row.sellAsIsValue!!)
    }

    @Test
    fun `evaluate line recommends sell instead when sell as is higher`() {
        val itemStats = stats(sellPercentile = 1000.0, sellVolume = 1000.0)
        val tritanium = stats(sellPercentile = 1.0, sellVolume = 1_000_000.0)

        val row = evaluateReprocessingLine(
            line(quantity = 1), 100, 1, listOf(35 to 0.01), itemStats, mapOf(35 to tritanium), tradingCfg, refiningCfg,
        )

        assertEquals(SELL_DECISION, row.decision)
        assertTrue(row.sellAsIsValue!! > row.refinedValue!!)
    }

    @Test
    fun `evaluate line refining tax reduces refined value`() {
        val itemStats = stats(sellPercentile = 0.01, sellVolume = 1000.0)
        val tritanium = stats(sellPercentile = 1000.0, sellVolume = 1_000_000.0)

        val noTax = evaluateReprocessingLine(
            line(quantity = 1000), 100, 1, listOf(35 to 0.01), itemStats, mapOf(35 to tritanium), tradingCfg,
            RefiningConfig(scrapmetalProcessingSkillLevel = 5, refiningTaxRate = 0.0),
        )
        val withTax = evaluateReprocessingLine(
            line(quantity = 1000), 100, 1, listOf(35 to 0.01), itemStats, mapOf(35 to tritanium), tradingCfg,
            RefiningConfig(scrapmetalProcessingSkillLevel = 5, refiningTaxRate = 0.1),
        )

        assertTrue(withTax.refinedValue!! < noTax.refinedValue!!)
        assertTrue(withTax.refiningTax!! > 0)
    }

    @Test
    fun `evaluate line sell as is value includes structure sell haircut`() {
        // Regression test for a confirmed real bug (desktop build,
        // business-logic audit 2026-08-29): sell_as_is_value used to be a
        // bare gross quantity x price, while refined_value already netted
        // out structure_sell_haircut on the mineral side - both options end
        // with a C-J sell order, so both must incur the same fee, or the
        // comparison is apples-to-oranges.
        val itemStats = stats(sellPercentile = 1.0, sellVolume = 1000.0)
        val row = evaluateReprocessingLine(line(quantity = 1000), 100, null, emptyList(), itemStats, emptyMap(), tradingCfg, refiningCfg)
        assertEquals(1000 * 1.0 * tradingCfg.structureSellHaircut, row.sellAsIsValue!!, 1e-9)
    }

    @Test
    fun `evaluate line reprocess decision flips once fee consistency is fixed`() {
        // Concrete scenario: refined_value sits strictly between the net
        // (haircut-adjusted) and gross sell-as-is values - a buggy formula
        // (bare gross sell_as_is, no haircut) would have wrongly said "Sell
        // instead" here; the fixed formula must say "Reprocess".
        val cfg = RefiningConfig(scrapmetalProcessingSkillLevel = 5, refiningTaxRate = 0.0) // 55% yield
        val itemStats = stats(sellPercentile = 1.0, sellVolume = 1000.0)
        val tritanium = stats(sellPercentile = 10.5, sellVolume = 1_000_000.0)

        val row = evaluateReprocessingLine(
            line(quantity = 100), 100, 1, listOf(35 to 0.19), itemStats, mapOf(35 to tritanium), tradingCfg, cfg,
        )

        val grossSellAsIs = 100 * 1.0 // what a buggy no-haircut formula would have compared against
        val netSellAsIs = grossSellAsIs * tradingCfg.structureSellHaircut // 94.63
        // minerals = floor(100 x 0.19 x 0.55) = 10; mineral_value = 10 x 10.5 x 0.9463 = 99.3615
        assertTrue(netSellAsIs < row.refinedValue!! && row.refinedValue!! < grossSellAsIs)
        assertEquals(netSellAsIs, row.sellAsIsValue!!, 1e-9)
        assertEquals(REPROCESS_DECISION, row.decision) // would have been SELL_DECISION before the fix
    }
}
