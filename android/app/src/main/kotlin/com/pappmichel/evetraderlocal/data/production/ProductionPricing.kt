package com.pappmichel.evetraderlocal.data.production

import com.pappmichel.evetraderlocal.data.auth.TokenManager
import com.pappmichel.evetraderlocal.data.esi.EsiClient
import com.pappmichel.evetraderlocal.data.esi.OrderStats
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/** What one unit of an item costs to acquire right now, and where from - a
 * Kotlin port of the desktop build's production/pricing.py, scoped to just
 * its buy-side comparison (`buy_price`/`buy_source`/`_candidate_prices`,
 * ported here as [buyPrice]/[buySource]/[candidatePrices]).
 *
 * Why this slice and not the rest of Production: every other Production
 * feature (Build Candidates, Planner, Ship Margins, Item Margin) needs a
 * *build* cost, which means walking a blueprint's bill of materials -
 * industryActivityMaterials/industryActivityProducts/industryActivity SDE
 * tables the Android SDE cache does not have yet (confirmed out of scope
 * when that cache was built - see data/sde/SdeRepository.kt's own
 * docstring and this task's own instructions). pricing.py's buy-side
 * comparison is the one piece of desktop Production that needs no
 * blueprint data at all - just two order books - so it is the one genuine
 * vertical slice portable without also adding a chunk of new SDE schema.
 * The "Item Lookup" screen (ItemLookupScreen.kt) surfaces exactly this:
 * "what does it cost to buy one unit of this item right now, home or
 * Jita?" - not desktop Item Lookup/Item Margin's fuller build-vs-buy
 * verdict, which needs the missing BOM data.
 *
 * Simplifications vs. desktop pricing.py, documented here rather than
 * silently:
 * - No Goonmetrics (appraise.gnf.lt) fallback. Desktop falls back to a
 *   Goonmetrics current-price snapshot when ESI has no producer character
 *   logged in, docking access fails, or the API is down; this port is
 *   ESI-only for both sides - a failed/absent lookup just returns no quote
 *   for that side rather than trying a second data source. Add the
 *   fallback later if a real gap shows up in practice; GoonmetricsClient
 *   already exists in this app for the Trading side (see
 *   StationTradingShortlistScreen.kt) and would need only its production
 *   equivalent of a market slug.
 * - Uses ESI's [OrderStats] (sellPercentile) directly as the price source
 *   for both sides, rather than desktop's shared `CurrentPrice` shape that
 *   also unifies in a Goonmetrics reading - there's only one source here,
 *   so the extra type has no purpose.
 * - `system_cost_indices_for` (job-fee cost-index lookup) is not ported -
 *   it only ever feeds `_job_cost_rate`, which is itself part of the build
 *   cost math this slice deliberately excludes.
 */

// ---------------------------------------------------------------- pure math

/** Landed unit price per source, for whichever sources actually have a
 * sell order listed. Both are inflated by the buy-side broker fee (same
 * character, same rate, wherever they buy); Jita's additionally carries
 * the haul cost of moving the item home, which a home purchase doesn't
 * need by definition. Mirrors pricing.py's `_candidate_prices` exactly,
 * including "a zero sell price means nothing listed, not free" (an
 * ESI OrderStats with no sell orders at all reports sellPercentile as
 * null OR the summarized 0.0 - both are treated as "not listed" here). */
fun candidatePrices(
    typeId: Int,
    home: Map<Int, OrderStats>,
    jita: Map<Int, OrderStats>,
    volumeM3: Double?,
    cfg: ProductionConfig,
): Map<String, Double> {
    val candidates = mutableMapOf<String, Double>()
    val homeSell = home[typeId]?.sellPercentile
    if (homeSell != null && homeSell > 0) {
        candidates["home"] = homeSell * (1 + cfg.jitaBuyBrokerFee)
    }
    val jitaSell = jita[typeId]?.sellPercentile
    if (jitaSell != null && jitaSell > 0) {
        candidates["jita"] = jitaSell * (1 + cfg.jitaBuyBrokerFee) + cfg.haulCostPerM3 * (volumeM3 ?: 0.0)
    }
    return candidates
}

/** Which market [buyPrice] sources `typeId` from - "home" or "jita",
 * whichever is cheaper landed, or whichever of the two has a sell order at
 * all, else null. Uses the same [candidatePrices] helper [buyPrice] does,
 * so the two can never disagree about the chosen source. */
fun buySource(
    typeId: Int,
    home: Map<Int, OrderStats>,
    jita: Map<Int, OrderStats>,
    volumeM3: Double? = null,
    cfg: ProductionConfig = ProductionConfig(),
): String? = candidatePrices(typeId, home, jita, volumeM3, cfg).minByOrNull { it.value }?.key

/** Cheapest way to acquire one unit of `typeId` right now: the home sell
 * price, or the Jita sell price plus haul cost, whichever is lower. Null
 * if neither market has a sell order for it. */
fun buyPrice(
    typeId: Int,
    home: Map<Int, OrderStats>,
    jita: Map<Int, OrderStats>,
    volumeM3: Double? = null,
    cfg: ProductionConfig = ProductionConfig(),
): Double? = candidatePrices(typeId, home, jita, volumeM3, cfg).values.minOrNull()

// ------------------------------------------------------------------- sourcing

/** Producer-character role prefix - the desktop build's
 * production/esi_sync.py `PRODUCER_ROLE_PREFIX`. Producer characters are
 * their own login role here, not Trading's "seller"/"buyer": the character
 * who can dock and read the home structure's order book isn't necessarily
 * the one running sell orders there. No login flow for this role exists
 * in the Android app yet - homePrices below degrades to "no home quotes"
 * (same as an unset homeLocationId) until one is added, which is why it's
 * not wired into ItemLookupScreen's happy path yet either; see that
 * screen's own docstring. */
const val PRODUCER_ROLE_PREFIX = "producer"

/** Current home-structure quotes for `typeIds`, from the live order book
 * of `cfg.homeLocationId` via any logged-in producer character (one full-
 * book download regardless of how many type_ids are asked for - ESI's
 * structure endpoint has no type filter). Tries every producer character
 * in turn, same as pricing.py's `home_prices`; a token that fails (no
 * docking access, expired, ESI outage) is skipped rather than aborting the
 * whole lookup. Returns an empty map (never throws) when `homeLocationId`
 * is unset or every producer character fails - "no home quote" is a
 * legitimate, expected outcome, not an error. */
suspend fun homePrices(
    typeIds: List<Int>,
    cfg: ProductionConfig,
    tokenManager: TokenManager,
    esi: EsiClient,
): Map<Int, OrderStats> = withContext(Dispatchers.IO) {
    val structureId = cfg.homeLocationId
    if (typeIds.isEmpty() || structureId == null) return@withContext emptyMap()
    for (record in tokenManager.listRecords(PRODUCER_ROLE_PREFIX)) {
        val result = runCatching {
            val token = tokenManager.getToken(record.role)
            esi.structureOrderStatsBulk(structureId, typeIds, token.accessToken)
        }
        result.getOrNull()?.let { return@withContext it }
    }
    emptyMap()
}

/** Same ESI-only shape as [homePrices], for the public Jita region order
 * book (no auth needed) - the counterpart of pricing.py's `jita_prices`.
 * Callers should pass a small, bounded `typeIds` list: ESI has no bulk
 * region-order endpoint, so [EsiClient.regionOrderStatsBulk] is one
 * request per type_id. */
suspend fun jitaPrices(
    typeIds: List<Int>,
    jitaRegionId: Int,
    esi: EsiClient,
): Map<Int, OrderStats> {
    if (typeIds.isEmpty()) return emptyMap()
    return esi.regionOrderStatsBulk(jitaRegionId, typeIds)
}
