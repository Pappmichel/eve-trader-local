package com.pappmichel.evetraderlocal.data.doctrine

import com.pappmichel.evetraderlocal.data.db.AppDatabase
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext

/** Persists [ContractSync]'s permanent history output into
 * [DoctrineContractHistoryEntity] - the Android counterpart of storage.py's
 * `upsert_doctrine_contract_history`/`load_doctrine_contract_history`.
 *
 * [upsertAll] runs [ContractSync.mergeHistoryRows] (a plain, fully
 * unit-tested Kotlin map keyed by `contractId`) before ever touching Room,
 * so "a later sync must not duplicate an already-recorded contract" is a
 * property of that pure function, not something riding solely on Room's own
 * `@Upsert`-by-primary-key behavior (which this pure-JVM test harness can't
 * exercise directly - see [ContractSync.mergeHistoryRows]'s own docstring).
 * Room's `@Upsert` still runs underneath (it always replaces a row sharing a
 * primary key, never inserts a second one), so the two layers agree rather
 * than one silently overriding the other. */
class DoctrineContractHistoryRepository(private val db: AppDatabase) {
    // Guards read-merge-write sync sequences the same way
    // DoctrineFittingRepository's own writeMutex does - two concurrent
    // syncs (unlikely on a single screen, but cheap to guard against) must
    // not race a stale in-memory `existing` list into overwriting the
    // other's freshly-written rows.
    private val writeMutex = Mutex()

    suspend fun list(): List<ContractSync.ContractHistoryRow> = withContext(Dispatchers.IO) {
        db.doctrineContractHistoryDao().all().map { it.toRow() }
    }

    suspend fun listForFitting(fittingId: String): List<ContractSync.ContractHistoryRow> = withContext(Dispatchers.IO) {
        db.doctrineContractHistoryDao().forFitting(fittingId).map { it.toRow() }
    }

    suspend fun upsertAll(incoming: List<ContractSync.ContractHistoryRow>) {
        if (incoming.isEmpty()) return
        writeMutex.withLock {
            withContext(Dispatchers.IO) {
                val existing = db.doctrineContractHistoryDao().all().map { it.toRow() }
                val merged = ContractSync.mergeHistoryRows(existing, incoming)
                db.doctrineContractHistoryDao().upsertAll(merged.map { it.toEntity() })
            }
        }
    }
}
