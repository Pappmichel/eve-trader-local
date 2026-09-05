package com.pappmichel.evetraderlocal.data.db

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase
import com.pappmichel.evetraderlocal.data.doctrine.DoctrineContractHistoryDao
import com.pappmichel.evetraderlocal.data.doctrine.DoctrineContractHistoryEntity
import com.pappmichel.evetraderlocal.data.sde.SdeBlueprintMaterialEntity
import com.pappmichel.evetraderlocal.data.sde.SdeBlueprintProductEntity
import com.pappmichel.evetraderlocal.data.sde.SdeCategoryEntity
import com.pappmichel.evetraderlocal.data.sde.SdeDao
import com.pappmichel.evetraderlocal.data.sde.SdeGroupEntity
import com.pappmichel.evetraderlocal.data.sde.SdeInventionProbabilityEntity
import com.pappmichel.evetraderlocal.data.sde.SdeMarketGroupEntity
import com.pappmichel.evetraderlocal.data.sde.SdeRefreshStateEntity
import com.pappmichel.evetraderlocal.data.sde.SdeSolarSystemEntity
import com.pappmichel.evetraderlocal.data.sde.SdeStationEntity
import com.pappmichel.evetraderlocal.data.sde.SdeTypeEntity
import com.pappmichel.evetraderlocal.data.sde.SdeTypeMaterialEntity

/** The local, single-user, on-device database - the direct Android
 * counterpart of the desktop build's own SQLite file (storage.py).
 * App-private storage (Room's default location) already is this
 * platform's "local-first, no server" answer, the same way a portable
 * .exe's own data folder is on desktop (see paths.py's data_dir()) - no
 * extra "where do files live" decision needed here.
 *
 * Version 2 adds the SDE cache tables (data/sde/) alongside the original
 * tokens/settings pair. Version 3 adds `sde_type_materials` and
 * `SdeTypeEntity.portionSize` for the Ore & Minerals / Reprocessing Quote
 * port (see SdeRepository's own docstring). Version 4 adds
 * `sde_blueprint_products`/`sde_blueprint_materials` (Manufacturing-only
 * blueprint BOM data) for Production's first real build-cost view - see
 * `data/production/ProductionBuildCost.kt`. Version 5 adds
 * `doctrine_contract_history` (the desktop build's own GitHub issue #19
 * table), the one Doctrine table that earns a real Room entity instead of
 * the settings-blob-JSON pattern every other Doctrine list uses - see that
 * entity's own docstring for why - for the real contract-sync/matching
 * engine (`data/doctrine/ContractSync.kt`). Version 6 adds
 * `sde_invention_probability` and widens `sde_blueprint_products`/
 * `sde_blueprint_materials` to also carry Invention (activityId=8) rows
 * (previously Manufacturing-only - see those two entities' own docstrings
 * for the primary-key change that required) for the real Invention
 * Estimator port (`data/production/InventionEstimator.kt`). */
@Database(
    entities = [
        TokenEntity::class, SettingsEntity::class,
        SdeTypeEntity::class, SdeGroupEntity::class, SdeCategoryEntity::class,
        SdeMarketGroupEntity::class, SdeSolarSystemEntity::class, SdeStationEntity::class,
        SdeRefreshStateEntity::class, SdeTypeMaterialEntity::class,
        SdeBlueprintProductEntity::class, SdeBlueprintMaterialEntity::class,
        DoctrineContractHistoryEntity::class, SdeInventionProbabilityEntity::class,
    ],
    version = 6,
    exportSchema = false,
)
abstract class AppDatabase : RoomDatabase() {
    abstract fun tokenDao(): TokenDao
    abstract fun settingsDao(): SettingsDao
    abstract fun sdeDao(): SdeDao
    abstract fun doctrineContractHistoryDao(): DoctrineContractHistoryDao

    companion object {
        @Volatile private var instance: AppDatabase? = null

        fun get(context: Context): AppDatabase =
            instance ?: synchronized(this) {
                instance ?: Room.databaseBuilder(
                    context.applicationContext, AppDatabase::class.java, "eve-trader-local.db"
                )
                    // Destructive, not a written Migration, and deliberately so
                    // for now: this app has never shipped or been installed
                    // anywhere real (see android/README.md's own limitations
                    // section), so there is no user data anywhere that a v1 ->
                    // v2 migration could protect. What it would drop is a
                    // `tokens` row (one SSO login, re-done in seconds) and a
                    // `settings` blob (defaults). The moment this is installed
                    // by anyone who is not the developer, this line must be
                    // replaced by real Migration objects - the SDE cache
                    // itself is genuinely disposable (a re-downloadable copy
                    // of a third party's file), but the tables sharing this
                    // database with it are not.
                    .fallbackToDestructiveMigration()
                    .build().also { instance = it }
            }
    }
}
