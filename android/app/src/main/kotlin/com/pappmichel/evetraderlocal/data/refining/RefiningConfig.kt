package com.pappmichel.evetraderlocal.data.refining

import com.pappmichel.evetraderlocal.data.db.AppDatabase
import com.pappmichel.evetraderlocal.data.db.SettingsEntity
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json

/** Settings for the Ore & Minerals tool, ported from the desktop build's
 * `refining/config.py` `RefiningConfig` - now the full dataclass shape,
 * covering both the scrapmetal path (Reprocessing Quote) and the ore/ice
 * path (Ore Shortlist, `ReprocessingYield.kt`'s `oreIceYield`).
 *
 * **History**: this originally carried only `scrapmetalProcessingSkillLevel`/
 * `refiningTaxRate` - the two fields Reprocessing Quote's `quote.py` port
 * actually read, because that view always reprocesses via the *scrapmetal*
 * path regardless of what kind of item was pasted (see
 * `ReprocessingYield.kt`'s own docstring on the scrapmetal/ore-ice
 * asymmetry). The ore/ice path's own structure/rig/security/implant/skill
 * fields were deliberately left off at that point - dead config with
 * nothing to read it, the same "port only what's used" call
 * `SdeRepository.kt` documents for its own table scope. They're added now,
 * alongside the Ore Shortlist port that actually reads them
 * (`OreShortlist.kt`'s `evaluateOreItem` -> `oreIceYield`). */
@Serializable
data class RefiningConfig(
    // -- Ore/ice path: structure/rig/security/implant - Settings-page
    // dropdowns, not pulled from ESI (confirmed with the user during the
    // parent's own planning: "structure type, rig, skill, and implant
    // should be configurable as settings via a dropdown. don't pull skills
    // via ESI") - independent of TradingConfig.structureId, since the
    // refining structure may be a different one (a dedicated Refinery)
    // than the one Trading/Reprocessing Quote sell at. Valid option sets
    // live in `ReprocessingYield.kt`'s `STRUCTURE_YIELD_MODIFIER`/
    // `RIG_YIELD_BONUS_POINTS`/`REPROCESSING_IMPLANT_BONUS`.
    val structureType: String = "Citadel (no bonuses)",
    val rigTier: String = "No Rig",
    /** Representative system security for the refining structure - -1.0
     * (deep null-sec/wormhole) .. 1.0 (highsec), same raw scale as the
     * SDE's own solar_system security field. Defaults to 0.0 (mid null-sec)
     * rather than null: unlike a resolved-from-ESI system id, this is a
     * plain manual number, and every "not set yet" value should look like a
     * real system, not a special-cased blank. */
    val securityStatus: Double = 0.0,
    val implant: String = "None",

    // -- Ore/ice path: skills (manual, not ESI-synced - see structureType above) --
    val reprocessingSkillLevel: Int = 0,
    val reprocessingEfficiencySkillLevel: Int = 0,
    /** {ore/ice family name: skill level} - one skill per ore/ice *family*
     * (e.g. "Veldspar" covers Veldspar/Concentrated Veldspar/Dense
     * Veldspar), not per exact type id. A family missing from this map is
     * treated as level 5 (maxed), not an error and not unskilled - see
     * `oreIceYield`'s own docstring for why (these are cheap skills almost
     * every active player has trained to 5; defaulting to 0 would
     * understate every not-yet-configured family's profit). */
    val oreFamilySkillLevels: Map<String, Int> = emptyMap(),

    // -- Scrapmetal path (independent of everything above - see
    // `ReprocessingYield.kt`'s own docstring for why structure/rig/
    // security/implant/the two skills above don't apply here) --
    /** "Scrapmetal Processing" skill level (0-5), manually entered - this
     * app does not pull skills via ESI, matching the desktop build's own
     * `RefiningConfig` (confirmed with the user during the parent's
     * planning: "don't pull skills via ESI"). Clamped to 0-5 by
     * `clampSkillLevel` wherever it's read, not here - a bad stored value
     * (e.g. a hand-edited settings blob) should be visible as a genuinely
     * bad reading elsewhere just once, not silently rewritten on load. */
    val scrapmetalProcessingSkillLevel: Int = 0,

    // -- Economics --
    /** Deducted from mineral value on both the Reprocessing-tab quote and
     * the Ore Shortlist - a separate field from any Trading-level fee,
     * since this tool's own refining structure may not be the one
     * Trading/Station Trading sell at. */
    val refiningTaxRate: Double = 0.0,
) {
    companion object {
        const val SCOPE = "refining"
    }
}

class RefiningConfigRepository(private val db: AppDatabase) {
    private val json = Json { ignoreUnknownKeys = true }

    suspend fun load(): RefiningConfig = withContext(Dispatchers.IO) {
        val stored = db.settingsDao().get(RefiningConfig.SCOPE) ?: return@withContext RefiningConfig()
        try {
            json.decodeFromString(stored.overridesJson)
        } catch (e: Exception) {
            RefiningConfig()
        }
    }

    suspend fun save(config: RefiningConfig) = withContext(Dispatchers.IO) {
        db.settingsDao().upsert(SettingsEntity(scope = RefiningConfig.SCOPE, overridesJson = json.encodeToString(config)))
    }
}
