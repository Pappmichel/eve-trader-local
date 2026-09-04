package com.pappmichel.evetraderlocal.ui.screens

import androidx.compose.foundation.layout.Arrangement
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
import com.pappmichel.evetraderlocal.data.esi.EsiClient
import com.pappmichel.evetraderlocal.data.sde.SdeRepository
import kotlinx.coroutines.launch

private fun fmtIsk(value: Double?): String = value?.let { "%,.2f ISK".format(it) } ?: "-"

/** Doctrine -> Contract History: every currently-visible finished contract
 * for the logged-in "Doctrine" character, with item names resolved - see
 * [ContractHistory]'s own docstring for why this is a simple read-only ESI
 * listing rather than a port of desktop's permanent, fitting-matched
 * `do_contract_history` action (that needs the full contract-sync/matching
 * engine, out of scope for this pass).
 *
 * Same "don't block on missing auth" shape as every other authenticated
 * live-fetch screen on this platform: no "Doctrine" character logged in
 * just surfaces as a status-line message pointing at Characters, not a
 * crash. */
@Composable
fun DoctrineContractHistoryScreen(database: AppDatabase, tokenManager: TokenManager) {
    val scope = rememberCoroutineScope()
    val sdeRepo = remember { SdeRepository(database) }
    val esi = remember { EsiClient() }

    var rows by remember { mutableStateOf<List<ContractHistory.Row>>(emptyList()) }
    var status by remember {
        mutableStateOf("Not fetched yet - always live, nothing is cached between refreshes.")
    }
    var busy by remember { mutableStateOf(false) }

    fun refresh() {
        busy = true
        status = "Fetching contracts..."
        rows = emptyList()
        scope.launch {
            try {
                val record = tokenManager.listRecords("doctrine").firstOrNull()
                    ?: error("No 'Doctrine' character is logged in yet (see Characters).")
                val token = tokenManager.getToken(record.role)
                rows = ContractHistory.fetch(esi, sdeRepo, token.characterId, token.accessToken)
                status = if (rows.isEmpty()) {
                    "No finished contracts currently visible to ESI for ${record.role} " +
                        "(ESI only retains ~30 days, or still-outstanding contracts)."
                } else {
                    "${rows.size} finished contract(s)."
                }
            } catch (e: Exception) {
                status = e.message ?: "Could not fetch contract history."
            } finally {
                busy = false
            }
        }
    }

    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        Text("Doctrine - Contract History", style = MaterialTheme.typography.titleLarge)
        Text(
            "Read-only listing of currently-visible finished contracts, not matched to any saved fitting - " +
                "see this screen's own docstring.",
            style = MaterialTheme.typography.bodySmall,
            modifier = Modifier.padding(vertical = 4.dp),
        )
        Button(onClick = { refresh() }, enabled = !busy, modifier = Modifier.padding(vertical = 8.dp)) {
            Text("Refresh")
        }
        if (busy) LinearProgressIndicator(modifier = Modifier.fillMaxWidth().padding(bottom = 8.dp))
        Text(status, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(bottom = 8.dp))

        LazyColumn(modifier = Modifier.fillMaxWidth()) {
            items(rows) { row ->
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
