package com.pappmichel.evetraderlocal.data.refining

import com.pappmichel.evetraderlocal.data.db.AppDatabase
import com.pappmichel.evetraderlocal.data.db.SettingsEntity
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json

/** Settings for the Ore & Minerals tool, ported from the desktop build's
 * `refining/config.py` `RefiningConfig` - but only the two fields
 * Reprocessing Quote actually reads, not that dataclass's full shape.
 *
 * The desktop `RefiningConfig` also carries the ore/ice path's
 * structure/rig/security/implant/skill fields (`structure_type`, `rig_tier`,
 * `security_status`, `implant`, `reprocessing_skill_level`,
 * `reprocessing_efficiency_skill_level`, `ore_family_skill_levels`) -
 * `quote.py`'s own `evaluate_reprocessing_line` never reads any of them,
 * because the Reprocessing tab always reprocesses via the *scrapmetal* path
 * (`scrapmetal_yield`), regardless of what kind of item was pasted (see that
 * module's own imports: only `scrapmetal_yield`, never `ore_ice_yield`).
 * Those fields exist on desktop for the Ore Shortlist instead (`pricing.py`'s
 * `evaluate_ore_item`), which is not part of this port - see
 * `ReprocessingQuote.kt`'s own module docstring for the full scope
 * decision. Porting them here now would be dead config with nothing to
 * read it, the same "port only what's used" call `SdeRepository.kt`
 * documents for its own table scope - add them back alongside whatever
 * ports the Ore Shortlist. */
@Serializable
data class RefiningConfig(
    /** "Scrapmetal Processing" skill level (0-5), manually entered - this
     * app does not pull skills via ESI, matching the desktop build's own
     * `RefiningConfig` (confirmed with the user during the parent's
     * planning: "don't pull skills via ESI"). Clamped to 0-5 by
     * `clampSkillLevel` wherever it's read, not here - a bad stored value
     * (e.g. a hand-edited settings blob) should be visible as a genuinely
     * bad reading elsewhere just once, not silently rewritten on load. */
    val scrapmetalProcessingSkillLevel: Int = 0,
    /** Deducted from mineral value on the Reprocessing-tab quote - a
     * separate field from any Trading-level fee, since this tool's own
     * refining structure may not be the one Trading/Station Trading use. */
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
