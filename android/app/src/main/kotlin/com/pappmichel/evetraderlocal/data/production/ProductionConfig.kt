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

    // -- Added for the Invention Estimator (data/production/InventionEstimator.kt) --

    // EVE's real invention skill bonus: the encryption skill is worth 1/40
    // (2.5%) per level, each of the two datacore/science skills 1/30
    // (~3.33%) per level - all three additive, never multiplied (see
    // InventionEstimator.kt's `skillMultiplier`). Matches
    // production/config.py's own defaults exactly (4/4/4 - a well-trained
    // but not maxed-out inventor), not an optimistic all-5s guess. EVE
    // skills only ever run 0-5; this port has no settings-screen field (or
    // config-load validator) to range-check a bad override the way
    // desktop's `validate_config_overrides` does, so a value outside 0-5
    // here just produces a probability the real game could never give -
    // not a crash, but not a real number either.
    val encryptionSkillLevel: Int = 4,
    val datacoreSkill1Level: Int = 4,
    val datacoreSkill2Level: Int = 4,

    // -- Added for the Stock Planner (data/production/ProductionEngine.kt,
    // the real port of desktop `engine.plan_production`) --

    // Live ESI system-cost-index lookup target (`EsiClient.
    // getSystemCostIndices`) - a single system id, unlike desktop's
    // `component_system_id`/`manufacturing_system_id` 2-way split (that
    // split exists only to separate reaction/rig-covered-component jobs
    // from everything else, which needs the structure/rig data this app
    // doesn't model at all - see ProductionBuildCost.kt's own module
    // docstring, point 3). Real, working config for the new
    // getSystemCostIndices ESI call this task's own instructions asked for,
    // but not yet consumed by [jobCostIndexRate]'s own flat rate or
    // [unitBuildCost]'s job-cost math - see ProductionEngine.kt's own module
    // docstring for why this pass deliberately stops short of rewiring that
    // shared function's cost formula.
    val manufacturingSystemId: Int? = null,
    // Manual override for the live-looked-up index above - matches
    // production/config.py's own `*_cost_index_override` fields (collapsed
    // to the one system-id split this app models). Same "real config, not
    // yet consumed" status as [manufacturingSystemId] above.
    val manufacturingCostIndexOverride: Double? = null,
    // Extra safety margin built into every intermediate material's target
    // quantity, sized off that material's whole-tree stock-oblivious run
    // count (see `ProductionEngine.kt`'s own `baseRuns`/`expandAll`
    // docstrings for exactly how) - matches production/config.py's
    // `component_overbuild` default exactly (70%: build noticeably more of
    // a shared component than the bare minimum, so a small demand swing
    // doesn't immediately create a new shortfall).
    val componentOverbuild: Double = 0.7,
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
