package com.pappmichel.evetraderlocal.data.trading

import com.pappmichel.evetraderlocal.data.esi.CharacterOrder
import com.pappmichel.evetraderlocal.data.esi.EsiClient
import com.pappmichel.evetraderlocal.data.esi.MarketOrder
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.withContext

/** Own open market order monitoring for Station Trading - "am I still the
 * best price on either side of my own orders at Jita's trade hub station" -
 * a Kotlin port of the desktop build's station_trading/undercut.py.
 *
 * Near-mirror of `UnlistedUndercut.kt`'s own undercut check (same
 * order_id-cross-referencing reasoning - a structure/region order book
 * carries no owning-character field, so an order can only be excluded by
 * matching order_id, never by price/type_id, which would false-positive on
 * a coincidentally identical own price), generalized two ways here: scoped
 * to `cfg.stationId` (Jita's NPC trade hub) instead of a player structure,
 * and duplicated for the buy side, which `UnlistedUndercut.kt` deliberately
 * never covers.
 *
 * Jita's trade hub is a public NPC station - no docking access is needed to
 * read the competing side of the book (`EsiClient.regionOrdersRaw`), only
 * to read the trader's own orders (`EsiClient.characterOrders`, already
 * scoped by esi-markets.read_character_orders.v1 on every registered
 * trader character).
 *
 * Pooled across every registered trader character, unlike Trading's own
 * Realized Trades/Unlisted Undercut (both simplified to a single character
 * on this platform) - Station Trading's own trader role has no reason to be
 * split into separate "buyer"/"seller" roles the way Trading's import
 * arbitrage does, so pooling every trader here costs nothing extra: it's
 * the same one role, just possibly several characters. */

/** One flagged undercut/outbid - the Kotlin counterpart of the dict
 * `check_undercut_pooled`/`check_buy_undercut_pooled` return per row. */
data class UndercutRow(
    val typeId: Int,
    val myPrice: Double,
    val competitorPrice: Double,
    val difference: Double,
)

/** Pure ranking/comparison core, shared by both sides - deliberately knows
 * nothing about ESI, so it can be unit-tested against hand-built fixtures
 * (see StationTradingUndercutTest.kt) the same way `TradeReconciliation.kt`'s
 * matcher is. `competitorOrdersByType` is expected to already be the full
 * region order book for exactly the type_ids `myOrders` cover - the
 * suspend orchestration below is what actually fetches that. */
fun computeUndercuts(
    myOrders: List<CharacterOrder>,
    competitorOrdersByType: Map<Int, List<MarketOrder>>,
    stationId: Long,
    isBuyOrder: Boolean,
): List<UndercutRow> {
    if (myOrders.isEmpty()) return emptyList()
    val myOrderIds = myOrders.map { it.orderId }.toSet()
    val myBestPrice = mutableMapOf<Int, Double>()
    for (o in myOrders) {
        val current = myBestPrice[o.typeId]
        myBestPrice[o.typeId] = when {
            current == null -> o.price
            isBuyOrder -> maxOf(current, o.price)
            else -> minOf(current, o.price)
        }
    }

    val competitorBest = mutableMapOf<Int, Double>()
    for (typeId in myBestPrice.keys) {
        for (o in competitorOrdersByType[typeId].orEmpty()) {
            if (o.isBuyOrder != isBuyOrder || o.locationId != stationId || o.orderId in myOrderIds) continue
            val existing = competitorBest[typeId]
            competitorBest[typeId] = when {
                existing == null -> o.price
                isBuyOrder -> maxOf(existing, o.price)
                else -> minOf(existing, o.price)
            }
        }
    }

    val results = mutableListOf<UndercutRow>()
    for ((typeId, myPrice) in myBestPrice) {
        val competitorPrice = competitorBest[typeId] ?: continue
        val beaten = if (isBuyOrder) competitorPrice > myPrice else competitorPrice < myPrice
        if (beaten) {
            results.add(UndercutRow(typeId, myPrice, competitorPrice, kotlin.math.abs(myPrice - competitorPrice)))
        }
    }
    return results.sortedByDescending { it.difference }
}

/** Fetches the region's full order book for every one of `typeIds`,
 * concurrently (one call per type_id, no bulk-region-book endpoint exists -
 * same constraint `EsiClient.regionOrderStatsBulk` already documents). */
private suspend fun fetchCompetitorOrdersByType(
    client: EsiClient,
    jitaRegionId: Int,
    typeIds: Collection<Int>,
): Map<Int, List<MarketOrder>> = withContext(Dispatchers.IO) {
    typeIds.map { typeId -> async { typeId to client.regionOrdersRaw(jitaRegionId, typeId) } }.awaitAll().toMap()
}

/** Sell side: pools every registered trader character's own sell orders at
 * `cfg.stationId`, flags any a genuinely different market participant beats
 * on price. `traders` is `(characterId, accessToken)` pairs - already
 * resolved through `TokenManager.getToken`, the same "caller resolves
 * fresh tokens, this file only takes them" split `RealizedTradesScreen.kt`
 * uses. */
suspend fun checkUndercutPooled(
    traders: List<Pair<Long, String>>,
    client: EsiClient,
    jitaRegionId: Int,
    cfg: StationTradingConfig,
): List<UndercutRow> {
    val myOrders = mutableListOf<CharacterOrder>()
    for ((characterId, accessToken) in traders) {
        myOrders.addAll(
            client.characterOrders(characterId, accessToken)
                .filter { !it.isBuyOrder && it.locationId == cfg.stationId }
        )
    }
    if (myOrders.isEmpty()) return emptyList()
    val competitorOrders = fetchCompetitorOrdersByType(client, jitaRegionId, myOrders.map { it.typeId }.toSet())
    return computeUndercuts(myOrders, competitorOrders, cfg.stationId, isBuyOrder = false)
}

/** Buy side mirror of [checkUndercutPooled] - "outbid" here means a
 * competing buy order now offers *more* than my own best bid, the opposite
 * comparison direction from the sell side. */
suspend fun checkBuyUndercutPooled(
    traders: List<Pair<Long, String>>,
    client: EsiClient,
    jitaRegionId: Int,
    cfg: StationTradingConfig,
): List<UndercutRow> {
    val myOrders = mutableListOf<CharacterOrder>()
    for ((characterId, accessToken) in traders) {
        myOrders.addAll(
            client.characterOrders(characterId, accessToken)
                .filter { it.isBuyOrder && it.locationId == cfg.stationId }
        )
    }
    if (myOrders.isEmpty()) return emptyList()
    val competitorOrders = fetchCompetitorOrdersByType(client, jitaRegionId, myOrders.map { it.typeId }.toSet())
    return computeUndercuts(myOrders, competitorOrders, cfg.stationId, isBuyOrder = true)
}
