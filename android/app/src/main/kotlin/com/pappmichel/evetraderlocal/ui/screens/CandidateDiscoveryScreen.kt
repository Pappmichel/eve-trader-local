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
import com.pappmichel.evetraderlocal.data.trading.Candidate
import com.pappmichel.evetraderlocal.data.trading.CandidateDiscovery
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
 * capital-module packaged-volume correction isn't ported). Each row also
 * has an "Add to Shortlist" button - a manual stand-in for the desktop
 * build's `refresh-and-prune` auto-add (which additionally applies
 * hit-rate/movement thresholds - not ported here, see ROADMAP.md's
 * Android section), so a candidate found here doesn't have to be retyped
 * by hand into the Shortlist screen's Add dialog. */
@Composable
fun CandidateDiscoveryScreen(database: AppDatabase) {
    val scope = rememberCoroutineScope()
    val configRepo = remember { TradingConfigRepository(database) }
    val shortlistRepo = remember { ShortlistRepository(database) }
    val sdeRepo = remember { SdeRepository(database) }

    var candidates by remember { mutableStateOf<List<Candidate>>(emptyList()) }
    var shortlistedIds by remember { mutableStateOf<Set<Int>>(emptySet()) }
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
        }
        if (busy) {
            LinearProgressIndicator(progress = { progress }, modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp))
        }
        Text(status, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(vertical = 8.dp))

        LazyColumn {
            items(candidates) { candidate ->
                val alreadyShortlisted = candidate.typeId in shortlistedIds
                ListItem(
                    headlineContent = { Text(candidate.item) },
                    supportingContent = { Text(candidate.marketGroupPath) },
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
