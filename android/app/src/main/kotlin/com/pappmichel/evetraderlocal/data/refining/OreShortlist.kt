package com.pappmichel.evetraderlocal.data.refining

import com.pappmichel.evetraderlocal.data.db.AppDatabase
import com.pappmichel.evetraderlocal.data.db.SettingsEntity
import com.pappmichel.evetraderlocal.data.esi.OrderStats
import com.pappmichel.evetraderlocal.data.sde.OreIceCandidateTypeRow
import com.pappmichel.evetraderlocal.data.sde.SdeRepository
import com.pappmichel.evetraderlocal.data.trading.TradingConfig
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.Json

/** Ore & Minerals -> Ore Shortlist, a Kotlin port of the desktop build's
 * `refining/candidate_discovery.py` + `refining/pricing.py` (GitHub issue
 * #91) - the ore/ice import+refine+sell shortlist, using `oreIceYield`
 * (`ReprocessingYield.kt`) rather than Reprocessing Quote's scrapmetal
 * math.
 *
 *     Landed Cost   = Jita sell percentile x (1 + jitaBuyBrokerFee) + volume x importCostPerM3
 *     Mineral Yield = one whole portion's worth of minerals, via oreIceYield
 *                     (this item's family) + applyYield
 *     Mineral Value = sum(mineral_qty x home-structure sell percentile x structureSellHaircut)
 *     Refining Tax  = Mineral Value x RefiningConfig.refiningTaxRate
 *     Net Sell      = Mineral Value - Refining Tax
 *     Profit        = Net Sell - (Landed Cost x portionSize), normalized back to per-unit
 *
 * Reuses `TradingConfig` for jitaRegionId/structureId/broker-fee/haircut/
 * haul-cost - Ore Shortlist buys at Jita and sells (refined minerals) at the
 * same structure Trading/Reprocessing Quote already use, same reasoning as
 * the desktop build's own `pricing.py` module docstring rather than
 * duplicating those fields onto `RefiningConfig`. Portion-size rounding
 * means yield is computed for one whole portion, not per unit - both landed
 * cost and profit are then normalized back to a per-unit figure so this
 * reports the same shape as Trading's own Shortlist (profit/unit, margin,
 * profit/m3).
 *
 * **Scope decision vs. desktop's `actions.py`**: candidate *discovery* is
 * fully ported (`buildOreCandidateUniverse`, a live SDE query - the ore/ice
 * candidate universe is small and fixed, exactly like the desktop build's
 * own scope call for `candidate_discovery.py`, so this needed no scoping
 * down at all, unlike the concern flagged in this port's own task
 * planning). What's simplified is the same gap `ReprocessingQuoteScreen.kt`
 * already documents: no Goonmetrics current-price fallback when no seller
 * token is registered for the home-structure mineral prices - without one
 * this screen still discovers/persists the shortlist and prices the ore
 * side (a public Jita region read), it just cannot price the mineral side. */

// ------------------------------------------------------------ Candidates

/** One compressed ore/ice type from the SDE category/group filter - the
 * fixed, auto-derived universe this shortlist is built from (unlike
 * Trading's own candidate search, there's no manual add-one-by-one step). */
data class OreCandidate(
    val typeId: Int,
    val item: String,
    /** e.g. "Veldspar" - `RefiningConfig.oreFamilySkillLevels` lookup key. */
    val family: String,
    val isIce: Boolean,
    /** The compressed type's own SDE volume (already reflects compression). */
    val volumeM3: Double,
)

/** Ore compression groups are per-family in the real SDE ("Compressed
 * Veldspar", "Compressed Scordite", ...), so the group name itself (minus
 * the "Compressed " prefix) already *is* the family - one group per family.
 * Ice compression instead shares one single "Compressed Ice" group across
 * every ice variant (Blue Ice, Clear Icicle, ...), each with its own
 * processing skill, so ice needs its family derived from the *type* name
 * instead - each compressed ice type is named "Compressed <Family>"
 * individually (e.g. "Compressed Blue Ice").
 *
 * `isIce` is decided by "Ice" appearing in the group name, matching the
 * desktop build's own real-group-name check. */
fun familyAndIsIce(typeName: String, groupName: String): Pair<String, Boolean> {
    val isIce = groupName.contains("Ice")
    val family = if (isIce) {
        typeName.removePrefix("Compressed ").trim()
    } else {
        groupName.removePrefix("Compressed ").trim()
    }
    return family to isIce
}

/** The fixed, SDE-derived candidate universe for the Ore Shortlist - every
 * published compressed ore/ice type, tagged with its family (for
 * `RefiningConfig.oreFamilySkillLevels`) and whether it's ice (the yield
 * formula is otherwise identical between the two - `isIce` matters only for
 * the family lookup and haul-volume/compression display, not the yield math
 * itself, since `oreIceYield` doesn't branch on ore vs. ice at all). */
suspend fun buildOreCandidateUniverse(sde: SdeRepository): List<OreCandidate> {
    val rows: List<OreIceCandidateTypeRow> = sde.oreIceCandidateTypes()
    val candidates = mutableListOf<OreCandidate>()
    for (row in rows) {
        val typeName = row.typeName
        val volume = row.volume
        val groupName = row.groupName
        if (typeName.isNullOrEmpty() || volume == null || volume <= 0.0 || groupName == null) continue
        val (family, isIce) = familyAndIsIce(typeName, groupName)
        candidates.add(OreCandidate(typeId = row.typeId, item = typeName, family = family, isIce = isIce, volumeM3 = volume))
    }
    return candidates
}

// ------------------------------------------------------------- Pricing

const val ORE_NO_MARKET_DATA_DECISION = "No market data"
const val ORE_SKIP_DECISION = "Skip"
const val ORE_IMPORT_DECISION = "Import"
const val ORE_INACTIVE_DECISION = "Inactive"

/** Fully computed Ore Shortlist row - mirrors the desktop build's
 * `OreShortlistRow` shape. */
data class OreShortlistRow(
    val itemId: Int,
    val item: String,
    val family: String,
    val isIce: Boolean,
    val active: Boolean,
    val volumeM3: Double?,
    /** Jita buy percentile x (1 + broker fee) + haul cost. */
    val landedCost: Double?,
    /** `oreIceYield` for this item's family. */
    val yieldPct: Double?,
    val mineralValue: Double?,
    val refiningTax: Double?,
    /** mineralValue - refiningTax. */
    val netSell: Double?,
    /** Currently-listed sell-order quantity at Jita for this ore/ice type itself. */
    val sellListedQty: Double?,
    val profitPerUnit: Double?,
    val margin: Double?,
    val profitPerM3: Double?,
    val decision: String,
)

/** The one definition of "what one unit really costs me, landed at the home
 * structure": Jita's sell percentile plus the buy-side broker fee, plus this
 * app's flat per-m3 haul charge. Null in means null out (nothing listed in
 * Jita right now). */
fun oreLandedCostPerUnit(jitaSell: Double?, volumeM3: Double, tradingCfg: TradingConfig): Double? {
    if (jitaSell == null) return null
    return jitaSell * (1 + tradingCfg.jitaBuyBrokerFee) + volumeM3 * tradingCfg.importCostPerM3
}

/** Every distinct material type id any candidate's portion-size batch
 * reprocesses into - a small, shared set (~15-20 minerals/ice products
 * total) worth fetching order-book stats for once via a bulk call, rather
 * than per-candidate. */
suspend fun mineralTypeIdsForCandidates(sde: SdeRepository, candidates: List<OreCandidate>): List<Int> {
    val ids = sortedSetOf<Int>()
    for (c in candidates) {
        sde.typeMaterials(c.typeId).forEach { (materialTypeId, _) -> ids.add(materialTypeId) }
    }
    return ids.toList()
}

/** Mirrors the desktop build's `pricing._decision` precedence, minus
 * "Already ordered" - the Ore Shortlist has no own-orders/buyer-covered
 * tracking (confirmed out of scope for this phase, matching the desktop
 * build). */
private fun oreDecision(
    active: Boolean, haveData: Boolean, profit: Double?, margin: Double?, tradingCfg: TradingConfig,
): String {
    if (!active) return ORE_INACTIVE_DECISION
    if (!haveData) return ORE_NO_MARKET_DATA_DECISION
    if (profit != null && margin != null && profit > tradingCfg.minProfitThreshold && margin >= tradingCfg.minMarginThreshold) {
        return ORE_IMPORT_DECISION
    }
    return ORE_SKIP_DECISION
}

/** Pure - `jitaStats`/`mineralStatsById`, `portionSize` and `materials` are
 * all pre-fetched by the caller (see `OreShortlistScreen.kt`), no
 * network/SDE calls here. */
fun evaluateOreItem(
    candidate: OreCandidate,
    active: Boolean,
    portionSize: Int?,
    materials: List<Pair<Int, Double>>,
    jitaStats: OrderStats?,
    mineralStatsById: Map<Int, OrderStats>,
    tradingCfg: TradingConfig,
    refiningCfg: RefiningConfig,
): OreShortlistRow {
    val jitaSell = jitaStats?.sellPercentile
    val sellListedQty = jitaStats?.sellVolume

    if (jitaSell == null || portionSize == null || portionSize <= 0) {
        return OreShortlistRow(
            itemId = candidate.typeId, item = candidate.item, family = candidate.family, isIce = candidate.isIce,
            active = active, volumeM3 = candidate.volumeM3, landedCost = null, yieldPct = null, mineralValue = null,
            refiningTax = null, netSell = null, sellListedQty = sellListedQty, profitPerUnit = null, margin = null,
            profitPerM3 = null, decision = oreDecision(active, false, null, null, tradingCfg),
        )
    }

    val unitLandedCost = oreLandedCostPerUnit(jitaSell, candidate.volumeM3, tradingCfg)!!
    val landedCostPerPortion = unitLandedCost * portionSize

    val yieldPct = oreIceYield(refiningCfg, candidate.family)
    val minerals = applyYield(portionSize, materials, portionSize, yieldPct)

    var mineralValue = 0.0
    var haveFullMineralData = minerals.isNotEmpty()
    for ((materialTypeId, qty) in minerals) {
        val stats = mineralStatsById[materialTypeId]
        if (stats?.sellPercentile == null) {
            haveFullMineralData = false
            continue
        }
        mineralValue += qty * stats.sellPercentile * tradingCfg.structureSellHaircut
    }

    if (!haveFullMineralData) {
        return OreShortlistRow(
            itemId = candidate.typeId, item = candidate.item, family = candidate.family, isIce = candidate.isIce,
            active = active, volumeM3 = candidate.volumeM3, landedCost = unitLandedCost, yieldPct = yieldPct,
            mineralValue = null, refiningTax = null, netSell = null, sellListedQty = sellListedQty,
            profitPerUnit = null, margin = null, profitPerM3 = null,
            decision = oreDecision(active, false, null, null, tradingCfg),
        )
    }

    val refiningTax = mineralValue * refiningCfg.refiningTaxRate
    val netSell = mineralValue - refiningTax
    val profitPerPortion = netSell - landedCostPerPortion
    val profitPerUnit = profitPerPortion / portionSize
    val margin = if (landedCostPerPortion != 0.0) profitPerPortion / landedCostPerPortion else null
    val profitPerM3 = if (candidate.volumeM3 != 0.0) profitPerUnit / candidate.volumeM3 else null

    // minProfitThreshold is a per-unit figure (matches Trading's own
    // Shortlist usage) - profitPerPortion would make the Import threshold
    // ~portionSize times too lenient (e.g. Veldspar's 100), same
    // confirmed-real-bug reasoning as the desktop build's own comment here.
    val decision = oreDecision(active, true, profitPerUnit, margin, tradingCfg)

    return OreShortlistRow(
        itemId = candidate.typeId, item = candidate.item, family = candidate.family, isIce = candidate.isIce,
        active = active, volumeM3 = candidate.volumeM3, landedCost = unitLandedCost, yieldPct = yieldPct,
        mineralValue = mineralValue, refiningTax = refiningTax, netSell = netSell, sellListedQty = sellListedQty,
        profitPerUnit = profitPerUnit, margin = margin, profitPerM3 = profitPerM3, decision = decision,
    )
}

fun evaluateOreShortlist(
    candidates: List<OreCandidate>,
    activeById: Map<Int, Boolean>,
    portionSizeById: Map<Int, Int?>,
    materialsById: Map<Int, List<Pair<Int, Double>>>,
    jitaStatsById: Map<Int, OrderStats>,
    mineralStatsById: Map<Int, OrderStats>,
    tradingCfg: TradingConfig,
    refiningCfg: RefiningConfig,
): List<OreShortlistRow> = candidates.map { c ->
    evaluateOreItem(
        candidate = c, active = activeById[c.typeId] ?: true, portionSize = portionSizeById[c.typeId],
        materials = materialsById[c.typeId] ?: emptyList(), jitaStats = jitaStatsById[c.typeId],
        mineralStatsById = mineralStatsById, tradingCfg = tradingCfg, refiningCfg = refiningCfg,
    )
}

// -------------------------------------------------------- Persistence

/** One persisted Ore Shortlist entry - a compressed ore/ice candidate
 * actively tracked, plus the active flag a user can toggle. The Android
 * counterpart of storage.py's `ore_shortlist` table, stored the same way
 * `StationTradingShortlistRepository` stores its own shortlist: one JSON
 * blob under its own scope in the shared `settings` table. */
@Serializable
data class OreShortlistItem(
    val itemId: Int,
    val item: String,
    val family: String,
    val isIce: Boolean,
    val active: Boolean = true,
)

/** Persists Ore Shortlist membership - the counterpart of the desktop
 * build's `storage.upsert_ore_shortlist`/`load_ore_shortlist`. */
class OreShortlistRepository(private val db: AppDatabase) {
    private val json = Json { ignoreUnknownKeys = true }
    private val serializer = ListSerializer(OreShortlistItem.serializer())

    companion object {
        const val SCOPE = "ore_shortlist_items"
    }

    suspend fun load(): List<OreShortlistItem> = withContext(Dispatchers.IO) {
        val stored = db.settingsDao().get(SCOPE) ?: return@withContext emptyList()
        try {
            json.decodeFromString(serializer, stored.overridesJson)
        } catch (e: Exception) {
            emptyList()
        }
    }

    suspend fun save(items: List<OreShortlistItem>) = withContext(Dispatchers.IO) {
        db.settingsDao().upsert(SettingsEntity(scope = SCOPE, overridesJson = json.encodeToString(serializer, items)))
    }

    /** Re-runs discovery (`buildOreCandidateUniverse`) and persists the
     * result: a type id newly discovered starts active; a type id that was
     * already on the list (and possibly deactivated by hand) keeps its
     * stored `active` flag rather than being silently reactivated on every
     * refresh - the direct counterpart of
     * `storage.upsert_ore_shortlist`'s own "insert or update, never resets
     * active" contract. */
    suspend fun upsertFromDiscovery(candidates: List<OreCandidate>) = withContext(Dispatchers.IO) {
        val existingActive = load().associate { it.itemId to it.active }
        val merged = candidates.map { c ->
            OreShortlistItem(
                itemId = c.typeId, item = c.item, family = c.family, isIce = c.isIce,
                active = existingActive[c.typeId] ?: true,
            )
        }
        save(merged)
    }

    suspend fun setActive(itemIds: Set<Int>, active: Boolean) = withContext(Dispatchers.IO) {
        val updated = load().map { if (it.itemId in itemIds) it.copy(active = active) else it }
        save(updated)
    }
}
