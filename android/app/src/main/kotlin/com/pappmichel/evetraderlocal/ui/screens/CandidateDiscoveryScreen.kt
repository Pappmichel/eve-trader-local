package com.pappmichel.evetraderlocal.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Check
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
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
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.pappmichel.evetraderlocal.data.db.AppDatabase
import com.pappmichel.evetraderlocal.data.history.GoonmetricsClient
import com.pappmichel.evetraderlocal.data.trading.Candidate
import com.pappmichel.evetraderlocal.data.trading.CandidateDiscovery
import com.pappmichel.evetraderlocal.data.trading.HistoryBacktest
import com.pappmichel.evetraderlocal.data.trading.NewCandidateResult
import com.pappmichel.evetraderlocal.data.trading.ShortlistItem
import com.pappmichel.evetraderlocal.data.trading.ShortlistRepository
import com.pappmichel.evetraderlocal.data.trading.TradingConfigRepository
import com.pappmichel.evetraderlocal.data.sde.SdeRepository
import kotlinx.coroutines.launch

/** Trading -> Candidate Discovery: the first real (non-placeholder) tool
 * screen, mirroring the desktop build's trading_candidate_discovery.py
 * view - "Discover" runs CandidateDiscovery.buildCandidateUniverse, which
 * prefers the local SDE cache (near-instant, no ESI calls) and only falls
 * back to the slower live-ESI market-group walk when that cache hasn't
 * been refreshed yet (see SdeDataScreen) - see CandidateDiscovery.kt's own
 * docstring for what's simplified in the SDE-backed path vs. desktop (the
 * capital-module packaged-volume correction isn't ported).
 *
 * Each row also has a manual "Add to Shortlist" button (no filter behind it
 * - an explicit user override), and a separate "Score & Add Recommended"
 * action fetches Goonmetrics Jita/reference-region history for every
 * not-yet-shortlisted candidate and runs it through
 * `HistoryBacktest.scoreCandidate` - the same hit-rate/avg-movement/margin
 * filter as the desktop build's `do_find_new_candidates` +
 * `do_add_to_shortlist` pair, auto-adding only what the filter recommends.
 * This is the auto-*add* half of desktop's `refresh-and-prune`; the
 * auto-*remove*/deactivate half lives on ShortlistScreen.kt's "Prune"
 * action instead (see that screen's own KDoc for why the port keeps that
 * split, rather than one combined action, matching how Android already
 * splits Discovery and Shortlist into separate screens/buttons where
 * desktop's `refresh-and-prune` is one CLI action). */
@Composable
fun CandidateDiscoveryScreen(database: AppDatabase) {
    val scope = rememberCoroutineScope()
    val configRepo = remember { TradingConfigRepository(database) }
    val shortlistRepo = remember { ShortlistRepository(database) }
    val sdeRepo = remember { SdeRepository(database) }

    var candidates by remember { mutableStateOf<List<Candidate>>(emptyList()) }
    var shortlistedIds by remember { mutableStateOf<Set<Int>>(emptySet()) }
    var scoresByType by remember { mutableStateOf<Map<Int, NewCandidateResult>>(emptyMap()) }
    var status by remember { mutableStateOf("Not run yet - Discover reads the local SDE cache when it's " +
        "populated (see SDE Data), or falls back to a slower live market-group walk otherwise.") }
    var busy by remember { mutableStateOf(false) }
    var progress by remember { mutableStateOf(0f) }

    LaunchedEffect(Unit) {
        shortlistedIds = shortlistRepo.load().map { it.itemId }.toSet()
    }

    fun addToShortlist(candidate: Candidate) {
        scope.launch {
            val added = shortlistRepo.addIfAbsent(
                ShortlistItem(
                    item = candidate.item, itemId = candidate.typeId, category = candidate.category,
                    volumeM3 = candidate.volumeM3, metaLevel = candidate.metaLevel,
                )
            )
            if (added) shortlistedIds = shortlistedIds + candidate.typeId
        }
    }

    fun scoreAndAddRecommended() {
        val toScore = candidates.filter { it.typeId !in shortlistedIds }
        if (toScore.isEmpty()) {
            status = "Nothing to score - discover candidates first, or everything found is already on the shortlist."
            return
        }
        busy = true
        status = "Fetching price history for ${toScore.size} candidate(s)..."
        scope.launch {
            try {
                val config = configRepo.load()
                val gm = GoonmetricsClient()
                val typeIds = toScore.map { it.typeId }
                val points = gm.priceHistoryChunked(config.jitaRegionId, typeIds) +
                    gm.priceHistoryChunked(config.referenceRegionId, typeIds)
                val histIndex = HistoryBacktest.indexHistory(points)
                val results = toScore.mapNotNull { HistoryBacktest.scoreCandidate(it, histIndex, config) }
                scoresByType = scoresByType + results.associateBy { it.typeId }

                val recommended = results.filter { it.add }
                var added = 0
                for (r in recommended) {
                    val candidate = toScore.first { it.typeId == r.typeId }
                    if (shortlistRepo.addIfAbsent(
                            ShortlistItem(
                                item = candidate.item, itemId = candidate.typeId, category = candidate.category,
                                volumeM3 = candidate.volumeM3, metaLevel = candidate.metaLevel,
                            )
                        )
                    ) added++
                }
                if (added > 0) shortlistedIds = shortlistedIds + recommended.map { it.typeId }
                status = "Scored ${results.size} of ${toScore.size} (some had no Goonmetrics history) - " +
                    "$added recommended and added to the shortlist."
            } catch (e: Exception) {
                status = e.message ?: "Scoring failed."
            } finally {
                busy = false
            }
        }
    }

    fun runDiscovery() {
        busy = true
        progress = 0f
        status = "Checking the local SDE cache..."
        scope.launch {
            try {
                val config = configRepo.load()
                val result = CandidateDiscovery.buildCandidateUniverse(config, sde = sdeRepo) { done, total ->
                    // Only ever invoked on the live-ESI fallback path - the
                    // SDE path returns in one shot, with nothing to report
                    // progress through.
                    if (total > 0) {
                        progress = done.toFloat() / total.toFloat()
                        status = "Resolving candidates via live ESI... $done / $total"
                    }
                }
                candidates = result.sortedBy { it.item }
                status = "${candidates.size} candidates found."
            } catch (e: Exception) {
                status = e.message ?: "Discovery failed."
            } finally {
                busy = false
            }
        }
    }

    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(enabled = !busy, onClick = { runDiscovery() }) { Text("Discover") }
            Button(enabled = !busy && candidates.isNotEmpty(), onClick = { scoreAndAddRecommended() }) {
                Text("Score & Add Recommended")
            }
        }
        if (busy) {
            LinearProgressIndicator(progress = { progress }, modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp))
        }
        Text(status, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(vertical = 8.dp))

        LazyColumn {
            items(candidates) { candidate ->
                val alreadyShortlisted = candidate.typeId in shortlistedIds
                val score = scoresByType[candidate.typeId]
                ListItem(
                    headlineContent = { Text(candidate.item) },
                    supportingContent = {
                        Column {
                            Text(candidate.marketGroupPath)
                            // Only present once "Score & Add Recommended" has run for
                            // this candidate - hit-rate/avg-movement filter, see
                            // HistoryBacktest.scoreCandidate.
                            score?.let {
                                Text(
                                    "Hit-rate ${"%.0f".format(it.hitRate * 100)}% · " +
                                        "Avg move ${"%.1f".format(it.avgSellMovement)}/day · ${it.recommendation}",
                                )
                            }
                        }
                    },
                    trailingContent = {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text("${candidate.category} · %.1f m3".format(candidate.volumeM3))
                            IconButton(enabled = !alreadyShortlisted, onClick = { addToShortlist(candidate) }) {
                                Icon(
                                    if (alreadyShortlisted) Icons.Filled.Check else Icons.Filled.Add,
                                    contentDescription = if (alreadyShortlisted) "Already on shortlist" else "Add to shortlist",
                                )
                            }
                        }
                    },
                )
                HorizontalDivider()
            }
        }
    }
}
