package com.pappmichel.evetraderlocal.data.db

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase

/** The local, single-user, on-device database - the direct Android
 * counterpart of the desktop build's own SQLite file (storage.py).
 * App-private storage (Room's default location) already is this
 * platform's "local-first, no server" answer, the same way a portable
 * .exe's own data folder is on desktop (see paths.py's data_dir()) - no
 * extra "where do files live" decision needed here. */
@Database(entities = [TokenEntity::class, SettingsEntity::class], version = 1, exportSchema = false)
abstract class AppDatabase : RoomDatabase() {
    abstract fun tokenDao(): TokenDao
    abstract fun settingsDao(): SettingsDao

    companion object {
        @Volatile private var instance: AppDatabase? = null

        fun get(context: Context): AppDatabase =
            instance ?: synchronized(this) {
                instance ?: Room.databaseBuilder(
                    context.applicationContext, AppDatabase::class.java, "eve-trader-local.db"
                ).build().also { instance = it }
            }
    }
}
