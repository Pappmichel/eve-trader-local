package com.pappmichel.evetraderlocal.data.trading

import com.pappmichel.evetraderlocal.data.esi.OrderStats
import com.pappmichel.evetraderlocal.data.history.HistoryPoint

/** Per-shortlist-row margin calculation, ported from the desktop build's
 * shortlist.py (see that file's own module docstring for the full
 * formula/decision-precedence writeup):
 *
 *     Landed Cost   = Jita sell percentile x (1 + jita_buy_broker_fee)
 *                     + volume_m3 x import_cost_per_m3
 *     Net Sell      = Structure sell percentile x structure_sell_haircut
 *     Profit / Unit = Net Sell - Landed Cost
 *     Margin        = Profit / Landed Cost
 *     Profit / m3   = Profit / volume_m3
 *     Profit / Day  = Profit / Unit x avg_daily_volume
 *     Decision      = Inactive | Missing ID | No market data | Skip |
 *                     Already ordered | Import
 *
 * Not ported yet (see ROADMAP.md's Android section): the Goonmetrics
 * current-price fallback when no seller token is available. Auto-add/prune
 * from Candidate Discovery *is* ported - see `HistoryBacktest.scoreCandidate`
 * (the hit-rate/avg-movement filter) plus CandidateDiscoveryScreen.kt's
 * "Add Recommended" action and ShortlistScreen.kt's "Prune" action.
 * "Already ordered" is reachable through two independent signals, both
 * wired from ShortlistScreen.kt: the seller's own open sell orders
 * (`ownOrdersByItem`, `EsiClient.characterOrders`, mirroring
 * `own_orders.py`'s `fetch_own_sell_orders`) and the buyer already being
 * covered (`buyerAlreadyCoveredIds`, mirroring `fetch_buyer_already_covered`
 * - an open buy order in Jita/at the structure, or existing inventory at a
 * Jita station/the structure).
 */

const val NO_MARKET_DATA_DECISION = "No market data"
const val SKIP_DECISION = "Skip"

private fun decision(
    active: Boolean, itemId: Int, sellVolume: Double?, profit: Double?, margin: Double?,
    ownOrdersRemaining: Double, buyerAlreadyCovered: Boolean, cfg: TradingConfig,
): String {
    if (!active) return "Inactive"
    if (itemId == 0) return "Missing ID"
    val haveData = sellVolume != null && profit != null && margin != null
    if (!haveData) return NO_MARKET_DATA_DECISION
    if (sellVolume!! > 0 && profit!! > cfg.minProfitThreshold && margin!! >= cfg.minMarginThreshold) {
        return if (ownOrdersRemaining > 0 || buyerAlreadyCovered) "Already ordered" else "Import"
    }
    return SKIP_DECISION
}

fun evaluateShortlistItem(
    item: ShortlistItem, ownOrdersRemaining: Double, jitaStats: OrderStats?, structureStats: OrderStats?,
    cfg: TradingConfig, buyerAlreadyCovered: Boolean = false, avgDailyVolume: Double? = null,
): ShortlistRow {
    if (item.itemId == 0) {
        return ShortlistRow(
            item = item.item, category = item.category, landedCost = null, netSell = null, sellVolume = null,
            ownOrdersRemaining = ownOrdersRemaining, profitPerUnit = null, margin = null, profitPerM3 = null,
            decision = decision(item.active, item.itemId, null, null, null, ownOrdersRemaining, buyerAlreadyCovered, cfg),
            active = item.active, avgDailyVolume = avgDailyVolume,
        )
    }

    // Deliberately sellPercentile (the ask - an instant-buy fill), not
    // buyPercentile, even though real purchases go through a standing buy
    // order - see shortlist.py's own comment on this for why.
    val jitaSell = jitaStats?.sellPercentile
    val importCost = item.volumeM3 * cfg.importCostPerM3
    val landedCost = jitaSell?.let { it * (1 + cfg.jitaBuyBrokerFee) + importCost }
    val netSell = structureStats?.sellPercentile?.let { it * cfg.structureSellHaircut }
    val sellVolume = structureStats?.sellVolume

    val profit = if (netSell != null && landedCost != null) netSell - landedCost else null
    val margin = if (profit != null && landedCost != null && landedCost != 0.0) profit / landedCost else null
    val profitM3 = if (profit != null && item.volumeM3 > 0) profit / item.volumeM3 else null

    return ShortlistRow(
        item = item.item, category = item.category, landedCost = landedCost, netSell = netSell,
        sellVolume = sellVolume, ownOrdersRemaining = ownOrdersRemaining, profitPerUnit = profit,
        margin = margin, profitPerM3 = profitM3,
        decision = decision(item.active, item.itemId, sellVolume, profit, margin, ownOrdersRemaining, buyerAlreadyCovered, cfg),
        active = item.active, avgDailyVolume = avgDailyVolume,
    )
}

fun evaluateShortlist(
    items: List<ShortlistItem>, jitaStatsByItem: Map<Int, OrderStats>, structureStatsByItem: Map<Int, OrderStats>,
    cfg: TradingConfig, ownOrdersByItem: Map<Int, Double> = emptyMap(),
    buyerAlreadyCoveredIds: Set<Int> = emptySet(), avgDailyVolumeByItem: Map<Int, Double> = emptyMap(),
): List<ShortlistRow> = items.map { item ->
    evaluateShortlistItem(
        item, ownOrdersByItem[item.itemId] ?: 0.0, jitaStatsByItem[item.itemId], structureStatsByItem[item.itemId], cfg,
        buyerAlreadyCovered = item.itemId in buyerAlreadyCoveredIds,
        avgDailyVolume = avgDailyVolumeByItem[item.itemId],
    )
}

/** Real average daily *market-wide* traded quantity per type_id, from
 * Goonmetrics reference-region history - the Kotlin counterpart of
 * shortlist.py's `average_market_daily_volume`. Averages `movement` (a
 * genuine unit count, see `HistoryPoint`) over every day Goonmetrics
 * returned, not a recent slice - same "average over every day available"
 * approach `HistoryBacktest` takes against the same reference region.
 *
 * This is what `ShortlistRow.avgDailyVolume`, and therefore Profit / Day,
 * is computed from. Deliberately neither `sellVolume`/order-book depth (a
 * single seller parking a large batch of a never-actually-sold item would
 * inflate Profit / Day purely from the listed quantity) nor the trader's
 * own realized sales (a prospective import candidate has, by definition,
 * never been sold by this trader) - the same two desktop GitHub issues
 * (#51, #100) shortlist.py's own docstring documents.
 *
 * A type_id Goonmetrics returned no history for is simply absent from the
 * result (the caller then leaves avgDailyVolume null) - never estimated
 * from something else. */
fun averageMarketDailyVolume(historyPoints: List<HistoryPoint>): Map<Int, Double> =
    historyPoints.groupBy { it.typeId }
        .mapValues { (_, points) -> points.sumOf { it.movement } / points.size }

/** Headline counts for one evaluation run - `avgMargin` averages only
 * positive margins, mirroring shortlist.py's `summary_counts`. */
data class ShortlistSummary(
    val importCandidates: Int, val alreadyOrdered: Int, val skipped: Int,
    val positiveMargin: Int, val avgMargin: Double?,
)

fun summaryCounts(rows: List<ShortlistRow>): ShortlistSummary {
    val margins = rows.mapNotNull { it.margin }.filter { it > 0 }
    return ShortlistSummary(
        importCandidates = rows.count { it.decision == "Import" },
        alreadyOrdered = rows.count { it.decision == "Already ordered" },
        skipped = rows.count { "Skip" in it.decision },
        positiveMargin = margins.size,
        avgMargin = if (margins.isNotEmpty()) margins.sum() / margins.size else null,
    )
}

/** One row of shortlist.py's `top_imports_by_daily_profit` output - a plain
 * dict there, a data class here for the same "checked field names" reason
 * `MarginTrend` already gives HistoryBacktest.kt's output. */
data class TopImportRow(
    val item: String, val profitPerUnit: Double, val margin: Double,
    val avgDailyVolume: Double, val maxProfitPerDay: Double, val decision: String,
)

/** Best items by Profit / Day = profitPerUnit x avgDailyVolume - real
 * market-wide traded quantity from Goonmetrics region history
 * (`averageMarketDailyVolume`), NOT order-book depth and NOT the trader's
 * own realized sales - mirrors shortlist.py's `top_imports_by_daily_profit`.
 *
 * This is a theoretical ceiling: "what a whole day of market turnover in
 * this item is worth", not a claim about what one seller could personally
 * capture. A tiny-volume, huge-per-unit item showing an enormous number is
 * mathematically correct for that question - don't cap or filter the
 * multiplication itself.
 *
 * A row with no avgDailyVolume yet (Goonmetrics has no history for it) is
 * excluded here rather than estimated from something else. */
fun topImportsByDailyProfit(rows: List<ShortlistRow>, topN: Int = 10): List<TopImportRow> =
    rows.mapNotNull { r ->
        val profit = r.profitPerUnit
        val volume = r.avgDailyVolume
        val margin = r.margin
        if (profit == null || volume == null || margin == null) null
        else TopImportRow(
            item = r.item, profitPerUnit = profit, margin = margin,
            avgDailyVolume = volume, maxProfitPerDay = profit * volume, decision = r.decision,
        )
    }.sortedByDescending { it.maxProfitPerDay }.take(topN)
