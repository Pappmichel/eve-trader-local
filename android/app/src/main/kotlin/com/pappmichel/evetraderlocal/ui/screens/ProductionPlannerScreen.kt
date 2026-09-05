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
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
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
import com.pappmichel.evetraderlocal.data.production.LeafRequirement
import com.pappmichel.evetraderlocal.data.production.ProductionConfigRepository
import com.pappmichel.evetraderlocal.data.production.aggregateLeaves
import com.pappmichel.evetraderlocal.data.production.buildMaterialTree
import com.pappmichel.evetraderlocal.data.production.homePrices
import com.pappmichel.evetraderlocal.data.production.jitaPrices
import com.pappmichel.evetraderlocal.data.production.priceLeafRequirements
import com.pappmichel.evetraderlocal.data.sde.SdeRepository
import com.pappmichel.evetraderlocal.data.trading.TradingConfigRepository
import kotlinx.coroutines.launch

private fun fmtIsk(value: Double): String = "%,.2f".format(value)
private fun fmtQty(value: Double): String = "%,.2f".format(value)

/** Production -> Planner: type in a target item and how many you want to
 * build, recursively explode its cached Manufacturing blueprint down to raw
 * minerals/base materials, and see the total quantity (and, where a market
 * quote exists, cost) of everything that has to be bought.
 *
 * See `data/production/ProductionPlanner.kt`'s own module docstring for the
 * full finding this screen is built on: desktop's actual Planner tab
 * (`production_planner.py`/`engine.plan_production`) is a completely
 * different, stock-target-driven buy/build optimizer this app has no
 * infrastructure for yet (no `stock_targets`/`manual_stock` tables, no
 * asset/industry-job ESI sync). This screen instead ports the recursive
 * BOM-explosion feature the task actually asked for - desktop's Item
 * Lookup/"Material Tree" view - into the Android app's still-placeholder
 * "Planner" menu slot, and documents that substitution rather than quietly
 * relabeling one desktop feature as another.
 *
 * Only the flat, aggregated buy list is shown (one row per leaf, summed
 * across every branch of the tree that needs it - see
 * [aggregateLeaves]'s own docstring) rather than the full per-level tree:
 * the tree can legitimately be dozens of nodes deep across many branches
 * for a capital ship, and "what do I actually need to go buy, and how much
 * of it in total" is the more directly actionable answer on a phone screen
 * - the same "one real, useful slice" precedent Item Lookup/Ship Margins
 * already set rather than reproducing every desktop widget faithfully.
 *
 * Lookup is by type_id only, same reduction (no name-search index in the
 * Android SDE cache) `ItemLookupScreen`/`ShipMarginScreen` already document;
 * once resolved, each row's SDE name is shown for readability.
 *
 * Pricing reuses [priceLeafRequirements] (`ProductionPlanner.kt`) against
 * both home and Jita quotes fetched for exactly the aggregated leaf set,
 * the same "one shared price fetch over exactly the type_ids this scan
 * needs" precedent `ProductionBuildCandidates.kt`/`ItemLookupScreen`
 * already set - home pricing degrades to "no home quotes" without a
 * configured `ProductionConfig.homeLocationId` and producer-character
 * token, same gap every other Production screen in this app documents. */
@Composable
fun ProductionPlannerScreen(database: AppDatabase, tokenManager: TokenManager) {
    val scope = rememberCoroutineScope()
    val sdeRepo = remember { SdeRepository(database) }
    val tradingConfigRepo = remember { TradingConfigRepository(database) }
    val productionConfigRepo = remember { ProductionConfigRepository(database) }
    val esi = remember { EsiClient() }

    var typeIdText by remember { mutableStateOf("") }
    var quantityText by remember { mutableStateOf("1") }
    var busy by remember { mutableStateOf(false) }
    var status by remember { mutableStateOf("Enter a target item and quantity, then Explode.") }
    var rootName by remember { mutableStateOf<String?>(null) }
    var rows by remember { mutableStateOf<List<Pair<String, LeafRequirement>>>(emptyList()) }
    var totalCost by remember { mutableStateOf<Double?>(null) }
    var haveResult by remember { mutableStateOf(false) }

    fun explode() {
        val typeId = typeIdText.trim().toIntOrNull()
        if (typeId == null) {
            status = "Enter a numeric type ID."
            return
        }
        val quantity = quantityText.trim().toDoubleOrNull()
        if (quantity == null || quantity <= 0.0) {
            status = "Quantity must be a positive number."
            return
        }
        busy = true
        haveResult = false
        status = "Exploding the bill of materials for $quantity x type $typeId..."
        scope.launch {
            try {
                val sdeType = sdeRepo.type(typeId)
                rootName = sdeType?.typeName
                val productionCfg = productionConfigRepo.load()
                val tradingCfg = tradingConfigRepo.load()

                val tree = buildMaterialTree(typeId, quantity, productionCfg, sdeRepo)
                val leaves = aggregateLeaves(tree)
                val leafIds = leaves.keys.toList()
                val home = homePrices(leafIds, productionCfg, tokenManager, esi)
                val jita = jitaPrices(leafIds, tradingCfg.jitaRegionId, esi)
                val priced = priceLeafRequirements(leaves, home, jita, productionCfg, sdeRepo)

                rows = priced.map { row -> (sdeRepo.type(row.typeId)?.typeName ?: row.typeId.toString()) to row }
                totalCost = priced.fold(0.0 to false) { (sum, any), row ->
                    if (row.totalCost != null) (sum + row.totalCost) to true else sum to any
                }.let { (sum, any) -> if (any) sum else null }
                haveResult = true
                status = if (tree.isExpanded) {
                    "Exploded into ${rows.size} leaf requirement(s)."
                } else {
                    "This item has no cached Manufacturing blueprint - nothing to explode, just buy it directly."
                }
            } catch (e: Exception) {
                status = e.message ?: "Explosion failed."
            } finally {
                busy = false
            }
        }
    }

    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedTextField(
                value = typeIdText,
                onValueChange = { typeIdText = it },
                label = { Text("Type ID") },
                modifier = Modifier.weight(1f),
            )
            OutlinedTextField(
                value = quantityText,
                onValueChange = { quantityText = it },
                label = { Text("Quantity") },
                modifier = Modifier.weight(1f),
            )
            Button(enabled = !busy, onClick = { explode() }) { Text("Explode") }
        }
        if (busy) {
            LinearProgressIndicator(modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp))
        }
        Text(status, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(vertical = 8.dp))

        if (haveResult) {
            HorizontalDivider()
            Column(modifier = Modifier.padding(vertical = 8.dp)) {
                Text(rootName ?: "(unknown item name)", style = MaterialTheme.typography.titleMedium)
                Text(
                    "Total buy cost: " + (totalCost?.let { "${fmtIsk(it)} ISK" } ?: "not fully priceable"),
                    style = MaterialTheme.typography.titleSmall,
                )
            }
            HorizontalDivider()
            LazyColumn(modifier = Modifier.fillMaxWidth()) {
                items(rows) { (name, row) ->
                    Row(
                        modifier = Modifier.fillMaxWidth().padding(vertical = 6.dp),
                        horizontalArrangement = Arrangement.SpaceBetween,
                    ) {
                        Column(modifier = Modifier.weight(1f)) {
                            Text(name, style = MaterialTheme.typography.bodyMedium)
                            Text(
                                "Qty: ${fmtQty(row.quantity)}",
                                style = MaterialTheme.typography.bodySmall,
                            )
                        }
                        Text(
                            row.totalCost?.let { "${fmtIsk(it)} ISK" } ?: "no price",
                            style = MaterialTheme.typography.bodyMedium,
                        )
                    }
                    HorizontalDivider()
                }
            }
        }
    }
}
