package com.pappmichel.evetraderlocal.data.doctrine

import org.junit.Assert.assertEquals
import org.junit.Test

/** [StockpileStatus.computeRows]'s `validContractsByFitting` wiring (GitHub
 * issue #36's additive contract-target multiplier), ported from the desktop
 * build's `tests/test_doctrine_validation.py::
 * test_build_stockpile_soll_adds_contract_shortfall_to_stockpile_target`
 * case, now exercised end to end through [StockpileStatus] the way
 * [ContractSync.validContractCounts]'s real output would feed it. */
class StockpileContractMultiplierTest {
    private val HULL = 1
    private val MODULE = 100

    private fun fitting(contractTarget: Int, stockpileTarget: Int) = SavedFitting(
        fittingId = "f1", name = "Fit", hullTypeId = HULL, hullName = "Hull", rawEft = "",
        items = listOf(SavedFittingItem(1, "low", MODULE, 1.0)),
        contractTarget = contractTarget, stockpileTarget = stockpileTarget, createdAt = "2026-01-01T00:00:00Z",
    )

    @Test
    fun noValidContracts_multiplierIsJustStockpileTarget() {
        val rows = StockpileStatus.computeRows(
            listOf(fitting(contractTarget = 5, stockpileTarget = 1)),
            availableByType = emptyMap(), typeName = { it.toString() },
            validContractsByFitting = emptyMap(),
        )
        // total_sets = 1 (stockpileTarget) + max(0, 5 - 0) = 6
        assertEquals(6.0, rows.first { it.typeId == MODULE }.requiredTotal, 0.0)
    }

    @Test
    fun validContracts_reduceTheContractShortfallPortion() {
        val rows = StockpileStatus.computeRows(
            listOf(fitting(contractTarget = 5, stockpileTarget = 1)),
            availableByType = emptyMap(), typeName = { it.toString() },
            validContractsByFitting = mapOf("f1" to 2),
        )
        // total_sets = 1 + max(0, 5 - 2) = 4
        assertEquals(4.0, rows.first { it.typeId == MODULE }.requiredTotal, 0.0)
    }

    @Test
    fun validContractsAlreadyMeetingTarget_floorsAtStockpileTargetOnly() {
        val rows = StockpileStatus.computeRows(
            listOf(fitting(contractTarget = 3, stockpileTarget = 2)),
            availableByType = emptyMap(), typeName = { it.toString() },
            validContractsByFitting = mapOf("f1" to 5),
        )
        // total_sets = 2 + max(0, 3 - 5) = 2
        assertEquals(2.0, rows.first { it.typeId == MODULE }.requiredTotal, 0.0)
    }

    @Test
    fun missingFittingEntry_defaultsToZeroValidContracts() {
        val rows = StockpileStatus.computeRows(
            listOf(fitting(contractTarget = 2, stockpileTarget = 0)),
            availableByType = emptyMap(), typeName = { it.toString() },
            validContractsByFitting = mapOf("some-other-fitting" to 9),
        )
        // total_sets = 0 + max(0, 2 - 0) = 2
        assertEquals(2.0, rows.first { it.typeId == MODULE }.requiredTotal, 0.0)
    }
}
