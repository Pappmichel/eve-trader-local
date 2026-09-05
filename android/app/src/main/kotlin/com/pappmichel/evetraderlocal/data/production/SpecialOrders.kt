package com.pappmichel.evetraderlocal.data.production

import com.pappmichel.evetraderlocal.data.db.AppDatabase
import com.pappmichel.evetraderlocal.data.db.SettingsEntity
import com.pappmichel.evetraderlocal.data.esi.OrderStats
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.Json
import java.time.Instant
import java.util.UUID

/** Production -> Special Orders: one-off build orders, tracked separately
 * from the permanent stock-target list - the Android counterpart of the
 * desktop build's `production/actions.py` `do_create_special_order`/
 * `do_list_special_orders`/`do_update_special_order`/
 * `do_remove_special_order` quartet, backed there by the `special_orders`/
 * `special_order_items` SQLite tables (`storage.py`).
 *
 * **What's ported: the order list itself.** Create/list/mark-done/reopen/
 * remove a named one-off order with its own line items (item + quantity) is
 * a cheap, local, no-network CRUD feature with no dependency on anything
 * this platform lacks, so it's ported in full - same one-JSON-blob-under-
 * its-own-`settings`-scope shape `DoctrineFittingRepository` already
 * established for "a named, persisted list of line items" (`SavedFitting`),
 * used here instead of new Room entities/DAOs/a migration for one more
 * list.
 *
 * **What's scoped out, and why: `compute-special-order`'s real buy/build
 * plan.** Desktop's `engine.plan_special_order` reuses the *entire*
 * `plan_production` machinery: `_base_runs`/`_expand_all` (recursive
 * demand expansion pooled with runs-based batching), `_build_build_list`/
 * `_build_buy_list` (grouped by job runs / total buy price, with a
 * `job_category` and a `buy_from` station), a live ESI system-cost-index
 * and adjusted-price (EIV) lookup, T2/invention decryptor modeling, a
 * `manual_build_buy`/`manual_stock` override table, and (when
 * `net_against_stock=True`) real ESI-synced character+corp asset stock
 * netted against both this order's own material closure *and* the
 * permanent `stock_targets` closure, surfaced as a `stock_overlap_warning`.
 * None of that whole-plan engine exists on this platform - what this port
 * has instead is [unitBuildCost] (`ProductionBuildCost.kt`), a
 * *single-item* "what would one unit cost right now" estimate (Manufacturing
 * only, one flat ME level, no stock awareness, no job-run batching, no
 * invention). It cannot answer "how many job runs, at what stations, buying
 * what from where" the way the real buy/build lists do, and there is no
 * ESI-synced asset stock or `stock_targets` table on Android at all, so
 * `net_against_stock` (and the stock-overlap warning it drives) has no
 * portable equivalent whatsoever - not even a narrowed one.
 *
 * What [computeSpecialOrderPlan] below offers instead is a **deliberately
 * smaller substitute**, not a narrowed port of `plan_special_order`: for
 * each line item, price "buy it outright" vs. "build one unit, recursively,
 * right now" with [unitBuildCost] and take the cheaper side, exactly the
 * same per-item Buy-vs-Build reasoning `ShipMarginScreen` already offers for
 * a single arbitrary item, just run once per line item and summed. It always
 * assumes zero current stock (there is nothing here to net against), never
 * groups multiple line items' shared sub-materials into one batched job run
 * the way `_expand_all`'s pooling ledger does, and never picks a specific
 * buy-from station. Treat its output as "roughly what this order would cost
 * to fulfill today", not the real desktop feature's actual shopping list. */
@Serializable
data class SpecialOrderLineItem(
    val typeId: Int,
    val typeName: String,
    val quantity: Double,
)

/** A one-off Special Order and its line items, flattened into one JSON
 * document the same way `SavedFitting` is - see this file's module
 * docstring for why `net_against_stock`/stock-overlap have no field here at
 * all (there is nothing on this platform that could ever compute them). */
@Serializable
data class SpecialOrder(
    val orderId: String,
    val note: String = "",
    val status: String = "open",
    val createdAt: String,
    val items: List<SpecialOrderLineItem>,
)

/** Persists Special Orders - the Android counterpart of desktop's
 * `storage.create_special_order`/`list_special_orders`/
 * `update_special_order`/`delete_special_order`, as one JSON blob under its
 * own `settings` scope, same shape `DoctrineFittingRepository` uses for
 * `SavedFitting`. */
class SpecialOrderRepository(private val db: AppDatabase) {
    private val json = Json { ignoreUnknownKeys = true }
    private val serializer = ListSerializer(SpecialOrder.serializer())

    // Guards save-after-load sequences the same way DoctrineFittingRepository's
    // own mutex does - two quick taps (e.g. create then immediately mark
    // done) racing a load-modify-save round trip must not silently drop one
    // side's write.
    private val writeMutex = Mutex()

    companion object {
        const val SCOPE = "special_orders"
        val STATUSES = listOf("open", "done")
    }

    /** Newest first - matches desktop's `list_special_orders`'s own
     * "ORDER BY created_at DESC". */
    suspend fun list(): List<SpecialOrder> = withContext(Dispatchers.IO) {
        val stored = db.settingsDao().get(SCOPE) ?: return@withContext emptyList()
        try {
            json.decodeFromString(serializer, stored.overridesJson).sortedByDescending { it.createdAt }
        } catch (e: Exception) {
            emptyList()
        }
    }

    private suspend fun saveAll(orders: List<SpecialOrder>) = withContext(Dispatchers.IO) {
        db.settingsDao().upsert(SettingsEntity(scope = SCOPE, overridesJson = json.encodeToString(serializer, orders)))
    }

    /** Mirrors `do_create_special_order`'s "at least one item, every
     * quantity positive" precondition - raises rather than silently
     * persisting an invalid order. */
    suspend fun create(items: List<SpecialOrderLineItem>, note: String = ""): SpecialOrder {
        require(items.isNotEmpty()) { "A special order needs at least one item." }
        require(items.all { it.quantity > 0.0 }) { "Every item's quantity must be positive." }
        return writeMutex.withLock {
            val order = SpecialOrder(
                orderId = UUID.randomUUID().toString(),
                note = note,
                createdAt = Instant.now().toString(),
                items = items,
            )
            saveAll(list() + order)
            order
        }
    }

    /** Partial update - mirrors `do_update_special_order`'s "status and/or
     * note" shape (used for both "mark done" and "reopen", same as desktop). */
    suspend fun update(orderId: String, status: String? = null, note: String? = null): SpecialOrder =
        writeMutex.withLock {
            require(status == null || status in STATUSES) { "Unknown status '$status'." }
            val current = list()
            val existing = current.firstOrNull { it.orderId == orderId }
                ?: throw NoSuchElementException("Special order $orderId not found.")
            val updated = existing.copy(status = status ?: existing.status, note = note ?: existing.note)
            saveAll(current.map { if (it.orderId == orderId) updated else it })
            updated
        }

    suspend fun remove(orderId: String) = writeMutex.withLock {
        saveAll(list().filterNot { it.orderId == orderId })
    }
}

/** One line item's scoped-down Buy-vs-Build estimate - see this file's
 * module docstring for exactly how this differs from desktop's real
 * `BuildJobEntry`/`BuyListEntry` rows. `unitCost`/`totalCost` are null when
 * `unitBuildCost` itself couldn't price the item at all (no sell order on
 * either market, no cached blueprint). */
data class SpecialOrderPlanRow(
    val typeId: Int,
    val typeName: String,
    val quantity: Double,
    val unitCost: Double?,
    val totalCost: Double?,
)

/** The scoped-down "Compute" step for a Special Order - see this file's
 * module docstring for why this is a deliberate substitute for
 * `engine.plan_special_order`, not a narrowed port of it. Pure given its
 * already-fetched `home`/`jita` price maps and a [ProductionBomSource], so
 * it's directly unit-testable with a fake BOM the same way
 * `ProductionBuildCostTest` already exercises `unitBuildCost`. */
suspend fun computeSpecialOrderPlan(
    items: List<SpecialOrderLineItem>,
    cfg: ProductionConfig,
    home: Map<Int, OrderStats>,
    jita: Map<Int, OrderStats>,
    bom: ProductionBomSource,
): List<SpecialOrderPlanRow> {
    val memo = mutableMapOf<Int, Double?>()
    return items.map { item ->
        val unitCost = unitBuildCost(item.typeId, cfg, home, jita, bom, memo)
        SpecialOrderPlanRow(
            typeId = item.typeId,
            typeName = item.typeName,
            quantity = item.quantity,
            unitCost = unitCost,
            totalCost = unitCost?.let { it * item.quantity },
        )
    }
}
