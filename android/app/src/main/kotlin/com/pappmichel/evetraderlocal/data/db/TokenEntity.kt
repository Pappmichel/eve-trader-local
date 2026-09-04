package com.pappmichel.evetraderlocal.data.db

import androidx.room.Dao
import androidx.room.Entity
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.Upsert

/** Mirrors the desktop build's `tokens` SQLite table (storage.py): role as
 * primary key, the whole TokenRecord in one column - same "single source
 * of truth, don't hand-map every field to its own column" choice that
 * table already makes. Unlike the desktop table, `recordJson` isn't plain
 * JSON - it's `TokenCrypto.encrypt`'s AES-256-GCM ciphertext (see that
 * object's own docstring for why), decrypted back to JSON by
 * `TokenManager` on read. */
@Entity(tableName = "tokens")
data class TokenEntity(
    @PrimaryKey val role: String,
    val recordJson: String,
)

@Dao
interface TokenDao {
    @Upsert
    suspend fun upsert(token: TokenEntity)

    @Query("SELECT * FROM tokens WHERE role = :role")
    suspend fun get(role: String): TokenEntity?

    @Query("SELECT * FROM tokens")
    suspend fun getAll(): List<TokenEntity>

    @Query("DELETE FROM tokens WHERE role = :role")
    suspend fun delete(role: String)
}
