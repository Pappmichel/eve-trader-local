package com.pappmichel.evetraderlocal.ui.screens

import androidx.compose.foundation.layout.Column
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
import com.pappmichel.evetraderlocal.data.auth.TokenManager
import com.pappmichel.evetraderlocal.data.db.AppDatabase
import com.pappmichel.evetraderlocal.data.esi.CharacterBlueprint
import com.pappmichel.evetraderlocal.data.esi.EsiClient
import com.pappmichel.evetraderlocal.data.production.OwnedBlueprintRow
import com.pappmichel.evetraderlocal.data.production.PRODUCER_ROLE_PREFIX
import com.pappmichel.evetraderlocal.data.production.groupOwnedBlueprints
import com.pappmichel.evetraderlocal.data.sde.SdeRepository
import kotlinx.coroutines.launch

/**
 * Production -> Owned Blueprints: every owned blueprint (character + corp)
 * across every logged-in "Producer" character, aggregated by (type, BPO/BPC,
 * ME, TE, runs) - a Kotlin port of the desktop build's `production/engine.py`
 * `list_owned_blueprints` (`production_owned_blueprints.py`'s
 * `OwnedBlueprintsView`), now a live ESI read rather than a synced local
 * cache - see `OwnedBlueprints.kt`'s own module docstring for the full list
 * of differences from the desktop version.
 *
 * A blueprint reported by more than one logged-in Producer character (a corp
 * blueprint visible to more than one of them) is de-duplicated by `item_id`
 * before grouping, same "ask each logged-in character, merge, dedupe"
 * pattern `ProductionCurrentJobsScreen.kt` already uses for industry jobs.
 */
@Composable
fun OwnedBlueprintsScreen(database: AppDatabase, tokenManager: TokenManager) {
    val scope = rememberCoroutineScope()
    val sdeRepo = remember { SdeRepository(database) }
    val esi = remember { EsiClient() }

    var busy by remember { mutableStateOf(false) }
    var status by remember { mutableStateOf("Loading...") }
    var rows by remember { mutableStateOf<List<OwnedBlueprintRow>>(emptyList()) }

    fun refresh() {
        busy = true
        status = "Fetching owned blueprints..."
        scope.launch {
            try {
                val records = tokenManager.listRecords(PRODUCER_ROLE_PREFIX)
                if (records.isEmpty()) {
                    rows = emptyList()
                    status = "No Producer characters logged in - log in under Characters first."
                    return@launch
                }

                val blueprintsById = mutableMapOf<Long, CharacterBlueprint>()
                var anySucceeded = false
                for (record in records) {
                    try {
                        val token = tokenManager.getToken(record.role)
                        esi.characterBlueprints(token.characterId, token.accessToken).forEach {
                            blueprintsById[it.itemId] = it
                        }
                        anySucceeded = true
                    } catch (e: Exception) {
                        // Best-effort per character - a stale/expired token
                        // for one Producer shouldn't hide every other one's
                        // blueprints, same reasoning ProductionCurrentJobsScreen
                        // and CharactersScreen's own wallet fetch already use.
                    }
                }
                val blueprints = blueprintsById.values.toList()

                val typeNames = blueprints.map { it.typeId }.distinct()
                    .mapNotNull { id -> sdeRepo.typeName(id)?.let { id to it } }
                    .toMap()

                rows = groupOwnedBlueprints(blueprints, typeNames)
                status = when {
                    rows.isNotEmpty() -> "${rows.size} owned blueprint group(s) across ${records.size} character(s)."
                    anySucceeded -> "No owned blueprints found."
                    else -> "Failed to fetch blueprints for every logged-in Producer character."
                }
            } catch (e: Exception) {
                status = e.message ?: "Failed to load owned blueprints."
            } finally {
                busy = false
            }
        }
    }

    LaunchedEffect(Unit) { refresh() }

    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        Button(enabled = !busy, onClick = { refresh() }) { Text("Refresh") }
        if (busy) {
            LinearProgressIndicator(modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp))
        }
        Text(status, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(vertical = 8.dp))

        LazyColumn(modifier = Modifier.weight(1f)) {
            items(rows) { row ->
                val kind = if (row.isOriginal) "BPO" else "BPC"
                val runsText = row.runs?.toString() ?: "-"
                ListItem(
                    headlineContent = { Text(row.typeName) },
                    supportingContent = {
                        Text(
                            "$kind x${row.quantity} - ME ${row.materialEfficiency} / " +
                                "TE ${row.timeEfficiency} - Runs: $runsText"
                        )
                    },
                )
                HorizontalDivider()
            }
        }
    }
}
