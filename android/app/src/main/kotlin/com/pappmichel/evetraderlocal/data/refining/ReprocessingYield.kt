package com.pappmichel.evetraderlocal.data.refining

import com.pappmichel.evetraderlocal.data.sde.SdeRepository
import kotlin.math.floor

/** Reprocessing-yield math for both of the desktop build's two structurally
 * different formulas, ported from `refining/constants.py` + `refining/
 * reprocessing.py` (GitHub issue #90, "Ore & Minerals").
 *
 * **History**: this file originally carried only the scrapmetal path -
 * `quote.py`'s Reprocessing tab (`ReprocessingQuote.kt`) always reprocesses
 * via scrapmetal math regardless of what was pasted, so the ore/ice formula
 * (`oreIceYield` below - structure/rig/security/implant/per-ore-family
 * skills, confirmed live against wiki.eveuniversity.org/Reprocessing) had no
 * caller on this platform yet and was left out rather than ported
 * speculatively. It's added now alongside the Ore Shortlist port
 * (`OreShortlist.kt`) that actually calls it.
 *
 * EVE's own hard skill-level ceiling - skills only ever run 0-5. */
const val MAX_SKILL_LEVEL = 5

// ---------------------------------------------------------- Ore/ice path
// Confirmed live against wiki.eveuniversity.org/Reprocessing (2026-08-23),
// two structurally different formulas confirmed to diverge this much, not
// an oversight (see the desktop build's constants.py module docstring):
//
//     Yield = (50 + Rig points) x (1 + Security modifier) x (1 + Structure modifier)
//                                x (1 + Reprocessing skill)
//                                x (1 + Reprocessing Efficiency skill)
//                                x (1 + ore-or-ice-family skill)
//                                x (1 + implant)
// Confirmed maximum: 90.6% (Tatara, T2-Rig, null-sec, max skills, RX-804) -
// (50+3) x 1.12 x 1.055 x 1.15 x 1.10 x 1.10 x 1.04 = 90.63%.

/** Universal starting point before any rig/structure/security modifier -
 * every reprocessing-capable structure starts here (real EVE constant, not
 * tool-specific). */
const val BASE_YIELD_POINTS = 50

/** Structure modifier (Sm), applied multiplicatively together with the
 * security modifier to (BASE_YIELD_POINTS + rig points) - NOT an additive
 * percentage-point bonus (see `oreIceBaseYield`). Only the two real
 * reprocessing-capable Upwell structures (plus the bonus-free Citadel) are
 * listed - Engineering Complexes can't fit reprocessing modules at all in
 * real EVE. `LinkedHashMap`'s insertion order is preserved deliberately -
 * this doubles as a Settings-page dropdown's option order. */
val STRUCTURE_YIELD_MODIFIER: Map<String, Double> = linkedMapOf(
    "Citadel (no bonuses)" to 0.0,
    "Athanor (M Refinery)" to 0.02,
    "Tatara (L Refinery)" to 0.055,
)

/** Rig bonus (Rm) - flat percentage POINTS added to `BASE_YIELD_POINTS`
 * before any multiplier is applied (not a fraction, not pre-scaled by
 * security - the security modifier applies to the whole
 * (BASE_YIELD_POINTS + rig points) sum, rig points included, in one
 * multiplicative step). */
val RIG_YIELD_BONUS_POINTS: Map<String, Int> = linkedMapOf(
    "No Rig" to 0,
    "T1-Rig" to 1,
    "T2-Rig" to 3,
)

/** Reprocessing implants (Zainou 'Beancounter' RX-80x). */
val REPROCESSING_IMPLANT_BONUS: Map<String, Double> = linkedMapOf(
    "None" to 0.0,
    "RX-801" to 0.01,
    "RX-802" to 0.02,
    "RX-804" to 0.04,
)

/** "Reprocessing", max +15% (L5). */
const val REPROCESSING_SKILL_BONUS_PER_LEVEL = 0.03

/** "Reprocessing Efficiency", max +10% (L5). */
const val REPROCESSING_EFFICIENCY_SKILL_BONUS_PER_LEVEL = 0.02

/** e.g. "Veldspar Processing", max +10% (L5). */
const val ORE_FAMILY_SKILL_BONUS_PER_LEVEL = 0.02

fun structureOptions(): List<String> = STRUCTURE_YIELD_MODIFIER.keys.toList()
fun rigOptions(): List<String> = RIG_YIELD_BONUS_POINTS.keys.toList()
fun implantOptions(): List<String> = REPROCESSING_IMPLANT_BONUS.keys.toList()

/** EVE classifies/displays system security at 1-decimal precision, not a
 * raw multi-decimal true-sec value (same real CCP rule: any positive
 * true-sec below 0.05 still rounds to 0.1, never down to a flat 0.0 - 0.0 is
 * reserved for genuine null-sec). */
private fun roundedSecurity(securityStatus: Double): Double {
    if (securityStatus > 0.0 && securityStatus < 0.05) return 0.1
    // Kotlin's Math.round-based rounding matches Python's round() closely
    // enough at one decimal place for every real security value (halfway
    // cases like 0.05 don't occur once the < 0.05 branch above has fired).
    return Math.round(securityStatus * 10.0) / 10.0
}

/** Sec in the real reprocessing formula (see this file's own docstring) -
 * 0.00 highsec, 0.06 lowsec, 0.12 null-sec/wormhole, confirmed live against
 * wiki.eveuniversity.org/Reprocessing (2026-08-23). Null security assumes
 * highsec (0.00, the lowest-bonus case) rather than over-crediting an
 * unverified location. */
fun securityYieldModifier(securityStatus: Double?): Double {
    if (securityStatus == null) return 0.0
    val rounded = roundedSecurity(securityStatus)
    return when {
        rounded >= 0.5 -> 0.0
        rounded > 0.0 -> 0.06
        else -> 0.12
    }
}

/** (50 + Rig points) x (1 + Security modifier) x (1 + Structure modifier) -
 * the one component of the real reprocessing formula that isn't a flat
 * per-level skill/implant bonus. The security modifier applies to the
 * *whole* (50 + rig points) sum in one multiplicative step, not just to the
 * rig's own points. */
fun oreIceBaseYield(cfg: RefiningConfig): Double {
    val basePoints = BASE_YIELD_POINTS + (RIG_YIELD_BONUS_POINTS[cfg.rigTier] ?: 0)
    val secModifier = securityYieldModifier(cfg.securityStatus)
    val structureModifier = STRUCTURE_YIELD_MODIFIER[cfg.structureType] ?: 0.0
    return (basePoints / 100.0) * (1 + secModifier) * (1 + structureModifier)
}

/** Effective reprocessing yield % for a compressed ore/ice item, given its
 * family (e.g. "Veldspar" - see `RefiningConfig.oreFamilySkillLevels`'s own
 * docstring for what a family is). A *known* family simply missing from the
 * map (the user hasn't entered a skill level for it in Settings yet) is
 * assumed maxed (level 5), not unskilled - these are cheap skills almost
 * every active player has trained to 5, and defaulting to 0 would
 * understate every not-yet-configured family's profit; `oreFamily = null`
 * itself (no family at all, e.g. a non-ore/ice item) still gets 0, there's
 * no family skill to assume anything about.
 *
 *     Yield = Base(structure, rig, security) x (1 + Reprocessing skill)
 *                                             x (1 + Reprocessing Efficiency skill)
 *                                             x (1 + ore-or-ice-family skill)
 *                                             x (1 + implant)
 * Confirmed maximum 90.6% (Tatara, T2-Rig, null-sec, max skills, RX-804). */
fun oreIceYield(cfg: RefiningConfig, oreFamily: String? = null): Double {
    val base = oreIceBaseYield(cfg)
    val reprocessing = clampSkillLevel(cfg.reprocessingSkillLevel)
    val efficiency = clampSkillLevel(cfg.reprocessingEfficiencySkillLevel)
    val oreFamilyLevel = if (oreFamily != null) clampSkillLevel(cfg.oreFamilySkillLevels[oreFamily] ?: 5) else 0
    val implantBonus = REPROCESSING_IMPLANT_BONUS[cfg.implant] ?: 0.0
    return base *
        (1 + reprocessing * REPROCESSING_SKILL_BONUS_PER_LEVEL) *
        (1 + efficiency * REPROCESSING_EFFICIENCY_SKILL_BONUS_PER_LEVEL) *
        (1 + oreFamilyLevel * ORE_FAMILY_SKILL_BONUS_PER_LEVEL) *
        (1 + implantBonus)
}

// -------------------------------------------------------- Scrapmetal path

/** Scrapmetal Processing base yield, before any skill bonus - a real EVE
 * constant, confirmed against wiki.eveuniversity.org/Reprocessing
 * (2026-08-23). */
const val SCRAPMETAL_BASE_YIELD = 0.50

/** "Scrapmetal Processing" skill, +1%/level, max +5% (L5) - 50% + 5% =
 * the confirmed 55% ceiling. The +2%/level figure floating around some
 * guides is wrong/inconsistent with that same confirmed 55% max
 * (50% + 5x2% = 60%, not 55%) - 1%/level is the value that actually
 * reconciles with it (same regression this formula's own desktop test
 * guards, ported case-for-case in ReprocessingYieldTest). */
const val SCRAPMETAL_SKILL_BONUS_PER_LEVEL = 0.01

/** Clamps a manually-entered skill level (this app doesn't pull skills via
 * ESI, see `RefiningConfig`'s own docstring) to EVE's real 0-5 range - a bad
 * Settings-page value (typo'd 15) should never silently inflate a yield
 * calculation past what's actually achievable in-game. */
fun clampSkillLevel(level: Int?): Int {
    if (level == null) return 0
    return level.coerceIn(0, MAX_SKILL_LEVEL)
}

/** Effective reprocessing yield % for a non-ore/ice item (modules/ammo/
 * drones/loot - the Reprocessing tab's primary use case). Structure/rig/
 * security/implant/the two ore-path skills above have NO effect here - a
 * genuine asymmetry vs. `oreIceYield`, confirmed against real in-game
 * values during the desktop build's own planning, not an oversight.
 * Confirmed maximum 55%. */
fun scrapmetalYield(cfg: RefiningConfig): Double {
    val skill = clampSkillLevel(cfg.scrapmetalProcessingSkillLevel)
    return SCRAPMETAL_BASE_YIELD + skill * SCRAPMETAL_SKILL_BONUS_PER_LEVEL
}

/** Reprocesses `quantity` units at `yieldPct` efficiency given a type's own
 * `portionSize` and raw `materials` table (both already fetched from the
 * SDE - see the `suspend` overload below), returning
 * `{material_type_id: output_quantity}`. Pure and synchronous on purpose:
 * this is the exact shape `ReprocessingQuoteTest` exercises without an
 * `SdeRepository` fixture, and it's what `evaluateReprocessingLine` calls
 * internally so that function can stay pure too.
 *
 * Two real-EVE roundings, both confirmed with the user during the desktop
 * build's own planning (not a continuous approximation):
 * 1. Whole-**portion** batching first - `quantity` is floor-divided by
 *    `portionSize` (e.g. Veldspar=100) to get the number of complete
 *    portions; leftover units below one portion yield nothing, same as
 *    reprocessing a partial stack in-game.
 * 2. Each material's output is then floored independently (not the total
 *    across materials), matching EVE's own per-material rounding.
 *
 * Returns an empty map for a null/non-positive `portionSize` (not yet
 * SDE-refreshed, or genuinely not reprocessable - e.g. a ship/skillbook/
 * BPO/BPC). */
fun applyYield(portionSize: Int?, materials: List<Pair<Int, Double>>, quantity: Int, yieldPct: Double): Map<Int, Int> {
    if (portionSize == null || portionSize <= 0) return emptyMap()
    val portions = quantity / portionSize
    if (portions <= 0) return emptyMap()
    return materials.associate { (materialTypeId, qtyPerPortion) ->
        materialTypeId to floor(portions * qtyPerPortion * yieldPct).toInt()
    }
}

/** Fetches `typeId`'s portion size and material table from the local SDE
 * cache and applies [applyYield] - the network-free (Room-only) convenience
 * wrapper a screen calls directly; anything that already has both values in
 * hand (like `evaluateReprocessingLine`) should call [applyYield] instead so
 * it stays a `suspend`-free, synchronously testable unit. */
suspend fun applyReprocessingYield(sde: SdeRepository, typeId: Int, quantity: Int, yieldPct: Double): Map<Int, Int> {
    val portionSize = sde.portionSize(typeId)
    val materials = sde.typeMaterials(typeId)
    return applyYield(portionSize, materials, quantity, yieldPct)
}
