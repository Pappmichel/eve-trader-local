package com.pappmichel.evetraderlocal.data.production

import com.pappmichel.evetraderlocal.data.db.AppDatabase
import com.pappmichel.evetraderlocal.data.db.SettingsEntity
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json

/** A small slice of the desktop build's production/config.py
 * `ProductionConfig` - only the fields a ported feature actually reads:
 * Item Lookup's buy-price comparison (see ProductionPricing.kt) plus, as of
 * the blueprint-BOM SDE port, Ship Margins & Market Status' single-item
 * build-cost view (see ProductionBuildCost.kt). The desktop dataclass also
 * carries build-candidate thresholds, per-structure/rig ME/TE settings, a
 * live system-cost-index lookup, invention-skill levels and stock-planner
 * knobs - none of those have a ported consumer here yet, so adding fields
 * for them now would just be dead config nobody reads. Add them alongside
 * whichever feature first needs them, the same incremental approach
 * StationTradingConfig followed - exactly what happened here: four fields
 * (marketFeesRate/materialEfficiency/jobCostIndexRate/facilityTaxRate)
 * added alongside the feature that needed them, the original three left
 * untouched.
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

    // -- Added for Ship Margins & Market Status' single-item build-cost
    // view (data/production/ProductionBuildCost.kt) - see that file's own
    // docstring for the full simplified-vs-desktop math these feed. --

    // Sell-side costs subtracted from a sale before the build margin is
    // computed: SCC surcharge + broker's fee + sales tax. Matches
    // production/config.py's `market_fees` default exactly (0.5% + 1.5% +
    // 3.37%, confirmed against the in-game sell-order breakdown).
    val marketFeesRate: Double = 0.0537,
    // Blueprint/BPO research level, as a flat ME percent applied to every
    // Manufacturing material (0-10, EVE's real ME range) - the Android
    // counterpart of desktop's per-blueprint owned-BPO lookup
    // (`engine._owned_bpo_mods`), which needs `character_blueprints` ESI
    // sync data this app doesn't have yet. Defaults to 10 (perfect
    // research), matching desktop's own ACTIVITY_MODS["Tech I"] fallback
    // baseline for an unowned/unresearched blueprint - not an optimistic
    // guess, the documented "assume perfect research" default the desktop
    // build itself falls back to.
    val materialEfficiency: Int = 10,
    // Job-fee cost-index rate, *before* facility tax/SCC surcharge are
    // added (see `jobCostRate` in ProductionBuildCost.kt) - the flat
    // ACTIVITY_MODS["Tech I"].job_cost_rate fallback desktop itself uses
    // whenever a live system cost index isn't available (see
    // production/constants.py). No live ESI system-cost-index lookup is
    // ported here (same reduction ProductionPricing.kt's own docstring
    // already documents for the job-fee half of Production), so this is
    // always the fallback value here, never a live-looked-up one.
    val jobCostIndexRate: Double = 0.077742,
    // Job-fee facility tax, additive on top of the index rather than
    // folded into it (see production/config.py's own comment on the real
    // EVE formula). 0.25% is the fixed NPC-station rate.
    val facilityTaxRate: Double = 0.0025,

    // -- Added for Build Candidates (data/production/ProductionBuildCandidates.kt) --

    // Minimum build-vs-buy margin (margin_home) a scanned catalog item must
    // clear to be surfaced as a candidate at all - matches
    // production/config.py's own `min_margin` default exactly (15%, "is this
    // worth building at all", same threshold desktop's stock-target planner
    // uses). `min_daily_profit`/Goonmetrics-movement ranking is not ported
    // (see ProductionBuildCandidates.kt's own docstring) - this is the one
    // gate this port actually has.
    val minMargin: Double = 0.15,
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
