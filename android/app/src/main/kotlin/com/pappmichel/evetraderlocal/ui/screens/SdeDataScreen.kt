package com.pappmichel.evetraderlocal.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.LinearProgressIndicator
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
import com.pappmichel.evetraderlocal.data.sde.SdeRepository
import kotlinx.coroutines.launch

/** SDE Data: the UI over the local Fuzzwork SDE cache (data/sde/), i.e. the
 * screen counterpart of the desktop build's `refresh-sde` CLI command.
 *
 * **Why its own drawer destination rather than a section of Settings.**
 * SettingsScreen exists on this platform, but it is a form over
 * `TradingConfig` fields (region ids, fees, thresholds) - values a user sets
 * once and occasionally tweaks. This is a different kind of action
 * (a ~19MB download with its own progress and its own "is it worth doing
 * right now" question), not a config value, so it gets its own screen next
 * to Characters and Settings rather than being squeezed into that form as an
 * odd extra card. Folding it into Settings later remains an easy move if
 * that ever reads better.
 *
 * The download warning is not boilerplate: this is ~19MB over whatever
 * connection the phone is on, which is a materially different proposition
 * from the same action on a desktop. Same honesty as
 * CandidateDiscoveryScreen's "can take a while" - tell the user what they are
 * about to spend before they spend it. "Check for Updates" exists precisely
 * so that spend can be avoided: one HEAD request, no body. */
@Composable
fun SdeDataScreen(database: AppDatabase) {
    val scope = rememberCoroutineScope()
    val repository = remember { SdeRepository(database) }

    var lastRefreshed by remember { mutableStateOf<String?>(null) }
    var rowCountSummary by remember { mutableStateOf("") }
    var status by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var progress by remember { mutableStateOf(0f) }

    suspend fun loadState() {
        lastRefreshed = repository.refreshState()?.refreshedAt
        val counts = repository.rowCounts()
        rowCountSummary = if (counts.values.sum() == 0) {
            "Cache is empty - nothing downloaded yet."
        } else {
            counts.entries.joinToString("\n") { (table, count) -> "$table: $count rows" }
        }
    }
    LaunchedEffect(Unit) { loadState() }

    Column(modifier = Modifier.fillMaxSize().padding(16.dp).verticalScroll(rememberScrollState())) {
        Text("SDE Data", style = MaterialTheme.typography.titleMedium)
        Spacer(Modifier.height(4.dp))
        Text(
            "A local copy of EVE's Static Data Export (type names, groups, categories, " +
                "market groups, stations, solar systems), republished as CSV by fuzzwork.co.uk " +
                "after every patch. Refreshing is its own update cycle, unrelated to app updates - " +
                "refresh after a CCP patch, not on a schedule.",
            style = MaterialTheme.typography.bodySmall,
        )

        Spacer(Modifier.height(12.dp))
        Text("Last refreshed: ${lastRefreshed ?: "never on this device"}")
        Spacer(Modifier.height(4.dp))
        Text(rowCountSummary, style = MaterialTheme.typography.bodySmall)

        Spacer(Modifier.height(12.dp))
        HorizontalDivider()
        Spacer(Modifier.height(12.dp))

        Text(
            "Refreshing downloads about 19 MB across six files. Prefer Wi-Fi, and keep this " +
                "screen open until it finishes - the cache is only replaced once every file has " +
                "downloaded, so interrupting it leaves the existing data untouched rather than " +
                "half-updated.",
            style = MaterialTheme.typography.bodySmall,
        )

        Spacer(Modifier.height(8.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(
                enabled = !busy,
                onClick = {
                    busy = true
                    progress = 0f
                    status = "Checking fuzzwork.co.uk for a newer dump..."
                    scope.launch {
                        try {
                            val staleness = repository.checkForNewerSde()
                            status = when {
                                !staleness.remoteCheckSucceeded ->
                                    "Could not reach fuzzwork.co.uk - staleness unknown."
                                staleness.newerSdeAvailable ->
                                    "A newer SDE dump is available - refreshing is worth it."
                                staleness.localRefreshedAt == null ->
                                    "Nothing cached yet, so there is nothing to compare - refresh to populate it."
                                else ->
                                    "Local cache matches the dump currently published."
                            }
                        } catch (e: Exception) {
                            status = e.message ?: "Update check failed."
                        } finally {
                            busy = false
                        }
                    }
                },
            ) { Text("Check for Updates") }

            Button(
                enabled = !busy,
                onClick = {
                    busy = true
                    progress = 0f
                    status = "Starting download..."
                    scope.launch {
                        try {
                            val result = repository.refresh { file, index, total ->
                                progress = (index - 1).toFloat() / total.toFloat()
                                status = "Downloading ${file.filename} ($index of $total)..."
                            }
                            loadState()
                            status = "Refreshed - ${result.totalRows} rows cached."
                        } catch (e: Exception) {
                            status = e.message ?: "SDE refresh failed - existing cache left unchanged."
                        } finally {
                            busy = false
                        }
                    }
                },
            ) { Text("Refresh SDE Data") }
        }

        if (busy) {
            LinearProgressIndicator(
                progress = { progress },
                modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp),
            )
        }

        Spacer(Modifier.height(8.dp))
        Text(status, style = MaterialTheme.typography.bodySmall)
    }
}
