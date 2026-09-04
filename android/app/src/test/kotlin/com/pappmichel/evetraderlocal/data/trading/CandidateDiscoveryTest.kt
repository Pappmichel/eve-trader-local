package com.pappmichel.evetraderlocal.data.trading

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** Ports the desktop build's `tests/test_candidate_discovery.py` coverage
 * of the pure helpers `CandidateDiscovery` still has an equivalent of -
 * `isWantedMarketPath`, `marketGroupPath`, and the string/volume fallback
 * half of `guessCategory` (the real-SDE-category-name half isn't ported,
 * since there's no SDE cache on this platform - see `CandidateDiscovery.kt`'s
 * own docstring). No network, no coroutines - these three don't touch
 * either. */
class CandidateDiscoveryTest {
    // ------------------------------------------------- isWantedMarketPath
    @Test
    fun `excluded top-level groups are rejected`() {
        val cfg = TradingConfig()
        for (path in listOf(
            "Ships > Frigates", "Blueprints > Ship Blueprints", "Apparel > Clothing",
            "Personalization > SKINs", "Pilot's Services > Clone Bay", "Structures > Citadels",
        )) {
            assertFalse(path, CandidateDiscovery.isWantedMarketPath(path, cfg))
        }
    }

    @Test
    fun `everything else is wanted`() {
        val cfg = TradingConfig()
        for (path in listOf(
            "Ship Equipment > Turrets", // "Ship Equipment" is not "Ships"
            "Skills > Trade", // deliberately taken off the exclusion list
            "Implants & Boosters > Attribute Enhancers",
            "Manufacture & Research > Materials",
            "Drones > Combat Drones",
        )) {
            assertTrue(path, CandidateDiscovery.isWantedMarketPath(path, cfg))
        }
    }

    @Test
    fun `matching is case insensitive`() {
        assertFalse(CandidateDiscovery.isWantedMarketPath("SHIPS > Frigates", TradingConfig()))
    }

    @Test
    fun `exclusions come from the config`() {
        val cfg = TradingConfig(excludedPathPrefixes = listOf("drones"))
        assertFalse(CandidateDiscovery.isWantedMarketPath("Drones > Combat Drones", cfg))
        assertTrue(CandidateDiscovery.isWantedMarketPath("Ships > Frigates", cfg))
    }

    // ------------------------------------------------------ guessCategory
    @Test
    fun `guessCategory - module or rig in name or path is Module-Rig`() {
        assertEquals("Module/Rig", CandidateDiscovery.guessCategory("Ship Equipment > Modules", "Large Shield Booster", 0.1))
        assertEquals("Module/Rig", CandidateDiscovery.guessCategory("Ship Equipment > Rigs", "Trimark", 0.1))
    }

    @Test
    fun `guessCategory - volume 5 or more is Module-Rig`() {
        assertEquals("Module/Rig", CandidateDiscovery.guessCategory("Manufacture > X", "Tritanium", 5.0))
    }

    @Test
    fun `guessCategory - otherwise Material`() {
        assertEquals("Material", CandidateDiscovery.guessCategory("Manufacture > X", "Tritanium", 0.01))
    }

    // --------------------------------------------------------- marketGroupPath
    @Test
    fun `marketGroupPath walks up to the root`() {
        val names = mapOf(1 to "Ship Equipment", 2 to "Turrets", 3 to "Hybrid")
        val parents = mapOf(1 to 0, 2 to 1, 3 to 2)
        assertEquals("Ship Equipment > Turrets > Hybrid", CandidateDiscovery.marketGroupPath(3, names, parents))
    }

    @Test
    fun `a parent cycle cannot loop forever`() {
        val path = CandidateDiscovery.marketGroupPath(1, mapOf(1 to "A", 2 to "B"), mapOf(1 to 2, 2 to 1))
        assertEquals(20, path.split(" > ").size)
    }
}
