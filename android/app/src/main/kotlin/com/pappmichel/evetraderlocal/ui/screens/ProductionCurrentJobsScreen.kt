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
import com.pappmichel.evetraderlocal.data.esi.EsiClient
import com.pappmichel.evetraderlocal.data.esi.IndustryJob
import com.pappmichel.evetraderlocal.data.production.CharacterSlotRow
import com.pappmichel.evetraderlocal.data.production.IndustryJobRow
import com.pappmichel.evetraderlocal.data.production.PRODUCER_ROLE_PREFIX
import com.pappmichel.evetraderlocal.data.production.ProductionConfigRepository
import com.pappmichel.evetraderlocal.data.production.buildIndustryJobRows
import com.pappmichel.evetraderlocal.data.production.characterSlotOverview
import com.pappmichel.evetraderlocal.data.production.homePrices
import com.pappmichel.evetraderlocal.data.production.jitaPrices
import com.pappmichel.evetraderlocal.data.sde.SdeRepository
import com.pappmichel.evetraderlocal.data.trading.TradingConfigRepository
import kotlinx.coroutines.launch

private fun fmtIsk(value: Double): String = "%,.2f ISK".format(value)

private fun fmtRemaining(seconds: Double?): String {
    if (seconds == null) return "?"
    if (seconds <= 0) return "done"
    val totalMinutes = (seconds / 60).toLong()
    val hours = totalMinutes / 60
    val minutes = totalMinutes % 60
    return if (hours > 0) "${hours}h ${minutes}m" else "${minutes}m"
}

/** Production -> Current Jobs & Slots: every active/paused/ready industry
 * job across every logged-in "Producer" character, plus a per-character,
 * per-category slot usage summary - a Kotlin port of the desktop build's
 * `production/jobs.py` (`list_current_jobs`/`character_slot_overview`), now
 * live off ESI rather than a synced local cache (see `ProductionJobs.kt`'s
 * own module docstring for the full list of differences, including why
 * this port can show real total/free slot counts the *local* desktop
 * build's own reduced version cannot).
 *
 * Corp jobs are folded in the same way ESI itself returns them for a
 * character with the right corp roles; if more than one logged-in Producer
 * character sees the same corp job, it is de-duplicated by job_id (last
 * writer wins) rather than shown twice. */
@Composable
fun ProductionCurrentJobsScreen(database: AppDatabase, tokenManager: TokenManager) {
    val scope = rememberCoroutineScope()
    val sdeRepo = remember { SdeRepository(database) }
    val productionConfigRepo = remember { ProductionConfigRepository(database) }
    val tradingConfigRepo = remember { TradingConfigRepository(database) }
    val esi = remember { EsiClient() }

    var busy by remember { mutableStateOf(false) }
    var status by remember { mutableStateOf("Loading...") }
    var jobRows by remember { mutableStateOf<List<IndustryJobRow>>(emptyList()) }
    var slotRows by remember { mutableStateOf<List<CharacterSlotRow>>(emptyList()) }

    fun refresh() {
        busy = true
        status = "Fetching industry jobs..."
        scope.launch {
            try {
                val records = tokenManager.listRecords(PRODUCER_ROLE_PREFIX)
                if (records.isEmpty()) {
                    jobRows = emptyList()
                    slotRows = emptyList()
                    status = "No Producer characters logged in - log in under Characters first."
                    return@launch
                }

                val installerNames = records.associate { it.characterId to it.characterName }
                val jobsById = mutableMapOf<Long, IndustryJob>()
                val skillLevelsByCharacter = mutableMapOf<Long, Map<Int, Int>>()

                for (record in records) {
                    try {
                        val token = tokenManager.getToken(record.role)
                        esi.characterIndustryJobs(token.characterId, token.accessToken).forEach {
                            jobsById[it.jobId] = it
                        }
                        val skills = esi.characterSkills(token.characterId, token.accessToken)
                        skillLevelsByCharacter[token.characterId] =
                            skills.associate { it.skillId to it.activeSkillLevel }
                    } catch (e: Exception) {
                        // Best-effort per character - a stale/expired token
                        // for one Producer shouldn't hide every other one's
                        // jobs, same reasoning CharactersScreen's own wallet
                        // balance fetch already uses.
                    }
                }
                val jobs = jobsById.values.toList()

                val productTypeIds = jobs.mapNotNull { it.productTypeId }.distinct()
                val blueprintTypeIds = jobs.map { it.blueprintTypeId }.distinct()
                val typeNames = (productTypeIds + blueprintTypeIds).distinct()
                    .mapNotNull { id -> sdeRepo.type(id)?.typeName?.let { id to it } }
                    .toMap()

                val productionCfg = productionConfigRepo.load()
                val tradingCfg = tradingConfigRepo.load()
                val home = homePrices(productTypeIds, productionCfg, tokenManager, esi)
                val jita = jitaPrices(productTypeIds, tradingCfg.jitaRegionId, esi)

                jobRows = buildIndustryJobRows(jobs, home, jita, sdeRepo, installerNames, typeNames)
                slotRows = characterSlotOverview(jobs, installerNames, skillLevelsByCharacter)
                status = "${jobRows.size} job(s) across ${records.size} character(s)."
            } catch (e: Exception) {
                status = e.message ?: "Failed to load jobs."
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

        if (slotRows.isNotEmpty()) {
            Text("Slots", style = MaterialTheme.typography.titleSmall, modifier = Modifier.padding(top = 8.dp))
            for (row in slotRows) {
                val totalText = row.totalSlots?.let { " / $it (${row.freeSlots} free)" } ?: ""
                Text(
                    "${row.characterName} - ${row.jobType}: ${row.usedSlots}$totalText",
                    style = MaterialTheme.typography.bodyMedium,
                )
            }
            HorizontalDivider(modifier = Modifier.padding(vertical = 8.dp))
        }

        Text("Jobs", style = MaterialTheme.typography.titleSmall)
        LazyColumn(modifier = Modifier.weight(1f)) {
            items(jobRows) { row ->
                ListItem(
                    headlineContent = { Text(row.typeName) },
                    supportingContent = {
                        Text(
                            "${row.activity} x${row.runs} - ${row.status} - ${row.installerName} - " +
                                fmtRemaining(row.remainingSeconds)
                        )
                    },
                    trailingContent = { row.outputValue?.let { Text(fmtIsk(it)) } },
                )
                HorizontalDivider()
            }
        }
    }
}
