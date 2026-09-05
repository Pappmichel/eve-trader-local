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
import androidx.compose.material3.OutlinedTextField
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
import com.pappmichel.evetraderlocal.data.esi.EsiClient
import com.pappmichel.evetraderlocal.data.production.ProductionConfigRepository
import com.pappmichel.evetraderlocal.data.production.SpecialOrder
import com.pappmichel.evetraderlocal.data.production.SpecialOrderLineItem
import com.pappmichel.evetraderlocal.data.production.SpecialOrderPlanRow
import com.pappmichel.evetraderlocal.data.production.SpecialOrderRepository
import com.pappmichel.evetraderlocal.data.production.computeSpecialOrderPlan
import com.pappmichel.evetraderlocal.data.production.homePrices
import com.pappmichel.evetraderlocal.data.production.jitaPrices
import com.pappmichel.evetraderlocal.data.production.structuralMaterialClosure
import com.pappmichel.evetraderlocal.data.sde.SdeRepository
import com.pappmichel.evetraderlocal.data.trading.TradingConfigRepository
import kotlinx.coroutines.launch

private fun fmtIsk(value: Double?): String = value?.let { "%,.2f ISK".format(it) } ?: "not priceable"
private fun fmtQty(value: Double): String = "%,.0f".format(value)

/** Production -> Special Orders: one-off build orders, tracked separately
 * from the permanent stock-target list - a Kotlin port of the desktop
 * build's `production_special_orders.py` tab (create/list/mark-done/reopen/
 * remove) on top of [SpecialOrderRepository]. See `data/production/
 * SpecialOrders.kt`'s own module docstring for exactly which half of the
 * desktop feature this is (the order list, in full) and which half it is
 * not (`engine.plan_special_order`'s real stock-aware buy/build planner,
 * which needs whole-plan infrastructure - job-run batching, live cost
 * indices, ESI-synced asset stock, `stock_targets` - that doesn't exist
 * anywhere else on this platform either). The "Compute" button below runs
 * [computeSpecialOrderPlan]'s scoped-down per-item Buy-vs-Build estimate
 * instead, clearly labeled as such rather than presented as the real
 * planner's output. */
@Composable
fun ProductionSpecialOrdersScreen(database: AppDatabase, tokenManager: TokenManager) {
    val scope = rememberCoroutineScope()
    val repo = remember { SpecialOrderRepository(database) }
    val sdeRepo = remember { SdeRepository(database) }
    val productionConfigRepo = remember { ProductionConfigRepository(database) }
    val tradingConfigRepo = remember { TradingConfigRepository(database) }
    val esi = remember { EsiClient() }

    var itemsText by remember { mutableStateOf("") }
    var noteText by remember { mutableStateOf("") }
    var orders by remember { mutableStateOf<List<SpecialOrder>>(emptyList()) }
    var busy by remember { mutableStateOf(false) }
    var status by remember {
        mutableStateOf("Enter items as NAME_OR_TYPE_ID:QUANTITY, comma-separated, e.g. Tritanium:1000, 34:500")
    }
    var computedOrderId by remember { mutableStateOf<String?>(null) }
    var planRows by remember { mutableStateOf<List<SpecialOrderPlanRow>>(emptyList()) }

    fun loadOrders() {
        scope.launch { orders = repo.list() }
    }

    LaunchedEffect(Unit) { loadOrders() }

    /** "Tritanium:1000, 34:500" -> resolved [SpecialOrderLineItem]s, the
     * same NAME_OR_TYPE_ID:QUANTITY grammar the desktop tab's own items
     * field uses, resolved through [SdeRepository.resolveTypeIdByName]
     * (exact, case-insensitive match) the same way `_create_order` resolves
     * through `storage.search_sde_types`'s own exact-match filter. Returns
     * null (leaving `status` set to the problem) on the first invalid spec,
     * rather than silently dropping it. */
    suspend fun parseItems(text: String): List<SpecialOrderLineItem>? {
        val specs = text.split(",").map { it.trim() }.filter { it.isNotEmpty() }
        if (specs.isEmpty()) {
            status = "Enter at least one NAME_OR_TYPE_ID:QUANTITY item."
            return null
        }
        val resolved = mutableListOf<SpecialOrderLineItem>()
        for (spec in specs) {
            val sep = spec.lastIndexOf(':')
            if (sep < 0) {
                status = "Invalid item '$spec' - expected NAME_OR_TYPE_ID:QUANTITY."
                return null
            }
            val nameOrId = spec.substring(0, sep).trim()
            val qty = spec.substring(sep + 1).trim().toDoubleOrNull()
            if (qty == null || qty <= 0.0) {
                status = "Invalid quantity in '$spec'."
                return null
            }
            val typeId = nameOrId.toIntOrNull() ?: sdeRepo.resolveTypeIdByName(nameOrId)
            if (typeId == null) {
                status = "No exact match for '$nameOrId'."
                return null
            }
            val typeName = sdeRepo.type(typeId)?.typeName
            if (typeName == null) {
                status = "Unknown type_id $typeId - refresh SDE first?"
                return null
            }
            resolved += SpecialOrderLineItem(typeId, typeName, qty)
        }
        return resolved
    }

    fun createOrder() {
        busy = true
        scope.launch {
            try {
                val parsed = parseItems(itemsText) ?: return@launch
                val order = repo.create(parsed, noteText.trim())
                itemsText = ""
                noteText = ""
                status = "Created special order ${order.orderId}."
                loadOrders()
            } catch (e: Exception) {
                status = e.message ?: "Failed to create special order."
            } finally {
                busy = false
            }
        }
    }

    fun updateStatus(orderId: String, newStatus: String) {
        scope.launch {
            try {
                val updated = repo.update(orderId, status = newStatus)
                status = "Special order ${updated.orderId} updated - status: ${updated.status}."
                loadOrders()
            } catch (e: Exception) {
                status = e.message ?: "Failed to update special order."
            }
        }
    }

    fun removeOrder(orderId: String) {
        scope.launch {
            try {
                repo.remove(orderId)
                if (computedOrderId == orderId) {
                    computedOrderId = null
                    planRows = emptyList()
                }
                status = "Removed special order $orderId."
                loadOrders()
            } catch (e: Exception) {
                status = e.message ?: "Failed to remove special order."
            }
        }
    }

    fun computeOrder(order: SpecialOrder) {
        busy = true
        computedOrderId = order.orderId
        status = "Computing buy/build estimate for this order..."
        scope.launch {
            try {
                val productionCfg = productionConfigRepo.load()
                val tradingCfg = tradingConfigRepo.load()
                val seedTypeIds = order.items.map { it.typeId }
                val closure = structuralMaterialClosure(seedTypeIds, sdeRepo)
                val pricedTypeIds = (seedTypeIds.toSet() + closure).toList()
                val home = homePrices(pricedTypeIds, productionCfg, tokenManager, esi)
                val jita = jitaPrices(pricedTypeIds, tradingCfg.jitaRegionId, esi)
                planRows = computeSpecialOrderPlan(order.items, productionCfg, home, jita, sdeRepo)
                val unpriceable = planRows.count { it.unitCost == null }
                status = "Computed ${planRows.size} line item(s)" +
                    if (unpriceable > 0) ", $unpriceable not priceable." else "."
            } catch (e: Exception) {
                status = e.message ?: "Failed to compute this order."
            } finally {
                busy = false
            }
        }
    }

    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        Text("Create Special Order", style = MaterialTheme.typography.titleSmall)
        OutlinedTextField(
            value = itemsText,
            onValueChange = { itemsText = it },
            label = { Text("Items (NAME_OR_TYPE_ID:QUANTITY, comma-separated)") },
            modifier = Modifier.fillMaxWidth().padding(top = 4.dp),
        )
        Row(
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            modifier = Modifier.fillMaxWidth().padding(top = 4.dp),
        ) {
            OutlinedTextField(
                value = noteText,
                onValueChange = { noteText = it },
                label = { Text("Note") },
                modifier = Modifier.weight(1f),
            )
            Button(enabled = !busy, onClick = { createOrder() }) { Text("Create") }
        }

        if (busy) {
            LinearProgressIndicator(modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp))
        }
        Text(status, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(vertical = 8.dp))
        HorizontalDivider()

        Text("Orders", style = MaterialTheme.typography.titleSmall, modifier = Modifier.padding(top = 8.dp))
        LazyColumn(modifier = Modifier.weight(1f)) {
            items(orders, key = { it.orderId }) { order ->
                ListItem(
                    headlineContent = {
                        Text(if (order.note.isBlank()) order.orderId else order.note)
                    },
                    supportingContent = {
                        Text(
                            "${order.status} - ${order.items.size} item(s) - ${order.createdAt}"
                        )
                    },
                )
                Row(
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    modifier = Modifier.fillMaxWidth().padding(start = 16.dp, bottom = 8.dp),
                ) {
                    if (order.status == "open") {
                        Button(enabled = !busy, onClick = { updateStatus(order.orderId, "done") }) { Text("Mark Done") }
                    } else {
                        Button(enabled = !busy, onClick = { updateStatus(order.orderId, "open") }) { Text("Reopen") }
                    }
                    Button(enabled = !busy, onClick = { computeOrder(order) }) { Text("Compute") }
                    Button(enabled = !busy, onClick = { removeOrder(order.orderId) }) { Text("Remove") }
                }
                if (computedOrderId == order.orderId && planRows.isNotEmpty()) {
                    Column(modifier = Modifier.fillMaxWidth().padding(start = 16.dp, bottom = 8.dp)) {
                        for (row in planRows) {
                            Text(
                                "${row.typeName} x${fmtQty(row.quantity)} - " +
                                    "${fmtIsk(row.unitCost)}/unit - total ${fmtIsk(row.totalCost)}",
                                style = MaterialTheme.typography.bodySmall,
                            )
                        }
                    }
                }
                HorizontalDivider()
            }
        }
    }
}
