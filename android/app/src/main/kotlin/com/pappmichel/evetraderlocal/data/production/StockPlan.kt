package com.pappmichel.evetraderlocal.data.production

import androidx.room.Dao
import androidx.room.Entity
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.Upsert
import com.pappmichel.evetraderlocal.data.db.AppDatabase
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/** Room counterparts of the desktop build's `stock_targets`/`manual_stock`/
 * `manual_build_buy` tables (storage.py) - the user-configured input to the
 * real stock-aware planner (`ProductionEngine.kt`'s port of
 * `engine.plan_production`). Modeled as real Room entities/DAOs, not the
 * settings-blob-JSON pattern [ProductionConfigRepository] uses: like
 * `doctrine_contract_history` (see that entity's own docstring for the same
 * reasoning), each of these three lists is meant to *grow* to an arbitrary,
 * user-managed size over the life of the install - a target per tracked
 * item, a manual count per item worth correcting, a manual Build/Buy
 * decision per item worth overriding - which is exactly the shape a real
 * table + per-row upsert/delete fits and a single ever-larger JSON blob
 * (fully re-read/re-written on every single edit) does not.
 *
 * All three keep the desktop schema's exact shape (down to column names/
 * types, `type_id` as the primary key) - there is nothing Android-specific
 * to add or narrow here, unlike the recursive engine math that reads them. */

/** A stock target: "keep at least `quantity` units of `typeId` on hand,
 * sold at the home structure unless `jitaTarget` says to price/margin-gate
 * it against Jita instead" - mirrors `stock_targets` exactly. `typeName` is
 * a display-only denormalized snapshot (engine.py itself only ever reads a
 * stock target's quantity/jitaTarget by type_id), same convention
 * `ManualStockEntity`/`DoctrineContractHistoryEntity` already use. */
@Entity(tableName = "stock_targets")
data class StockTargetEntity(
    @PrimaryKey val typeId: Int,
    val typeName: String,
    val quantity: Double,
    val jitaTarget: Boolean = false,
)

@Dao
interface StockTargetDao {
    @Upsert
    suspend fun upsert(row: StockTargetEntity)

    @Query("DELETE FROM stock_targets WHERE typeId = :typeId")
    suspend fun delete(typeId: Int)

    @Query("SELECT * FROM stock_targets ORDER BY typeName")
    suspend fun all(): List<StockTargetEntity>
}

/** Manual stock override: a genuinely separate signal from ESI-synced
 * assets, not a simplification of them - "I physically counted N units and
 * it doesn't match what ESI/the plan thinks" (a delivery ESI hasn't caught
 * up on, stock kept somewhere no producer character can see, a deliberate
 * correction) - mirrors `manual_stock` exactly. Added on top of live ESI
 * assets by [currentStock], never replacing them. */
@Entity(tableName = "manual_stock")
data class ManualStockEntity(
    @PrimaryKey val typeId: Int,
    val typeName: String,
    val count: Double = 0.0,
)

@Dao
interface ManualStockDao {
    @Upsert
    suspend fun upsert(row: ManualStockEntity)

    @Query("DELETE FROM manual_stock WHERE typeId = :typeId")
    suspend fun delete(typeId: Int)

    @Query("SELECT * FROM manual_stock ORDER BY typeName")
    suspend fun all(): List<ManualStockEntity>
}

/** Manual Build/Buy override: forces "always Build" or "always Buy" for a
 * specific `typeId` regardless of what the modeled unit cost would
 * otherwise decide - mirrors `manual_build_buy` exactly. Consulted by
 * [buyOrBuildDecision] ahead of any cost comparison at all. */
@Entity(tableName = "manual_build_buy")
data class ManualBuildBuyEntity(
    @PrimaryKey val typeId: Int,
    /** "Build" or "Buy" - not a Kotlin enum, matching the desktop table's
     * own plain `CHECK (decision IN ('Build', 'Buy'))` TEXT column; Room has
     * no CHECK-constraint support so this port relies on
     * [BuildOrBuyDecision]'s own factory to keep the value real. */
    val decision: String,
)

/** The only two values a manual Build/Buy override (or [buyOrBuildDecision]'s
 * own verdict) can ever take - kept as string constants rather than an enum
 * so it stays a drop-in match for [ManualBuildBuyEntity.decision] and for
 * every desktop-mirrored "Build"/"Buy" string this file's engine functions
 * return. */
object BuildOrBuyDecision {
    const val BUILD = "Build"
    const val BUY = "Buy"
}

@Dao
interface ManualBuildBuyDao {
    @Upsert
    suspend fun upsert(row: ManualBuildBuyEntity)

    @Query("DELETE FROM manual_build_buy WHERE typeId = :typeId")
    suspend fun delete(typeId: Int)

    @Query("SELECT * FROM manual_build_buy")
    suspend fun all(): List<ManualBuildBuyEntity>
}

/** CRUD + bulk-load facade over the three DAOs above, the same
 * "`suspend`, `Dispatchers.IO`-wrapped, one class per feature's Room tables"
 * shape [ProductionConfigRepository]/[DoctrineContractHistoryRepository]
 * already use. [loadStockTargets]/[loadManualStock]/[loadManualBuildBuy]
 * return the plain-map/list shapes `ProductionEngine.kt` actually consumes
 * (mirroring `storage.load_stock_targets`/`load_manual_stock`/
 * `load_manual_build_buy`'s own return shapes exactly), so the engine
 * itself never has to know these are backed by Room at all. */
class StockPlanRepository(private val db: AppDatabase) {
    private val stockTargetDao = db.stockTargetDao()
    private val manualStockDao = db.manualStockDao()
    private val manualBuildBuyDao = db.manualBuildBuyDao()

    suspend fun listStockTargets(): List<StockTargetEntity> = withContext(Dispatchers.IO) { stockTargetDao.all() }

    suspend fun upsertStockTarget(typeId: Int, typeName: String, quantity: Double, jitaTarget: Boolean) =
        withContext(Dispatchers.IO) {
            stockTargetDao.upsert(StockTargetEntity(typeId, typeName, quantity, jitaTarget))
        }

    suspend fun deleteStockTarget(typeId: Int) = withContext(Dispatchers.IO) { stockTargetDao.delete(typeId) }

    /** `(typeId, typeName, quantity, jitaTarget)` for every configured stock
     * target - the exact tuple shape `engine.load_stock_targets`/
     * `plan_production`'s own iteration expects. */
    suspend fun loadStockTargets(): List<StockTarget> = withContext(Dispatchers.IO) {
        stockTargetDao.all().map { StockTarget(it.typeId, it.typeName, it.quantity, it.jitaTarget) }
    }

    suspend fun listManualStock(): List<ManualStockEntity> = withContext(Dispatchers.IO) { manualStockDao.all() }

    suspend fun upsertManualStock(typeId: Int, typeName: String, count: Double) = withContext(Dispatchers.IO) {
        manualStockDao.upsert(ManualStockEntity(typeId, typeName, count))
    }

    suspend fun deleteManualStock(typeId: Int) = withContext(Dispatchers.IO) { manualStockDao.delete(typeId) }

    /** `typeId -> count` - mirrors `storage.load_manual_stock`'s own dict
     * return shape exactly. */
    suspend fun loadManualStock(): Map<Int, Double> = withContext(Dispatchers.IO) {
        manualStockDao.all().associate { it.typeId to it.count }
    }

    suspend fun listManualBuildBuy(): List<ManualBuildBuyEntity> =
        withContext(Dispatchers.IO) { manualBuildBuyDao.all() }

    suspend fun upsertManualBuildBuy(typeId: Int, decision: String) = withContext(Dispatchers.IO) {
        manualBuildBuyDao.upsert(ManualBuildBuyEntity(typeId, decision))
    }

    suspend fun deleteManualBuildBuy(typeId: Int) = withContext(Dispatchers.IO) { manualBuildBuyDao.delete(typeId) }

    /** `typeId -> "Build"|"Buy"` - mirrors `storage.load_manual_build_buy`'s
     * own dict return shape exactly. */
    suspend fun loadManualBuildBuy(): Map<Int, String> = withContext(Dispatchers.IO) {
        manualBuildBuyDao.all().associate { it.typeId to it.decision }
    }
}

/** One configured stock target, as `ProductionEngine.kt` consumes it -
 * mirrors the desktop `(type_id, type_name, quantity, jita_target)` tuple
 * `storage.load_stock_targets` returns, given a real name instead of a bare
 * positional tuple since nothing else in this Kotlin port uses raw tuples
 * for a 4-field row. */
data class StockTarget(
    val typeId: Int,
    val typeName: String,
    val quantity: Double,
    val jitaTarget: Boolean,
)
