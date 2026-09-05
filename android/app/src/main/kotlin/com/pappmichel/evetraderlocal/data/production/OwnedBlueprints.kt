package com.pappmichel.evetraderlocal.data.production

import com.pappmichel.evetraderlocal.data.esi.CharacterBlueprint

/**
 * Production's Owned Blueprints view: a Kotlin port of the desktop build's
 * `production/engine.py` `list_owned_blueprints` (`production_owned_
 * blueprints.py`'s `do_list_owned_blueprints`) - every owned blueprint
 * (character + corp), aggregated across identical (type_id, is_original,
 * ME, TE, runs) groups.
 *
 * A BPO is identified by `runs == -1` (ESI's own "this blueprint is an
 * original" sentinel - a BPO has infinite runs, so ESI never reports a real
 * run count for one), not by any explicit is-copy flag - that field only
 * exists on the *asset* endpoints, never on `/characters/{id}/blueprints/`
 * itself. See [CharacterBlueprint]'s own doc comment for why `quantity` is
 * *not* used for this (it is usually another ESI sentinel, -1 original / -2
 * copy, not a real stack size).
 *
 * Simplifications vs. desktop `engine.list_owned_blueprints`, documented
 * rather than silent:
 * - **Live, not synced.** Desktop reads a local cache `production/esi_sync
 *   .py` populates ahead of time from every logged-in producer character
 *   (character + corp blueprints). This port fetches directly from ESI's
 *   `/characters/{id}/blueprints/` per screen visit, for whichever
 *   "Producer" characters are currently logged in, merging their results
 *   (a corp blueprint visible to more than one logged-in character with
 *   the right corp role is de-duplicated by `item_id`) - same live-read
 *   pattern as `ProductionJobs.kt`'s own port of Current Jobs & Slots.
 * - **No location breakdown.** Desktop's own read (`storage.
 *   load_owned_blueprints`) already discards location when it loads the
 *   row tuple, aggregating purely by (type_id, is_original, me, te, runs) -
 *   this port matches that exactly, so there is nothing further reduced
 *   here on that axis.
 */

/** One owned blueprint, aggregated across every location and every logged-in
 * character that reported it - mirrors `models.OwnedBlueprintRow` exactly,
 * minus the desktop dataclass's own field ordering (irrelevant in Kotlin).
 * `runs` is null for a BPO (infinite, nothing to display - matches the
 * desktop view's own `"-" if row.is_original else row.runs` cell). */
data class OwnedBlueprintRow(
    val typeId: Int,
    val typeName: String,
    val isOriginal: Boolean,
    val quantity: Int,
    val materialEfficiency: Int,
    val timeEfficiency: Int,
    val runs: Int?,
)

/** `true` when a `/characters/{id}/blueprints/` row's `runs` field marks it
 * as a BPO (`-1`, ESI's "this blueprint is an original" sentinel) rather
 * than a BPC. Pulled out of [groupOwnedBlueprints] as its own function since
 * it is the one piece of real classification logic this view has - matches
 * `engine.list_owned_blueprints`'s own `runs == -1` check. */
fun isBlueprintOriginal(runs: Int): Boolean = runs == -1

/** Groups raw `/characters/{id}/blueprints/` rows (already merged and
 * de-duplicated by `item_id` across every logged-in producer character, the
 * live-ESI counterpart of desktop's own multi-character `storage.
 * load_owned_blueprints` union) into aggregated [OwnedBlueprintRow]s and
 * resolves each group's display name via `typeNames` - a direct port of
 * `engine.list_owned_blueprints`'s grouping loop, sorted the same way
 * (`rows.sort(key=lambda r: r.type_name)`). `typeNames` misses fall back to
 * the bare type id, same as that function's own `sde_type[2] if sde_type
 * else str(type_id)` fallback. */
fun groupOwnedBlueprints(
    blueprints: List<CharacterBlueprint>,
    typeNames: Map<Int, String>,
): List<OwnedBlueprintRow> {
    data class Key(val typeId: Int, val isOriginal: Boolean, val me: Int, val te: Int, val runs: Int?)

    val grouped = mutableMapOf<Key, Int>()
    for (bp in blueprints) {
        val isOriginal = isBlueprintOriginal(bp.runs)
        val key = Key(
            typeId = bp.typeId,
            isOriginal = isOriginal,
            me = bp.materialEfficiency,
            te = bp.timeEfficiency,
            runs = if (isOriginal) null else bp.runs,
        )
        // ESI's `quantity` field for a blueprint item is usually a sentinel
        // (-1 original / -2 copy), not an actual stack size - only add it
        // when it looks like a real positive count, else this row is 1 item.
        // Matches engine.list_owned_blueprints's own identical guard.
        val add = if (bp.quantity > 0) bp.quantity.toInt() else 1
        grouped[key] = (grouped[key] ?: 0) + add
    }

    return grouped.map { (key, qty) ->
        OwnedBlueprintRow(
            typeId = key.typeId,
            typeName = typeNames[key.typeId] ?: key.typeId.toString(),
            isOriginal = key.isOriginal,
            quantity = qty,
            materialEfficiency = key.me,
            timeEfficiency = key.te,
            runs = key.runs,
        )
    }.sortedBy { it.typeName }
}
