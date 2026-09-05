package com.pappmichel.evetraderlocal.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.pappmichel.evetraderlocal.data.auth.TokenManager
import com.pappmichel.evetraderlocal.data.db.AppDatabase
import com.pappmichel.evetraderlocal.data.esi.CharacterWalletTransaction
import com.pappmichel.evetraderlocal.data.esi.EsiClient
import com.pappmichel.evetraderlocal.data.esi.JITA_SOLAR_SYSTEM_ID
import com.pappmichel.evetraderlocal.data.sde.SdeRepository
import com.pappmichel.evetraderlocal.data.trading.BUY_LOOKBACK_MULTIPLIER
import com.pappmichel.evetraderlocal.data.trading.RealizedSummary
import com.pappmichel.evetraderlocal.data.trading.RealizedTrade
import com.pappmichel.evetraderlocal.data.trading.TradingConfigRepository
import com.pappmichel.evetraderlocal.data.trading.averageDailySoldByType
import com.pappmichel.evetraderlocal.data.trading.buysAtStations
import com.pappmichel.evetraderlocal.data.trading.fetchRecentJournalAmounts
import com.pappmichel.evetraderlocal.data.trading.fetchRecentTransactions
import com.pappmichel.evetraderlocal.data.trading.reconcileRealizedTrades
import com.pappmichel.evetraderlocal.data.trading.sellsAtStructure
import com.pappmichel.evetraderlocal.data.trading.summarizeRealizedTrades
import kotlinx.coroutines.launch

/** Trading -> Realized Trades & Transactions: FIFO-matched buy(Jita)/
 * sell(structure) pairs with their realized profit, mirroring the desktop
 * build's trading_realized_transactions.py view. Rows are sorted by sell
 * date ascending - the order reconcileRealizedTrades itself documents and
 * produces, the same default sort the desktop table applies.
 *
 * Two deliberate differences from that desktop view:
 *
 * - **No "last saved run" to open with.** Desktop loads
 *   `storage.latest_realized_trades()` on open and only re-runs on the
 *   button; here a reconciliation is live-only, on the button press, since
 *   nothing about a run is persisted (see TradeReconciliation.kt's own note
 *   on why) - the screen starts empty and says so.
 * - **No Wallet Transactions tab.** Desktop groups a second, raw
 *   per-character transaction listing into the same tab. That is a
 *   different question ("show me everything my wallet did") answered by a
 *   different desktop action (`do_wallet_transactions`); this screen ports
 *   the matched-P&L half only, and the menu entry keeps its full name so
 *   the missing half is visible rather than quietly renamed away.
 *
 * Pooled across every registered buyer and seller character, matching
 * desktop's `reconcile_realized_trades`: every buyer's Jita buys and every
 * seller's structure sells are fetched and combined before FIFO-matching
 * once (any buyer's purchase can fund any seller's sale - they are pooled,
 * not paired 1:1 by character), and every seller's wallet-journal entries
 * are unioned by journal entry id for the real-tax refinement. See
 * TradeReconciliation.kt's own docstring for the full rationale. */
@Composable
fun RealizedTradesScreen(database: AppDatabase, tokenManager: TokenManager) {
    val scope = rememberCoroutineScope()
    val configRepo = remember { TradingConfigRepository(database) }
    val sdeRepo = remember { SdeRepository(database) }
    val client = remember { EsiClient() }

    var trades by remember { mutableStateOf<List<RealizedTrade>>(emptyList()) }
    var summary by remember { mutableStateOf<RealizedSummary?>(null) }
    // {item name: average units sold per day}, this run's matched trades
    // only - see averageDailySoldByType's own note on why a live run stands
    // in for desktop's stored one here.
    var dailySold by remember { mutableStateOf<List<Pair<String, Double>>>(emptyList()) }
    var status by remember {
        mutableStateOf(
            "Not run yet - Reconcile matches every buyer character's Jita buys against " +
                "every seller character's structure sells, live (nothing is saved between runs)."
        )
    }
    var busy by remember { mutableStateOf(false) }

    fun reconcile() {
        busy = true
        trades = emptyList()
        summary = null
        dailySold = emptyList()
        status = "Loading configuration and characters..."
        scope.launch {
            try {
                val config = configRepo.load()
                val structureId = config.structureId
                if (structureId == null) {
                    status = "No structure id configured yet - sells can't be located without one."
                    return@launch
                }
                val buyerRecords = tokenManager.listRecords("buyer")
                val sellerRecords = tokenManager.listRecords("seller")
                if (buyerRecords.isEmpty() || sellerRecords.isEmpty()) {
                    status = "At least one buyer *and* one seller character must be logged in (see Characters)."
                    return@launch
                }

                status = "Resolving Jita's stations..."
                // Prefer the whole Forge region's stations from the local
                // SDE cache when it's populated - the same live-import
                // buyer could just as easily use a different Jita-system
                // station, or (less commonly) another station in The Forge
                // entirely, and only the SDE-backed region query can see
                // those. Falls back to the single-system live-ESI list
                // (narrower, but always available) whenever the cache is
                // empty - see TradeReconciliation.kt's own note on what
                // that narrower fallback costs.
                val jitaStations = sdeRepo.stationIdsInRegion(config.jitaRegionId.toLong()).toSet()
                    .ifEmpty { client.solarSystemStationIds(JITA_SOLAR_SYSTEM_ID).toSet() }

                // Pooled across every registered buyer/seller character (see
                // this screen's own docstring and TradeReconciliation.kt):
                // every buyer's buys and every seller's sells are combined
                // into one flat list each before matching once. Unlike
                // OwnedBlueprintsScreen/ProductionCurrentJobsScreen's
                // per-character best-effort merge, a fetch failure for any
                // one character here is allowed to abort the whole run -
                // same as desktop's reconcile_realized_trades, which never
                // catches fetch_recent_transactions' own exceptions either
                // (only the wallet-journal fetch is best-effort, on both
                // platforms).
                status = "Fetching every buyer's wallet transactions..."
                val rawBuys = mutableListOf<CharacterWalletTransaction>()
                for (buyerRecord in buyerRecords) {
                    val buyerToken = tokenManager.getToken(buyerRecord.role).accessToken
                    rawBuys += fetchRecentTransactions(
                        client, buyerRecord.characterId, buyerToken,
                        config.lookbackDays.toLong() * BUY_LOOKBACK_MULTIPLIER,
                    )
                }
                status = "Fetching every seller's wallet transactions..."
                val rawSells = mutableListOf<CharacterWalletTransaction>()
                val journalAmounts = mutableMapOf<Long, Double>()
                for (sellerRecord in sellerRecords) {
                    val sellerToken = tokenManager.getToken(sellerRecord.role).accessToken
                    rawSells += fetchRecentTransactions(
                        client, sellerRecord.characterId, sellerToken, config.lookbackDays.toLong(),
                    )
                    // Best-effort per seller, exactly like the desktop
                    // version: no journal for one seller (missing scope, ESI
                    // outage) just means that seller's sells fall back to the
                    // fully modeled post-tax price, without blocking the
                    // others'.
                    journalAmounts += fetchRecentJournalAmounts(
                        client, sellerRecord.characterId, sellerToken, config.lookbackDays.toLong(),
                    )
                }

                val buys = buysAtStations(rawBuys, jitaStations)
                val sells = sellsAtStructure(rawSells, structureId)

                // No shortlist coverage guarantee for realized trades - this
                // screen reconciles any traded type_id, not just shortlisted
                // ones - so every traded type is resolved from ESI - once per
                // type_id no matter how many trades match it, and a failed
                // lookup degrades to "no volume known" (zero freight) rather
                // than blocking the whole run.
                status = "Resolving item names and volumes..."
                val itemNames = mutableMapOf<Int, String>()
                val itemVolumes = mutableMapOf<Int, Double>()
                for (typeId in sells.map { it.typeId }.toSet()) {
                    try {
                        val info = client.getTypeInfo(typeId)
                        itemNames[typeId] = info.name
                        itemVolumes[typeId] = info.packagedVolume ?: info.volume ?: 0.0
                    } catch (e: Exception) {
                        itemVolumes[typeId] = 0.0
                    }
                }

                val matched = reconcileRealizedTrades(
                    buys = buys,
                    sells = sells,
                    journalAmountByRefId = journalAmounts,
                    itemNames = itemNames,
                    itemVolumes = itemVolumes,
                    config = config,
                )
                trades = matched
                summary = summarizeRealizedTrades(matched)
                dailySold = averageDailySoldByType(matched, config.lookbackDays)
                    .entries.sortedByDescending { it.value }
                    .map { (typeId, qty) -> (itemNames[typeId] ?: typeId.toString()) to qty }
                status = if (matched.isEmpty()) {
                    "No matched trades in the last ${config.lookbackDays} days " +
                        "(${buys.size} Jita buy(s), ${sells.size} structure sell(s) seen)."
                } else {
                    "${matched.size} matched trade(s) from ${buys.size} buy(s) and ${sells.size} sell(s)."
                }
            } catch (e: Exception) {
                status = e.message ?: "Reconciliation failed."
            } finally {
                busy = false
            }
        }
    }

    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(enabled = !busy, onClick = { reconcile() }) { Text("Reconcile") }
        }
        if (busy) {
            LinearProgressIndicator(modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp))
        }
        Text(status, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(vertical = 8.dp))

        summary?.let {
            Text(
                "Total realized profit: ${fmtIsk(it.totalRealizedProfit)}  ·  " +
                    "Average margin: ${fmtPercent(it.averageMargin)}",
                style = MaterialTheme.typography.bodyMedium,
            )
            if (it.top3ItemsByProfit.isNotEmpty()) {
                Text(
                    "Top items: " + it.top3ItemsByProfit.joinToString(", ") { (item, profit) ->
                        "$item (${fmtIsk(profit)})"
                    },
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.padding(bottom = 8.dp),
                )
            }
        }

        if (dailySold.isNotEmpty()) {
            Text(
                "Avg daily sold (this run): " + dailySold.joinToString(", ") { (item, qty) ->
                    "$item (%.1f/day)".format(qty)
                },
                style = MaterialTheme.typography.bodySmall,
                modifier = Modifier.padding(bottom = 8.dp),
            )
        }

        LazyColumn {
            items(trades) { trade ->
                ListItem(
                    headlineContent = { Text(trade.item) },
                    supportingContent = {
                        Text(
                            "Bought ${shortDate(trade.buyDate)} @ ${fmtIsk(trade.buyUnitPrice)}  ->  " +
                                "sold ${shortDate(trade.sellDate)} @ ${fmtIsk(trade.sellUnitPrice)}  " +
                                "(${trade.matchedQty} matched)"
                        )
                    },
                    trailingContent = {
                        Text("${fmtIsk(trade.realizedProfit)} · ${fmtPercent(trade.margin)}")
                    },
                )
                HorizontalDivider()
            }
        }
    }
}

/** Same thousands-separated, two-decimal ISK shape as the desktop views'
 * shared `fmt_isk` helper (gui/views/production_common.py). */
private fun fmtIsk(value: Double): String = "%,.2f ISK".format(value)

private fun fmtPercent(value: Double): String = "%.1f%%".format(value * 100)

/** ESI dates are "2026-09-04T12:34:56Z"; a phone row has no room for the
 * seconds, and the matching itself never looks at this string (see
 * TradeReconciliation.kt on why dates stay raw everywhere else). */
private fun shortDate(date: String): String = date.take(10)
