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
import com.pappmichel.evetraderlocal.data.trading.STATION_TRADING_SKILL_LABELS
import com.pappmichel.evetraderlocal.data.trading.StationTradingConfigRepository
import com.pappmichel.evetraderlocal.data.trading.TradingConfigRepository
import com.pappmichel.evetraderlocal.data.trading.UndercutRow
import com.pappmichel.evetraderlocal.data.trading.checkBuyUndercutPooled
import com.pappmichel.evetraderlocal.data.trading.checkUndercutPooled
import com.pappmichel.evetraderlocal.data.trading.orderSlotsFromSkills
import kotlinx.coroutines.launch

private data class TraderSkillSummary(
    val characterName: String,
    val levels: Map<String, Int>,
    val orderSlots: Int?,
    val error: String? = null,
)

/** Station Trading -> Undercut & Skills: a Kotlin port of the desktop
 * build's `do_check_undercut`/`do_get_skill_summary`, combined into one
 * screen (the drawer entry itself is named "Undercut & Skills", matching
 * `ToolMenus.kt`).
 *
 * Undercut Check is bidirectional and pooled across every registered
 * "trader" character (see `StationTradingUndercut.kt`'s own docstring for
 * why pooling costs nothing extra here, unlike Trading's buyer/seller
 * split) - flags any of their own sell *or* buy orders at Jita's trade hub
 * that a genuinely different market participant now beats.
 *
 * Skills is informational only: pulled trade-skill levels per trader
 * character plus the derived order-slot count. Fee/tax discount skills
 * (Accounting, Broker Relations, Advanced Broker Relations) are shown as
 * raw levels, never turned into a numeric discount - those also depend on
 * NPC corp standings this app has no way to read (same omission the
 * desktop build documents in constants.py). */
@Composable
fun StationTradingUndercutScreen(database: AppDatabase, tokenManager: TokenManager) {
    val scope = rememberCoroutineScope()
    val tradingConfigRepo = remember { TradingConfigRepository(database) }
    val stationConfigRepo = remember { StationTradingConfigRepository(database) }
    val esi = remember { EsiClient() }

    var sellRows by remember { mutableStateOf<List<UndercutRow>>(emptyList()) }
    var buyRows by remember { mutableStateOf<List<UndercutRow>>(emptyList()) }
    var undercutStatus by remember {
        mutableStateOf("Not run yet - checks every registered trader's own orders at Jita's trade hub.")
    }
    var undercutBusy by remember { mutableStateOf(false) }

    var skillSummaries by remember { mutableStateOf<List<TraderSkillSummary>>(emptyList()) }
    var skillsStatus by remember { mutableStateOf("Not run yet.") }
    var skillsBusy by remember { mutableStateOf(false) }

    fun checkUndercut() {
        undercutBusy = true
        undercutStatus = "Loading trader characters..."
        scope.launch {
            try {
                val traderRecords = tokenManager.listRecords("trader")
                if (traderRecords.isEmpty()) {
                    undercutStatus = "No trader characters registered yet (see Characters)."
                    return@launch
                }
                val traders = traderRecords.map { it.characterId to tokenManager.getToken(it.role).accessToken }
                val cfg = stationConfigRepo.load()
                val tradingCfg = tradingConfigRepo.load()
                undercutStatus = "Checking sell and buy orders..."
                sellRows = checkUndercutPooled(traders, esi, tradingCfg.jitaRegionId, cfg)
                buyRows = checkBuyUndercutPooled(traders, esi, tradingCfg.jitaRegionId, cfg)
                undercutStatus = "${sellRows.size} sell-side, ${buyRows.size} buy-side undercut(s) found."
            } catch (e: Exception) {
                undercutStatus = e.message ?: "Undercut check failed."
            } finally {
                undercutBusy = false
            }
        }
    }

    fun loadSkills() {
        skillsBusy = true
        skillsStatus = "Loading trader characters..."
        scope.launch {
            try {
                val traderRecords = tokenManager.listRecords("trader")
                if (traderRecords.isEmpty()) {
                    skillsStatus = "No trader characters registered yet (see Characters)."
                    return@launch
                }
                skillSummaries = traderRecords.map { record ->
                    try {
                        val token = tokenManager.getToken(record.role)
                        val levelsBySkillId = esi.characterSkills(record.characterId, token.accessToken)
                            .associate { it.skillId to it.activeSkillLevel }
                        val levelsByLabel = STATION_TRADING_SKILL_LABELS.entries.associate { (skillId, label) ->
                            label to (levelsBySkillId[skillId] ?: 0)
                        }
                        TraderSkillSummary(
                            characterName = record.characterName,
                            levels = levelsByLabel,
                            orderSlots = orderSlotsFromSkills(levelsBySkillId),
                        )
                    } catch (e: Exception) {
                        TraderSkillSummary(record.characterName, emptyMap(), null, e.message ?: "skipped")
                    }
                }
                skillsStatus = "${skillSummaries.size} trader character(s)."
            } catch (e: Exception) {
                skillsStatus = e.message ?: "Could not load skills."
            } finally {
                skillsBusy = false
            }
        }
    }

    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        Text("Undercut Check", style = MaterialTheme.typography.titleMedium)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(enabled = !undercutBusy, onClick = { checkUndercut() }) { Text("Check Undercuts") }
        }
        if (undercutBusy) {
            LinearProgressIndicator(modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp))
        }
        Text(undercutStatus, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(vertical = 8.dp))

        if (sellRows.isNotEmpty()) {
            Text("Sell side", style = MaterialTheme.typography.labelLarge)
            sellRows.forEach { UndercutRowItem(it) }
        }
        if (buyRows.isNotEmpty()) {
            Text("Buy side", style = MaterialTheme.typography.labelLarge)
            buyRows.forEach { UndercutRowItem(it) }
        }

        HorizontalDivider(modifier = Modifier.padding(vertical = 16.dp))

        Text("Skills", style = MaterialTheme.typography.titleMedium)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(enabled = !skillsBusy, onClick = { loadSkills() }) { Text("Load Skills") }
        }
        if (skillsBusy) {
            LinearProgressIndicator(modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp))
        }
        Text(skillsStatus, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(vertical = 8.dp))

        LazyColumn {
            items(skillSummaries) { summary ->
                ListItem(
                    headlineContent = { Text(summary.characterName) },
                    supportingContent = {
                        Text(
                            summary.error ?: (
                                summary.levels.entries.joinToString(", ") { (label, level) -> "$label $level" } +
                                    " · ${summary.orderSlots} order slots"
                                )
                        )
                    },
                )
                HorizontalDivider()
            }
        }
    }
}

@Composable
private fun UndercutRowItem(row: UndercutRow) {
    ListItem(
        headlineContent = { Text("Type ${row.typeId}") },
        supportingContent = { Text("mine ${"%,.2f".format(row.myPrice)} vs. ${"%,.2f".format(row.competitorPrice)}") },
        trailingContent = { Text("%,.2f".format(row.difference)) },
    )
    HorizontalDivider()
}
