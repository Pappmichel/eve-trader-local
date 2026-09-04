package com.pappmichel.evetraderlocal.data.production

import com.pappmichel.evetraderlocal.data.db.AppDatabase
import com.pappmichel.evetraderlocal.data.db.SettingsEntity
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json

/** A small slice of the desktop build's production/config.py
 * `ProductionConfig` - only the fields Item Lookup's buy-price comparison
 * actually reads (see ProductionPricing.kt). The desktop dataclass also
 * carries build-candidate thresholds, structure/rig ME/TE settings,
 * job-cost-index overrides, invention-skill levels and stock-planner
 * knobs - none of those have a ported consumer here yet (no bill-of-
 * materials/blueprint data exists in the Android SDE cache, see
 * ProductionPricing.kt's module docstring), so adding fields for them now
 * would just be dead config nobody reads. Add them alongside whichever
 * feature first needs them, the same incremental approach
 * StationTradingConfig followed.
 *
 * Persisted as its own JSON blob under scope "production" in the shared
 * `settings` table, same one-scope-per-tool-config shape
 * TradingConfigRepository/StationTradingConfigRepository already
 * established - this is a separate scope from the parent desktop app's own
 * "production" stored-overrides scope even though the name matches, since
 * the two apps have entirely separate SQLite databases (this is Android's
 * Room `settings` table, not eve_trader_local's). */
@Serializable
data class ProductionConfig(
    // Buy-side broker fee, applied to whichever market a material is
    // sourced from - same character, same rate wherever they buy. Matches
    // production/config.py's own default.
    val jitaBuyBrokerFee: Double = 0.0147,
    // ISK/m3 to move goods from Jita to the home structure. Only
    // Jita-sourced material is charged this - anything already at home
    // needs no hauling. Matches production/config.py's own default.
    val haulCostPerM3: Double = 900.0,
    // Structure ID whose live order book is read first for the "home"
    // side of the comparison - unset (null) is fully supported, same as
    // desktop's Optional[int] = None: home_prices/homePrices simply
    // returns no home quotes and every lookup falls back to Jita-only.
    val homeLocationId: Long? = null,
) {
    companion object {
        const val SCOPE = "production"
    }
}

class ProductionConfigRepository(private val db: AppDatabase) {
    private val json = Json { ignoreUnknownKeys = true }

    suspend fun load(): ProductionConfig = withContext(Dispatchers.IO) {
        val stored = db.settingsDao().get(ProductionConfig.SCOPE) ?: return@withContext ProductionConfig()
        try {
            json.decodeFromString(stored.overridesJson)
        } catch (e: Exception) {
            ProductionConfig()
        }
    }

    suspend fun save(config: ProductionConfig) = withContext(Dispatchers.IO) {
        db.settingsDao().upsert(
            SettingsEntity(scope = ProductionConfig.SCOPE, overridesJson = json.encodeToString(config))
        )
    }
}
