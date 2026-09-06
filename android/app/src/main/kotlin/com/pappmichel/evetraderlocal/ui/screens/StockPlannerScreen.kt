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
import androidx.compose.material3.Checkbox
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
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
import com.pappmichel.evetraderlocal.data.production.BuildOrBuyDecision
import com.pappmichel.evetraderlocal.data.production.LiveStockSource
import com.pappmichel.evetraderlocal.data.production.ManualBuildBuyEntity
import com.pappmichel.evetraderlocal.data.production.ManualStockEntity
import com.pappmichel.evetraderlocal.data.production.PlanProductionResult
import com.pappmichel.evetraderlocal.data.production.ProductionConfigRepository
import com.pappmichel.evetraderlocal.data.production.StockPlanRepository
import com.pappmichel.evetraderlocal.data.production.StockTargetEntity
import com.pappmichel.evetraderlocal.data.production.homePrices
import com.pappmichel.evetraderlocal.data.production.jitaPrices
import com.pappmichel.evetraderlocal.data.production.planProduction
import com.pappmichel.evetraderlocal.data.production.structuralMaterialClosure
import com.pappmichel.evetraderlocal.data.sde.SdeRepository
import com.pappmichel.evetraderlocal.data.trading.TradingConfigRepository
import kotlinx.coroutines.launch

private fun fmtIsk(value: Double?): String = value?.let { "%,.2f ISK".format(it) } ?: "not priceable"
private fun fmtQty(value: Double): String = "%,.2f".format(value)
private fun fmtPct(value: Double?): String = value?.let { "%.1f%%".format(it * 100) } ?: "n/a"

/** Production -> Stock Planner: the real, stock-target-driven buy/build
 * optimizer this session's own task history repeatedly found missing
 * underneath Planner, Special Orders, Logistics, and Asset-Optimized
 * Planner - a genuine port of `engine.plan_production` (see
 * `data/production/ProductionEngine.kt`'s own module docstring for the full
 * algorithm and every scope decision), not another substitute.
 *
 * **Why a new, distinct screen rather than replacing the existing
 * "Planner" menu entry.** `ProductionPlannerScreen`/`ProductionPlanner.kt`
 * already ports a *different*, real desktop feature under that name (the
 * recursive BOM-explosion "Material Tree" view, `engine.
 * build_material_tree` - see that file's own module docstring for the full
 * finding) and is a working, tested, useful screen in its own right - it
 * answers "what does this one item's recipe need", which stays a genuinely
 * different question from this screen's "given everything I've configured
 * as a standing target, what should I buy/build right now". Swapping the
 * existing route out from under it would either delete a real, working
 * feature or silently misname this one; adding this as its own "Stock
 * Planner" menu entry keeps both, correctly labeled, matching this task's
 * own explicit "add as a distinct new screen and leave the substitute where
 * it is" option.
 *
 * Three sections, top to bottom: **Stock Targets** (add/remove; the
 * `stock_targets` table - a type_id, a target quantity, and a Jita-target
 * flag), **Manual Stock** (add/remove; the `manual_stock` override table -
 * a physically-counted correction on top of live ESI assets), and **Manual
 * Build/Buy** (add/remove; the `manual_build_buy` table - force "always
 * Build"/"always Buy" for a type_id regardless of the modeled cost). Below
 * them, **Compute Plan** runs [planProduction] and shows its three result
 * lists (Inventory, Buy List, Build List) - no Invention Needs list, see
 * `ProductionEngine.kt`'s own module docstring for why.
 *
 * Stock is read live via [LiveStockSource] (character-assets/industry-jobs
 * only, no corp assets yet - see that class's own docstring), the same
 * "live, not synced" pattern every other ESI-asset-shaped Production screen
 * in this app already follows; home-market pricing needs a producer
 * character's structure-docking token, the same gap every other Production
 * screen here documents. */
@Composable
fun StockPlannerScreen(database: AppDatabase, tokenManager: TokenManager) {
    val scope = rememberCoroutineScope()
    val repo = remember { StockPlanRepository(database) }
    val sdeRepo = remember { SdeRepository(database) }
    val productionConfigRepo = remember { ProductionConfigRepository(database) }
    val tradingConfigRepo = remember { TradingConfigRepository(database) }
    val esi = remember { EsiClient() }
    val stockSource = remember { LiveStockSource(tokenManager, esi) }

    var targets by remember { mutableStateOf<List<StockTargetEntity>>(emptyList()) }
    var manualStock by remember { mutableStateOf<List<ManualStockEntity>>(emptyList()) }
    var overrides by remember { mutableStateOf<List<ManualBuildBuyEntity>>(emptyList()) }

    var targetNameText by remember { mutableStateOf("") }
    var targetQtyText by remember { mutableStateOf("") }
    var targetJita by remember { mutableStateOf(false) }
    var stockNameText by remember { mutableStateOf("") }
    var stockCountText by remember { mutableStateOf("") }
    var overrideNameText by remember { mutableStateOf("") }
    var overrideBuild by remember { mutableStateOf(true) }

    var busy by remember { mutableStateOf(false) }
    var status by remember { mutableStateOf("Configure at least one stock target, then Compute Plan.") }
    var plan by remember { mutableStateOf<PlanProductionResult?>(null) }

    suspend fun reload() {
        targets = repo.listStockTargets()
        manualStock = repo.listManualStock()
        overrides = repo.listManualBuildBuy()
    }

    LaunchedEffect(Unit) { reload() }

    /** Resolves "NAME_OR_TYPE_ID" the same way `ProductionSpecialOrdersScreen`
     * does - an exact numeric type_id, or an exact-match SDE name lookup. */
    suspend fun resolveType(nameOrId: String): Pair<Int, String>? {
        val trimmed = nameOrId.trim()
        val typeId = trimmed.toIntOrNull() ?: sdeRepo.resolveTypeIdByName(trimmed) ?: return null
        val typeName = sdeRepo.type(typeId)?.typeName ?: return null
        return typeId to typeName
    }

    fun addStockTarget() {
        val qty = targetQtyText.trim().toDoubleOrNull()
        if (qty == null || qty <= 0.0) {
            status = "Quantity must be a positive number."
            return
        }
        scope.launch {
            val resolved = resolveType(targetNameText)
            if (resolved == null) {
                status = "No exact match for '$targetNameText'."
                return@launch
            }
            val (typeId, typeName) = resolved
            repo.upsertStockTarget(typeId, typeName, qty, targetJita)
            targetNameText = ""
            targetQtyText = ""
            targetJita = false
            status = "Added stock target: $typeName."
            reload()
        }
    }

    fun addManualStock() {
        val count = stockCountText.trim().toDoubleOrNull()
        if (count == null || count < 0.0) {
            status = "Count must be a non-negative number."
            return
        }
        scope.launch {
            val resolved = resolveType(stockNameText)
            if (resolved == null) {
                status = "No exact match for '$stockNameText'."
                return@launch
            }
            val (typeId, typeName) = resolved
            repo.upsertManualStock(typeId, typeName, count)
            stockNameText = ""
            stockCountText = ""
            status = "Added manual stock: $typeName."
            reload()
        }
    }

    fun addOverride() {
        scope.launch {
            val resolved = resolveType(overrideNameText)
            if (resolved == null) {
                status = "No exact match for '$overrideNameText'."
                return@launch
            }
            val (typeId, typeName) = resolved
            repo.upsertManualBuildBuy(typeId, if (overrideBuild) BuildOrBuyDecision.BUILD else BuildOrBuyDecision.BUY)
            overrideNameText = ""
            status = "Added manual override: $typeName."
            reload()
        }
    }

    fun computePlan() {
        if (targets.isEmpty()) {
            status = "Add at least one stock target first."
            return
        }
        busy = true
        plan = null
        status = "Computing the stock plan..."
        scope.launch {
            try {
                val cfg = productionConfigRepo.load()
                val tradingCfg = tradingConfigRepo.load()
                val stockTargets = repo.loadStockTargets()
                val manualStockMap = repo.loadManualStock()
                val manualOverrides = repo.loadManualBuildBuy()

                val seedTypeIds = stockTargets.map { it.typeId }
                val closure = structuralMaterialClosure(seedTypeIds, sdeRepo)
                val pricedTypeIds = (seedTypeIds.toSet() + closure).toList()
                val home = homePrices(pricedTypeIds, cfg, tokenManager, esi)
                val jita = jitaPrices(pricedTypeIds, tradingCfg.jitaRegionId, esi)

                plan = planProduction(
                    cfg, stockTargets, manualStockMap, manualOverrides, home, jita, sdeRepo, stockSource,
                ) { typeId -> sdeRepo.type(typeId)?.typeName ?: typeId.toString() }

                status = "Computed: ${plan?.inventory?.size ?: 0} target(s), " +
                    "${plan?.buyList?.size ?: 0} buy line(s), ${plan?.buildList?.size ?: 0} build job(s)."
            } catch (e: Exception) {
                status = e.message ?: "Failed to compute the stock plan."
            } finally {
                busy = false
            }
        }
    }

    LazyColumn(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        item {
            Text("Stock Targets", style = MaterialTheme.typography.titleSmall)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                OutlinedTextField(
                    value = targetNameText, onValueChange = { targetNameText = it },
                    label = { Text("Name or type ID") }, modifier = Modifier.weight(1f),
                )
                OutlinedTextField(
                    value = targetQtyText, onValueChange = { targetQtyText = it },
                    label = { Text("Target qty") }, modifier = Modifier.weight(1f),
                )
            }
            Row(
                horizontalArrangement = Arrangement.spacedBy(4.dp),
                modifier = Modifier.fillMaxWidth().padding(top = 2.dp),
            ) {
                Checkbox(checked = targetJita, onCheckedChange = { targetJita = it })
                Text("Jita target (else home)", style = MaterialTheme.typography.bodySmall)
            }
            Button(enabled = !busy, onClick = { addStockTarget() }) { Text("Add Target") }
            HorizontalDivider(modifier = Modifier.padding(vertical = 8.dp))
        }
        items(targets, key = { it.typeId }) { t ->
            ListItem(
                headlineContent = { Text(t.typeName) },
                supportingContent = {
                    Text("Target ${fmtQty(t.quantity)}${if (t.jitaTarget) " - Jita" else " - Home"}")
                },
                trailingContent = {
                    TextButton(onClick = { scope.launch { repo.deleteStockTarget(t.typeId); reload() } }) {
                        Text("Remove")
                    }
                },
            )
        }

        item {
            HorizontalDivider(modifier = Modifier.padding(vertical = 8.dp))
            Text("Manual Stock (correction on top of ESI assets)", style = MaterialTheme.typography.titleSmall)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                OutlinedTextField(
                    value = stockNameText, onValueChange = { stockNameText = it },
                    label = { Text("Name or type ID") }, modifier = Modifier.weight(1f),
                )
                OutlinedTextField(
                    value = stockCountText, onValueChange = { stockCountText = it },
                    label = { Text("Count") }, modifier = Modifier.weight(1f),
                )
            }
            Button(enabled = !busy, onClick = { addManualStock() }) { Text("Add Manual Stock") }
            HorizontalDivider(modifier = Modifier.padding(vertical = 8.dp))
        }
        items(manualStock, key = { it.typeId }) { m ->
            ListItem(
                headlineContent = { Text(m.typeName) },
                supportingContent = { Text("Count ${fmtQty(m.count)}") },
                trailingContent = {
                    TextButton(onClick = { scope.launch { repo.deleteManualStock(m.typeId); reload() } }) {
                        Text("Remove")
                    }
                },
            )
        }

        item {
            HorizontalDivider(modifier = Modifier.padding(vertical = 8.dp))
            Text("Manual Build/Buy Override", style = MaterialTheme.typography.titleSmall)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                OutlinedTextField(
                    value = overrideNameText, onValueChange = { overrideNameText = it },
                    label = { Text("Name or type ID") }, modifier = Modifier.weight(1f),
                )
                TextButton(onClick = { overrideBuild = !overrideBuild }) {
                    Text(if (overrideBuild) "Force: Build" else "Force: Buy")
                }
            }
            Button(enabled = !busy, onClick = { addOverride() }) { Text("Add Override") }
            HorizontalDivider(modifier = Modifier.padding(vertical = 8.dp))
        }
        items(overrides, key = { it.typeId }) { o ->
            ListItem(
                headlineContent = { Text(o.typeId.toString()) },
                supportingContent = { Text("Always ${o.decision}") },
                trailingContent = {
                    TextButton(onClick = { scope.launch { repo.deleteManualBuildBuy(o.typeId); reload() } }) {
                        Text("Remove")
                    }
                },
            )
        }

        item {
            HorizontalDivider(modifier = Modifier.padding(vertical = 8.dp))
            Button(enabled = !busy, onClick = { computePlan() }) { Text("Compute Plan") }
            if (busy) {
                LinearProgressIndicator(modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp))
            }
            Text(status, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(vertical = 8.dp))
        }

        plan?.let { result ->
            item {
                Text("Inventory", style = MaterialTheme.typography.titleSmall)
            }
            items(result.inventory) { row ->
                Text(
                    "${row.typeName} (${row.activity}): ${fmtQty(row.currentStock)} / ${fmtQty(row.target)}" +
                        " - missing ${fmtQty(row.totalMissing)}",
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.padding(vertical = 2.dp),
                )
            }
            item {
                HorizontalDivider(modifier = Modifier.padding(vertical = 8.dp))
                Text("Buy List", style = MaterialTheme.typography.titleSmall)
            }
            items(result.buyList) { row ->
                Text(
                    "${row.typeName} x${fmtQty(row.quantity)} @ ${fmtIsk(row.unitPrice)}" +
                        " = ${fmtIsk(row.totalPrice)} (${row.buyFrom ?: "no source"}," +
                        " ${fmtPct(row.onHandPct / 100.0)} on hand)",
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.padding(vertical = 2.dp),
                )
            }
            item {
                HorizontalDivider(modifier = Modifier.padding(vertical = 8.dp))
                Text("Build List", style = MaterialTheme.typography.titleSmall)
            }
            items(result.buildList) { row ->
                Text(
                    "${row.typeName}: ${row.jobRuns} run(s) -> ${fmtQty(row.quantity)} units" +
                        " @ ${fmtIsk(row.unitBuildCost)}/unit (margin ${fmtPct(row.margin)})",
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.padding(vertical = 2.dp),
                )
            }
        }
    }
}
