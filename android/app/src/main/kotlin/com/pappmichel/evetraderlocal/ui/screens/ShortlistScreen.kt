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
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
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
import com.pappmichel.evetraderlocal.data.trading.ShortlistItem
import com.pappmichel.evetraderlocal.data.trading.ShortlistRepository
import com.pappmichel.evetraderlocal.data.trading.ShortlistRow
import com.pappmichel.evetraderlocal.data.trading.TradingConfigRepository
import com.pappmichel.evetraderlocal.data.trading.evaluateShortlist
import com.pappmichel.evetraderlocal.data.trading.summaryCounts
import kotlinx.coroutines.launch

private fun fmtPct(value: Double?): String = if (value != null) "%.1f%%".format(value * 100) else "-"
private fun fmtIsk(value: Double?): String = if (value != null) "%,.2f".format(value) else "-"

/** Trading -> Shortlist: the live import/sell decision table, mirroring the
 * desktop build's trading_shortlist.py view - manual membership management
 * (add/remove/toggle-active) plus a "Refresh" action running
 * `evaluateShortlist` against live Jita + structure order-book prices plus
 * the seller's own open sell orders there (see `Shortlist.kt`'s own
 * docstring for what's still simplified vs. the desktop version: no
 * buyer-covered tracking, no Goonmetrics fallback or Profit/Day, no
 * auto-add/prune from Candidate Discovery). */
@Composable
fun ShortlistScreen(database: AppDatabase, tokenManager: TokenManager) {
    val scope = rememberCoroutineScope()
    val shortlistRepo = remember { ShortlistRepository(database) }
    val configRepo = remember { TradingConfigRepository(database) }

    var items by remember { mutableStateOf<List<ShortlistItem>>(emptyList()) }
    var rows by remember { mutableStateOf<List<ShortlistRow>>(emptyList()) }
    var status by remember { mutableStateOf("Loading saved shortlist...") }
    var busy by remember { mutableStateOf(false) }
    var showAddDialog by remember { mutableStateOf(false) }

    LaunchedEffect(Unit) {
        items = shortlistRepo.load()
        status = if (items.isEmpty()) "No shortlist items yet - add one, then Refresh." else
            "${items.size} item(s) - click Refresh for live prices."
    }

    fun persist(newItems: List<ShortlistItem>) {
        items = newItems
        scope.launch { shortlistRepo.save(newItems) }
    }

    fun refresh() {
        busy = true
        status = "Fetching live prices..."
        scope.launch {
            try {
                val cfg = configRepo.load()
                val activeIds = items.filter { it.itemId != 0 }.map { it.itemId }
                val esi = EsiClient()
                val jitaStats = esi.regionOrderStatsBulk(cfg.jitaRegionId, activeIds)
                val sellerToken = tokenManager.listRecords("seller").firstOrNull()?.let { tokenManager.getToken(it.role) }
                val structureStats = if (cfg.structureId != null && sellerToken != null) {
                    esi.structureOrderStatsBulk(cfg.structureId, activeIds, sellerToken.accessToken)
                } else emptyMap()
                // How much of each item the seller already has listed for
                // sale at the structure right now - see own_orders.py's
                // fetch_own_sell_orders. Gates the Import/"Already ordered"
                // decision in evaluateShortlistItem; buyer-covered (already
                // in inventory/on a buy order) isn't tracked yet. Best-effort:
                // a seller character authorized before this scope existed
                // won't have esi-markets.read_character_orders.v1 yet, so a
                // 403 here falls back to "none known" rather than failing
                // the whole refresh - re-logging in as Seller picks up the
                // scope.
                var ownOrdersUnavailable = false
                val ownOrdersByItem = if (cfg.structureId != null && sellerToken != null) {
                    try {
                        esi.characterOrders(sellerToken.characterId, sellerToken.accessToken)
                            .filter { !it.isBuyOrder && it.locationId == cfg.structureId }
                            .groupBy { it.typeId }
                            .mapValues { (_, orders) -> orders.sumOf { it.volumeRemain } }
                    } catch (e: Exception) {
                        ownOrdersUnavailable = true
                        emptyMap()
                    }
                } else emptyMap()

                val evaluated = evaluateShortlist(items, jitaStats, structureStats, cfg, ownOrdersByItem)
                rows = evaluated.sortedByDescending { it.margin ?: Double.NEGATIVE_INFINITY }
                val summary = summaryCounts(evaluated)
                status = "Import: ${summary.importCandidates} · Already ordered: ${summary.alreadyOrdered} · " +
                    "Skipped: ${summary.skipped} · Avg margin: ${fmtPct(summary.avgMargin)}"
                if (cfg.structureId == null) status += " (no structure configured - Net Sell left blank)"
                else if (structureStats.isEmpty() && activeIds.isNotEmpty()) status += " (no seller character logged in - Net Sell left blank)"
                if (ownOrdersUnavailable) status += " (couldn't read own orders - re-log in as Seller for the read_character_orders scope)"
            } catch (e: Exception) {
                status = e.message ?: "Refresh failed."
            } finally {
                busy = false
            }
        }
    }

    Scaffold(
        floatingActionButton = {
            FloatingActionButton(onClick = { showAddDialog = true }) {
                Icon(Icons.Filled.Add, contentDescription = "Add item")
            }
        },
    ) { padding ->
        Column(modifier = Modifier.fillMaxSize().padding(padding).padding(16.dp)) {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(enabled = !busy && items.isNotEmpty(), onClick = { refresh() }) { Text("Refresh") }
            }
            if (busy) {
                LinearProgressIndicator(modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp))
            }
            Text(status, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(vertical = 8.dp))

            val rowByItem = rows.associateBy { it.item }
            LazyColumn {
                items(items) { item ->
                    val row = rowByItem[item.item]
                    ListItem(
                        headlineContent = { Text(item.item) },
                        supportingContent = {
                            Text(
                                if (row != null) "${row.decision} · Margin ${fmtPct(row.margin)} · " +
                                    "Profit/Unit ${fmtIsk(row.profitPerUnit)}"
                                else "${item.category} · %.1f m3".format(item.volumeM3),
                            )
                        },
                        leadingContent = {
                            Checkbox(
                                checked = item.active,
                                onCheckedChange = { checked ->
                                    persist(items.map { if (it == item) it.copy(active = checked) else it })
                                },
                            )
                        },
                        trailingContent = {
                            IconButton(onClick = { persist(items.filter { it != item }) }) {
                                Icon(Icons.Filled.Delete, contentDescription = "Remove")
                            }
                        },
                    )
                    HorizontalDivider()
                }
            }
        }
    }

    if (showAddDialog) {
        AddShortlistItemDialog(
            onDismiss = { showAddDialog = false },
            onAdd = { newItem ->
                persist(items + newItem)
                showAddDialog = false
            },
        )
    }
}

@Composable
private fun AddShortlistItemDialog(onDismiss: () -> Unit, onAdd: (ShortlistItem) -> Unit) {
    var name by remember { mutableStateOf("") }
    var typeId by remember { mutableStateOf("") }
    var category by remember { mutableStateOf("") }
    var volume by remember { mutableStateOf("") }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Add shortlist item") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(value = name, onValueChange = { name = it }, label = { Text("Item name") })
                OutlinedTextField(value = typeId, onValueChange = { typeId = it }, label = { Text("Type ID") })
                OutlinedTextField(value = category, onValueChange = { category = it }, label = { Text("Category") })
                OutlinedTextField(value = volume, onValueChange = { volume = it }, label = { Text("Volume (m3)") })
            }
        },
        confirmButton = {
            TextButton(
                enabled = name.isNotBlank() && typeId.toIntOrNull() != null && volume.toDoubleOrNull() != null,
                onClick = {
                    onAdd(
                        ShortlistItem(
                            item = name, itemId = typeId.toIntOrNull() ?: 0, category = category,
                            volumeM3 = volume.toDoubleOrNull() ?: 0.0,
                        )
                    )
                },
            ) { Text("Add") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}
