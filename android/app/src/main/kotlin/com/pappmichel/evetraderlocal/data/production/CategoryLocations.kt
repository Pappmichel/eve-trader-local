package com.pappmichel.evetraderlocal.data.production

import androidx.room.Dao
import androidx.room.Entity
import androidx.room.Query
import androidx.room.Upsert
import com.pappmichel.evetraderlocal.data.db.AppDatabase
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/** Room counterparts of the desktop build's `job_category_locations`/
 * `category_location_options` tables (storage.py) - Logistics' own
 * "which structure builds each job_category" config, the only input
 * [logisticsStatus]/[distributionRecommendations] (`LogisticsEngine.kt`)
 * need beyond an already-computed build list. Modeled as real Room
 * entities, the same "meant to grow, per-row CRUD" shape `StockPlan.kt`'s
 * three tables already established for this app's other Production
 * configuration lists, rather than the settings-blob-JSON pattern
 * [ProductionConfigRepository] uses - one row per (job) category here is
 * exactly as small and fixed-shape as a stock target row, not a blob worth
 * rewriting whole on every single edit.
 *
 * Two separate tables, matching desktop's own split: [CategoryLocationEntity]
 * is the *currently active* location for a category (one row per category,
 * category as the primary key - setting a new active location replaces the
 * old one); [CategoryLocationOptionEntity] is a saved quick-switch list (a
 * category can remember several past/likely locations, not just its
 * current one) - matches `do_set_category_location`'s own desktop behavior
 * of writing to *both* tables at once (see `LogisticsRepository.
 * setCategoryLocation` below). */

@Entity(tableName = "category_locations", primaryKeys = ["category"])
data class CategoryLocationEntity(
    val category: String,
    val locationId: Long,
)

@Dao
interface CategoryLocationDao {
    @Upsert
    suspend fun upsert(row: CategoryLocationEntity)

    @Query("DELETE FROM category_locations WHERE category = :category")
    suspend fun delete(category: String)

    @Query("SELECT * FROM category_locations")
    suspend fun all(): List<CategoryLocationEntity>
}

@Entity(tableName = "category_location_options", primaryKeys = ["category", "locationId"])
data class CategoryLocationOptionEntity(
    val category: String,
    val locationId: Long,
)

@Dao
interface CategoryLocationOptionDao {
    @Upsert
    suspend fun upsert(row: CategoryLocationOptionEntity)

    @Query("DELETE FROM category_location_options WHERE category = :category AND locationId = :locationId")
    suspend fun delete(category: String, locationId: Long)

    @Query("SELECT * FROM category_location_options ORDER BY category, locationId")
    suspend fun all(): List<CategoryLocationOptionEntity>
}

/** CRUD + bulk-load facade over both DAOs above - same shape
 * [StockPlanRepository] already established. [loadCategoryLocations]/
 * [loadCategoryLocationOptions] return the plain-map shapes
 * `LogisticsEngine.kt`/a Logistics screen actually consume, mirroring
 * `storage.load_category_locations`/`load_category_location_options`'s own
 * dict return shapes exactly. */
class CategoryLocationRepository(private val db: AppDatabase) {
    private val locationDao = db.categoryLocationDao()
    private val optionDao = db.categoryLocationOptionDao()

    suspend fun listCategoryLocations(): List<CategoryLocationEntity> = withContext(Dispatchers.IO) { locationDao.all() }

    suspend fun listCategoryLocationOptions(): List<CategoryLocationOptionEntity> =
        withContext(Dispatchers.IO) { optionDao.all() }

    /** `category -> locationId` for every category with a currently-assigned
     * active location - mirrors `storage.load_category_locations`. */
    suspend fun loadCategoryLocations(): Map<String, Long> = withContext(Dispatchers.IO) {
        locationDao.all().associate { it.category to it.locationId }
    }

    /** `category -> [locationId, ...]` (ascending) - mirrors
     * `storage.load_category_location_options`. */
    suspend fun loadCategoryLocationOptions(): Map<String, List<Long>> = withContext(Dispatchers.IO) {
        optionDao.all().groupBy({ it.category }, { it.locationId })
    }

    /** Assigns `category` to build its jobs at `locationId`, also
     * remembering it as a quick-switch option - mirrors
     * `do_set_category_location` exactly (setting a category active
     * shouldn't require a separate "save as option" step too). */
    suspend fun setCategoryLocation(category: String, locationId: Long) = withContext(Dispatchers.IO) {
        locationDao.upsert(CategoryLocationEntity(category, locationId))
        optionDao.upsert(CategoryLocationOptionEntity(category, locationId))
    }

    suspend fun clearCategoryLocation(category: String) = withContext(Dispatchers.IO) { locationDao.delete(category) }

    suspend fun addCategoryLocationOption(category: String, locationId: Long) = withContext(Dispatchers.IO) {
        optionDao.upsert(CategoryLocationOptionEntity(category, locationId))
    }

    suspend fun removeCategoryLocationOption(category: String, locationId: Long) = withContext(Dispatchers.IO) {
        optionDao.delete(category, locationId)
    }
}
