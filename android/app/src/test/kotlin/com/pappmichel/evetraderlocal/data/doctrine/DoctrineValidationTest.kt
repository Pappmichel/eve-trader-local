package com.pappmichel.evetraderlocal.data.doctrine

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** Ported case-for-case from the desktop build's
 * `tests/test_doctrine_validation.py`, restricted to the subset
 * [DoctrineValidation] actually ports - Soll construction, stockpile
 * allocation/deviation, and the worst-of aggregators. The contract-side
 * cases (hull gate, Ist, `compute_deviations`, `pair_wrong_variants`,
 * `match_score`, `contract_status`, `contract_ampel`) have no Kotlin
 * counterpart yet - see [DoctrineValidation]'s own docstring for why. */
class DoctrineValidationTest {
    private val HULL = 999

    private fun fittingItems() = listOf(
        SavedFittingItem(1, "low", 100, 1.0),
        SavedFittingItem(2, "high", 200, 1.0),
        SavedFittingItem(3, "cargo", 300, 10.0),
        SavedFittingItem(4, "charge", 400, 1.0),
    )

    // --------------------------------------------------------------- Soll construction
    @Test
    fun buildContractSoll_excludesHullAndSplitsClasses() {
        val (exact, consume) = DoctrineValidation.buildContractSoll(fittingItems())
        assertEquals(mapOf(100 to 1.0, 200 to 1.0), exact)
        assertEquals(mapOf(300 to 10.0, 400 to 1.0), consume)
    }

    @Test
    fun buildContractSoll_fuelbayAndShipMaintenanceBayAreConsumeTolerant() {
        val items = listOf(
            SavedFittingItem(1, "fuelbay", 500, 2500.0),
            SavedFittingItem(2, "shipmaintenancebay", 600, 1.0),
        )
        val (exact, consume) = DoctrineValidation.buildContractSoll(items)
        assertEquals(emptyMap<Int, Double>(), exact)
        assertEquals(mapOf(500 to 2500.0, 600 to 1.0), consume)
    }

    @Test
    fun charge_mergesWithExistingCargoQuantity_notAdditive() {
        val items = listOf(SavedFittingItem(1, "cargo", 300, 5.0), SavedFittingItem(2, "charge", 300, 1.0))
        val (_exact, consume) = DoctrineValidation.buildContractSoll(items)
        assertEquals(mapOf(300 to 5.0), consume) // not 6 - floor-of-1 absorbed into existing cargo qty
    }

    @Test
    fun chargeOnlyType_getsFloorOfOne() {
        val items = listOf(SavedFittingItem(1, "charge", 400, 1.0))
        val (_exact, consume) = DoctrineValidation.buildContractSoll(items)
        assertEquals(mapOf(400 to 1.0), consume)
    }

    @Test
    fun buildStockpileSoll_includesHullAndMultipliesByTarget() {
        val soll = DoctrineValidation.buildStockpileSoll(fittingItems(), hullTypeId = HULL, stockpileTarget = 3)
        assertEquals(3.0 to "exact", soll[HULL])
        assertEquals(3.0 to "exact", soll[100])
        assertEquals(3.0 to "exact", soll[200])
        assertEquals(30.0 to "consume", soll[300])
        assertEquals(3.0 to "consume", soll[400])
    }

    @Test
    fun buildStockpileSoll_addsContractShortfallToStockpileTarget() {
        // total_sets = 1 (stockpileTarget) + max(0, 5 - 2) = 4
        val soll = DoctrineValidation.buildStockpileSoll(
            fittingItems(), hullTypeId = HULL, stockpileTarget = 1, contractTarget = 5, validContracts = 2,
        )
        assertEquals(4.0 to "exact", soll[HULL])
        assertEquals(40.0 to "consume", soll[300])
    }

    @Test
    fun buildStockpileSoll_contractShortfallFloorsAtZeroWhenAlreadyMet() {
        val soll = DoctrineValidation.buildStockpileSoll(
            fittingItems(), hullTypeId = HULL, stockpileTarget = 2, contractTarget = 3, validContracts = 5,
        )
        assertEquals(2.0 to "exact", soll[HULL])
    }

    @Test
    fun buildStockpileSoll_defaultsToStockpileTargetOnly() {
        val soll = DoctrineValidation.buildStockpileSoll(fittingItems(), hullTypeId = HULL, stockpileTarget = 3)
        assertEquals(3.0 to "exact", soll[HULL])
    }

    // --------------------------------------------------------------- stockpile (C.2/C.3)
    @Test
    fun allocateStockpile_greedyPriorityOrder() {
        val soll = listOf(
            "f1" to mapOf(100 to (8.0 to "exact")),
            "f2" to mapOf(100 to (8.0 to "exact")),
        )
        val alloc = DoctrineValidation.allocateStockpile(soll, mapOf(100 to 10.0))
        assertEquals(8.0, alloc.getValue("f1").getValue(100), 0.0)
        assertEquals(2.0, alloc.getValue("f2").getValue(100), 0.0) // only 2 left after f1 takes its full 8
    }

    @Test
    fun allocateStockpile_neverOverallocates() {
        val soll = listOf("f1" to mapOf(100 to (5.0 to "exact")))
        val alloc = DoctrineValidation.allocateStockpile(soll, mapOf(100 to 20.0))
        assertEquals(5.0, alloc.getValue("f1").getValue(100), 0.0)
    }

    @Test
    fun stockpileDeviation_exactClassAnyShortfallIsCritical() {
        val (shortfall, severity) = DoctrineValidation.stockpileDeviation(
            requiredTotal = 5.0, allocated = 4.0, itemClass = "exact", cargoTolerancePct = 0.9,
        )
        assertEquals(1.0, shortfall, 0.0)
        assertEquals(DoctrineValidation.SEVERITY_CRITICAL, severity)
    }

    @Test
    fun stockpileDeviation_consumeClassWithinTolerance() {
        val (shortfall, severity) = DoctrineValidation.stockpileDeviation(
            requiredTotal = 10.0, allocated = 9.0, itemClass = "consume", cargoTolerancePct = 0.9,
        )
        assertEquals(1.0, shortfall, 0.0)
        assertEquals(DoctrineValidation.SEVERITY_TOLERABLE, severity)
    }

    @Test
    fun stockpileDeviation_noShortfallIsNull() {
        val (shortfall, severity) = DoctrineValidation.stockpileDeviation(
            requiredTotal = 5.0, allocated = 5.0, itemClass = "exact", cargoTolerancePct = 0.9,
        )
        assertEquals(0.0, shortfall, 0.0)
        assertNull(severity)
    }

    @Test
    fun stockpileDeviation_consumeClassBelowToleranceIsCritical() {
        val (_shortfall, severity) = DoctrineValidation.stockpileDeviation(
            requiredTotal = 10.0, allocated = 5.0, itemClass = "consume", cargoTolerancePct = 0.9,
        )
        assertEquals(DoctrineValidation.SEVERITY_CRITICAL, severity)
    }

    // --------------------------------------------------------------- ampel aggregation (B.8)
    @Test
    fun stockpileAmpel_grayWhenAssetsUnavailable() {
        assertEquals(DoctrineValidation.AMPEL_GRAY, DoctrineValidation.stockpileAmpel(false, 2, null))
    }

    @Test
    fun stockpileAmpel_greenWhenNoDeviation() {
        assertEquals(DoctrineValidation.AMPEL_GREEN, DoctrineValidation.stockpileAmpel(true, 2, null))
    }

    @Test
    fun stockpileAmpel_redOnCritical() {
        assertEquals(DoctrineValidation.AMPEL_RED, DoctrineValidation.stockpileAmpel(true, 2, DoctrineValidation.SEVERITY_CRITICAL))
    }

    @Test
    fun worstAmpel_prefersNonGrayAndWorstOfRemaining() {
        assertEquals("red", DoctrineValidation.worstAmpel(listOf("green", "red", "gray")))
        assertEquals("gray", DoctrineValidation.worstAmpel(listOf("gray", "gray")))
        assertEquals("yellow", DoctrineValidation.worstAmpel(listOf("green", "yellow")))
        assertEquals("gray", DoctrineValidation.worstAmpel(emptyList()))
    }

    @Test
    fun worstSeverity_prefersNonNullAndWorstOfRemaining() {
        assertEquals(
            DoctrineValidation.SEVERITY_CRITICAL,
            DoctrineValidation.worstSeverity(listOf(DoctrineValidation.SEVERITY_TOLERABLE, DoctrineValidation.SEVERITY_CRITICAL, null)),
        )
        assertNull(DoctrineValidation.worstSeverity(listOf(null, null)))
        assertEquals(
            DoctrineValidation.SEVERITY_TOLERABLE,
            DoctrineValidation.worstSeverity(listOf(DoctrineValidation.SEVERITY_INFO, DoctrineValidation.SEVERITY_TOLERABLE)),
        )
        assertNull(DoctrineValidation.worstSeverity(emptyList()))
    }
}
