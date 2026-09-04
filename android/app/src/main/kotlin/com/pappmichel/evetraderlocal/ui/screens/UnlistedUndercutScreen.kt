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
import com.pappmichel.evetraderlocal.data.trading.ShortlistRepository
import com.pappmichel.evetraderlocal.data.trading.TradingConfigRepository
import com.pappmichel.evetraderlocal.data.trading.UndercutResult
import com.pappmichel.evetraderlocal.data.trading.UnlistedStockResult
import com.pappmichel.evetraderlocal.data.trading.checkUndercut
import com.pappmichel.evetraderlocal.data.trading.findUnlistedStock
import kotlinx.coroutines.launch

private fun fmtIsk(value: Double): String = "%,.2f".format(value)

/** Trading -> Unlisted Stock & Undercut Check: two independent, always-live
 * one-shot checks, mirroring the desktop build's trading_unlisted_undercut.py
 * view (single-seller only, unlike that view's pooled-across-every-seller
 * checks - see `UnlistedUndercut.kt`'s own docstring). Neither check has a
 * saved snapshot to load on open - both always make a fresh ESI call, same
 * restraint the desktop view documents. Item names come from the shortlist
 * (the only local list of type_id -> name this platform has, no SDE cache
 * yet) - a flagged type_id not on the shortlist shows its bare number
 * instead (only reachable for Undercut Check, since Unlisted Stock is
 * already scoped to shortlist item_ids). */
@Composable
fun UnlistedUndercutScreen(database: AppDatabase, tokenManager: TokenManager) {
    val scope = rememberCoroutineScope()
    val shortlistRepo = remember { ShortlistRepository(database) }
    val configRepo = remember { TradingConfigRepository(database) }

    var undercutRows by remember { mutableStateOf<List<UndercutResult>>(emptyList()) }
    var unlistedRows by remember { mutableStateOf<List<UnlistedStockResult>>(emptyList()) }
    var itemNames by remember { mutableStateOf<Map<Int, String>>(emptyMap()) }
    var status by remember { mutableStateOf("Neither check has run yet - both always fetch live.") }
    var busy by remember { mutableStateOf(false) }

    fun nameFor(typeId: Int) = itemNames[typeId] ?: typeId.toString()

    fun checkUndercutAction() {
        busy = true
        status = "Checking your own sell orders against competing orders..."
        scope.launch {
            try {
                val cfg = configRepo.load()
                val structureId = cfg.structureId ?: error("No structure configured - set one in Settings first.")
                val sellerRecord = tokenManager.listRecords("seller").firstOrNull()
                    ?: error("No seller character is logged in yet.")
                val sellerToken = tokenManager.getToken(sellerRecord.role)
                itemNames = shortlistRepo.load().associate { it.itemId to it.item }
                val esi = EsiClient()
                val myOrders = esi.characterOrders(sellerToken.characterId, sellerToken.accessToken)
                val book = esi.structureOrdersRaw(structureId, sellerToken.accessToken)
                undercutRows = checkUndercut(myOrders, book, structureId)
                status = if (undercutRows.isEmpty()) "None of your sell orders are currently undercut."
                else "${undercutRows.size} order(s) currently undercut."
            } catch (e: Exception) {
                status = e.message ?: "Undercut check failed."
            } finally {
                busy = false
            }
        }
    }

    fun checkUnlistedAction() {
        busy = true
        status = "Checking structure stock against open sell orders..."
        scope.launch {
            try {
                val cfg = configRepo.load()
                val structureId = cfg.structureId ?: error("No structure configured - set one in Settings first.")
                val sellerRecord = tokenManager.listRecords("seller").firstOrNull()
                    ?: error("No seller character is logged in yet.")
                val sellerToken = tokenManager.getToken(sellerRecord.role)
                val shortlistItems = shortlistRepo.load()
                itemNames = shortlistItems.associate { it.itemId to it.item }
                val shortlistItemIds = shortlistItems.mapNotNull { it.itemId.takeIf { id -> id != 0 } }.toSet()
                val esi = EsiClient()
                val ownSellRemaining = esi.characterOrders(sellerToken.characterId, sellerToken.accessToken)
                    .filter { !it.isBuyOrder && it.locationId == structureId }
                    .groupBy { it.typeId }
                    .mapValues { (_, orders) -> orders.sumOf { it.volumeRemain } }
                val assets = esi.characterAssets(sellerToken.characterId, sellerToken.accessToken)
                unlistedRows = findUnlistedStock(assets, ownSellRemaining, shortlistItemIds, structureId)
                status = if (unlistedRows.isEmpty()) "No unlisted stock found - everything at the structure is listed."
                else "${unlistedRows.size} item(s) with unlisted stock."
            } catch (e: Exception) {
                status = e.message ?: "Unlisted stock check failed."
            } finally {
                busy = false
            }
        }
    }

    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(enabled = !busy, onClick = { checkUnlistedAction() }) { Text("Check Unlisted Stock") }
            Button(enabled = !busy, onClick = { checkUndercutAction() }) { Text("Check Undercut") }
        }
        if (busy) {
            LinearProgressIndicator(modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp))
        }
        Text(status, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(vertical = 8.dp))

        if (unlistedRows.isNotEmpty()) {
            Text("Unlisted Stock", style = MaterialTheme.typography.titleSmall, modifier = Modifier.padding(top = 8.dp))
            LazyColumn {
                items(unlistedRows) { row ->
                    ListItem(
                        headlineContent = { Text(nameFor(row.typeId)) },
                        trailingContent = { Text("Unlisted: %,.0f".format(row.unlistedQuantity)) },
                    )
                    HorizontalDivider()
                }
            }
        }
        if (undercutRows.isNotEmpty()) {
            Text("Undercut Check", style = MaterialTheme.typography.titleSmall, modifier = Modifier.padding(top = 8.dp))
            LazyColumn {
                items(undercutRows) { row ->
                    ListItem(
                        headlineContent = { Text(nameFor(row.typeId)) },
                        supportingContent = { Text("Mine: ${fmtIsk(row.myPrice)} · Competitor: ${fmtIsk(row.competitorPrice)}") },
                        trailingContent = { Text("-${fmtIsk(row.difference)}") },
                    )
                    HorizontalDivider()
                }
            }
        }
    }
}
