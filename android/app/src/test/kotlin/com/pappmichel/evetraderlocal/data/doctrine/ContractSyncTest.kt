package com.pappmichel.evetraderlocal.data.doctrine

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** [ContractSync]'s matching + sync/history logic, ported case-for-case from
 * the desktop build's `tests/test_doctrine_engine.py` (`match_and_validate_
 * contract`), `tests/test_doctrine_esi_sync.py` (`sync_contracts`
 * prefilter/dispatch), and `tests/test_doctrine_contract_history.py`
 * (GitHub issue #19's history recording + upsert/dedup rule). */
class ContractSyncTest {
    private val RIFTER = 1
    private val DAMAGE_CONTROL = 2
    private val AUTOCANNON = 3
    private val AMMO = 4
    private val STRUCTURE_ID = 60_003_760L
    private val CHAR_ID = 2_112_625_428L

    private fun fitting(): SavedFitting = SavedFitting(
        fittingId = "fit-1", name = "C-J Doctrine Fit", hullTypeId = RIFTER, hullName = "Rifter", rawEft = "",
        items = listOf(
            SavedFittingItem(1, "low", DAMAGE_CONTROL, 1.0),
            SavedFittingItem(2, "high", AUTOCANNON, 1.0),
            SavedFittingItem(2, "charge", AMMO, 1.0),
        ),
        createdAt = "2026-01-01T00:00:00Z",
    )

    // --------------------------------------------------------------- matchAndValidate (engine.py)
    @Test
    fun matchAndValidate_exactMatchIsValid() {
        val candidates = ContractSync.loadCandidates(listOf(fitting()))
        val items = listOf(
            DoctrineValidation.ContractItemRow(1, 1, RIFTER, 1.0, true, true),
            DoctrineValidation.ContractItemRow(1, 2, DAMAGE_CONTROL, 1.0, true, false),
            DoctrineValidation.ContractItemRow(1, 3, AUTOCANNON, 1.0, true, false),
            DoctrineValidation.ContractItemRow(1, 4, AMMO, 1.0, true, false),
        )
        val result = ContractSync.matchAndValidate(1, "Rifter Fleet Fit", items, candidates, 0.9)
        assertEquals("fit-1", result.matchedFittingId)
        assertEquals(DoctrineValidation.VALIDATION_VALID, result.status)
        assertTrue(result.deviations.isEmpty())
        assertTrue(result.score > 0)
    }

    @Test
    fun matchAndValidate_noHullPresent() {
        val candidates = ContractSync.loadCandidates(listOf(fitting()))
        val items = listOf(DoctrineValidation.ContractItemRow(2, 1, DAMAGE_CONTROL, 1.0, true, false))
        val result = ContractSync.matchAndValidate(2, null, items, candidates, 0.9)
        assertNull(result.matchedFittingId)
        assertEquals(ContractSync.NO_HULL_MATCH, result.status)
        assertTrue(result.deviations.isEmpty())
    }

    @Test
    fun matchAndValidate_missingAmmoIsInvalid() {
        val candidates = ContractSync.loadCandidates(listOf(fitting()))
        val items = listOf(
            DoctrineValidation.ContractItemRow(3, 1, RIFTER, 1.0, true, true),
            DoctrineValidation.ContractItemRow(3, 2, DAMAGE_CONTROL, 1.0, true, false),
            DoctrineValidation.ContractItemRow(3, 3, AUTOCANNON, 1.0, true, false),
        )
        val result = ContractSync.matchAndValidate(3, null, items, candidates, 0.9)
        assertEquals("fit-1", result.matchedFittingId)
        assertEquals(DoctrineValidation.VALIDATION_INVALID, result.status)
        assertTrue(result.deviations.any { it.typeId == AMMO && it.kind == DoctrineValidation.DEVIATION_KIND_MISSING })
    }

    // --------------------------------------------------------------- matchContracts (esi_sync.py sync_contracts)
    private fun items(contractId: Long) = listOf(
        DoctrineValidation.ContractItemRow(contractId, 1, RIFTER, 1.0, true, true),
        DoctrineValidation.ContractItemRow(contractId, 2, DAMAGE_CONTROL, 1.0, true, false),
        DoctrineValidation.ContractItemRow(contractId, 3, AUTOCANNON, 1.0, true, false),
        DoctrineValidation.ContractItemRow(contractId, 4, AMMO, 1.0, true, false),
    )

    private fun rawContract(
        id: Long, status: String = "outstanding", startLocationId: Long? = 60_003_760L,
        issuerId: Long = CHAR_ID, acceptorId: Long? = null, dateCompleted: String? = null,
    ) = ContractSync.RawContract(
        contractId = id, type = "item_exchange", status = status, title = "Rifter Fit",
        startLocationId = startLocationId, issuerId = issuerId, price = 1_000_000.0,
        dateIssued = "2026-01-01T00:00:00Z", dateCompleted = dateCompleted, acceptorId = acceptorId,
    )

    @Test
    fun matchContracts_matchesAndReportsAValidActiveContract() {
        val candidates = ContractSync.loadCandidates(listOf(fitting()))
        val outcome = ContractSync.matchContracts(
            CHAR_ID, STRUCTURE_ID, listOf(rawContract(555)), mapOf(555L to items(555)), candidates, 0.9,
        )
        assertEquals(1, outcome.activeContracts.size)
        assertEquals("fit-1", outcome.activeContracts[0].matchedFittingId)
        assertEquals(DoctrineValidation.VALIDATION_VALID, outcome.activeContracts[0].validationStatus)
        assertEquals(0, outcome.noHullMatchCount)
        assertTrue(outcome.historyRows.isEmpty())
    }

    @Test
    fun matchContracts_dropsContractWithNoMatchingHull() {
        val candidates = ContractSync.loadCandidates(listOf(fitting()))
        val noHullItems = listOf(DoctrineValidation.ContractItemRow(777, 1, AMMO, 100.0, true, false))
        val outcome = ContractSync.matchContracts(
            CHAR_ID, STRUCTURE_ID, listOf(rawContract(777)), mapOf(777L to noHullItems), candidates, 0.9,
        )
        assertTrue(outcome.activeContracts.isEmpty())
        assertEquals(1, outcome.noHullMatchCount)
    }

    @Test
    fun matchContracts_ignoresContractsAtADifferentStructure() {
        val candidates = ContractSync.loadCandidates(listOf(fitting()))
        val outcome = ContractSync.matchContracts(
            CHAR_ID, STRUCTURE_ID, listOf(rawContract(888, startLocationId = STRUCTURE_ID + 1)),
            mapOf(888L to items(888)), candidates, 0.9,
        )
        assertTrue(outcome.activeContracts.isEmpty())
        assertTrue(outcome.historyRows.isEmpty())
    }

    @Test
    fun matchContracts_ignoresContractsIssuedBySomeoneElse() {
        // scope decision 1 (no corp scope on this platform): only contracts
        // this character itself issued are ever relevant.
        val candidates = ContractSync.loadCandidates(listOf(fitting()))
        val outcome = ContractSync.matchContracts(
            CHAR_ID, STRUCTURE_ID, listOf(rawContract(900, issuerId = CHAR_ID + 1)),
            mapOf(900L to items(900)), candidates, 0.9,
        )
        assertTrue(outcome.activeContracts.isEmpty())
    }

    @Test
    fun matchContracts_finishedMatchedContractProducesAHistoryRow() {
        // GitHub issue #19 parity.
        val candidates = ContractSync.loadCandidates(listOf(fitting()))
        val finished = rawContract(555, status = "finished_issuer", acceptorId = 90_000_001L, dateCompleted = "2026-01-02T00:00:00Z")
        val outcome = ContractSync.matchContracts(
            CHAR_ID, STRUCTURE_ID, listOf(finished), mapOf(555L to items(555)), candidates, 0.9,
        )
        assertTrue(outcome.activeContracts.isEmpty()) // finished, not syncable
        assertEquals(1, outcome.historyRows.size)
        val row = outcome.historyRows[0]
        assertEquals(555L, row.contractId)
        assertEquals("fit-1", row.fittingId)
        assertEquals("C-J Doctrine Fit", row.fittingName)
        assertEquals(RIFTER, row.hullTypeId)
        assertEquals(90_000_001L, row.acceptorId)
        assertEquals("2026-01-02T00:00:00Z", row.dateCompleted)
    }

    @Test
    fun matchContracts_finishedContractNeverMatchedIsNotRecorded() {
        val candidates = ContractSync.loadCandidates(listOf(fitting()))
        val finished = rawContract(999, status = "finished")
        val noMatchItems = listOf(DoctrineValidation.ContractItemRow(999, 1, AMMO, 5.0, true, false)) // no Rifter hull at all
        val outcome = ContractSync.matchContracts(
            CHAR_ID, STRUCTURE_ID, listOf(finished), mapOf(999L to noMatchItems), candidates, 0.9,
        )
        assertTrue(outcome.historyRows.isEmpty())
    }

    @Test
    fun matchContracts_contractWithNoFetchedItemsIsDroppedThisRun() {
        val candidates = ContractSync.loadCandidates(listOf(fitting()))
        val outcome = ContractSync.matchContracts(
            CHAR_ID, STRUCTURE_ID, listOf(rawContract(1234)), emptyMap(), candidates, 0.9,
        )
        assertTrue(outcome.activeContracts.isEmpty())
        assertTrue(outcome.historyRows.isEmpty())
        assertEquals(0, outcome.noHullMatchCount) // dropped before ever matching, not counted as "no relevant hull"
    }

    // --------------------------------------------------------------- mergeHistoryRows (upsert/dedup)
    private fun historyRow(contractId: Long, acceptorId: Long? = null) = ContractSync.ContractHistoryRow(
        contractId = contractId, fittingId = "fit-1", fittingName = "C-J Doctrine Fit", hullTypeId = RIFTER,
        title = "title", price = 1_000_000.0, acceptorId = acceptorId, dateIssued = "2026-01-01T00:00:00Z",
        dateCompleted = "2026-01-02T00:00:00Z", status = "finished_issuer",
    )

    @Test
    fun mergeHistoryRows_newContractIsAdded() {
        val merged = ContractSync.mergeHistoryRows(emptyList(), listOf(historyRow(555)))
        assertEquals(1, merged.size)
        assertEquals(555L, merged[0].contractId)
    }

    @Test
    fun mergeHistoryRows_sameContractIdUpdatesRatherThanDuplicates() {
        // A later sync resolving a previously-unresolvable field (e.g. an
        // acceptor id becoming known) updates the existing row in place, not
        // a second row - storage.py's own ON CONFLICT DO UPDATE, ported as a
        // plain, fully-testable Kotlin map (see mergeHistoryRows's own
        // docstring on why this can't ride solely on Room's @Upsert in this
        // pure-JVM harness).
        val existing = listOf(historyRow(555, acceptorId = null))
        val incoming = listOf(historyRow(555, acceptorId = 90_000_001L))
        val merged = ContractSync.mergeHistoryRows(existing, incoming)
        assertEquals(1, merged.size)
        assertEquals(90_000_001L, merged[0].acceptorId)
    }

    @Test
    fun mergeHistoryRows_unrelatedExistingRowsSurvive() {
        val existing = listOf(historyRow(111), historyRow(222))
        val incoming = listOf(historyRow(333))
        val merged = ContractSync.mergeHistoryRows(existing, incoming)
        assertEquals(setOf(111L, 222L, 333L), merged.map { it.contractId }.toSet())
    }

    @Test
    fun mergeHistoryRows_repeatedSyncsOfTheSameFinishedContractStayAtOneRow() {
        // Simulates two syncs in a row both observing contract 555 as
        // finished (this platform re-matches every sync rather than reusing
        // a persisted "active" snapshot - see ContractSync's own docstring,
        // scope decision 2) - must still converge to exactly one history row.
        val candidates = ContractSync.loadCandidates(listOf(fitting()))
        val finished = rawContract(555, status = "finished_issuer", dateCompleted = "2026-01-02T00:00:00Z")
        val firstSync = ContractSync.matchContracts(CHAR_ID, STRUCTURE_ID, listOf(finished), mapOf(555L to items(555)), candidates, 0.9)
        val secondSync = ContractSync.matchContracts(CHAR_ID, STRUCTURE_ID, listOf(finished), mapOf(555L to items(555)), candidates, 0.9)

        var store = emptyList<ContractSync.ContractHistoryRow>()
        store = ContractSync.mergeHistoryRows(store, firstSync.historyRows)
        store = ContractSync.mergeHistoryRows(store, secondSync.historyRows)
        assertEquals(1, store.size)
        assertEquals(555L, store[0].contractId)
    }

    // --------------------------------------------------------------- validContractCounts
    @Test
    fun validContractCounts_countsOnlyValidNonExpiredPerFitting() {
        val contracts = listOf(
            ContractSync.SyncedContract(1, "outstanding", null, null, "fit-1", 1.0, emptyList(), DoctrineValidation.VALIDATION_VALID),
            ContractSync.SyncedContract(2, "outstanding", null, null, "fit-1", 1.0, emptyList(), DoctrineValidation.VALIDATION_VALID),
            ContractSync.SyncedContract(3, "expired", null, null, "fit-1", 1.0, emptyList(), DoctrineValidation.VALIDATION_VALID), // expired - excluded
            ContractSync.SyncedContract(4, "outstanding", null, null, "fit-1", 1.0, emptyList(), DoctrineValidation.VALIDATION_TOLERABLE), // not valid - excluded
            ContractSync.SyncedContract(5, "outstanding", null, null, "fit-2", 1.0, emptyList(), DoctrineValidation.VALIDATION_VALID),
        )
        val counts = ContractSync.validContractCounts(contracts)
        assertEquals(2, counts["fit-1"])
        assertEquals(1, counts["fit-2"])
    }

    @Test
    fun validContractCounts_emptyWhenNoneValid() {
        assertTrue(ContractSync.validContractCounts(emptyList()).isEmpty())
    }
}
