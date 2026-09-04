package com.pappmichel.evetraderlocal.data.db

import androidx.room.Dao
import androidx.room.Entity
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.Upsert

/** Mirrors the desktop build's `settings` SQLite table (storage.py):
 * scope (e.g. "trading", "production") as primary key, its config
 * overrides serialized as one JSON blob - not yet read by any real config
 * dataclass on this platform (see ui/screens - there is no SettingsScreen
 * ported yet), but the storage shape is settled now so a future config
 * port has somewhere to land without a schema change. */
@Entity(tableName = "settings")
data class SettingsEntity(
    @PrimaryKey val scope: String,
    val overridesJson: String,
)

@Dao
interface SettingsDao {
    @Upsert
    suspend fun upsert(settings: SettingsEntity)

    @Query("SELECT * FROM settings WHERE scope = :scope")
    suspend fun get(scope: String): SettingsEntity?
}
