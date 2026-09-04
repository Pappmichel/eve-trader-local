package com.pappmichel.evetraderlocal.data.trading

import org.junit.Assert.assertEquals
import org.junit.Test

/** Ported case-for-case from the desktop build's
 * tests/test_station_trading_constants.py. */
class StationTradingConstantsTest {

    @Test
    fun `no skills gives the base slot count`() {
        assertEquals(5, orderSlotsFromSkills(emptyMap()))
    }

    @Test
    fun `each slot skill adds its own per-level bonus`() {
        assertEquals(5 + 4 * 5, orderSlotsFromSkills(mapOf(SKILL_TRADE to 5)))
        assertEquals(5 + 8 * 5, orderSlotsFromSkills(mapOf(SKILL_RETAIL to 5)))
        assertEquals(5 + 16 * 5, orderSlotsFromSkills(mapOf(SKILL_WHOLESALE to 5)))
        assertEquals(5 + 32 * 5, orderSlotsFromSkills(mapOf(SKILL_TYCOON to 5)))
    }

    @Test
    fun `slot skills stack additively`() {
        val levels = mapOf(SKILL_TRADE to 5, SKILL_RETAIL to 5, SKILL_WHOLESALE to 5, SKILL_TYCOON to 5)
        assertEquals(5 + 4 * 5 + 8 * 5 + 16 * 5 + 32 * 5, orderSlotsFromSkills(levels))
    }

    @Test
    fun `fee discount skills never affect order slots`() {
        val levels = mapOf(
            SKILL_ACCOUNTING to 5, SKILL_BROKER_RELATIONS to 5, SKILL_ADVANCED_BROKER_RELATIONS to 5,
        )
        assertEquals(5, orderSlotsFromSkills(levels))
    }

    @Test
    fun `unlisted skills default to zero`() {
        assertEquals(5, orderSlotsFromSkills(mapOf(999999 to 5)))
    }
}
