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
import com.pappmichel.evetraderlocal.data.esi.EsiClient
import com.pappmichel.evetraderlocal.data.esi.JITA_SOLAR_SYSTEM_ID
import com.pappmichel.evetraderlocal.data.trading.BUY_LOOKBACK_MULTIPLIER
import com.pappmichel.evetraderlocal.data.trading.RealizedSummary
import com.pappmichel.evetraderlocal.data.trading.RealizedTrade
import com.pappmichel.evetraderlocal.data.trading.TradingConfigRepository
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
 * Single buyer + single seller: the first registered token of each role,
 * not every one of them (see TradeReconciliation.kt on why this port is
 * single-character where desktop pools). */
@Composable
fun RealizedTradesScreen(database: AppDatabase, tokenManager: TokenManager) {
    val scope = rememberCoroutineScope()
    val configRepo = remember { TradingConfigRepository(database) }
    val client = remember { EsiClient() }

    var trades by remember { mutableStateOf<List<RealizedTrade>>(emptyList()) }
    var summary by remember { mutableStateOf<RealizedSummary?>(null) }
    var status by remember {
        mutableStateOf(
            "Not run yet - Reconcile matches the buyer character's Jita buys against the " +
                "seller character's structure sells, live (nothing is saved between runs)."
        )
    }
    var busy by remember { mutableStateOf(false) }

    fun reconcile() {
        busy = true
        trades = emptyList()
        summary = null
        status = "Loading configuration and characters..."
        scope.launch {
            try {
                val config = configRepo.load()
                val structureId = config.structureId
                if (structureId == null) {
                    status = "No structure id configured yet - sells can't be located without one."
                    return@launch
                }
                val buyerRecord = tokenManager.listRecords("buyer").firstOrNull()
                val sellerRecord = tokenManager.listRecords("seller").firstOrNull()
                if (buyerRecord == null || sellerRecord == null) {
                    status = "A buyer *and* a seller character must be logged in (see Characters)."
                    return@launch
                }
                // Through getToken, not the stored record's own accessToken:
                // a token sitting in the database is very often already
                // expired, and refreshing is TokenManager's job.
                val buyerToken = tokenManager.getToken(buyerRecord.role).accessToken
                val sellerToken = tokenManager.getToken(sellerRecord.role).accessToken

                status = "Resolving Jita's stations..."
                val jitaStations = client.solarSystemStationIds(JITA_SOLAR_SYSTEM_ID).toSet()

                status = "Fetching the buyer's wallet transactions..."
                val rawBuys = fetchRecentTransactions(
                    client, buyerRecord.characterId, buyerToken,
                    config.lookbackDays.toLong() * BUY_LOOKBACK_MULTIPLIER,
                )
                status = "Fetching the seller's wallet transactions..."
                val rawSells = fetchRecentTransactions(
                    client, sellerRecord.characterId, sellerToken, config.lookbackDays.toLong(),
                )

                // Best-effort, exactly like the desktop version: no journal
                // (missing scope, ESI outage) just means every sell falls back
                // to the fully modeled post-tax price.
                status = "Fetching the seller's wallet journal..."
                val journalAmounts = fetchRecentJournalAmounts(
                    client, sellerRecord.characterId, sellerToken, config.lookbackDays.toLong(),
                )

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
