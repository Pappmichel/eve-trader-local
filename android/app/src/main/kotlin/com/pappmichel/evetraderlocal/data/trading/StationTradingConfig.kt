package com.pappmichel.evetraderlocal.data.trading

import com.pappmichel.evetraderlocal.data.db.AppDatabase
import com.pappmichel.evetraderlocal.data.db.SettingsEntity
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json

/** Same fields/defaults as the desktop build's station_trading/config.py
 * `StationTradingConfig` - a separate config from `TradingConfig`, since
 * Station Trading is its own tool with its own thresholds, not a Trading
 * sub-mode. Persisted as its own JSON blob under scope "station_trading" in
 * the shared `settings` table, same one-scope-per-tool-config shape
 * `TradingConfigRepository` already established. */
@Serializable
data class StationTradingConfig(
    // Jita 4 - Moon 4 - Caldari Navy Assembly Plant - confirmed against the
    // desktop build's local SDE sde_stations table, not assumed from memory.
    // The region is deliberately not a separate field here - callers reuse
    // TradingConfig.jitaRegionId directly, the same "don't duplicate a
    // Trading-level concept this tool doesn't own" precedent
    // production/pricing.py's jita_prices sets on desktop.
    val stationId: Long = 60_003_760,

    // Starting defaults for the base-game NPC-station rates (no standings/
    // skill discount applied) - not asserted precise, meant to be checked
    // against the in-game Market window. Itemized separately (unlike
    // Trading's blended structureSellHaircut) because Station Trading
    // charges broker fee on both order legs (buy and sell) and sales tax
    // only on the sell leg.
    val brokerFeeRate: Double = 0.05,
    val salesTaxRate: Double = 0.075,

    // Candidate-discovery gates. minDailyVolume's shape was checked live
    // against Jita's real market on desktop (2026-08-28): items clearing an
    // 8% spread are sharply bimodal - thousands of dead items (volume in the
    // low hundreds, meaningless on a near-empty order book) vs. a small
    // cluster of genuinely liquid commodities (volume in the tens of
    // millions+). 1000 sits well inside that gap, not a precisely-derived
    // number, and already narrows a live count from 10,000+ to ~1,300 - a
    // real opportunity set, not something to additionally cap by default.
    val minSpreadThreshold: Double = 0.08,
    val minDailyVolume: Double = 1000.0,

    // Same "off by default, user opts in" shape as TradingConfig's own
    // enforceShortlistCap/maxActiveShortlistItems - an always-on hard cap
    // was confirmed wrong on desktop (2026-08-29): minDailyVolume above is
    // the real noise filter, a cap on top of it should be an explicit
    // choice.
    val enforceShortlistCap: Boolean = false,
    val maxActiveShortlistItems: Int = 300,
) {
    companion object {
        const val SCOPE = "station_trading"
    }
}

class StationTradingConfigRepository(private val db: AppDatabase) {
    private val json = Json { ignoreUnknownKeys = true }

    suspend fun load(): StationTradingConfig = withContext(Dispatchers.IO) {
        val stored = db.settingsDao().get(StationTradingConfig.SCOPE) ?: return@withContext StationTradingConfig()
        try {
            json.decodeFromString(stored.overridesJson)
        } catch (e: Exception) {
            StationTradingConfig()
        }
    }

    suspend fun save(config: StationTradingConfig) = withContext(Dispatchers.IO) {
        db.settingsDao().upsert(
            SettingsEntity(scope = StationTradingConfig.SCOPE, overridesJson = json.encodeToString(config))
        )
    }
}
