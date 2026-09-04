package com.pappmichel.evetraderlocal.data.trading

import com.pappmichel.evetraderlocal.data.db.AppDatabase
import com.pappmichel.evetraderlocal.data.db.SettingsEntity
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
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

    // Guards addIfAbsent's load-check-save sequence: without it, two calls
    // racing (e.g. tapping "Add to Shortlist" on two different Candidate
    // Discovery rows in quick succession) can both read the same snapshot,
    // and whichever's save() lands second silently overwrites - and drops
    // - the other's addition.
    private val writeMutex = Mutex()

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

    /** Adds `item` unless an entry with the same `itemId` is already
     * present, atomically (see `writeMutex`). Returns whether it was
     * added. */
    suspend fun addIfAbsent(item: ShortlistItem): Boolean = writeMutex.withLock {
        val current = load()
        if (current.any { it.itemId == item.itemId }) return@withLock false
        save(current + item)
        true
    }
}
