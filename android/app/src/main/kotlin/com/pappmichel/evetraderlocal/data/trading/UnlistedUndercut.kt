package com.pappmichel.evetraderlocal.data.trading

import com.pappmichel.evetraderlocal.data.esi.CharacterAsset
import com.pappmichel.evetraderlocal.data.esi.CharacterOrder
import com.pappmichel.evetraderlocal.data.esi.MarketOrder

/** Two independent, always-live "how do things stand right now" checks,
 * ported from the desktop build's `own_orders.py` - single-seller only
 * (the Kotlin port's Shortlist screen already made that simplification;
 * the desktop functions these mirror are actually "_pooled" across every
 * registered seller character, for the "shared structure hangar,
 * multiple sellers" case - not reproduced here). Pure: no network call,
 * nothing cached - the caller (`UnlistedUndercutScreen.kt`) fetches
 * everything fresh via `EsiClient` each time a check runs. */

data class UndercutResult(val typeId: Int, val myPrice: Double, val competitorPrice: Double, val difference: Double)

data class UnlistedStockResult(val typeId: Int, val assetQuantity: Double, val unlistedQuantity: Double)

/** Location flags that look like stock but aren't actually available to
 * list for sale (asset-safety holds, delivery bays, corp market escrow) -
 * same set as `esi_client.py`'s `NON_STOCK_LOCATION_FLAGS`. */
val NON_STOCK_LOCATION_FLAGS = setOf("AssetSafety", "Deliveries", "CorpDeliveries", "CorpMarket")

/** Which of the seller's own sell orders at `structureId` a competitor is
 * currently beating - mirrors `own_orders.py`'s `check_undercut_pooled`.
 * `structureOrders` is the structure's full order book (see
 * `EsiClient.structureOrdersRaw`) - ESI's structure order book carries no
 * owning-character field per order, so "which of these are mine" can only
 * be decided by cross-referencing `orderId` against `myOrders` (which does
 * carry your own order id), never by type_id/price matching, which would
 * false-positive on a coincidentally identical price from another seller.
 * Only orders actually beaten (competitor price strictly lower) are
 * returned, sorted by difference descending - same order
 * `check_undercut_pooled` documents. */
fun checkUndercut(myOrders: List<CharacterOrder>, structureOrders: List<MarketOrder>, structureId: Long): List<UndercutResult> {
    val mySellOrders = myOrders.filter { !it.isBuyOrder && it.locationId == structureId }
    if (mySellOrders.isEmpty()) return emptyList()
    val myOrderIds = mySellOrders.map { it.orderId }.toSet()
    val myBestPrice = mutableMapOf<Int, Double>()
    for (o in mySellOrders) {
        val current = myBestPrice[o.typeId]
        if (current == null || o.price < current) myBestPrice[o.typeId] = o.price
    }

    val competitorBest = mutableMapOf<Int, Double>()
    for (o in structureOrders) {
        if (o.isBuyOrder || o.orderId in myOrderIds) continue
        if (o.typeId !in myBestPrice) continue
        val current = competitorBest[o.typeId]
        if (current == null || o.price < current) competitorBest[o.typeId] = o.price
    }

    return myBestPrice.mapNotNull { (typeId, myPrice) ->
        val competitorPrice = competitorBest[typeId]
        if (competitorPrice != null && competitorPrice < myPrice) {
            UndercutResult(typeId, myPrice, competitorPrice, myPrice - competitorPrice)
        } else null
    }.sortedByDescending { it.difference }
}

/** Shortlist items the seller physically has at `structureId` (excluding
 * `NON_STOCK_LOCATION_FLAGS`) with no open sell order there at all right
 * now - stock that could be listed but apparently was forgotten. Mirrors
 * `own_orders.py`'s `fetch_seller_stock_without_order_pooled`. Only
 * `shortlistItemIds` are considered, and only a complete absence of a
 * sell order counts (a partially listed item isn't flagged) - the same
 * two scope limits the desktop version documents. Known limitation
 * (shared with the desktop original): only sees items sitting directly in
 * the structure's own location_id - one nested inside a container or a
 * parked ship there carries that container's/ship's own item_id as its
 * location_id instead. */
fun findUnlistedStock(
    assets: List<CharacterAsset>, ownSellOrderRemaining: Map<Int, Double>,
    shortlistItemIds: Set<Int>, structureId: Long,
): List<UnlistedStockResult> {
    val assetQty = mutableMapOf<Int, Double>()
    for (a in assets) {
        if (a.typeId !in shortlistItemIds) continue
        if (a.locationId != structureId) continue
        if (a.locationFlag in NON_STOCK_LOCATION_FLAGS) continue
        assetQty[a.typeId] = (assetQty[a.typeId] ?: 0.0) + a.quantity
    }
    return assetQty.filter { (typeId, _) -> (ownSellOrderRemaining[typeId] ?: 0.0) <= 0.0 }
        .map { (typeId, qty) -> UnlistedStockResult(typeId, qty, qty) }
}
