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
import com.pappmichel.evetraderlocal.data.db.AppDatabase
import com.pappmichel.evetraderlocal.data.history.GoonmetricsClient
import com.pappmichel.evetraderlocal.data.trading.HistoryBacktest
import com.pappmichel.evetraderlocal.data.trading.MarginTrend
import com.pappmichel.evetraderlocal.data.trading.ShortlistRepository
import com.pappmichel.evetraderlocal.data.trading.TradingConfigRepository
import kotlinx.coroutines.launch

/** Trading -> Price History: margin momentum per shortlist item, mirroring
 * the desktop build's trading_price_history.py view (item, recent avg
 * margin, baseline avg margin, trend - sorted by trend descending, the same
 * "no inherent order, so rank by the most decision-relevant column" default
 * that view documents).
 *
 * **Deliberate architectural difference from desktop: this fetches, desktop
 * reads a cache.** The desktop view is a pure local computation over
 * `storage.read_goonmetrics_history_for_types` - a local price-history cache
 * that gets populated *incidentally*, as a side effect of candidate searches
 * the user ran for other reasons. None of that exists on Android (no history
 * table, and candidate discovery here doesn't fetch history at all), and
 * building that whole caching pipeline is not what unblocks this screen.
 * Instead this follows the pattern the other live Trading screens on this
 * platform already established: no persisted snapshot at all, just a live
 * fetch on a button press, computed fresh every time. On Refresh it loads
 * the shortlist, fetches Goonmetrics history live for both
 * `jitaRegionId` and `referenceRegionId`, and runs
 * `HistoryBacktest.computeMarginTrends` on the freshly fetched points.
 *
 * The tradeoff, stated plainly: a Refresh here does a real network fetch of
 * ~28 days of history per shortlist type_id (chunked, the same way desktop's
 * own `price_history_chunked` chunks it), where desktop pays nothing because
 * the data was already sitting in its local cache. Two regions x a shortlist
 * that is normally tens of items is a handful of requests, not a candidate
 * search's worth - but it is a real fetch, and it happens every press. A
 * local history cache remains a reasonable later addition (see ROADMAP.md's
 * Android section); it is not a prerequisite for this screen. */
@Composable
fun PriceHistoryScreen(database: AppDatabase) {
    val scope = rememberCoroutineScope()
    val configRepo = remember { TradingConfigRepository(database) }
    val shortlistRepo = remember { ShortlistRepository(database) }
    val client = remember { GoonmetricsClient() }

    var rows by remember { mutableStateOf<List<Pair<String, MarginTrend>>>(emptyList()) }
    var status by remember {
        mutableStateOf(
            "Not run yet - Refresh fetches ~28 days of region price history live for every " +
                "shortlist item (no local history cache on this platform, see this screen's own notes)."
        )
    }
    var busy by remember { mutableStateOf(false) }

    fun refresh() {
        busy = true
        status = "Loading shortlist..."
        scope.launch {
            try {
                val config = configRepo.load()
                // Matches desktop's do_shortlist_trends: `if i.item_id and
                // i.volume_m3` - no active filter either (an inactive
                // shortlist item's trend is still worth seeing).
                val shortlist = shortlistRepo.load().filter { it.itemId != 0 && it.volumeM3 > 0 }
                if (shortlist.isEmpty()) {
                    rows = emptyList()
                    status = "No shortlist items yet - nothing to compute a margin trend for."
                    return@launch
                }
                val typeIds = shortlist.map { it.itemId }
                status = "Fetching price history for ${typeIds.size} item(s) in 2 regions..."
                val history = client.priceHistoryChunked(config.jitaRegionId, typeIds) +
                    client.priceHistoryChunked(config.referenceRegionId, typeIds)

                val volumes = shortlist.associate { it.itemId to it.volumeM3 }
                val names = shortlist.associate { it.itemId to it.item }
                val trends = HistoryBacktest.computeMarginTrends(history, volumes, config)
                rows = trends.values
                    .sortedByDescending { it.trendPct }
                    .map { (names[it.typeId] ?: it.typeId.toString()) to it }
                status = if (rows.isEmpty()) {
                    "No shortlist item has enough paired price history yet."
                } else {
                    "${rows.size} item(s) with enough paired history to show a trend."
                }
            } catch (e: Exception) {
                status = e.message ?: "Could not compute margin trends."
            } finally {
                busy = false
            }
        }
    }

    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(enabled = !busy, onClick = { refresh() }) { Text("Refresh") }
        }
        if (busy) {
            LinearProgressIndicator(modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp))
        }
        Text(status, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(vertical = 8.dp))

        LazyColumn {
            items(rows) { (name, trend) ->
                ListItem(
                    headlineContent = { Text(name) },
                    supportingContent = {
                        Text(
                            "recent ${formatPct(trend.recentAvgMargin)} · " +
                                "baseline ${formatPct(trend.baselineAvgMargin)}"
                        )
                    },
                    trailingContent = { Text(formatPct(trend.trendPct)) },
                )
                HorizontalDivider()
            }
        }
    }
}

private fun formatPct(value: Double): String = "%.1f%%".format(value * 100)
