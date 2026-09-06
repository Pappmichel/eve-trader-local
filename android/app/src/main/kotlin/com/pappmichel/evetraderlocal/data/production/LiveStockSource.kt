package com.pappmichel.evetraderlocal.data.production

import com.pappmichel.evetraderlocal.data.auth.TokenManager
import com.pappmichel.evetraderlocal.data.esi.EsiClient

/** Real, live implementation of [StockSource] - the "for every logged-in
 * producer character" ESI read [StockSource]'s own KDoc anticipates,
 * following the exact "live, not synced" pattern `ProductionJobs.kt`/
 * `OwnedBlueprints.kt` already established for this app's other ESI-asset-
 * shaped Production views (fetch fresh per screen visit, merge across every
 * currently logged-in "Producer" character, no local persistence).
 *
 * **Character assets only, not corp.** Desktop's `storage.
 * esi_stock_at_location` deliberately reads both `character_assets` *and*
 * `corp_assets` ("do I own this anywhere" is corp-wide, not scoped to one
 * curated structure - see `ProductionEngine.kt`'s own module docstring).
 * This app's `EsiClient` has no `/corporations/{id}/assets/` method at all
 * yet (unlike blueprints, where the same `/characters/{id}/blueprints/`
 * endpoint already surfaces visible corp-hangar rows for free - see
 * `OwnedBlueprints.kt`'s own docstring) - adding one is real, separate ESI-
 * client scope this pass doesn't need to reach `plan_production`'s core
 * algorithm, so [ownedQuantity] only ever sums personal character assets.
 * Add a corp-assets `EsiClient` method and fold its results in here
 * whenever a caller actually needs corp-hangar stock counted.
 *
 * A failed lookup for one producer character (expired/un-refreshable token,
 * missing scope, ESI outage) is skipped rather than aborting the whole
 * read, matching [ProductionPricing.homePrices]'s own "a token that fails
 * doesn't abort the whole lookup" precedent. */
class LiveStockSource(
    private val tokenManager: TokenManager,
    private val esi: EsiClient,
) : StockSource {
    /** Statuses that still count as "in progress" for `plan_production`'s
     * own "as good as in stock" sizing purpose - mirrors
     * `storage.esi_incoming_industry_qty`'s own filter (delivered/cancelled/
     * reverted jobs are done, one way or another, and no longer represent
     * future output). */
    private val incomingStatuses = setOf("active", "paused", "ready")

    override suspend fun ownedQuantity(typeId: Int): Double {
        var total = 0.0
        for (record in tokenManager.listRecords(PRODUCER_ROLE_PREFIX)) {
            val assets = runCatching { esi.characterAssets(record.characterId, record.accessToken) }.getOrNull()
                ?: continue
            total += assets.filter { it.typeId == typeId }.sumOf { it.quantity }
        }
        return total
    }

    override suspend fun incomingIndustryRuns(typeId: Int): Double {
        var runs = 0.0
        for (record in tokenManager.listRecords(PRODUCER_ROLE_PREFIX)) {
            val jobs = runCatching { esi.characterIndustryJobs(record.characterId, record.accessToken) }.getOrNull()
                ?: continue
            runs += jobs.filter { it.productTypeId == typeId && it.status in incomingStatuses }
                .sumOf { it.runs.toDouble() }
        }
        return runs
    }
}
