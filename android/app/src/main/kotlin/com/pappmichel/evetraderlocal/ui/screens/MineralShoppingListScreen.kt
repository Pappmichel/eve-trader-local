package com.pappmichel.evetraderlocal.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.weight
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
import com.pappmichel.evetraderlocal.data.refining.DirectMineralPurchase
import com.pappmichel.evetraderlocal.data.refining.MineralRequirement
import com.pappmichel.evetraderlocal.data.refining.MineralShoppingListLine
import com.pappmichel.evetraderlocal.data.refining.MineralShoppingListPlan
import com.pappmichel.evetraderlocal.data.refining.OrePurchase
import com.pappmichel.evetraderlocal.data.refining.RefiningConfigRepository
import com.pappmichel.evetraderlocal.data.refining.ShoppingListPlan
import com.pappmichel.evetraderlocal.data.refining.buildMineralShoppingList
import com.pappmichel.evetraderlocal.data.refining.fetchMineralShoppingListPrices
import com.pappmichel.evetraderlocal.data.refining.fetchOreCandidateJitaStats
import com.pappmichel.evetraderlocal.data.refining.fetchOreOptionsForMinerals
import com.pappmichel.evetraderlocal.data.refining.optimizeShoppingList
import com.pappmichel.evetraderlocal.data.refining.resolveTypeId
import com.pappmichel.evetraderlocal.data.sde.SdeRepository
import com.pappmichel.evetraderlocal.data.trading.TradingConfigRepository
import kotlinx.coroutines.launch

/** Ore & Minerals -> Mineral Shopping List: enter the minerals (and
 * quantities) a build needs, then either see today's cost to buy them
 * outright at Jita (**Price**) or run the real joint buy-vs-refine solver
 * (**Optimize**). A Kotlin port of the desktop build's
 * `do_optimize_mineral_shopping_list` (GitHub issue #93) - see
 * `data/refining/MineralShoppingList.kt`'s module docstring for the full
 * finding on `optimizer.py` (a genuine mixed-integer LP) and
 * `data/refining/MineralShoppingListOptimizer.kt`'s own module docstring for
 * the investigation that made porting that solver (`optimizeShoppingList`,
 * on ojAlgo) practical after all.
 *
 * **Both paths are wired up, as two separate actions, not one replacing the
 * other.** `optimizeShoppingList` already reports `savingsVsAllDirect`
 * (the joint plan's saving vs. buying every required mineral outright), so
 * its own output subsumes the "what does direct-buy alone cost" question
 * numerically - but keeping **Price** as its own button, showing
 * `buildMineralShoppingList`'s original per-mineral-independent breakdown,
 * costs nothing (it was already a working, tested path) and gives a second,
 * simpler answer to sanity-check the optimizer's plan against - useful the
 * first time this screen is trusted with a real shopping trip, and whenever
 * the optimizer can't fully price a plan (e.g. an ore candidate list that
 * doesn't yet cover every requirement) but Jita can still price the
 * minerals directly. Pressing one action leaves the other's last result
 * on screen (each has its own state) until re-run or the requirement list
 * changes underneath it (`removeRequirement` clears both).
 *
 * **Optimize needs the same `RefiningConfig` (structure/rig/security/
 * implant/skills) Ore Shortlist reads** to compute ore yield
 * (`fetchOreOptionsForMinerals` -> `oreIceYield`) - like
 * `OreShortlistScreen.kt`, this screen does NOT collect those inline; it
 * loads whatever is saved in Settings via `RefiningConfigRepository`,
 * matching the existing convention that structure/rig/security/implant/
 * skills are a single global Settings-page config, not re-entered per
 * screen (see `RefiningConfig.kt`'s own docstring on why those are
 * dropdowns instead of ESI-pulled).
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
    val refiningConfigRepo = remember { RefiningConfigRepository(database) }
    val esi = remember { EsiClient() }

    var nameText by remember { mutableStateOf("") }
    var qtyText by remember { mutableStateOf("") }
    var requirements by remember { mutableStateOf<List<MineralRequirement>>(emptyList()) }
    var plan by remember { mutableStateOf<MineralShoppingListPlan?>(null) }
    var optimizedPlan by remember { mutableStateOf<ShoppingListPlan?>(null) }
    var status by remember {
        mutableStateOf("Add each mineral your build needs, then Price or Optimize the list.")
    }
    var optimizeStatus by remember { mutableStateOf<String?>(null) }
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
        optimizedPlan = null
        optimizeStatus = null
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

    fun optimize() {
        if (requirements.isEmpty()) {
            optimizeStatus = "Add at least one mineral and quantity first."
            return
        }
        busy = true
        optimizeStatus = "Fetching ore/ice and mineral prices..."
        scope.launch {
            try {
                val tradingCfg = tradingConfigRepo.load()
                val refiningCfg = refiningConfigRepo.load()
                val mineralTypeIds = requirements.filter { it.requiredQty > 0 }.map { it.typeId }

                // Two independent price fetches, exactly like Ore
                // Shortlist's own "ore side" vs "mineral side" split: Jita
                // stats for every ore/ice candidate (to build yield-priced
                // ore columns) and Jita stats for the required minerals
                // themselves (for the direct-buy columns/comparison).
                val oreJitaStatsById = fetchOreCandidateJitaStats(esi, tradingCfg, sdeRepo)
                val oreOptions = fetchOreOptionsForMinerals(
                    sde = sdeRepo,
                    jitaStatsById = oreJitaStatsById,
                    tradingCfg = tradingCfg,
                    refiningCfg = refiningCfg,
                    mineralTypeIds = mineralTypeIds,
                )
                val mineralStatsById = fetchMineralShoppingListPrices(esi, tradingCfg, requirements)
                val directPriceById = mineralTypeIds.associateWith { typeId ->
                    mineralStatsById[typeId]?.sellPercentile?.let { it * (1 + tradingCfg.jitaBuyBrokerFee) }
                }

                val result = optimizeShoppingList(requirements, oreOptions, directPriceById)
                optimizedPlan = result
                optimizeStatus = buildString {
                    append("Optimized total: %,.2f ISK".format(result.totalCost))
                    append(" (%,.2f ore + %,.2f direct).".format(result.oreCost, result.directCost))
                    result.savingsVsAllDirect?.let { savings ->
                        append(" Saves %,.2f ISK vs. buying everything outright.".format(savings))
                    }
                }
            } catch (e: Exception) {
                optimizedPlan = null
                optimizeStatus = e.message ?: "Could not optimize the shopping list."
            } finally {
                busy = false
            }
        }
    }

    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        Text("Mineral Shopping List", style = MaterialTheme.typography.titleMedium)
        Text(
            "Price: buy every mineral outright at Jita. Optimize: the real buy-vs-refine solver (may buy and refine ore instead).",
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
            Button(enabled = !busy && requirements.isNotEmpty(), onClick = { optimize() }) { Text("Optimize") }
        }
        if (busy) {
            LinearProgressIndicator(modifier = Modifier.fillMaxWidth().padding(bottom = 8.dp))
        }
        Text(status, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(bottom = 4.dp))
        optimizeStatus?.let {
            Text(it, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(bottom = 8.dp))
        }

        val optimized = optimizedPlan
        val lines = plan?.lines
        if (optimized != null) {
            LazyColumn(modifier = Modifier.weight(1f)) {
                if (optimized.orePurchases.isNotEmpty()) {
                    item { Text("Buy & refine", style = MaterialTheme.typography.titleSmall, modifier = Modifier.padding(top = 4.dp)) }
                    items(optimized.orePurchases) { ore -> OrePurchaseLineItem(ore) }
                }
                if (optimized.directPurchases.isNotEmpty()) {
                    item { Text("Buy direct", style = MaterialTheme.typography.titleSmall, modifier = Modifier.padding(top = 4.dp)) }
                    items(optimized.directPurchases) { direct -> DirectPurchaseLineItem(direct) }
                }
            }
        } else if (lines != null) {
            LazyColumn(modifier = Modifier.weight(1f)) {
                items(lines) { line -> MineralShoppingListLineItem(line) }
            }
        } else {
            LazyColumn(modifier = Modifier.weight(1f)) {
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

@Composable
private fun OrePurchaseLineItem(ore: OrePurchase) {
    val subtitle = "${ore.portions} portion(s) · ${ore.units} unit(s) · %,.2f ISK/unit".format(ore.landedCostPerUnit)
    ListItem(
        headlineContent = { Text(ore.item) },
        supportingContent = { Text(subtitle) },
        trailingContent = { Text("%,.2f".format(ore.totalCost)) },
    )
    HorizontalDivider()
}

@Composable
private fun DirectPurchaseLineItem(direct: DirectMineralPurchase) {
    ListItem(
        headlineContent = { Text(direct.name) },
        supportingContent = { Text("qty ${direct.quantity} · %,.2f ISK/unit".format(direct.landedCostPerUnit)) },
        trailingContent = { Text("%,.2f".format(direct.totalCost)) },
    )
    HorizontalDivider()
}
