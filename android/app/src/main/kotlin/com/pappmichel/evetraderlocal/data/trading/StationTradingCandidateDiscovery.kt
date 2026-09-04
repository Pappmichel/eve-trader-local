package com.pappmichel.evetraderlocal.data.trading

import com.pappmichel.evetraderlocal.data.esi.EsiClient
import com.pappmichel.evetraderlocal.data.esi.OrderStats
import com.pappmichel.evetraderlocal.data.history.CurrentPrice
import com.pappmichel.evetraderlocal.data.history.GoonmetricsClient

/** Station Trading candidate discovery - a Kotlin port of the desktop
 * build's station_trading/candidate_discovery.py, the same two-stage shape
 * Trading's own history_backtest.py -> shortlist.py split uses: one cheap
 * Goonmetrics-wide pass narrows the whole Jita market down to a shortlist,
 * then a bounded live-ESI pass confirms only what made the cut.
 *
 * `rankCandidates` does one Goonmetrics current-price dump for the whole
 * Jita market (already a single HTTP call - see `GoonmetricsClient.
 * currentPrices`) and ranks by bid-ask spread + real daily-traded volume -
 * never touches ESI. `confirmLive` is the only place this file calls
 * `EsiClient.regionOrderStatsBulk`, and only against an already-narrowed,
 * persisted shortlist - no bulk-region endpoint exists, so pricing
 * "everything Goonmetrics knows about Jita" would mean one ESI call per
 * item across the whole market. */

/** One discovered candidate - the Kotlin counterpart of the dict
 * `discover_candidates` returns on desktop. */
data class StationTradingCandidate(
    val typeId: Int,
    val buy: Double,
    val sell: Double,
    val spreadPct: Double,
    val avgDailyVolume: Double,
)

/** Every item whose current Goonmetrics spread clears
 * `cfg.minSpreadThreshold` and whose real average daily traded volume
 * (Goonmetrics region history, not order-book depth - depth is the wrong
 * signal, see the desktop module's own docstring) clears
 * `cfg.minDailyVolume`. Sorted by spread * volume, richest first.
 *
 * Unlike Production's discovery, which always caps at top_n=200, there is
 * no always-on cap here - `minDailyVolume` is the real noise filter
 * (confirmed live on desktop: it already brings a real candidate count from
 * 10,000+ down to ~1,300, a real opportunity set worth seeing in full).
 * `topN` only caps when explicitly passed, or when
 * `cfg.enforceShortlistCap` is on (`cfg.maxActiveShortlistItems`) - the same
 * "off by default, user opts in" shape as `TradingConfig`'s own
 * `enforceShortlistCap`/`maxActiveShortlistItems`.
 *
 * `movementByType` is `{type_id: [daily units traded, ...]}`, already
 * filtered to only the type_ids that passed the spread gate - the caller
 * (`discoverCandidates` below) fetches history for just those, the same
 * "cheap first pass narrows the expensive second pass" order the desktop
 * version follows. */
fun rankCandidates(
    prices: List<CurrentPrice>,
    movementByType: Map<Int, List<Double>>,
    cfg: StationTradingConfig,
    topN: Int? = null,
): List<StationTradingCandidate> {
    val spreadHits = prices.mapNotNull { p ->
        if (p.buy <= 0 || p.sell <= 0 || p.buy >= p.sell) return@mapNotNull null
        val spread = (p.sell - p.buy) / p.sell
        if (spread < cfg.minSpreadThreshold) return@mapNotNull null
        StationTradingCandidate(p.typeId, p.buy, p.sell, spread, 0.0)
    }
    if (spreadHits.isEmpty()) return emptyList()

    val results = spreadHits.mapNotNull { hit ->
        val days = movementByType[hit.typeId]
        val avgDailyVolume = if (days.isNullOrEmpty()) 0.0 else days.sum() / days.size
        if (avgDailyVolume < cfg.minDailyVolume) return@mapNotNull null
        hit.copy(avgDailyVolume = avgDailyVolume)
    }.sortedByDescending { it.spreadPct * it.avgDailyVolume }

    val effectiveTopN = topN ?: if (cfg.enforceShortlistCap) cfg.maxActiveShortlistItems else null
    return if (effectiveTopN != null) results.take(effectiveTopN) else results
}

/** Orchestration half of `rankCandidates`: one Goonmetrics current-price
 * dump for the whole Jita market, then Goonmetrics history *only* for the
 * type_ids that already cleared the spread gate. */
suspend fun discoverCandidates(
    goonmetrics: GoonmetricsClient,
    jitaRegionId: Int,
    cfg: StationTradingConfig,
    topN: Int? = null,
): List<StationTradingCandidate> {
    val prices = goonmetrics.currentPrices("jita")
    val spreadHitIds = prices.mapNotNull { p ->
        if (p.buy <= 0 || p.sell <= 0 || p.buy >= p.sell) return@mapNotNull null
        val spread = (p.sell - p.buy) / p.sell
        if (spread < cfg.minSpreadThreshold) null else p.typeId
    }
    if (spreadHitIds.isEmpty()) return emptyList()

    val movementByType = mutableMapOf<Int, MutableList<Double>>()
    for (point in goonmetrics.priceHistoryChunked(jitaRegionId, spreadHitIds)) {
        movementByType.getOrPut(point.typeId) { mutableListOf() }.add(point.movement)
    }
    return rankCandidates(prices, movementByType, cfg, topN)
}

/** Live ESI order-book confirmation for an already-bounded set of type_ids
 * (the persisted shortlist, never the whole market - see this file's own
 * docstring). Jita's trade hub is a public NPC station, so no
 * character/token is needed. */
suspend fun confirmLive(
    typeIds: List<Int>,
    jitaRegionId: Int,
    client: EsiClient,
): Map<Int, OrderStats> {
    if (typeIds.isEmpty()) return emptyMap()
    return client.regionOrderStatsBulk(jitaRegionId, typeIds)
}
