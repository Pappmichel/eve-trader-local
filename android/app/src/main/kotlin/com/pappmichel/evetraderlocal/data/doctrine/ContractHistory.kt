package com.pappmichel.evetraderlocal.data.doctrine

import com.pappmichel.evetraderlocal.data.esi.EsiClient
import com.pappmichel.evetraderlocal.data.esi.EsiContract
import com.pappmichel.evetraderlocal.data.sde.SdeRepository

/** Doctrine -> Contract History: "which of my contracts have actually
 * finished (sold), and what was in them" - a new authenticated ESI surface
 * for this Android build (`/characters/{id}/contracts/` +
 * `/characters/{id}/contracts/{id}/items/`, see [EsiClient.characterContracts]/
 * [EsiClient.characterContractItems]).
 *
 * **Deliberately NOT a port of `doctrine/actions.py`'s `do_contract_history`.**
 * That desktop action reads a *permanent, already-recorded* history table
 * (`doctrine_contract_history`) that only ever gets a row written into it by
 * the full contract-sync/matching pipeline (`esi_sync.sync_contracts` ->
 * `engine.match_and_validate_contract` -> `storage.
 * upsert_doctrine_contract_history`, see `test_doctrine_contract_history.py`)
 * the moment ESI first reports a previously-*matched* contract as finished.
 * Porting that faithfully needs: (1) the contract-sync loop itself
 * (`esi_sync.py`, ~300 lines, itself dependent on `engine.py`'s matching/
 * deviation math - `DoctrineValidation.kt` in this pass deliberately only
 * ports the stockpile-side half of that, see its own docstring), (2) a
 * persisted history table this platform's Room schema doesn't have, and (3)
 * "only a contract this app itself watched go outstanding -> finished
 * counts" - a genuinely different, much narrower feature than "list what ESI
 * currently reports". This screen instead answers the more modest, directly-
 * answerable-from-ESI-alone question: "of my currently-visible contracts,
 * which are done, and what did they contain" - every [finished] contract ESI
 * currently still retains (its own 30-day/still-in-progress retention rule,
 * not filtered further here), with item names resolved, but with no
 * matched-fitting/doctrine attribution at all (there is no persisted match
 * to attribute from). A future pass that ports the real sync/matching engine
 * can replace this with the faithful permanent-history version; this one is
 * a real, useful, honestly-scoped read-only view in the meantime. */
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
}
