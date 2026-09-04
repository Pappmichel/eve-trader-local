package com.pappmichel.evetraderlocal.data.trading

import com.pappmichel.evetraderlocal.data.esi.CharacterWalletTransaction
import com.pappmichel.evetraderlocal.data.esi.EsiClient
import java.time.Instant
import java.time.temporal.ChronoUnit

/** Realized trade history - what actually happened, not what could - ported
 * from the desktop build's trade_reconciliation.py. Wallet transactions for
 * the buyer character (importing from Jita) are matched against the seller
 * character's structure sells per type_id, FIFO, over a lookback window, to
 * get realized profit per matched pair.
 *
 * Split of concerns here, mirroring the pure-logic style the rest of
 * `data/trading/` already uses: `reconcileRealizedTrades` and
 * `summarizeRealizedTrades` are pure functions over already-fetched
 * transactions (no network, no Android, no storage - the whole reason they
 * are unit-testable at all, see TradeReconciliationTest.kt), while the
 * paging/journal fetch helpers below take an `EsiClient` explicitly, the
 * same way CandidateDiscovery.buildCandidateUniverse does. The desktop
 * module is one file for both halves too; only its `storage` reads have no
 * counterpart here (see the location note below).
 *
 * What is simplified vs. the desktop version, and why:
 *
 * - **One buyer and one seller character**, not desktop's pooled
 *   multi-character matching (`reconcile_realized_trades` takes *lists* of
 *   (character_id, auth_role) pairs and pools every buyer's buys against
 *   every seller's sells, for a setup where one alt buys and another
 *   sells). This port's callers use the first registered buyer/seller
 *   token; the matcher itself is already pooled-shaped (it takes flat buy
 *   and sell lists, not characters), so widening it later is a caller-side
 *   change only.
 * - **Buy-side location filter is Jita's own stations, not the whole Jita
 *   region.** Desktop filters buys to `storage.get_station_ids_in_region(
 *   cfg.jita_region_id)` - every station in The Forge - out of its local
 *   SDE-backed tables. There is no SDE cache on this platform yet (see
 *   ROADMAP.md's Android section), so `EsiClient.solarSystemStationIds`
 *   fetches the *solar system's* own station list live from public ESI
 *   instead. That is genuinely narrower: a buyer who imports through some
 *   other station in The Forge (real, if uncommon) has those buys silently
 *   dropped, and their sales then look like sales with no cost basis at all
 *   rather than being mis-matched - under-reported profit, never invented
 *   profit. Widen it to the full region the moment the SDE cache lands.
 * - **The sell side does get the wallet-journal tax refinement**, same as
 *   desktop: a sale's real post-tax ISK is looked up by its own
 *   `journal_ref_id` (see `journalAmountByRefId`/ASSUMED_TAX_RATE_IN_
 *   DEFAULT_HAIRCUT), with the fully modeled `unit_price * haircut` as the
 *   fallback whenever the journal is unavailable or that entry is missing.
 * - **Item names/volumes have no shortlist to draw on for realized trades
 *   specifically** (unlike Price History and Shortlist itself, this screen
 *   reconciles *any* traded type_id, not just shortlisted ones), so callers
 *   backfill every traded type_id from ESI's public /universe/types/
 *   endpoint - the same best-effort backfill desktop's own `_type_info`
 *   does for types that fall through its shortlist-derived maps.
 * - **Nothing is persisted.** Desktop stores a run (`storage.save_realized_
 *   trades`) and its Realized Trades tab opens showing the last saved one;
 *   here a run is live-only, on a button press, the same "always fresh, no
 *   saved snapshot" stance the rest of this port's tool screens take. The
 *   one desktop feature that genuinely needs the stored run -
 *   `average_daily_sold_by_type`, which reads the last run's rows back out
 *   of SQLite - is therefore not ported at all rather than half-ported.
 */

/** ESI's fixed per-call cap for /characters/{id}/wallet/transactions/. */
const val WALLET_TRANSACTIONS_PAGE_SIZE = 2500

/** Buys are fetched over a longer window than sells: a sale inside
 * `lookbackDays` can legitimately be funded by inventory bought well before
 * that window started (an item sitting at the structure waiting to sell),
 * and the matcher's buy-date-before-sell-date rule means a too-old buy that
 * was never even fetched looks identical to "no cost basis at all" - the
 * sale is dropped rather than matched to a fabricated later buy. Safe, but
 * an avoidable under-report. A flat multiplier (not its own config field)
 * scales with whatever the user already means by "recent" while staying
 * bounded, unlike an uncapped buy fetch, which would make reconciliation
 * very slow for a character with years of trading history. */
const val BUY_LOOKBACK_MULTIPLIER = 3

/** `structureSellHaircut` bundles SCC surcharge + broker's fee + sales tax
 * into one multiplier (see TradingConfig/config.py: 0.5% + 1.5% + 3.37% =
 * 5.37%). To use the real sales tax from the wallet journal without adding
 * a second config field holding the SCC+broker portion separately, this is
 * the tax rate baked into that *default* haircut, used only to back it out:
 * (structureSellHaircut + ASSUMED_TAX_RATE_IN_DEFAULT_HAIRCUT) isolates the
 * SCC+broker-only retention ratio, which then multiplies the *real*
 * post-tax proceeds instead of the raw unit price. Exact while
 * structureSellHaircut is its default; a user who has customized it
 * (different skills/standings) gets a close-but-not-exact SCC+broker
 * estimate - still strictly better on the tax term than the fully modeled
 * formula, which is the one thing this is here to improve. */
const val ASSUMED_TAX_RATE_IN_DEFAULT_HAIRCUT = 0.0337

private const val MARKET_TRANSACTION_REF_TYPE = "market_transaction"

/** One FIFO-matched buy (Jita) / sell (structure) pair for the same
 * type_id - the direct counterpart of models.py's `RealizedTrade`.
 * `buyQty`/`sellQty` are the *whole* transactions either side of the match;
 * `matchedQty` is how much of them this particular pairing consumed, and
 * the only quantity any figure derived from this row may use (one
 * transaction can span several rows). */
data class RealizedTrade(
    val typeId: Int,
    val item: String,
    val buyDate: String,
    val buyQty: Long,
    val buyUnitPrice: Double,
    val sellDate: String,
    val sellQty: Long,
    val sellUnitPrice: Double,
    val matchedQty: Long,
    val realizedProfit: Double,
    val margin: Double,
)

/** What `summarize_realized` returns as a dict on desktop, as a real type
 * here - the screen reads all three fields, so there is nothing to gain
 * from a stringly-keyed map. */
data class RealizedSummary(
    val totalRealizedProfit: Double,
    val averageMargin: Double,
    val top3ItemsByProfit: List<Pair<String, Double>>,
)

/** Dates are compared and sorted as raw ESI strings, not parsed instants -
 * exactly as the desktop version does (`lst.sort(key=lambda t: t["date"])`,
 * `if buy["date"] > sell["date"]`). ESI's timestamps are always
 * zero-padded UTC "…Z" ISO-8601, so lexicographic order *is* chronological
 * order for them, and not parsing avoids introducing a second, subtly
 * different notion of ordering into the matcher. Parsing only happens where
 * real date arithmetic is needed: the lookback cutoff below. */
private fun parseIso(timestamp: String): Instant = Instant.parse(timestamp)

/** True while `timestamp` is inside the lookback window ending at `now`. */
private fun isWithinLookback(timestamp: String, lookbackDays: Long, now: Instant): Boolean =
    try {
        !parseIso(timestamp).isBefore(now.minus(lookbackDays, ChronoUnit.DAYS))
    } catch (e: Exception) {
        // An unparseable date is ESI returning something this port has never
        // seen; dropping that one transaction is safer than aborting a whole
        // reconciliation over it.
        false
    }

/** Buys that are actually buys *and* happened at one of `stationIds`.
 *
 * Both sides must be location-filtered, not just the sell side: any wallet
 * transaction the buyer character made *anywhere* would otherwise enter the
 * FIFO match and get paired against an unrelated structure sale, producing
 * a wrong landed cost/profit/margin for that trade (a confirmed bug in the
 * parent repo, where the buy side had no location filter at all). */
fun buysAtStations(
    transactions: List<CharacterWalletTransaction>,
    stationIds: Set<Long>,
): List<CharacterWalletTransaction> =
    transactions.filter { it.isBuy && it.locationId in stationIds }

/** Sells that are actually sells *and* happened at the configured structure. */
fun sellsAtStructure(
    transactions: List<CharacterWalletTransaction>,
    structureId: Long,
): List<CharacterWalletTransaction> =
    transactions.filter { !it.isBuy && it.locationId == structureId }

/** Matches `buys` against `sells` per type_id, FIFO. Both lists are
 * expected to be location-filtered already (see buysAtStations/
 * sellsAtStructure) - this function deliberately knows nothing about
 * locations, so that the filter used can differ per platform (see this
 * file's own note on the Jita-stations vs. Jita-region difference) without
 * the matcher changing at all.
 *
 * `journalAmountByRefId` maps a sale's `journal_ref_id` to the real ISK
 * amount credited for it; pass an empty map to get the fully modeled sell
 * price for every row. `itemNames`/`itemVolumes` are looked up per type_id;
 * a type missing from `itemVolumes` gets zero freight rather than a
 * guess. */
fun reconcileRealizedTrades(
    buys: List<CharacterWalletTransaction>,
    sells: List<CharacterWalletTransaction>,
    journalAmountByRefId: Map<Long, Double>,
    itemNames: Map<Int, String>,
    itemVolumes: Map<Int, Double>,
    config: TradingConfig,
): List<RealizedTrade> {
    val buysByType = buys.groupBy { it.typeId }.mapValues { (_, list) -> list.sortedBy { it.date } }
    val sellsByType = sells.groupBy { it.typeId }.mapValues { (_, list) -> list.sortedBy { it.date } }

    val results = mutableListOf<RealizedTrade>()
    for ((typeId, sellTxns) in sellsByType) {
        val buyQueue = buysByType[typeId].orEmpty()
        // The queue's *remaining* quantities, kept beside it rather than by
        // copying each transaction: one buy can be consumed by several sells,
        // and the transaction's own `quantity` still has to report the whole
        // original fill on every row it produces.
        val remainingOnBuy = LongArray(buyQueue.size) { buyQueue[it].quantity }
        var buyIdx = 0
        val freight = (itemVolumes[typeId] ?: 0.0) * config.importCostPerM3

        for (sell in sellTxns) {
            var remainingToMatch = sell.quantity
            while (remainingToMatch > 0 && buyIdx < buyQueue.size) {
                val buy = buyQueue[buyIdx]
                if (buy.date > sell.date) {
                    // A sale can't be funded by inventory bought *after* it
                    // sold. FIFO alone only orders buys chronologically - it
                    // never checks that a matched buy actually predates its
                    // sell, and in the parent repo 20% of matched rows (21.8%
                    // of reported profit) had buy_date > sell_date, from sells
                    // whose real cost basis was bought before the window even
                    // started. buyQueue is sorted ascending and buyIdx never
                    // rewinds, so every later buy is at least this late too:
                    // break rather than skip, leaving this buy available to a
                    // genuinely later sell. This sell's unmatched remainder is
                    // simply dropped - "no real data yet" honesty rather than
                    // a fabricated cost basis. It does not recover the true
                    // pre-window basis (that needs seeding the queue with real
                    // pre-window inventory); it only stops a wrong, later one
                    // substituting for it.
                    break
                }
                val matched = minOf(remainingToMatch, remainingOnBuy[buyIdx])
                if (matched <= 0) {
                    buyIdx++
                    continue
                }
                // importCostPerM3 is an ISK-per-m3 *rate*, never a flat
                // per-unit fee - freight has to scale with the item's own
                // per-unit volume, or cheap/small/bulk-traded items (ammo, ice
                // products, ...) get a wildly overstated landed cost.
                val landed = buy.unitPrice * (1 + config.jitaBuyBrokerFee) + freight
                val journalAmount = sell.journalRefId?.let { journalAmountByRefId[it] }
                val netSell = if (journalAmount != null && sell.quantity > 0) {
                    (journalAmount / sell.quantity) *
                        (config.structureSellHaircut + ASSUMED_TAX_RATE_IN_DEFAULT_HAIRCUT)
                } else {
                    sell.unitPrice * config.structureSellHaircut
                }
                val profitPerUnit = netSell - landed
                results.add(
                    RealizedTrade(
                        typeId = typeId,
                        item = itemNames[typeId] ?: typeId.toString(),
                        buyDate = buy.date,
                        buyQty = buy.quantity,
                        buyUnitPrice = buy.unitPrice,
                        sellDate = sell.date,
                        sellQty = sell.quantity,
                        sellUnitPrice = sell.unitPrice,
                        matchedQty = matched,
                        realizedProfit = profitPerUnit * matched,
                        margin = if (landed != 0.0) profitPerUnit / landed else 0.0,
                    )
                )
                remainingOnBuy[buyIdx] -= matched
                remainingToMatch -= matched
                if (remainingOnBuy[buyIdx] <= 0) buyIdx++
            }
        }
    }
    return results.sortedBy { it.sellDate }
}

/** Total profit, weighted average margin and the three items that made the
 * most. The average margin is weighted by cost basis (buy price x matched
 * quantity), not a plain mean of per-row margins - one tiny trade with a
 * freak margin must not weigh as much as a large one. */
fun summarizeRealizedTrades(trades: List<RealizedTrade>): RealizedSummary {
    val totalProfit = trades.sumOf { it.realizedProfit }
    val weightedDenominator = trades.sumOf { it.buyUnitPrice * it.matchedQty }
    val averageMargin = if (weightedDenominator != 0.0) totalProfit / weightedDenominator else 0.0

    val byItem = mutableMapOf<String, Double>()
    for (trade in trades) {
        byItem[trade.item] = (byItem[trade.item] ?: 0.0) + trade.realizedProfit
    }
    val top3 = byItem.entries.sortedByDescending { it.value }.take(3).map { it.key to it.value }

    return RealizedSummary(
        totalRealizedProfit = totalProfit,
        averageMargin = averageMargin,
        top3ItemsByProfit = top3,
    )
}

/** Pages through `characterWalletTransactions` via `fromId` (cursor
 * pagination on the oldest transaction_id of the previous page) until
 * either a page's oldest transaction predates the cutoff, or a short page
 * signals there is nothing older left. A single un-paginated call only ever
 * sees the most recent 2500 transactions, which silently dropped
 * older-but-still-in-window trades for any character trading more than that
 * within `lookbackDays` (confirmed real-world symptom in the parent repo: a
 * frequently traded item missing from Realized Trades despite clearly
 * having sold inside the window).
 *
 * The `page.size < WALLET_TRANSACTIONS_PAGE_SIZE` stop condition relies on
 * ESI's per-call cap staying at 2500 - correct today, but it would silently
 * under-page (looking exactly like "no older transactions") if CCP lowered
 * it. */
suspend fun fetchRecentTransactions(
    client: EsiClient,
    characterId: Long,
    accessToken: String,
    lookbackDays: Long,
    now: Instant = Instant.now(),
): List<CharacterWalletTransaction> {
    val all = mutableListOf<CharacterWalletTransaction>()
    var fromId: Long? = null
    while (true) {
        val page = client.characterWalletTransactions(characterId, accessToken, fromId)
        if (page.isEmpty()) break
        all.addAll(page)
        val oldest = page.minByOrNull { it.transactionId } ?: break
        if (!isWithinLookback(oldest.date, lookbackDays, now) ||
            page.size < WALLET_TRANSACTIONS_PAGE_SIZE
        ) {
            break
        }
        fromId = oldest.transactionId
    }
    return all.filter { isWithinLookback(it.date, lookbackDays, now) }
}

/** {journal entry id: amount} for this character's `market_transaction`
 * journal entries within `lookbackDays` - the lookup reconcileRealizedTrades
 * uses to find a specific sell's real post-tax proceeds via that
 * transaction's own `journal_ref_id`. Best-effort: any ESI failure (missing
 * scope, outage, ...) returns an empty map rather than throwing, so a
 * wallet-journal problem degrades reconciliation to the fully modeled
 * formula instead of blocking it entirely - the same stance the type-info
 * backfill takes. */
suspend fun fetchRecentJournalAmounts(
    client: EsiClient,
    characterId: Long,
    accessToken: String,
    lookbackDays: Long,
    now: Instant = Instant.now(),
): Map<Long, Double> = try {
    client.characterWalletJournal(characterId, accessToken)
        .filter { it.refType == MARKET_TRANSACTION_REF_TYPE && isWithinLookback(it.date, lookbackDays, now) }
        .associate { it.id to it.amount }
} catch (e: Exception) {
    emptyMap()
}
