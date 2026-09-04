package com.pappmichel.evetraderlocal.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.Icon
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
import com.pappmichel.evetraderlocal.data.refining.MineralRequirement
import com.pappmichel.evetraderlocal.data.refining.MineralShoppingListLine
import com.pappmichel.evetraderlocal.data.refining.MineralShoppingListPlan
import com.pappmichel.evetraderlocal.data.refining.buildMineralShoppingList
import com.pappmichel.evetraderlocal.data.refining.fetchMineralShoppingListPrices
import com.pappmichel.evetraderlocal.data.refining.resolveTypeId
import com.pappmichel.evetraderlocal.data.sde.SdeRepository
import com.pappmichel.evetraderlocal.data.trading.TradingConfigRepository
import kotlinx.coroutines.launch

/** Ore & Minerals -> Mineral Shopping List: enter the minerals (and
 * quantities) a build needs, get today's cost to buy them outright at Jita.
 * A Kotlin port of the desktop build's `do_optimize_mineral_shopping_list`
 * (GitHub issue #93) - see `data/refining/MineralShoppingList.kt`'s module
 * docstring for the full finding on `optimizer.py` (a genuine mixed-integer
 * LP) and the scope decision this screen implements instead (buy each
 * mineral independently at its cheapest current Jita listing - no
 * ore-refining alternative in this pass). That file's docstring is the
 * canonical explanation; this screen is just its UI.
 *
 * Unlike Reprocessing Quote (which parses an Inventory-window paste),
 * requirements are entered one at a time by item name + quantity - the
 * desktop build's own `do_save_mineral_requirements`/`do_set_mineral_
 * requirement` split exists because its web editor submits a whole list at
 * once while its CLI upserts one at a time; this screen only needs the
 * latter shape, and (like Reprocessing Quote's own paste-item resolution)
 * has no saved/persisted requirement list on this platform yet - each visit
 * starts from an empty list, matching this port's "manually entered today"
 * scope note in `MineralShoppingList.kt`. */
@Composable
fun MineralShoppingListScreen(database: AppDatabase, @Suppress("UNUSED_PARAMETER") tokenManager: TokenManager) {
    val scope = rememberCoroutineScope()
    val sdeRepo = remember { SdeRepository(database) }
    val tradingConfigRepo = remember { TradingConfigRepository(database) }
    val esi = remember { EsiClient() }

    var nameText by remember { mutableStateOf("") }
    var qtyText by remember { mutableStateOf("") }
    var requirements by remember { mutableStateOf<List<MineralRequirement>>(emptyList()) }
    var plan by remember { mutableStateOf<MineralShoppingListPlan?>(null) }
    var status by remember {
        mutableStateOf("Add each mineral your build needs, then Price the list.")
    }
    var busy by remember { mutableStateOf(false) }

    fun addRequirement() {
        val name = nameText.trim()
        val qty = qtyText.trim().toDoubleOrNull()
        if (name.isEmpty()) {
            status = "Enter a mineral name."
            return
        }
        if (qty == null || qty <= 0) {
            status = "Required quantity must be a number greater than 0."
            return
        }
        busy = true
        scope.launch {
            try {
                val typeId = resolveTypeId(sdeRepo, name)
                if (typeId == null) {
                    status = "No exact match in the SDE for \"$name\" - refresh SDE first?"
                    return@launch
                }
                requirements = requirements.filterNot { it.typeId == typeId } +
                    MineralRequirement(typeId = typeId, name = name, requiredQty = qty)
                nameText = ""
                qtyText = ""
                status = "${requirements.size} mineral(s) listed - Price when ready."
            } finally {
                busy = false
            }
        }
    }

    fun removeRequirement(typeId: Int) {
        requirements = requirements.filterNot { it.typeId == typeId }
        plan = null
    }

    fun priceList() {
        if (requirements.isEmpty()) {
            status = "Add at least one mineral and quantity first."
            return
        }
        busy = true
        status = "Fetching Jita prices..."
        scope.launch {
            try {
                val tradingCfg = tradingConfigRepo.load()
                val statsById = fetchMineralShoppingListPrices(esi, tradingCfg, requirements)
                val result = buildMineralShoppingList(requirements, statsById, tradingCfg)
                plan = result
                status = if (result.unpriceable.isEmpty()) {
                    "Total: %,.2f ISK to buy everything outright at Jita right now.".format(result.totalCost)
                } else {
                    "Total (priceable lines only): %,.2f ISK - ${result.unpriceable.size} line(s) have no Jita sell orders right now."
                        .format(result.totalCost)
                }
            } catch (e: Exception) {
                status = e.message ?: "Could not price the shopping list."
            } finally {
                busy = false
            }
        }
    }

    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        Text("Mineral Shopping List", style = MaterialTheme.typography.titleMedium)
        Text(
            "Buy-outright cost only - no ore-refining comparison yet, see this screen's own docstring.",
            style = MaterialTheme.typography.bodySmall,
            modifier = Modifier.padding(bottom = 8.dp),
        )
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(bottom = 8.dp)) {
            OutlinedTextField(
                value = nameText,
                onValueChange = { nameText = it },
                label = { Text("Mineral name") },
                modifier = Modifier.weight(2f),
            )
            OutlinedTextField(
                value = qtyText,
                onValueChange = { qtyText = it },
                label = { Text("Qty") },
                modifier = Modifier.width(100.dp),
            )
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(bottom = 8.dp)) {
            Button(enabled = !busy, onClick = { addRequirement() }) { Text("Add") }
            Button(enabled = !busy && requirements.isNotEmpty(), onClick = { priceList() }) { Text("Price") }
        }
        if (busy) {
            LinearProgressIndicator(modifier = Modifier.fillMaxWidth().padding(bottom = 8.dp))
        }
        Text(status, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(bottom = 8.dp))

        val lines = plan?.lines
        if (lines != null) {
            LazyColumn {
                items(lines) { line -> MineralShoppingListLineItem(line) }
            }
        } else {
            LazyColumn {
                items(requirements) { req ->
                    ListItem(
                        headlineContent = { Text(req.name) },
                        supportingContent = { Text("qty ${req.requiredQty}") },
                        trailingContent = {
                            IconButton(onClick = { removeRequirement(req.typeId) }) {
                                Icon(Icons.Filled.Close, contentDescription = "Remove")
                            }
                        },
                    )
                    HorizontalDivider()
                }
            }
        }
    }
}

@Composable
private fun MineralShoppingListLineItem(line: MineralShoppingListLine) {
    val subtitle = if (line.totalCost != null) {
        "qty ${line.quantity} · %,.2f ISK/unit".format(line.unitCost)
    } else {
        "qty ${line.quantity} · no Jita sell orders right now"
    }
    ListItem(
        headlineContent = { Text(line.name) },
        supportingContent = { Text(subtitle) },
        trailingContent = {
            Text(if (line.totalCost != null) "%,.2f".format(line.totalCost) else "-")
        },
    )
    HorizontalDivider()
}
