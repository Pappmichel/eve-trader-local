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
import com.pappmichel.evetraderlocal.data.esi.CharacterAsset
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
 * view - pooled across every registered seller character, same as that
 * desktop view's `check_undercut_pooled`/`fetch_seller_stock_without_order_
 * pooled` (see `UnlistedUndercut.kt`'s own docstring for how the pooling is
 * split between this screen and those pure functions). Neither check has a
 * saved snapshot to load on open - both always make a fresh ESI call, same
 * restraint the desktop view documents. A fetch failure for any one seller
 * aborts the whole check rather than being silently skipped - same as
 * desktop, where actions.do_check_undercut/do_check_seller_unlisted_stock
 * let an ESIError from the pooled function surface as one ActionError for
 * the whole run, never a per-character skip. Item names come from the
 * shortlist (the only local list of type_id -> name this platform has, no
 * SDE cache yet) - a flagged type_id not on the shortlist shows its bare
 * number instead (only reachable for Undercut Check, since Unlisted Stock is
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
        status = "Checking every seller's sell orders against competing orders..."
        scope.launch {
            try {
                val cfg = configRepo.load()
                val structureId = cfg.structureId ?: error("No structure configured - set one in Settings first.")
                val sellerRecords = tokenManager.listRecords("seller")
                if (sellerRecords.isEmpty()) error("No seller character is logged in yet.")
                itemNames = shortlistRepo.load().associate { it.itemId to it.item }
                val esi = EsiClient()
                // Pooled across every registered seller character (issue #46:
                // several sellers share the same structure's order slots) -
                // myOrders is the union over all of them, so checkUndercut
                // excludes every one of their own orders from the competitor
                // comparison, not just whichever one would otherwise be
                // checked alone. See UnlistedUndercut.kt's own docstring.
                val myOrders = sellerRecords.flatMap { record ->
                    val token = tokenManager.getToken(record.role)
                    esi.characterOrders(token.characterId, token.accessToken)
                }
                // The structure's order book is one shared/global fetch - any
                // one seller with docking access is enough, same as desktop's
                // check_undercut_pooled.
                val firstSellerToken = tokenManager.getToken(sellerRecords.first().role)
                val book = esi.structureOrdersRaw(structureId, firstSellerToken.accessToken)
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
        status = "Checking structure stock across every seller against open sell orders..."
        scope.launch {
            try {
                val cfg = configRepo.load()
                val structureId = cfg.structureId ?: error("No structure configured - set one in Settings first.")
                val sellerRecords = tokenManager.listRecords("seller")
                if (sellerRecords.isEmpty()) error("No seller character is logged in yet.")
                val shortlistItems = shortlistRepo.load()
                itemNames = shortlistItems.associate { it.itemId to it.item }
                val shortlistItemIds = shortlistItems.mapNotNull { it.itemId.takeIf { id -> id != 0 } }.toSet()
                val esi = EsiClient()
                // Pooled across every registered seller character (issue #46:
                // the structure hangar is shared) - remaining sell volume is
                // summed per type_id across all of them, and every seller's
                // assets are combined, so "no sell order at all" and the
                // quantity found reflect the whole team, not one character.
                // See UnlistedUndercut.kt's own docstring.
                val ownSellRemaining = mutableMapOf<Int, Double>()
                val assets = mutableListOf<CharacterAsset>()
                for (record in sellerRecords) {
                    val token = tokenManager.getToken(record.role)
                    esi.characterOrders(token.characterId, token.accessToken)
                        .filter { !it.isBuyOrder && it.locationId == structureId }
                        .groupBy { it.typeId }
                        .mapValues { (_, orders) -> orders.sumOf { it.volumeRemain } }
                        .forEach { (typeId, remaining) ->
                            ownSellRemaining[typeId] = (ownSellRemaining[typeId] ?: 0.0) + remaining
                        }
                    assets += esi.characterAssets(token.characterId, token.accessToken)
                }
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
