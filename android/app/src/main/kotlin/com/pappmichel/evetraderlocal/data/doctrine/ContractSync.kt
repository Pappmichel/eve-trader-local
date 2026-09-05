package com.pappmichel.evetraderlocal.data.doctrine

import com.pappmichel.evetraderlocal.data.esi.EsiClient
import com.pappmichel.evetraderlocal.data.esi.EsiContract

/** Doctrine's real contract-sync/matching engine - the Android counterpart of
 * the desktop build's `doctrine/esi_sync.py` (`sync_contracts`) +
 * `doctrine/engine.py` (`load_match_candidates`/`match_and_validate_contract`),
 * built on [DoctrineValidation]'s now-fully-ported Soll/Ist/deviation/score
 * math. This is what [ContractHistory.kt] and [DoctrineStockpileStatusScreen]
 * were both waiting on - see those files for how each consumes it.
 *
 * **What "matching" means (ported case-for-case from desktop):** every saved,
 * active [SavedFitting] is a candidate. For one contract's item list, a
 * candidate clears the *hull gate* first ([DoctrineValidation.hullGateSatisfied]
 * - the fitting's hull type must be present and `isIncluded`); every
 * candidate that clears it gets a weighted multiset-overlap
 * [DoctrineValidation.matchScore] (0.8 * exact-slot overlap + 0.2 *
 * consumable overlap) against the contract's Ist multiset. The
 * highest-scoring candidate wins if its score clears
 * [DoctrineValidation.MATCH_THRESHOLD] (0.5); a tie is broken by whichever
 * tied candidate's name appears in the contract's own title, and failing
 * that by candidate list order (save order = priority, same convention
 * [StockpileStatus] already documents). No candidate clearing the hull gate
 * at all means this contract isn't a doctrine sale ([NO_HULL_MATCH]) and is
 * dropped entirely, never persisted - same as desktop.
 *
 * **What "permanent" means:** ESI's `/characters/{id}/contracts/` only ever
 * returns a rolling window (still-outstanding contracts, plus roughly the
 * last 30 days of finished ones - see [EsiClient.characterContracts]'s own
 * docstring). [DoctrineContractHistoryEntity] is a Room table that
 * [mergeHistoryRows] upserts into on every [sync] call, one row per
 * `contractId`, and is never wholesale-replaced - so a finished, matched
 * contract's record survives long after ESI itself stops returning it.
 *
 * **Three deliberate scope decisions vs. the desktop engine, each forced by
 * a concrete gap already present on this platform (not a new one introduced
 * here) - documented rather than silently narrowed:**
 *
 * 1. **Character contracts only, no corporation contracts.** Desktop fetches
 *    both `character_contracts` and (best-effort) `corporation_contracts`
 *    per registered character. This Android build's "Doctrine" login role
 *    only requests `esi-contracts.read_character_contracts.v1`
 *    (`CharactersScreen.kt`'s own `DOCTRINE_SCOPES`) - no
 *    `esi-contracts.read_corporation_contracts.v1`, and [EsiClient] has no
 *    corporation-contracts endpoint at all. Requesting that scope means a
 *    dev-portal app change plus a new consent screen for every existing
 *    login, well beyond this pass - so corp-issued doctrine contracts
 *    simply aren't visible to this sync, same as any character whose token
 *    lacks the scope on desktop degrades to "skipped" for that half.
 * 2. **No re-match-avoidance / no persisted "active contracts" snapshot.**
 *    Desktop persists its currently-outstanding contract snapshot
 *    (`doctrine_contracts`) partly as a real UI need (Contracts.tsx lists
 *    it) and partly so an unchanged contract's already-fetched items don't
 *    need re-fetching. Neither reason applies here: this build's Contract
 *    History screen reads the *permanent* history table, not a live
 *    "currently outstanding" listing, and re-fetching one contract's items
 *    on every sync is one extra ESI call per contract, not a real cost at
 *    this app's scale. The direct consequence: desktop's stricter
 *    "history only ever reuses a fitting_id an earlier sync already matched
 *    while the contract was still outstanding" rule (GitHub issue #19 -
 *    deliberately excludes a contract that was *already finished the very
 *    first time it was ever seen*) has no equivalent here - [matchContracts]
 *    re-matches a finished contract's items directly, every sync, the same
 *    way it matches an outstanding one. This is strictly more permissive
 *    (a contract can enter history on the very sync that first observes it
 *    finished, even on a fresh install) but answers the same underlying
 *    question - "which fitting does this contract's item list belong to" -
 *    correctly either way, since ESI still serves a finished contract's
 *    items directly within its own retention window (confirmed: the
 *    pre-existing `ContractHistory.fetch` already fetched items for
 *    `finished*`-status contracts this same way).
 * 3. **No acceptor-name resolution.** Desktop resolves `acceptor_id ->
 *    acceptor_name` via `ESIClient.resolve_names` (`POST
 *    /universe/names/`) when writing history rows. [EsiClient] has no POST
 *    request path at all (every existing method is a GET) - adding one
 *    purely for a display-only name lookup is out of scope for a
 *    contract-matching pass; [DoctrineContractHistoryEntity.acceptorId] is
 *    still stored (a future pass can resolve it), `acceptorName` is not. */
object ContractSync {

    /** Internal-only status sentinel, mirroring engine.py's own
     * `NO_HULL_MATCH` - never one of [DoctrineValidation]'s `VALIDATION_*`
     * constants and never persisted; a contract that gets this is dropped
     * before it ever reaches [SyncOutcome]. */
    const val NO_HULL_MATCH = "no_relevant_hull"

    const val CONTRACT_TYPE_ITEM_EXCHANGE = "item_exchange"

    // constants.py's SYNCABLE_CONTRACT_STATUSES/FINISHED_CONTRACT_STATUSES.
    val SYNCABLE_STATUSES = setOf("outstanding", "expired")
    val FINISHED_STATUSES = setOf("finished", "finished_issuer", "finished_contractor")

    /** engine.py's `_Candidate` - one active saved fitting plus its
     * precomputed exact/consume Soll (built once per [sync]/[matchContracts]
     * call, reused across every contract matched against it). */
    data class MatchCandidate(
        val fitting: SavedFitting,
        val exactSoll: Map<Int, Double>,
        val consumeSoll: Map<Int, Double>,
    )

    /** engine.py's `load_match_candidates`, in the same doctrine-then-fitting
     * (here: save) order that both [StockpileStatus] and this matcher treat
     * as priority/tiebreak order. */
    fun loadCandidates(activeFittings: List<SavedFitting>): List<MatchCandidate> =
        activeFittings.map { f ->
            val (exact, consume) = DoctrineValidation.buildContractSoll(f.items)
            MatchCandidate(f, exact, consume)
        }

    /** One contract's match outcome - engine.py's `match_and_validate_contract`
     * return tuple, as a data class. */
    data class MatchResult(
        val matchedFittingId: String?,
        val score: Double,
        val deviations: List<DoctrineValidation.DeviationRow>,
        val status: String,
    )

    /** engine.py's `match_and_validate_contract`: hull-gates every candidate,
     * scores the survivors, picks the winner (title-hint tiebreak, then
     * candidate-list order), and computes that winner's deviation table +
     * status. `groupIdOf`/`slotOf` feed [DoctrineValidation.pairWrongVariants]
     * - see that function's own docstring on `slotOf`'s always-null reality
     * on this platform. */
    fun matchAndValidate(
        contractId: Long,
        contractTitle: String?,
        contractItems: List<DoctrineValidation.ContractItemRow>,
        candidates: List<MatchCandidate>,
        cargoTolerancePctDefault: Double,
        groupIdOf: (Int) -> Int? = { null },
        slotOf: (Int) -> String? = { null },
    ): MatchResult {
        val scored = candidates.mapNotNull { c ->
            if (!DoctrineValidation.hullGateSatisfied(contractItems, c.fitting.hullTypeId)) return@mapNotNull null
            val ist = DoctrineValidation.buildContractIst(contractItems, c.fitting.hullTypeId)
            c to DoctrineValidation.matchScore(c.exactSoll, c.consumeSoll, ist)
        }
        if (scored.isEmpty()) return MatchResult(null, 0.0, emptyList(), NO_HULL_MATCH)

        val maxScore = scored.maxOf { it.second }
        if (!DoctrineValidation.clearsMatchThreshold(maxScore)) {
            return MatchResult(null, maxScore, emptyList(), DoctrineValidation.VALIDATION_UNMATCHED)
        }

        val tied = scored.filter { it.second == maxScore }
        var winner = tied.first().first
        if (tied.size > 1 && !contractTitle.isNullOrBlank()) {
            val titleMatches = tied.filter { it.first.fitting.name.lowercase() in contractTitle.lowercase() }
            if (titleMatches.size == 1) winner = titleMatches.first().first
        }

        val tolerance = winner.fitting.cargoTolerancePct ?: cargoTolerancePctDefault
        var deviations = DoctrineValidation.computeDeviations(
            contractId, winner.exactSoll, winner.consumeSoll, contractItems, winner.fitting.hullTypeId, tolerance,
        )
        deviations = DoctrineValidation.pairWrongVariants(deviations, groupIdOf, slotOf)
        val status = DoctrineValidation.contractStatus(deviations, matched = true)
        return MatchResult(winner.fitting.fittingId, maxScore, deviations, status)
    }

    /** One currently-syncable (outstanding/expired) contract's match outcome
     * - what [StockpileStatus]'s contract-target multiplier needs a count of
     * ("valid_contracts"), the Android counterpart of the rows
     * `doctrine_contracts` stores on desktop (kept in-memory here only, not
     * persisted - see this object's own docstring, scope decision 2). */
    data class SyncedContract(
        val contractId: Long,
        val status: String,
        val title: String?,
        val price: Double?,
        val matchedFittingId: String?,
        val matchScore: Double,
        val deviations: List<DoctrineValidation.DeviationRow>,
        val validationStatus: String,
    )

    /** One permanent history row - [DoctrineContractHistoryEntity]'s
     * pre-persistence shape (`toEntity()`/`fromEntity()` live on that Room
     * type, see `DoctrineContractHistoryEntity.kt`). */
    data class ContractHistoryRow(
        val contractId: Long,
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

    data class SyncOutcome(
        val activeContracts: List<SyncedContract>,
        val historyRows: List<ContractHistoryRow>,
        val noHullMatchCount: Int,
    )

    /** One already-fetched raw contract - [EsiContract]'s fields, unwrapped
     * from ESI's own schema so [matchContracts] (the pure core this whole
     * object builds on) never depends on the network types directly and can
     * be exercised with plain fixtures in a test (same "match desktop's own
     * stub-the-boundary test shape" reasoning `test_doctrine_esi_sync.py`
     * already follows). */
    data class RawContract(
        val contractId: Long,
        val type: String,
        val status: String,
        val title: String?,
        val startLocationId: Long?,
        val issuerId: Long,
        val price: Double?,
        val dateIssued: String,
        val dateCompleted: String?,
        val acceptorId: Long?,
    )

    /** engine.py's `match_and_validate_contract` + esi_sync.py's
     * `sync_contracts` prefilter/dispatch loop, combined into one pure
     * function: given every contract this character's token can currently
     * see (already fetched) plus each syncable/finished one's already-
     * fetched item list, returns the currently-active matched contracts
     * (for the Stockpile contract-target multiplier) and the finished ones
     * that belong in permanent history.
     *
     * Pre-filter (esi_sync.py's `_passes_prefilter`/`_passes_history_filter`,
     * `_issued_by_own_identity`): `item_exchange` type, at `structureId`,
     * issued by `characterId` itself - no corp-issuer check (scope decision
     * 1 above, this build has no corporation_id to check against anyway).
     * A contract whose items failed to fetch (`itemsByContractId` has no
     * entry for it) is silently dropped this run, same as desktop's own
     * "never write a contract with no items, that would look like a real
     * invalid doctrine violation instead of a transient data gap" rule -
     * it's simply retried on the next [sync] call, nothing to report here
     * since the caller already knows which contract ids it fetched. */
    fun matchContracts(
        characterId: Long,
        structureId: Long,
        rawContracts: List<RawContract>,
        itemsByContractId: Map<Long, List<DoctrineValidation.ContractItemRow>>,
        candidates: List<MatchCandidate>,
        cargoTolerancePctDefault: Double,
        groupIdOf: (Int) -> Int? = { null },
        slotOf: (Int) -> String? = { null },
    ): SyncOutcome {
        fun passesPrefilter(c: RawContract, statuses: Set<String>) =
            c.type == CONTRACT_TYPE_ITEM_EXCHANGE && c.status in statuses &&
                c.startLocationId == structureId && c.issuerId == characterId

        val active = mutableListOf<SyncedContract>()
        val history = mutableListOf<ContractHistoryRow>()
        var noHullMatchCount = 0

        for (raw in rawContracts) {
            val isSyncable = passesPrefilter(raw, SYNCABLE_STATUSES)
            val isFinished = !isSyncable && passesPrefilter(raw, FINISHED_STATUSES)
            if (!isSyncable && !isFinished) continue

            val items = itemsByContractId[raw.contractId] ?: continue
            val (fittingId, score, deviations, status) = matchAndValidate(
                raw.contractId, raw.title, items, candidates, cargoTolerancePctDefault, groupIdOf, slotOf,
            )

            if (isSyncable) {
                if (status == NO_HULL_MATCH) {
                    noHullMatchCount++
                    continue
                }
                active.add(SyncedContract(raw.contractId, raw.status, raw.title, raw.price, fittingId, score, deviations, status))
            } else {
                // GitHub issue #19 parity: a finished contract that never
                // matched any doctrine fitting isn't a doctrine sale at all
                // (someone else's unrelated item_exchange at the same
                // structure, or a genuine near-miss below MATCH_THRESHOLD)
                // and must not clutter permanent history.
                if (fittingId == null) continue
                val fitting = candidates.first { it.fitting.fittingId == fittingId }.fitting
                history.add(ContractHistoryRow(
                    raw.contractId, fittingId, fitting.name, fitting.hullTypeId, raw.title, raw.price,
                    raw.acceptorId, raw.dateIssued, raw.dateCompleted, raw.status,
                ))
            }
        }
        return SyncOutcome(active, history, noHullMatchCount)
    }

    /** storage.py's `upsert_doctrine_contract_history` ON CONFLICT DO UPDATE,
     * as a pure pre-persistence merge: `incoming` rows replace any existing
     * row with the same `contractId` (last-sync-wins on every field, which
     * is all this needs since - unlike desktop - no field here is ever
     * resolved gradually across syncs, see this object's own docstring on
     * why acceptor-name resolution isn't ported), everything else in
     * `existing` is carried forward untouched. [DoctrineContractHistoryRepository]
     * calls this before writing to Room, so "don't duplicate an
     * already-seen contract" is a plain, fully-testable Kotlin map keyed by
     * `contractId` rather than relying on a SQL `ON CONFLICT` clause this
     * pure-JVM test harness can't exercise directly (see this feature's own
     * verification notes). */
    fun mergeHistoryRows(existing: List<ContractHistoryRow>, incoming: List<ContractHistoryRow>): List<ContractHistoryRow> {
        val byId = existing.associateByTo(LinkedHashMap()) { it.contractId }
        for (row in incoming) byId[row.contractId] = row
        return byId.values.toList()
    }

    /** The IO half: fetches this character's currently-visible contracts +
     * (per syncable/finished candidate) their item lists from ESI, then
     * hands everything to [matchContracts]. A per-contract item-fetch
     * failure is swallowed the same way esi_sync.py's own `_fetch_items`
     * degrades (that contract is simply dropped from this run, see
     * [matchContracts]'s own docstring) rather than aborting the whole
     * sync over one bad contract. */
    suspend fun sync(
        esi: EsiClient,
        characterId: Long,
        accessToken: String,
        structureId: Long,
        candidates: List<MatchCandidate>,
        cargoTolerancePctDefault: Double = 0.9,
        groupIdOf: (Int) -> Int? = { null },
        slotOf: (Int) -> String? = { null },
    ): SyncOutcome {
        val contracts = esi.characterContracts(characterId, accessToken)
        val raw = contracts.map { it.toRawContract() }
        val relevant = raw.filter {
            it.type == CONTRACT_TYPE_ITEM_EXCHANGE && it.issuerId == characterId && it.startLocationId == structureId &&
                (it.status in SYNCABLE_STATUSES || it.status in FINISHED_STATUSES)
        }
        val itemsByContractId = mutableMapOf<Long, List<DoctrineValidation.ContractItemRow>>()
        for (c in relevant) {
            try {
                val items = esi.characterContractItems(characterId, c.contractId, accessToken)
                itemsByContractId[c.contractId] = items.map { i ->
                    DoctrineValidation.ContractItemRow(c.contractId, i.recordId, i.typeId, i.quantity.toDouble(), i.isIncluded, i.isSingleton)
                }
            } catch (e: Exception) {
                // Dropped this run, retried next sync - see matchContracts's docstring.
            }
        }
        return matchContracts(characterId, structureId, raw, itemsByContractId, candidates, cargoTolerancePctDefault, groupIdOf, slotOf)
    }

    /** How many currently-active contracts each fitting has at `valid`
     * status - engine.py's `fitting_status`'s own `valid = sum(1 for c in
     * contracts if c.validation_status == "valid" and c.status !=
     * "expired")`, the exact input [StockpileStatus.computeRows]'s
     * `validContractsByFitting` (the GitHub issue #36 contract-target
     * multiplier) needs. An expired contract stops counting toward its
     * fitting's target the moment it expires, even though [SYNCABLE_STATUSES]
     * still includes "expired" (a contract that just expired should stay
     * visible with a clear marker rather than silently vanishing, same
     * reasoning constants.py's own docstring gives - not relevant here since
     * this platform has no "currently outstanding" listing UI, but the
     * counting rule itself is still correct to keep). */
    fun validContractCounts(activeContracts: List<SyncedContract>): Map<String, Int> =
        activeContracts
            .filter { it.validationStatus == DoctrineValidation.VALIDATION_VALID && it.status != "expired" }
            .mapNotNull { it.matchedFittingId }
            .groupingBy { it }
            .eachCount()

    private fun EsiContract.toRawContract() = RawContract(
        contractId = contractId, type = type, status = status, title = title, startLocationId = startLocationId,
        issuerId = issuerId, price = price, dateIssued = dateIssued, dateCompleted = dateCompleted, acceptorId = acceptorId,
    )
}
