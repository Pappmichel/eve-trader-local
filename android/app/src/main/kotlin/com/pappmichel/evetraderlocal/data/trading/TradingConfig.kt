package com.pappmichel.evetraderlocal.data.trading

import com.pappmichel.evetraderlocal.data.db.AppDatabase
import com.pappmichel.evetraderlocal.data.db.SettingsEntity
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

/** Same fields/defaults as the desktop build's config.py `TradingConfig`
 * dataclass - only the ones candidate discovery actually reads so far (see
 * ROADMAP.md's Android section); more fields get added here as more of
 * Trading is ported, the same way the desktop version's dataclass grew
 * tool by tool. Persisted as one JSON blob under scope "trading" in the
 * `settings` table (data/db/SettingsEntity.kt) - the same shape
 * storage.py's own `settings` table uses. */
@Serializable
data class TradingConfig(
    val jitaRegionId: Int = 10_000_002,
    val referenceRegionId: Int = 10_000_009,
    val structureId: Long? = null,
    val importCostPerM3: Double = 900.0,
    val structureSellHaircut: Double = 0.9463,
    val jitaBuyBrokerFee: Double = 0.0147,
    val minProfitThreshold: Double = 0.0,
    val minMarginThreshold: Double = 0.05,
    val minHitRate: Double = 0.30,
    val minAvgMovement: Double = 0.0,
    val excludedPathPrefixes: List<String> = listOf(
        "ships", "blueprints", "apparel", "personalization", "pilot's services", "structures",
    ),
) {
    companion object {
        const val SCOPE = "trading"
    }
}

class TradingConfigRepository(private val db: AppDatabase) {
    private val json = Json { ignoreUnknownKeys = true }

    suspend fun load(): TradingConfig = withContext(Dispatchers.IO) {
        val stored = db.settingsDao().get(TradingConfig.SCOPE) ?: return@withContext TradingConfig()
        try {
            json.decodeFromString(stored.overridesJson)
        } catch (e: Exception) {
            TradingConfig()
        }
    }

    suspend fun save(config: TradingConfig) = withContext(Dispatchers.IO) {
        db.settingsDao().upsert(SettingsEntity(scope = TradingConfig.SCOPE, overridesJson = json.encodeToString(config)))
    }
}
