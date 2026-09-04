package com.pappmichel.evetraderlocal.data.refining

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** Ported case-for-case from the desktop build's
 * `tests/test_refining_reprocessing.py`, both the scrapmetal path and the
 * ore/ice path (`oreIceYield`/`oreIceBaseYield`/`securityYieldModifier`).
 * `applyYield` here takes an already-fetched `portionSize`/`materials` pair
 * directly rather than reading a real SQLite SDE cache the way the desktop
 * test's `_seed_sde`/`db` fixture does - same fixture data, hand-built
 * instead of seeded into a database. */
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

    // ------------------------------------------------------- Ore/ice path

    @Test
    fun `ore ice yield confirmed maximum`() {
        // Tatara, T2-Rig, null-sec, max skills, RX-804 -> confirmed 90.63%
        // ceiling (ReprocessingYield.kt's own module docstring - a real
        // historical-bug-fix figure, not an arbitrary test value).
        val cfg = RefiningConfig(
            structureType = "Tatara (L Refinery)", rigTier = "T2-Rig", securityStatus = -0.5,
            implant = "RX-804", reprocessingSkillLevel = 5, reprocessingEfficiencySkillLevel = 5,
            oreFamilySkillLevels = mapOf("Veldspar" to 5),
        )
        val pct = oreIceYield(cfg, oreFamily = "Veldspar")
        assertEquals(90.63, Math.round(pct * 10000) / 100.0, 1e-9)
    }

    @Test
    fun `ore ice base yield citadel no bonuses highsec`() {
        // Base case: Citadel, no rig, highsec (sec modifier 0), no bonuses at
        // all -> exactly 50%.
        val cfg = RefiningConfig(securityStatus = 1.0)
        assertEquals(0.5, oreIceBaseYield(cfg), 1e-9)
    }

    @Test
    fun `security yield modifier lowsec and nullsec`() {
        val cfgLow = RefiningConfig(securityStatus = 0.4) // rounds to 0.4 -> lowsec bucket
        val cfgNull = RefiningConfig(securityStatus = -0.5) // nullsec/wormhole bucket
        assertTrue(oreIceBaseYield(cfgLow) > oreIceBaseYield(RefiningConfig(securityStatus = 1.0)))
        assertTrue(oreIceBaseYield(cfgNull) > oreIceBaseYield(cfgLow))
    }

    @Test
    fun `unknown ore family missing from map assumes maxed`() {
        // A family simply absent from oreFamilySkillLevels is assumed level 5
        // (maxed), not unskilled - see oreIceYield's own docstring for why.
        val cfgMissing = RefiningConfig() // empty map
        val cfgExplicitMax = RefiningConfig(oreFamilySkillLevels = mapOf("Veldspar" to 5))
        assertEquals(oreIceYield(cfgExplicitMax, "Veldspar"), oreIceYield(cfgMissing, "Veldspar"), 1e-9)
    }

    @Test
    fun `ore ice yield no family gets no family bonus`() {
        val cfg = RefiningConfig(oreFamilySkillLevels = mapOf("Veldspar" to 5))
        val withFamily = oreIceYield(cfg, "Veldspar")
        val withoutFamily = oreIceYield(cfg, null)
        assertTrue(withoutFamily < withFamily)
    }
}
