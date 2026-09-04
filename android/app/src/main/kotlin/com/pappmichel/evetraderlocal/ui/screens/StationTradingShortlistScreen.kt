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
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.pappmichel.evetraderlocal.data.db.AppDatabase
import com.pappmichel.evetraderlocal.data.esi.EsiClient
import com.pappmichel.evetraderlocal.data.esi.OrderStats
import com.pappmichel.evetraderlocal.data.history.GoonmetricsClient
import com.pappmichel.evetraderlocal.data.sde.SdeRepository
import com.pappmichel.evetraderlocal.data.trading.StationTradingConfig
import com.pappmichel.evetraderlocal.data.trading.StationTradingConfigRepository
import com.pappmichel.evetraderlocal.data.trading.StationTradingShortlistItem
import com.pappmichel.evetraderlocal.data.trading.StationTradingShortlistRepository
import com.pappmichel.evetraderlocal.data.trading.TradingConfigRepository
import com.pappmichel.evetraderlocal.data.trading.confirmLive
import com.pappmichel.evetraderlocal.data.trading.discoverCandidates
import java.time.Instant
import kotlinx.coroutines.launch

/** Station Trading -> Shortlist: a Kotlin port of the desktop build's
 * trading_station_trading.py Shortlist tab, backed by
 * `station_trading/candidate_discovery.py` and `actions.do_refresh_shortlist`/
 * `do_get_shortlist`.
 *
 * "Discover" re-runs `discoverCandidates` (one Goonmetrics current-price
 * scan of the whole Jita market, ranked by spread * real daily volume) and
 * persists the result - a newly found type_id starts active, a previously
 * deactivated one stays deactivated (see `StationTradingShortlistRepository.
 * upsertFromDiscovery`). Every row shown is then live-confirmed against
 * ESI's real order book (`confirmLive`, bounded to just this shortlist,
 * never the whole market) and profit-annotated from *that* live price, not
 * the persisted discovery-time spread - the same "never trust a stale
 * number when a live one is one bounded call away" rule
 * `actions._build_shortlist_rows` documents.
 *
 * Item names/categories are resolved from the local SDE cache
 * (`SdeRepository.typeName`/`categoryNameFor`), the same lookups
 * `storage.get_sde_type` backs on desktop - a row whose type_id isn't in
 * the cache yet (never refreshed, or a corner-case type Fuzzwork's dump
 * doesn't carry) falls back to its bare type_id/"Unknown" rather than
 * blocking the row. */
@Composable
fun StationTradingShortlistScreen(database: AppDatabase) {
    val scope = rememberCoroutineScope()
    val tradingConfigRepo = remember { TradingConfigRepository(database) }
    val configRepo = remember { StationTradingConfigRepository(database) }
    val shortlistRepo = remember { StationTradingShortlistRepository(database) }
    val sdeRepo = remember { SdeRepository(database) }
    val goonmetrics = remember { GoonmetricsClient() }
    val esi = remember { EsiClient() }

    data class ShortlistRow(
        val item: StationTradingShortlistItem,
        val stats: OrderStats?,
        val name: String,
        val category: String?,
    )

    var rows by remember { mutableStateOf<List<ShortlistRow>>(emptyList()) }
    var stationCfg by remember { mutableStateOf(StationTradingConfig()) }
    var status by remember { mutableStateOf("Loading shortlist...") }
    var busy by remember { mutableStateOf(false) }

    fun profitAndMargin(stats: OrderStats?, cfg: StationTradingConfig): Pair<Double?, Double?> {
        val buy = stats?.buyPercentile
        val sell = stats?.sellPercentile
        if (buy == null || sell == null || buy <= 0) return null to null
        val buyCost = buy * (1 + cfg.brokerFeeRate)
        val sellNet = sell * (1 - cfg.brokerFeeRate - cfg.salesTaxRate)
        val profitPerUnit = sellNet - buyCost
        return profitPerUnit to profitPerUnit / buyCost
    }

    suspend fun refreshRows() {
        stationCfg = configRepo.load()
        val tradingCfg = tradingConfigRepo.load()
        val items = shortlistRepo.load()
        val live = confirmLive(items.map { it.typeId }, tradingCfg.jitaRegionId, esi)
        rows = items.map {
            ShortlistRow(
                item = it,
                stats = live[it.typeId],
                name = sdeRepo.typeName(it.typeId) ?: it.typeId.toString(),
                category = sdeRepo.categoryNameFor(it.typeId),
            )
        }
        status = if (rows.isEmpty()) {
            "No shortlist items yet - Discover to scan the Jita market."
        } else {
            "${rows.size} shortlisted item(s)."
        }
    }

    LaunchedEffect(Unit) {
        try {
            refreshRows()
        } catch (e: Exception) {
            status = e.message ?: "Could not load the shortlist."
        }
    }

    fun discover() {
        busy = true
        status = "Scanning the Jita market via Goonmetrics..."
        scope.launch {
            try {
                val cfg = configRepo.load()
                val tradingCfg = tradingConfigRepo.load()
                val candidates = discoverCandidates(goonmetrics, tradingCfg.jitaRegionId, cfg)
                shortlistRepo.upsertFromDiscovery(candidates, Instant.now().toString())
                refreshRows()
                status = "Discovered ${candidates.size} candidate(s). " + status
            } catch (e: Exception) {
                status = e.message ?: "Discovery failed."
            } finally {
                busy = false
            }
        }
    }

    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(enabled = !busy, onClick = { discover() }) { Text("Discover") }
        }
        if (busy) {
            LinearProgressIndicator(modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp))
        }
        Text(status, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(vertical = 8.dp))

        LazyColumn {
            items(rows) { row ->
                val (profitPerUnit, margin) = profitAndMargin(row.stats, stationCfg)
                ListItem(
                    headlineContent = { Text(row.name) },
                    supportingContent = {
                        Text(
                            "${row.category ?: "Unknown"} · spread ${"%.1f%%".format(row.item.spreadPct * 100)} · " +
                                "avg/day ${"%.0f".format(row.item.avgDailyVolume)}" +
                                if (!row.item.active) " · inactive" else ""
                        )
                    },
                    trailingContent = {
                        Text(
                            if (profitPerUnit != null && margin != null) {
                                "${"%,.2f".format(profitPerUnit)} ISK · ${"%.1f%%".format(margin * 100)}"
                            } else {
                                "no live price"
                            }
                        )
                    },
                )
                HorizontalDivider()
            }
        }
    }
}
