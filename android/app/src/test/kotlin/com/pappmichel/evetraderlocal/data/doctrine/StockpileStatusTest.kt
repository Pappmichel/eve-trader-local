package com.pappmichel.evetraderlocal.data.doctrine

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class StockpileStatusTest {
    private val HULL_A = 1
    private val HULL_B = 2
    private val MODULE = 100

    private fun fitting(id: String, hullTypeId: Int, stockpileTarget: Int) = SavedFitting(
        fittingId = id, name = "Fit $id", hullTypeId = hullTypeId, hullName = "Hull $hullTypeId",
        rawEft = "", items = listOf(SavedFittingItem(1, "low", MODULE, 1.0)),
        stockpileTarget = stockpileTarget, createdAt = "2026-01-01T00:00:00Z",
    )

    @Test
    fun computeRows_emptyFittingList_isEmpty() {
        assertTrue(StockpileStatus.computeRows(emptyList(), emptyMap(), { it.toString() }).isEmpty())
    }

    @Test
    fun computeRows_allocatesSharedItemByFittingOrder() {
        // f1 (priority) needs 8 modules (target 8), f2 needs 8 too - only 10 available.
        val f1 = fitting("f1", HULL_A, stockpileTarget = 8)
        val f2 = fitting("f2", HULL_B, stockpileTarget = 8)
        val rows = StockpileStatus.computeRows(
            fittings = listOf(f1, f2), availableByType = mapOf(MODULE to 10.0), typeName = { it.toString() },
        )
        val f1Module = rows.first { it.fittingId == "f1" && it.typeId == MODULE }
        val f2Module = rows.first { it.fittingId == "f2" && it.typeId == MODULE }
        assertEquals(8.0, f1Module.available, 0.0)
        assertEquals(0.0, f1Module.shortfall, 0.0)
        assertEquals(2.0, f2Module.available, 0.0)
        assertEquals(6.0, f2Module.shortfall, 0.0)
        assertEquals(DoctrineValidation.SEVERITY_CRITICAL, f2Module.severity)
    }

    @Test
    fun computeRows_hullGetsHullSlotSection() {
        val f1 = fitting("f1", HULL_A, stockpileTarget = 1)
        val rows = StockpileStatus.computeRows(listOf(f1), mapOf(HULL_A to 1.0, MODULE to 1.0), { it.toString() })
        val hullRow = rows.first { it.typeId == HULL_A }
        assertEquals("hull", hullRow.slotSection)
    }

    @Test
    fun aggregate_sumsAcrossFittingsAndSortsByShortfallDescending() {
        // stockpileTarget=8 means each fitting's hull requirement is also 8
        // (not 1 - see buildStockpileSoll: total_sets multiplies every exact
        // position, hull included), so both hulls are fully stocked here to
        // isolate the module's own shortfall as the one under test.
        val f1 = fitting("f1", HULL_A, stockpileTarget = 8)
        val f2 = fitting("f2", HULL_B, stockpileTarget = 8)
        val available = mapOf(MODULE to 10.0, HULL_A to 8.0, HULL_B to 8.0)
        val rows = StockpileStatus.computeRows(listOf(f1, f2), available, { "Module" })
        val aggregated = StockpileStatus.aggregate(rows)
        val moduleRow = aggregated.first { it.typeId == MODULE }
        assertEquals(16.0, moduleRow.requiredTotal, 0.0)
        assertEquals(10.0, moduleRow.available, 0.0)
        assertEquals(6.0, moduleRow.shortfall, 0.0)
        assertEquals(2, moduleRow.fittingCount)
        // Sorted descending by shortfall - the module row (shortfall 6) must
        // outrank the two hull rows (shortfall 0 each, fully stocked at 1).
        assertEquals(MODULE, aggregated.first().typeId)
    }

    @Test
    fun aggregate_noShortfallHasNullSeverity() {
        val f1 = fitting("f1", HULL_A, stockpileTarget = 1)
        val rows = StockpileStatus.computeRows(listOf(f1), mapOf(HULL_A to 1.0, MODULE to 1.0), { it.toString() })
        val aggregated = StockpileStatus.aggregate(rows)
        assertTrue(aggregated.all { it.severity == null })
    }
}
