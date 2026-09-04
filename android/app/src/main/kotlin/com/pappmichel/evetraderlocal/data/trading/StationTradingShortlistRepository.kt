package com.pappmichel.evetraderlocal.data.trading

import com.pappmichel.evetraderlocal.data.db.AppDatabase
import com.pappmichel.evetraderlocal.data.db.SettingsEntity
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.Json

/** One persisted Station Trading shortlist row - what `discoverCandidates`
 * found *at discovery time* (spread/volume), plus the active flag a user
 * can toggle. Deliberately a different shape from Trading's own
 * `ShortlistItem`: this is a different tool's shortlist (Station Trading
 * candidates at Jita's own trade hub, discovered from a Goonmetrics
 * spread/volume scan), not the import-arbitrage shortlist Trading's
 * Shortlist screen manages - conflating the two would mean one type_id
 * could only ever mean one thing across two unrelated tools. */
@Serializable
data class StationTradingShortlistItem(
    val typeId: Int,
    val spreadPct: Double,
    val avgDailyVolume: Double,
    val discoveredAt: String,
    val active: Boolean = true,
)

/** Persists Station Trading's shortlist membership - the counterpart of the
 * desktop build's storage.py `load_station_trading_shortlist`/
 * `upsert_station_trading_shortlist`, as one JSON blob under its own scope
 * in the shared `settings` table, same shape `ShortlistRepository`/
 * `TradingConfigRepository` already use. */
class StationTradingShortlistRepository(private val db: AppDatabase) {
    private val json = Json { ignoreUnknownKeys = true }
    private val serializer = ListSerializer(StationTradingShortlistItem.serializer())

    companion object {
        const val SCOPE = "station_trading_shortlist_items"
    }

    suspend fun load(): List<StationTradingShortlistItem> = withContext(Dispatchers.IO) {
        val stored = db.settingsDao().get(SCOPE) ?: return@withContext emptyList()
        try {
            json.decodeFromString(serializer, stored.overridesJson)
        } catch (e: Exception) {
            emptyList()
        }
    }

    suspend fun save(items: List<StationTradingShortlistItem>) = withContext(Dispatchers.IO) {
        db.settingsDao().upsert(SettingsEntity(scope = SCOPE, overridesJson = json.encodeToString(serializer, items)))
    }

    /** Re-runs discovery and persists the result: a type_id newly discovered
     * starts active; a type_id that was already on the list (and possibly
     * deactivated by hand) keeps its stored `active` flag rather than being
     * silently reactivated on every refresh - the direct counterpart of
     * `storage.upsert_station_trading_shortlist`'s own "insert or update,
     * never resets active" contract. */
    suspend fun upsertFromDiscovery(candidates: List<StationTradingCandidate>, discoveredAt: String) =
        withContext(Dispatchers.IO) {
            val existingActive = load().associate { it.typeId to it.active }
            val merged = candidates.map { c ->
                StationTradingShortlistItem(
                    typeId = c.typeId, spreadPct = c.spreadPct, avgDailyVolume = c.avgDailyVolume,
                    discoveredAt = discoveredAt, active = existingActive[c.typeId] ?: true,
                )
            }
            save(merged)
        }

    suspend fun setActive(typeIds: Set<Int>, active: Boolean) = withContext(Dispatchers.IO) {
        val updated = load().map { if (it.typeId in typeIds) it.copy(active = active) else it }
        save(updated)
    }
}
