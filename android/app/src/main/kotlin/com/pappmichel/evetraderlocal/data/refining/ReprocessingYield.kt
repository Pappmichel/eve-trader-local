package com.pappmichel.evetraderlocal.data.refining

import com.pappmichel.evetraderlocal.data.sde.SdeRepository
import kotlin.math.floor

/** Scrapmetal-path reprocessing-yield math, ported from the desktop build's
 * `refining/constants.py` + `refining/reprocessing.py` (GitHub issue #90,
 * "Ore & Minerals") - the scrapmetal path only, not the ore/ice path. See
 * `RefiningConfig.kt`'s own docstring for why: `quote.py`'s Reprocessing tab
 * (the one view this port scopes in - see `ReprocessingQuote.kt`) always
 * reprocesses via scrapmetal math regardless of what was pasted, so the
 * ore/ice formula (`ore_ice_yield` - structure/rig/security/implant/
 * per-ore-family skills, confirmed live against wiki.eveuniversity.org/
 * Reprocessing) has no caller on this platform yet and is left out rather
 * than ported speculatively.
 *
 * EVE's own hard skill-level ceiling - skills only ever run 0-5. */
const val MAX_SKILL_LEVEL = 5

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
 * security/implant have NO effect here - a genuine asymmetry vs. the
 * (unported) ore/ice path, confirmed against real in-game values during the
 * desktop build's own planning, not an oversight. Confirmed maximum 55%. */
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
