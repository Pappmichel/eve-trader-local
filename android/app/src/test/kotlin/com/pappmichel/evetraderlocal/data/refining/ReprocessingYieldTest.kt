package com.pappmichel.evetraderlocal.data.refining

import org.junit.Assert.assertEquals
import org.junit.Test

/** Ported case-for-case from the desktop build's
 * `tests/test_refining_reprocessing.py` - scrapmetal-path cases only (see
 * `ReprocessingYield.kt`'s own docstring for why the ore/ice path isn't
 * ported). `applyYield` here takes an already-fetched `portionSize`/
 * `materials` pair directly rather than reading a real SQLite SDE cache the
 * way the desktop test's `_seed_sde`/`db` fixture does - same fixture data,
 * hand-built instead of seeded into a database. */
class ReprocessingYieldTest {
    private val TRITANIUM = 34
    private val PYERITE = 35
    private val MEGACYTE = 16
    private val DAMAGED_ARMOR_PLATE_MATERIALS = listOf(TRITANIUM to 100.0, PYERITE to 50.0, MEGACYTE to 3.0)
    private val VELDSPAR_MATERIALS = listOf(TRITANIUM to 415.0)

    @Test
    fun `scrapmetal yield confirmed maximum`() {
        // 50% base + 5 x 1%%/level -> confirmed 55% ceiling. Also regression-
        // tests the desktop build's confirmed-wrong "2%%/level" figure some
        // guides use (would wrongly give 60%%).
        val cfg = RefiningConfig(scrapmetalProcessingSkillLevel = 5)
        assertEquals(55.0, scrapmetalYield(cfg) * 100, 1e-9)
    }

    @Test
    fun `scrapmetal yield clamps an out-of-range skill level`() {
        val tooHigh = RefiningConfig(scrapmetalProcessingSkillLevel = 15)
        val maxed = RefiningConfig(scrapmetalProcessingSkillLevel = 5)
        assertEquals(scrapmetalYield(maxed), scrapmetalYield(tooHigh), 1e-9)
    }

    @Test
    fun `apply reprocessing yield portion batching and per material floor`() {
        // 250 Veldspar at portion_size=100 -> 2 complete portions (the 50
        // leftover units below one portion yield nothing) ->
        // floor(2 * 415 * 0.5) = 415 Tritanium.
        val result = applyYield(100, VELDSPAR_MATERIALS, 250, 0.5)
        assertEquals(mapOf(TRITANIUM to 415), result)
    }

    @Test
    fun `apply reprocessing yield below one portion yields nothing`() {
        assertEquals(emptyMap<Int, Int>(), applyYield(100, VELDSPAR_MATERIALS, 99, 0.9063))
    }

    @Test
    fun `apply reprocessing yield per material independent floor`() {
        // portion_size 1 (Damaged Armor Plate) -> 7 units = 7 portions.
        // Tritanium: floor(7 * 100 * 0.55) = 385
        // Pyerite:   floor(7 * 50  * 0.55) = 192 (192.5 truncated, not rounded)
        // Megacyte:  floor(7 * 3   * 0.55) = 11  (11.55 truncated)
        val result = applyYield(1, DAMAGED_ARMOR_PLATE_MATERIALS, 7, 0.55)
        assertEquals(mapOf(TRITANIUM to 385, PYERITE to 192, MEGACYTE to 11), result)
    }

    @Test
    fun `apply reprocessing yield unknown type returns empty`() {
        // No SDE portion_size/material rows at all (not yet SDE-refreshed, or
        // genuinely not reprocessable - a ship/skillbook/BPO/BPC) -> {}.
        assertEquals(emptyMap<Int, Int>(), applyYield(null, emptyList(), 100, 0.5))
    }
}
