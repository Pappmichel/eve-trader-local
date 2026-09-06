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

/** Hangar divisions an item can sit in that don't count as usable stock -
 * mirrors `storage.NON_STOCK_LOCATION_FLAGS` exactly: Asset Safety needs a
 * paid retrieval trip first, and the delivery/market flags are items in
 * transit or already sold, not material a job can start with today. */
private val NON_STOCK_LOCATION_FLAGS = setOf("AssetSafety", "Deliveries", "CorpDeliveries", "CorpMarket")

/** Real, live implementation of [LocationStockSource] - Logistics'
 * "how much of this is sitting at *this specific* structure" question,
 * which [LiveStockSource]'s own corp-wide, no-location-filter
 * [StockSource.ownedQuantity] cannot answer (see `LogisticsEngine.kt`'s own
 * module docstring for why this is a genuinely separate interface).
 *
 * **Character assets only, not corp** - the exact same gap
 * [LiveStockSource] itself documents (this app's `EsiClient` has no
 * `/corporations/{id}/assets/` method yet), inherited here rather than
 * re-derived since both classes are missing the identical underlying ESI
 * endpoint. A failed lookup for one producer character is skipped rather
 * than aborting the whole read, matching [LiveStockSource]'s own
 * precedent. */
class LiveLocationStockSource(
    private val tokenManager: TokenManager,
    private val esi: EsiClient,
) : LocationStockSource {
    override suspend fun stockAt(typeId: Int, locationId: Long): Double {
        var total = 0.0
        for (record in tokenManager.listRecords(PRODUCER_ROLE_PREFIX)) {
            val assets = runCatching { esi.characterAssets(record.characterId, record.accessToken) }.getOrNull()
                ?: continue
            total += assets
                .filter { it.typeId == typeId && it.locationId == locationId && it.locationFlag !in NON_STOCK_LOCATION_FLAGS }
                .sumOf { it.quantity }
        }
        return total
    }
}
