package com.pappmichel.evetraderlocal.data.doctrine

import androidx.room.Dao
import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.Upsert

/** Room counterpart of the desktop build's `doctrine_contract_history` table
 * (storage.py, GitHub issue #19): a permanent, append-only "who bought what
 * and when" log, independent of any live/currently-visible ESI contract
 * listing - a finished, matched contract's row here survives long after
 * ESI's own `/characters/{id}/contracts/` stops returning it (its ~30-day
 * retention window, see [com.pappmichel.evetraderlocal.data.esi.EsiClient.characterContracts]).
 *
 * `fittingName`/`hullTypeId` are a denormalized snapshot captured at the
 * moment [ContractSync] recorded this contract, not a live join against
 * [DoctrineFittingRepository] - stays meaningful even if that fitting is
 * later edited/deactivated/deleted, same reasoning storage.py's own schema
 * comment gives.
 *
 * Chosen as a proper Room table rather than the settings-blob-JSON pattern
 * [DoctrineFittingRepository]/[ShortlistRepository] use for small, bounded
 * config/lists: unlike those, this table is meant to *grow* indefinitely
 * across every sync for the life of the install, which is exactly the shape
 * a real table + indexed queries fits and a single ever-growing JSON blob
 * (fully re-read/re-written on every single insert) does not. */
@Entity(tableName = "doctrine_contract_history", indices = [Index("fittingId")])
data class DoctrineContractHistoryEntity(
    @PrimaryKey val contractId: Long,
    val fittingId: String,
    val fittingName: String,
    val hullTypeId: Int,
    val title: String?,
    val price: Double?,
    val acceptorId: Long?,
    val dateIssued: String,
    val dateCompleted: String?,
    val status: String,
)

fun ContractSync.ContractHistoryRow.toEntity() = DoctrineContractHistoryEntity(
    contractId = contractId, fittingId = fittingId, fittingName = fittingName, hullTypeId = hullTypeId,
    title = title, price = price, acceptorId = acceptorId, dateIssued = dateIssued,
    dateCompleted = dateCompleted, status = status,
)

fun DoctrineContractHistoryEntity.toRow() = ContractSync.ContractHistoryRow(
    contractId = contractId, fittingId = fittingId, fittingName = fittingName, hullTypeId = hullTypeId,
    title = title, price = price, acceptorId = acceptorId, dateIssued = dateIssued,
    dateCompleted = dateCompleted, status = status,
)

@Dao
interface DoctrineContractHistoryDao {
    @Upsert
    suspend fun upsertAll(rows: List<DoctrineContractHistoryEntity>)

    // Most-recently-completed first; a row with no dateCompleted at all
    // (shouldn't normally happen - ESI sets it the moment a contract
    // finishes - but the field is nullable in ESI's own model, and
    // [ContractSync] carries it through as-is) sorts last rather than
    // first, same "NULLS LAST" intent as storage.py's own read - SQLite's
    // ORDER BY treats NULL as the smallest value on DESC too, so an
    // explicit CASE keeps the same ordering Room's raw column compare
    // would not otherwise guarantee, across the string column type here.
    @Query(
        "SELECT * FROM doctrine_contract_history " +
            "ORDER BY (dateCompleted IS NULL), dateCompleted DESC",
    )
    suspend fun all(): List<DoctrineContractHistoryEntity>

    @Query("SELECT * FROM doctrine_contract_history WHERE fittingId = :fittingId")
    suspend fun forFitting(fittingId: String): List<DoctrineContractHistoryEntity>
}
