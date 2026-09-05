package com.pappmichel.evetraderlocal.data.doctrine

import com.pappmichel.evetraderlocal.data.esi.EsiClient
import com.pappmichel.evetraderlocal.data.esi.EsiContract
import com.pappmichel.evetraderlocal.data.sde.SdeRepository

/** Doctrine -> Contract History. **Now a real port of `doctrine/actions.py`'s
 * `do_contract_history`**: [syncAndPersist] runs the real contract-sync/
 * matching engine ([ContractSync.sync]) and writes every newly-finished,
 * fitting-matched contract into the permanent
 * [DoctrineContractHistoryRepository] store, and [loadPermanentHistory]
 * reads that store back - `doctrine_contract_history`'s exact role on
 * desktop (GitHub issue #19), not a live "what ESI still happens to
 * retain" listing. See [ContractSync]'s own docstring for what matching
 * means and the three scope decisions vs. desktop's fuller engine (no
 * corp contracts, no persisted "active" snapshot, no acceptor-name
 * resolution).
 *
 * [finishedOnly]/[fetch] are kept as a secondary, honestly-scoped live-ESI
 * view: a permanent-history row only exists for a contract that matched
 * some *saved, active* fitting well enough to clear
 * [DoctrineValidation.MATCH_THRESHOLD] - a finished item_exchange contract
 * that doesn't (wrong structure, no fitting saved yet, or a genuine
 * near-miss) simply never appears there, the same way it never reaches
 * desktop's own `doctrine_contract_history`. [fetch] still answers "what
 * does ESI currently say is finished, regardless of any match" for anyone
 * who wants to see that raw picture (e.g. while still building out their
 * fitting library) - unchanged from before this pass. */
object ContractHistory {

    // doctrine/constants.py's FINISHED_CONTRACT_STATUSES - ESI's own
    // contract-status vocabulary for "this was actually accepted/sold",
    // duplicated here rather than cross-imported (see DoctrineValidation.kt's
    // own note on this codebase's "small curated constants, independently
    // duplicated" convention).
    val FINISHED_STATUSES = setOf("finished", "finished_issuer", "finished_contractor")

    /** One resolved item line on a finished contract. */
    data class Item(
        val typeId: Int,
        val typeName: String,
        val quantity: Long,
        val isIncluded: Boolean,
    )

    /** One finished contract, with its items resolved to display names. */
    data class Row(
        val contractId: Long,
        val title: String?,
        val status: String,
        val price: Double?,
        val forCorporation: Boolean,
        val dateIssued: String,
        val dateCompleted: String?,
        val items: List<Item>,
    )

    /** Filters ESI's raw contract list down to [FINISHED_STATUSES] - pure,
     * no network. */
    fun finishedOnly(contracts: List<EsiContract>): List<EsiContract> =
        contracts.filter { it.status in FINISHED_STATUSES }

    /** Fetches every currently-visible finished contract for `characterId`
     * and resolves each one's item names via the local SDE cache
     * ([SdeRepository.typeName]) - a lookup miss (an item not in this
     * platform's curated cache) falls back to the bare type_id string,
     * same "never block the whole run on one bad lookup" pattern every
     * other name-resolution helper on this platform already follows. */
    suspend fun fetch(esi: EsiClient, sde: SdeRepository, characterId: Long, accessToken: String): List<Row> {
        val contracts = finishedOnly(esi.characterContracts(characterId, accessToken))
        return contracts.map { c ->
            val items = try {
                esi.characterContractItems(characterId, c.contractId, accessToken)
            } catch (e: Exception) {
                emptyList()
            }
            val resolvedItems = items.map { i ->
                val name = sde.typeName(i.typeId) ?: i.typeId.toString()
                Item(typeId = i.typeId, typeName = name, quantity = i.quantity, isIncluded = i.isIncluded)
            }
            Row(
                contractId = c.contractId, title = c.title, status = c.status, price = c.price,
                forCorporation = c.forCorporation, dateIssued = c.dateIssued, dateCompleted = c.dateCompleted,
                items = resolvedItems,
            )
        }.sortedByDescending { it.dateCompleted ?: it.dateIssued }
    }

    /** Runs [ContractSync.sync] against `characterId`'s current contracts at
     * `structureId` and merges any newly-finished, matched contracts into
     * the permanent store via [historyRepo]. Returns the full
     * [ContractSync.SyncOutcome] (active matched contracts too - the
     * Stockpile Status screen's own multiplier read, see
     * [StockpileStatus]'s docstring, uses this same call) so a caller
     * doesn't have to sync twice to get both pieces. */
    suspend fun syncAndPersist(
        esi: EsiClient,
        historyRepo: DoctrineContractHistoryRepository,
        characterId: Long,
        accessToken: String,
        structureId: Long,
        candidates: List<ContractSync.MatchCandidate>,
        cargoTolerancePctDefault: Double = 0.9,
        groupIdOf: (Int) -> Int? = { null },
        slotOf: (Int) -> String? = { null },
    ): ContractSync.SyncOutcome {
        val outcome = ContractSync.sync(
            esi, characterId, accessToken, structureId, candidates, cargoTolerancePctDefault, groupIdOf, slotOf,
        )
        historyRepo.upsertAll(outcome.historyRows)
        return outcome
    }

    /** One permanent history row, ready for display - [DoctrineContractHistoryRepository.list]'s
     * rows with a resolved fitting/hull name already denormalized onto them
     * ([ContractSync.ContractHistoryRow.fittingName]/`hullTypeId` - only the
     * hull's own display name needs a fresh [SdeRepository] lookup here). */
    data class PermanentRow(
        val contractId: Long,
        val fittingName: String,
        val hullName: String,
        val title: String?,
        val price: Double?,
        val acceptorId: Long?,
        val dateIssued: String,
        val dateCompleted: String?,
        val status: String,
    )

    /** Reads every permanently-recorded contract back, most-recently-
     * completed first (the DAO's own ordering) - the Android counterpart of
     * storage.py's `load_doctrine_contract_history`. */
    suspend fun loadPermanentHistory(historyRepo: DoctrineContractHistoryRepository, sde: SdeRepository): List<PermanentRow> =
        historyRepo.list().map { r ->
            PermanentRow(
                contractId = r.contractId, fittingName = r.fittingName,
                hullName = sde.typeName(r.hullTypeId) ?: r.hullTypeId.toString(),
                title = r.title, price = r.price, acceptorId = r.acceptorId,
                dateIssued = r.dateIssued, dateCompleted = r.dateCompleted, status = r.status,
            )
        }
}
