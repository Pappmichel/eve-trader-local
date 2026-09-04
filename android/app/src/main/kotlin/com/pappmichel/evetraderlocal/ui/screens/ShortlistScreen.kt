package com.pappmichel.evetraderlocal.ui.screens

import androidx.compose.foundation.clickable
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

// Stable, never changes - distinct from cfg.jitaRegionId (The Forge
// region). Same constant own_orders.py's JITA_SOLAR_SYSTEM_ID names.
private const val JITA_SOLAR_SYSTEM_ID = 30000142

private fun fmtPct(value: Double?): String = if (value != null) "%.1f%%".format(value * 100) else "-"
private fun fmtIsk(value: Double?): String = if (value != null) "%,.2f".format(value) else "-"

/** Trading -> Shortlist: the live import/sell decision table, mirroring the
 * desktop build's trading_shortlist.py view - manual membership management
 * (add/remove/toggle-active) plus a "Refresh" action running
 * `evaluateShortlist` against live Jita + structure order-book prices, the
 * seller's own open sell orders there, and the buyer's own coverage (open
 * buy orders/existing inventory) - see `Shortlist.kt`'s own docstring for
 * what's still simplified vs. the desktop version: no Goonmetrics
 * fallback or Profit/Day, no auto-add/prune from Candidate Discovery. */
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
    var editingItem by remember { mutableStateOf<ShortlistItem?>(null) }

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
                // Best-effort: a stale/revoked refresh token here used to
                // abort the whole refresh (including the Jita-only pricing
                // above, which needs no login at all) - now it just means
                // no seller-specific data, same degrade-gracefully pattern
                // the rest of this function already follows.
                val sellerRecord = tokenManager.listRecords("seller").firstOrNull()
                var sellerTokenInvalid = false
                val sellerToken = sellerRecord?.let {
                    try {
                        tokenManager.getToken(it.role)
                    } catch (e: Exception) {
                        sellerTokenInvalid = true
                        null
                    }
                }
                val structureStats = if (cfg.structureId != null && sellerToken != null) {
                    esi.structureOrderStatsBulk(cfg.structureId, activeIds, sellerToken.accessToken)
                } else emptyMap()
                // How much of each item the seller already has listed for
                // sale at the structure right now - see own_orders.py's
                // fetch_own_sell_orders. One of two independent signals
                // gating the Import/"Already ordered" decision (the other,
                // buyer coverage, is computed below). Best-effort: a seller
                // character authorized before this scope existed won't have
                // esi-markets.read_character_orders.v1 yet, so a 403 here
                // falls back to "none known" rather than failing the whole
                // refresh - re-logging in as Seller picks up the scope.
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

                // Items the buyer needn't import more of: either an open
                // BUY order in Jita or at the structure, or existing
                // inventory at a Jita station or the structure - see
                // own_orders.py's fetch_buyer_already_covered. Best-effort
                // for the same re-login-picks-up-new-scopes reason as
                // ownOrdersByItem above.
                var buyerCoveredUnavailable = false
                val buyerToken = tokenManager.listRecords("buyer").firstOrNull()?.let {
                    try { tokenManager.getToken(it.role) } catch (e: Exception) { null }
                }
                val buyerAlreadyCoveredIds = if (buyerToken != null) {
                    try {
                        val covered = mutableSetOf<Int>()
                        esi.characterOrders(buyerToken.characterId, buyerToken.accessToken)
                            .filter { it.isBuyOrder && (it.regionId == cfg.jitaRegionId || it.locationId == cfg.structureId) }
                            .forEach { covered.add(it.typeId) }
                        val jitaStationIds = esi.solarSystemStationIds(JITA_SOLAR_SYSTEM_ID).toSet()
                        esi.characterAssets(buyerToken.characterId, buyerToken.accessToken)
                            .filter { it.locationId in jitaStationIds || it.locationId == cfg.structureId }
                            .forEach { covered.add(it.typeId) }
                        covered
                    } catch (e: Exception) {
                        buyerCoveredUnavailable = true
                        emptySet()
                    }
                } else emptySet()

                val evaluated = evaluateShortlist(items, jitaStats, structureStats, cfg, ownOrdersByItem, buyerAlreadyCoveredIds)
                rows = evaluated.sortedByDescending { it.margin ?: Double.NEGATIVE_INFINITY }
                val summary = summaryCounts(evaluated)
                status = "Import: ${summary.importCandidates} · Already ordered: ${summary.alreadyOrdered} · " +
                    "Skipped: ${summary.skipped} · Avg margin: ${fmtPct(summary.avgMargin)}"
                if (cfg.structureId == null) status += " (no structure configured - Net Sell left blank)"
                else if (sellerTokenInvalid) status += " (seller token invalid - re-log in as Seller - Net Sell left blank)"
                else if (structureStats.isEmpty() && activeIds.isNotEmpty()) status += " (no seller character logged in - Net Sell left blank)"
                if (ownOrdersUnavailable) status += " (couldn't read own orders - re-log in as Seller for the read_character_orders scope)"
                if (buyerCoveredUnavailable) status += " (couldn't check buyer coverage - re-log in as Buyer for the orders/assets scopes)"
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
                        modifier = Modifier.clickable { editingItem = item },
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
        ShortlistItemDialog(
            existing = null,
            isDuplicateTypeId = { typeId -> items.any { it.itemId == typeId } },
            onDismiss = { showAddDialog = false },
            onSave = { newItem ->
                persist(items + newItem)
                showAddDialog = false
            },
        )
    }
    editingItem?.let { existing ->
        ShortlistItemDialog(
            existing = existing,
            isDuplicateTypeId = { typeId -> typeId != existing.itemId && items.any { it.itemId == typeId } },
            onDismiss = { editingItem = null },
            onSave = { edited ->
                persist(items.map { if (it == existing) edited else it })
                editingItem = null
            },
        )
    }
}

/** Add-or-edit dialog: `existing == null` means "Add shortlist item" (a
 * fresh, always-active entry); otherwise this pre-fills from - and
 * preserves - that item's `active` flag while editing its other fields.
 * Tapping a row in the list (as opposed to its checkbox/delete button)
 * opens this in edit mode - the only way to fix a typo used to be delete
 * and re-add. `isDuplicateTypeId` blocks saving a type ID already used by
 * a *different* shortlist entry - unlike Candidate Discovery's Add button
 * (which silently no-ops via `ShortlistRepository.addIfAbsent`), this is
 * a manual form, so a rejected duplicate needs to say why rather than
 * just doing nothing. */
@Composable
private fun ShortlistItemDialog(
    existing: ShortlistItem?, isDuplicateTypeId: (Int) -> Boolean,
    onDismiss: () -> Unit, onSave: (ShortlistItem) -> Unit,
) {
    var name by remember { mutableStateOf(existing?.item ?: "") }
    var typeId by remember { mutableStateOf(existing?.itemId?.takeIf { it != 0 }?.toString() ?: "") }
    var category by remember { mutableStateOf(existing?.category ?: "") }
    var volume by remember { mutableStateOf(existing?.volumeM3?.toString() ?: "") }
    var duplicateError by remember { mutableStateOf(false) }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(if (existing == null) "Add shortlist item" else "Edit shortlist item") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(value = name, onValueChange = { name = it }, label = { Text("Item name") })
                OutlinedTextField(
                    value = typeId, onValueChange = { typeId = it; duplicateError = false },
                    label = { Text("Type ID") }, isError = duplicateError,
                    supportingText = { if (duplicateError) Text("Already on the shortlist") },
                )
                OutlinedTextField(value = category, onValueChange = { category = it }, label = { Text("Category") })
                OutlinedTextField(value = volume, onValueChange = { volume = it }, label = { Text("Volume (m3)") })
            }
        },
        confirmButton = {
            // .trim() so stray whitespace (an easy copy-paste artifact)
            // doesn't turn an otherwise-valid number into a rejected one.
            TextButton(
                enabled = name.isNotBlank() && typeId.trim().toIntOrNull() != null && volume.trim().toDoubleOrNull() != null,
                onClick = {
                    val newTypeId = typeId.trim().toIntOrNull() ?: 0
                    if (isDuplicateTypeId(newTypeId)) {
                        duplicateError = true
                        return@TextButton
                    }
                    onSave(
                        ShortlistItem(
                            item = name.trim(), itemId = newTypeId, category = category.trim(),
                            volumeM3 = volume.trim().toDoubleOrNull() ?: 0.0, active = existing?.active ?: true,
                        )
                    )
                },
            ) { Text(if (existing == null) "Add" else "Save") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}
