package com.pappmichel.evetraderlocal.data.trading

import com.pappmichel.evetraderlocal.data.db.AppDatabase
import com.pappmichel.evetraderlocal.data.db.SettingsEntity
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.Json

/** Persists the shortlist *membership* list (as opposed to the evaluated
 * `ShortlistRow`s a refresh computes from it, which aren't persisted here -
 * see ROADMAP.md's Android section) - same one-JSON-blob-per-scope shape
 * as `TradingConfigRepository`, counterpart of the desktop build's
 * storage.py `load_shortlist`/`upsert_shortlist`. */
class ShortlistRepository(private val db: AppDatabase) {
    private val json = Json { ignoreUnknownKeys = true }
    private val serializer = ListSerializer(ShortlistItem.serializer())

    companion object {
        const val SCOPE = "shortlist_items"
    }

    suspend fun load(): List<ShortlistItem> = withContext(Dispatchers.IO) {
        val stored = db.settingsDao().get(SCOPE) ?: return@withContext emptyList()
        try {
            json.decodeFromString(serializer, stored.overridesJson)
        } catch (e: Exception) {
            emptyList()
        }
    }

    suspend fun save(items: List<ShortlistItem>) = withContext(Dispatchers.IO) {
        db.settingsDao().upsert(SettingsEntity(scope = SCOPE, overridesJson = json.encodeToString(serializer, items)))
    }
}
