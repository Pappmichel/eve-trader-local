package com.pappmichel.evetraderlocal.data.trading

import com.pappmichel.evetraderlocal.data.esi.OrderStats

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
 *     Decision      = Inactive | Missing ID | No market data | Skip |
 *                     Already ordered | Import
 *
 * Not ported yet (see ROADMAP.md's Android section): Profit / Day (needs
 * Goonmetrics region history for real market-wide average daily volume,
 * same "not sell_volume/order-book depth, not this trader's own realized
 * sales" caveats as shortlist.py's `average_market_daily_volume`), the
 * Goonmetrics current-price fallback when no seller token is available,
 * buyer-covered tracking (always false here - a buyer already having the
 * item in inventory or on a standing buy order isn't checked; the
 * seller's own open sell orders ARE, via `ownOrdersByItem` -
 * ShortlistScreen.kt's `EsiClient.characterOrders` call, mirroring
 * `own_orders.py`'s `fetch_own_sell_orders` - so "Already ordered" is
 * reachable through that path), and auto-add/prune from Candidate
 * Discovery.
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
    cfg: TradingConfig, buyerAlreadyCovered: Boolean = false,
): ShortlistRow {
    if (item.itemId == 0) {
        return ShortlistRow(
            item = item.item, category = item.category, landedCost = null, netSell = null, sellVolume = null,
            ownOrdersRemaining = ownOrdersRemaining, profitPerUnit = null, margin = null, profitPerM3 = null,
            decision = decision(item.active, item.itemId, null, null, null, ownOrdersRemaining, buyerAlreadyCovered, cfg),
            active = item.active,
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
        active = item.active,
    )
}

fun evaluateShortlist(
    items: List<ShortlistItem>, jitaStatsByItem: Map<Int, OrderStats>, structureStatsByItem: Map<Int, OrderStats>,
    cfg: TradingConfig, ownOrdersByItem: Map<Int, Double> = emptyMap(),
): List<ShortlistRow> = items.map { item ->
    evaluateShortlistItem(
        item, ownOrdersByItem[item.itemId] ?: 0.0, jitaStatsByItem[item.itemId], structureStatsByItem[item.itemId], cfg,
    )
}

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
