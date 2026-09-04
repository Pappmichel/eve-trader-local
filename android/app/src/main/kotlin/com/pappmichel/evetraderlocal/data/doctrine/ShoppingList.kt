package com.pappmichel.evetraderlocal.data.doctrine

import com.pappmichel.evetraderlocal.data.esi.EsiClient
import com.pappmichel.evetraderlocal.data.esi.OrderStats
import com.pappmichel.evetraderlocal.data.history.CurrentPrice
import com.pappmichel.evetraderlocal.data.history.GoonmetricsClient
import com.pappmichel.evetraderlocal.data.trading.TradingConfig

/** Doctrine -> Shopping List: every item with a real Stockpile Status
 * shortfall, with a Buy-C-J-vs-Buy-Jita price comparison - the Android
 * counterpart of `doctrine/engine.py`'s `shopping_list_rows`/
 * `_shopping_prices`.
 *
 * **Deliberate scope cut vs. desktop: no "Build" option.** Desktop's real
 * Shopping List is a three-way Build-vs-Buy-C-J-vs-Buy-Jita comparison,
 * reusing Production's real recursive build-cost engine
 * (`production.engine.unit_cost_detail` - full blueprint-material-tree
 * walk, ME/TE, T2 invention decryptor choice, system cost indices). This
 * platform's own Production port (`data/production/ProductionBuildCost.kt`)
 * is intentionally NOT that engine - it's Ship Margins' single-item,
 * non-recursive build-cost estimate (BOM one level deep, see that file's
 * own docstring), which has no meaningful way to answer "what would it cost
 * to build N units of an arbitrary shortfall item, following its own BOM
 * recursively" the way `unit_cost_detail` can. Porting the full recursive
 * engine for this one feature was judged out of scope for this pass (same
 * "don't duplicate a possibly-divergent second cost estimate" reasoning
 * `engine.py`'s own module docstring gives for reusing Production's engine
 * on desktop rather than writing a second one) - so this is a two-way
 * Buy-C-J-vs-Buy-Jita comparison only, and every row's `recommendedSource`
 * is one of "C-J"/"Jita"/null, never "Build". A future pass that ports a
 * real recursive build-cost walk can add the third column here without
 * changing this file's shape.
 *
 * Pricing mirrors `_shopping_prices`: C-J price is `homeSell * (1 +
 * brokerFee)` (placing your own buy order at the home market); Jita-landed
 * is `jitaSell * (1 + brokerFee) + importCostPerM3 * volume` (buying at
 * Jita and hauling home) - both real order-book prices, not Goonmetrics
 * quotes with no backing order book (this file's `home`/`jita` inputs come
 * from [EsiClient]/[GoonmetricsClient] the same way, see
 * [DoctrineShoppingListScreen] for how they're fetched). */
object ShoppingList {

    /** One priced shortfall line - mirrors `doctrine/models.py`'s
     * `ShoppingListRow`, minus `buildCost` (see this object's own docstring
     * on why "Build" isn't a column here). */
    data class Row(
        val typeId: Int,
        val typeName: String,
        val shortfall: Double,
        val cjPrice: Double?,
        val jitaLandedPrice: Double?,
        val recommendedSource: String?, // "C-J" | "Jita" | null
        val totalCost: Double?,
    )

    /** Builds the priced shopping list from [StockpileStatus.aggregate]'s
     * output, filtering to real shortfalls (`shortfall > 0`, same filter
     * `shopping_list_rows` itself applies) and pricing each one against
     * pre-fetched `home`/`jita` order-book stats + per-type packaged volume
     * (for the Jita haul leg). Pure - no network here, matching
     * `_shopping_prices`' own pure-function contract; [fetchHomePrices]/
     * [fetchJitaPrices] below do the actual IO. */
    fun build(
        aggregatedRows: List<StockpileStatus.AggregatedRow>,
        home: Map<Int, Double>,
        jita: Map<Int, Double>,
        volumeByType: Map<Int, Double>,
        importCostPerM3: Double,
        jitaBuyBrokerFee: Double,
    ): List<Row> {
        val shortfalls = aggregatedRows.filter { it.shortfall > 0 }
        return shortfalls.map { row ->
            val homeSell = home[row.typeId]
            val cjPrice = if (homeSell != null && homeSell > 0) homeSell * (1 + jitaBuyBrokerFee) else null
            val jitaSell = jita[row.typeId]
            val volume = volumeByType[row.typeId] ?: 0.0
            val jitaLanded = if (jitaSell != null && jitaSell > 0) {
                jitaSell * (1 + jitaBuyBrokerFee) + importCostPerM3 * volume
            } else null

            val candidates = listOfNotNull(
                cjPrice?.let { "C-J" to it },
                jitaLanded?.let { "Jita" to it },
            )
            val recommended = candidates.minByOrNull { it.second }
            val totalCost = recommended?.second?.times(row.shortfall)

            Row(
                typeId = row.typeId, typeName = row.typeName, shortfall = row.shortfall,
                cjPrice = cjPrice, jitaLandedPrice = jitaLanded,
                recommendedSource = recommended?.first, totalCost = totalCost,
            )
        }
    }

    /** Home ("C-J") market quotes via Goonmetrics' appraise.gnf.lt current-
     * prices dump for `homeMarketSlug` (e.g. a player structure's market
     * slug there) - the counterpart of `production_pricing.home_prices`.
     * Returns an empty map (never throws) when `homeMarketSlug` is null/
     * blank, same as desktop's `home_prices` returning `{}` for an unset
     * `cfg.home_market`. */
    suspend fun fetchHomePrices(goonmetrics: GoonmetricsClient, homeMarketSlug: String?): Map<Int, Double> {
        if (homeMarketSlug.isNullOrBlank()) return emptyMap()
        val prices: List<CurrentPrice> = goonmetrics.currentPrices(homeMarketSlug)
        return prices.filter { it.sell > 0 }.associate { it.typeId to it.sell }
    }

    /** Jita sell-percentile quotes via [EsiClient.regionOrderStatsBulk] - the
     * counterpart of `production_pricing.jita_prices`, reusing the same
     * bulk regional order-book endpoint every other Jita-pricing view on
     * this platform already uses (no new price machinery). */
    suspend fun fetchJitaPrices(esi: EsiClient, tradingCfg: TradingConfig, typeIds: List<Int>): Map<Int, Double> {
        if (typeIds.isEmpty()) return emptyMap()
        val stats: Map<Int, OrderStats> = esi.regionOrderStatsBulk(tradingCfg.jitaRegionId, typeIds)
        return stats.mapNotNull { (typeId, s) -> s.sellPercentile?.let { typeId to it } }.toMap()
    }

    /** Packaged volume per type_id (for the Jita haul leg) via
     * [EsiClient.getTypeInfo] - one call per distinct type_id, a failed
     * lookup degrades to 0 (no haul cost added, same "best effort, never
     * blocks the whole run" pattern [RealizedTradesScreen]'s own item-volume
     * resolution uses). */
    suspend fun fetchVolumes(esi: EsiClient, typeIds: List<Int>): Map<Int, Double> {
        val result = mutableMapOf<Int, Double>()
        for (typeId in typeIds.distinct()) {
            result[typeId] = try {
                val info = esi.getTypeInfo(typeId)
                info.packagedVolume ?: info.volume ?: 0.0
            } catch (e: Exception) {
                0.0
            }
        }
        return result
    }
}
