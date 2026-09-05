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
import com.pappmichel.evetraderlocal.data.auth.TokenManager
import com.pappmichel.evetraderlocal.data.db.AppDatabase
import com.pappmichel.evetraderlocal.data.doctrine.ContractHistory
import com.pappmichel.evetraderlocal.data.doctrine.ContractSync
import com.pappmichel.evetraderlocal.data.doctrine.DoctrineContractHistoryRepository
import com.pappmichel.evetraderlocal.data.doctrine.DoctrineFittingRepository
import com.pappmichel.evetraderlocal.data.esi.EsiClient
import com.pappmichel.evetraderlocal.data.sde.SdeRepository
import com.pappmichel.evetraderlocal.data.trading.TradingConfigRepository
import kotlinx.coroutines.launch

private fun fmtIsk(value: Double?): String = value?.let { "%,.2f ISK".format(it) } ?: "-"

/** Doctrine -> Contract History: **now the real, permanent, fitting-matched
 * history** - a genuine port of desktop's `do_contract_history` action, on
 * top of [ContractSync]'s real matching engine and
 * [DoctrineContractHistoryRepository]'s permanent Room store (GitHub issue
 * #19). "Sync Contracts" fetches the Doctrine character's current contracts,
 * matches them against every saved active fitting, and upserts any
 * newly-finished match into that store; the list below always reads the
 * store back, so a contract stays visible long after ESI itself stops
 * returning it. See [ContractSync]'s own docstring for exactly what
 * "matching"/"permanent" mean here and the three scope decisions vs.
 * desktop's fuller engine (no corp contracts, no persisted "active"
 * snapshot, no acceptor-name resolution).
 *
 * "Show Live ESI Listing" keeps the previous pass's simpler, unmatched view
 * available underneath - see [ContractHistory.fetch]'s own docstring for why
 * that secondary view is still worth keeping (e.g. while still building out
 * a fitting library, before anything can match yet).
 *
 * Same "don't block on missing auth" shape as every other authenticated
 * live-fetch screen on this platform: no "Doctrine" character logged in
 * just surfaces as a status-line message pointing at Characters, not a
 * crash - the permanent-history list itself still loads and displays fine
 * with no character logged in at all, since it's a local Room read. */
@Composable
fun DoctrineContractHistoryScreen(database: AppDatabase, tokenManager: TokenManager) {
    val scope = rememberCoroutineScope()
    val sdeRepo = remember { SdeRepository(database) }
    val fittingRepo = remember { DoctrineFittingRepository(database) }
    val historyRepo = remember { DoctrineContractHistoryRepository(database) }
    val tradingCfgRepo = remember { TradingConfigRepository(database) }
    val esi = remember { EsiClient() }

    var permanentRows by remember { mutableStateOf<List<ContractHistory.PermanentRow>>(emptyList()) }
    var liveRows by remember { mutableStateOf<List<ContractHistory.Row>>(emptyList()) }
    var showLive by remember { mutableStateOf(false) }
    var status by remember { mutableStateOf("Loading permanent history...") }
    var busy by remember { mutableStateOf(false) }

    fun loadPermanent() {
        scope.launch {
            try {
                permanentRows = ContractHistory.loadPermanentHistory(historyRepo, sdeRepo)
                status = if (permanentRows.isEmpty()) {
                    "No permanently-recorded contracts yet - press Sync Contracts once a doctrine sale has finished."
                } else {
                    "${permanentRows.size} permanently-recorded contract(s)."
                }
            } catch (e: Exception) {
                status = e.message ?: "Could not load permanent history."
            }
        }
    }

    LaunchedEffect(Unit) { loadPermanent() }

    fun syncContracts() {
        busy = true
        status = "Syncing contracts..."
        scope.launch {
            try {
                val record = tokenManager.listRecords("doctrine").firstOrNull()
                    ?: error("No 'Doctrine' character is logged in yet (see Characters).")
                val structureId = tradingCfgRepo.load().structureId
                    ?: error("No structure configured yet (see Settings) - contract sync needs one to filter by.")
                val token = tokenManager.getToken(record.role)
                val candidates = ContractSync.loadCandidates(fittingRepo.listActive())
                val outcome = ContractHistory.syncAndPersist(
                    esi = esi, historyRepo = historyRepo, characterId = token.characterId,
                    accessToken = token.accessToken, structureId = structureId, candidates = candidates,
                    groupIdOf = { typeId -> sdeRepo.type(typeId)?.groupId },
                )
                loadPermanent()
                status = "Synced: ${outcome.historyRows.size} newly-finished matched contract(s) recorded, " +
                    "${outcome.activeContracts.size} currently active matched contract(s)."
            } catch (e: Exception) {
                status = e.message ?: "Could not sync contracts."
            } finally {
                busy = false
            }
        }
    }

    fun fetchLive() {
        busy = true
        scope.launch {
            try {
                val record = tokenManager.listRecords("doctrine").firstOrNull()
                    ?: error("No 'Doctrine' character is logged in yet (see Characters).")
                val token = tokenManager.getToken(record.role)
                liveRows = ContractHistory.fetch(esi, sdeRepo, token.characterId, token.accessToken)
                status = "${liveRows.size} finished contract(s) currently visible to ESI (unmatched raw listing)."
            } catch (e: Exception) {
                status = e.message ?: "Could not fetch live contract listing."
            } finally {
                busy = false
            }
        }
    }

    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        Text("Doctrine - Contract History", style = MaterialTheme.typography.titleLarge)
        Text(
            "Permanent, fitting-matched history - survives past ESI's own rolling contract-visibility window. " +
                "See this screen's own docstring for what's simplified vs. the desktop engine.",
            style = MaterialTheme.typography.bodySmall,
            modifier = Modifier.padding(vertical = 4.dp),
        )
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(vertical = 8.dp)) {
            Button(onClick = { syncContracts() }, enabled = !busy) { Text("Sync Contracts") }
            Button(onClick = { showLive = !showLive; if (showLive && liveRows.isEmpty()) fetchLive() }, enabled = !busy) {
                Text(if (showLive) "Hide Live ESI Listing" else "Show Live ESI Listing")
            }
        }
        if (busy) LinearProgressIndicator(modifier = Modifier.fillMaxWidth().padding(bottom = 8.dp))
        Text(status, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(bottom = 8.dp))

        if (!showLive) {
            LazyColumn(modifier = Modifier.fillMaxWidth()) {
                items(permanentRows) { row ->
                    ListItem(
                        headlineContent = { Text(row.title?.takeIf { it.isNotBlank() } ?: "Contract ${row.contractId}") },
                        supportingContent = {
                            Column {
                                Text("${row.fittingName} (${row.hullName}) · Status: ${row.status}")
                                Text(
                                    "Completed: ${row.dateCompleted?.take(10) ?: "-"} · Buyer: ${row.acceptorId ?: "-"}",
                                    style = MaterialTheme.typography.bodySmall,
                                )
                            }
                        },
                        trailingContent = { Text(fmtIsk(row.price)) },
                    )
                    HorizontalDivider()
                }
            }
        } else {
            Text(
                "Every currently-visible finished contract per ESI, not matched to any saved fitting.",
                style = MaterialTheme.typography.bodySmall,
                modifier = Modifier.padding(bottom = 4.dp),
            )
            LazyColumn(modifier = Modifier.fillMaxWidth()) {
                items(liveRows) { row ->
                    ListItem(
                        headlineContent = { Text(row.title?.takeIf { it.isNotBlank() } ?: "Contract ${row.contractId}") },
                        supportingContent = {
                            Column {
                                Text("Status: ${row.status} · Completed: ${row.dateCompleted?.take(10) ?: "-"}")
                                Text(
                                    row.items.joinToString(", ") { "${it.typeName} x${it.quantity}" }
                                        .ifBlank { "(no items resolved)" },
                                    style = MaterialTheme.typography.bodySmall,
                                )
                            }
                        },
                        trailingContent = { Text(fmtIsk(row.price)) },
                    )
                    HorizontalDivider()
                }
            }
        }
    }
}
