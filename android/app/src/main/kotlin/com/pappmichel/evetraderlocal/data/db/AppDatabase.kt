package com.pappmichel.evetraderlocal.data.db

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase
import com.pappmichel.evetraderlocal.data.sde.SdeCategoryEntity
import com.pappmichel.evetraderlocal.data.sde.SdeDao
import com.pappmichel.evetraderlocal.data.sde.SdeGroupEntity
import com.pappmichel.evetraderlocal.data.sde.SdeMarketGroupEntity
import com.pappmichel.evetraderlocal.data.sde.SdeRefreshStateEntity
import com.pappmichel.evetraderlocal.data.sde.SdeSolarSystemEntity
import com.pappmichel.evetraderlocal.data.sde.SdeStationEntity
import com.pappmichel.evetraderlocal.data.sde.SdeTypeEntity

/** The local, single-user, on-device database - the direct Android
 * counterpart of the desktop build's own SQLite file (storage.py).
 * App-private storage (Room's default location) already is this
 * platform's "local-first, no server" answer, the same way a portable
 * .exe's own data folder is on desktop (see paths.py's data_dir()) - no
 * extra "where do files live" decision needed here.
 *
 * Version 2 adds the SDE cache tables (data/sde/) alongside the original
 * tokens/settings pair. */
@Database(
    entities = [
        TokenEntity::class, SettingsEntity::class,
        SdeTypeEntity::class, SdeGroupEntity::class, SdeCategoryEntity::class,
        SdeMarketGroupEntity::class, SdeSolarSystemEntity::class, SdeStationEntity::class,
        SdeRefreshStateEntity::class,
    ],
    version = 2,
    exportSchema = false,
)
abstract class AppDatabase : RoomDatabase() {
    abstract fun tokenDao(): TokenDao
    abstract fun settingsDao(): SettingsDao
    abstract fun sdeDao(): SdeDao

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
