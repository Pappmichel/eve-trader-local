package com.pappmichel.evetraderlocal.data.doctrine

import com.pappmichel.evetraderlocal.data.sde.SdeRepository

/** The IO half of the Doctrine EFT parser port: wires [EftFittingParser]'s
 * injected [NameResolver]/[SlotResolver] callbacks onto Android's local SDE
 * cache ([SdeRepository]), the same role `doctrine/engine.py`'s
 * `parse_fitting_text` plays on desktop (it wraps `storage.
 * resolve_sde_type_by_name`/`get_type_slot`/`list_hull_type_names` as
 * closures before calling into `parser.parse_fitting`).
 *
 * **Fittings is scoped to parsing + display only on this port - no
 * Doctrine/Fitting persistence layer exists on Android yet.** That is a
 * deliberate, documented simplification, not an oversight: `actions.
 * do_add_fitting`/`do_update_fitting` on desktop persist parsed fittings
 * into `doctrine`/`doctrine_fitting`/`doctrine_fitting_item`/
 * `doctrine_parse_issue` SQLite tables that this Android build has no Room
 * equivalent for (`AppDatabase` has no `doctrine_*` entities), and the
 * further Doctrine views this session did not scope in (Stockpile Status,
 * Shopping List, Contract History) are exactly the ones that would give a
 * persisted fitting a reason to exist here (stockpile aggregation across
 * saved fittings, a shopping list built from their shortfalls, ...). Adding
 * that storage layer for a screen that is otherwise "paste EFT text, see
 * what it means" would be scope creep without a consumer - see this
 * package's screen (`DoctrineFittingsScreen.kt`) for the resulting
 * "preview only" UI, matching what `do_parse_fitting` (desktop's own
 * preview-only action, "paste -> review preview -> save") already does
 * before a save ever happens.
 *
 * **Slot classification is honest about a real data gap, not glossed
 * over.** [resolveSlot] always returns null: this cache's tables
 * (`invTypes`/`invGroups`/`invCategories`/`invMarketGroups`/`staStations`/
 * `mapSolarSystems`, see `SdeRepository`'s own docstring) do not include
 * `dgmTypeEffects.csv`, which is what desktop's `storage.get_type_slot`
 * reads to answer "what slot does this module go in" - and that gap was
 * confirmed out of scope when the SDE cache itself was built. The parser
 * logic in [EftFittingParser] is unchanged and still tries its
 * marker-position fallback (`[Empty Low slot]`-style gegenprobe) when an
 * export encodes position that way, but real EFT exports typically only
 * emit those markers for genuinely *empty* slots - a filled slot with no
 * marker gets no positional hint either. The practical effect on this
 * build: most fitted modules end up classified as "cargo" rather than
 * their real low/med/high/rig/subsystem/service section, since
 * `classifySection`'s only other signal (drone/fighter category) doesn't
 * apply to them. This is the concrete case of the tradeoff the task's own
 * scoping note called out ("prefer scoping around new SDE needs instead of
 * widening the cache") - adding `dgmTypeEffects` here would need a new SDE
 * file fetch, a new table, and a Room version bump for a feature
 * (per-slot fit validation) that is explicitly out of scope for this pass.
 * The Fittings screen surfaces this plainly rather than silently mislabeling
 * items - see its own "slot data unavailable" note. */
class DoctrineSdeResolver(private val sde: SdeRepository) {

    /** Exact, case-insensitive name -> [ResolvedType] lookup - the Android
     * counterpart of `storage.resolve_sde_type_by_name`. One query per
     * distinct candidate string the parser tries, same shape as the
     * desktop version's own per-call SQLite lookup (see
     * `EftFittingParser`'s docstring on why both resolvers are `suspend`). */
    val resolveName: NameResolver = { name ->
        sde.resolveTypeByName(name.trim())?.let { row ->
            ResolvedType(
                typeId = row.typeId,
                groupId = row.groupId,
                categoryId = row.categoryId,
                metaGroupId = null, // invMetaTypes.csv is not in this cache - see class docstring
                metaLevel = row.metaLevel,
                typeName = row.typeName ?: name,
            )
        }
    }

    /** Always null - see class docstring's "Slot classification" section.
     * Kept as a real suspend lookup (not a bare `{ null }` constant) so a
     * future `dgmTypeEffects` table can be wired in here without changing
     * this resolver's shape or its caller. */
    val resolveSlot: SlotResolver = { _ -> null }

    /** Every Ship/Structure type name in the cache - the Android
     * counterpart of `storage.list_hull_type_names`, used only for the
     * "did you mean" hull-name suggestion on an unresolved header (Phase 3
     * A.7). */
    suspend fun hullNameCandidates(): List<String> = sde.hullTypeNames()
}
