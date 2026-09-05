package com.pappmichel.evetraderlocal.data.production

import com.pappmichel.evetraderlocal.data.esi.OrderStats
import kotlin.math.ceil

/** Production -> Planner: a Kotlin port of the multi-level bill-of-materials
 * explosion at the heart of the desktop build's Planner tab
 * (`gui/views/production_planner.py`) - given a target item and a quantity,
 * recursively explode its blueprint materials down through every
 * sub-component that is itself manufacturable, all the way to raw
 * minerals/base materials, aggregating total quantities (and, where a price
 * is available, costs) for everything that has to be bought.
 *
 * **Finding on desktop's actual "Planner" (read in full before writing this
 * file, per this port's own process): it is NOT a recursive-explosion
 * feature at all.** `production_planner.py` + `engine.plan_production` is a
 * stock-target-driven, multi-target, ESI-asset-aware buy/build optimizer:
 * it nets every configured stock target against live owned assets and
 * incoming industry jobs (`engine._current_stock`), pools demand for any
 * component shared across *multiple* targets (`engine._expand_all`/
 * `_base_runs`), and produces four separate outputs (an inventory-vs-target
 * table, a build job list with job-run counts, a buy list, and an invention-
 * needs list) - none of which is a single recursive BOM tree for one item.
 * That whole feature needs infrastructure this port doesn't have (a
 * `stock_targets`/`manual_stock` table, `character_assets`/
 * `character_industry_jobs` ESI sync across every producer character, and
 * the invention-decryptor cost machinery in `invention.py`) and is
 * deliberately out of scope here - see `ProductionBuildCost.kt`/
 * `ProductionBuildCandidates.kt`'s own docstrings for the buy-vs-build core
 * this file *does* reuse, and this file's own scope list below.
 *
 * The actual recursive-explosion feature this task describes - "given a
 * target ship/item and quantity, recursively explode its blueprint
 * materials... down to raw minerals" - is desktop's *Item Lookup* tab
 * (`gui/views/production_item_lookup.py`'s "Material Tree" sub-view,
 * backed by `engine.build_material_tree`/`actions.do_build_material_tree`),
 * not its Planner tab. This port fills the Android app's still-placeholder
 * "Planner" menu slot (`ui/nav/ToolMenus.kt`) with that recursive-explosion
 * feature instead of the stock-aware optimizer, matching the task's own
 * description of what to build and its own scope-pragmatically guidance:
 * port the pure BOM-explosion math this platform *can* support today, and
 * document the gap rather than silently relabeling one desktop feature as
 * another.
 *
 * **What's ported**, mirroring `engine.build_material_tree` almost exactly
 * (it already reuses [ProductionBomSource] and [materialQty] the same way
 * `unitBuildCost` does, so no new BOM-reading code is needed):
 * - Recursive explosion of `typeId`'s cached Manufacturing blueprint
 *   materials (Manufacturing-only, same narrowing [unitBuildCost] documents
 *   - no Reaction/Invention/Copying activity types exist in the SDE cache).
 * - Real ME-batch material-quantity rounding via [materialQty] (shared with
 *   the rest of Production, so a sub-material's quantity here can never
 *   drift from what [unitBuildCost] would compute for the same node) and
 *   real run-count scaling (`runs = ceil(quantity / productQuantity)`).
 * - [aggregateLeaves]: flattening the tree into one map of "what actually
 *   has to be bought" (`typeId -> total quantity`), summing a shared
 *   sub-component or raw material across every branch of the tree that
 *   needs it - the desktop `plan_production` pools demand *across targets*;
 *   this pools demand *across branches of one target's own tree*, which is
 *   the equivalent question for a single-item explosion.
 * - Depth-capped recursion ([MAX_BOM_DEPTH], shared with [unitBuildCost]) as
 *   a defensive cycle guard. A real SDE bill of materials never legitimately
 *   nests this deep, and desktop's own `build_material_tree`/`MAX_DEPTH`
 *   docstring calls this "guards against an unexpected SDE cycle" rather
 *   than a case it expects to hit - but *this* port additionally treats a
 *   depth-capped node as a leaf to buy (see [MaterialTreeNode.isExpanded])
 *   rather than silently truncating it, specifically so a pathological
 *   cycle (a manufacturable material that is itself the target, or a cycle
 *   introduced deeper in the tree by a stale/incomplete SDE cache) degrades
 *   to "buy the raw quantity you'd need at this depth" instead of an
 *   infinite loop or a stack overflow - the same fail-safe direction
 *   [unitBuildCost]'s memo-based cycle guard takes, applied to a tree walk
 *   that (unlike [unitBuildCost]) has no memo to short-circuit on, because
 *   the same type_id can legitimately appear at different quantities in
 *   different branches of one explosion (e.g. two sibling components both
 *   needing Tritanium) and must be re-expanded each time, not cached.
 *
 * **What's scoped out**, beyond the "wrong desktop feature" finding above:
 * 1. **No manual-stock or ESI-asset offsetting.** Every quantity in
 *    [MaterialTreeNode]/[aggregateLeaves] is a *gross* requirement - nothing
 *    here subtracts owned inventory the way desktop's stock-aware planner
 *    (or its separate `manual_stock` ledger) does. This is a pure "what does
 *    the recipe call for" explosion, matching `build_material_tree`'s own
 *    documented intent ("the point is exploring a recipe, not planning a
 *    shopping list").
 * 2. **No per-node buy-vs-build decision.** Every node with a cached
 *    Manufacturing blueprint is always expanded (exactly like
 *    `build_material_tree`, which explodes "regardless of whether building
 *    any given node would currently be economical") - this is not
 *    [unitBuildCost]'s recursive cheaper-of-buy-or-build costing. Costing a
 *    leaf requirement (once aggregated) still reuses [buyPrice] from
 *    `ProductionPricing.kt`, so the cost shown for "what you'd have to buy"
 *    is real, just not a build-vs-buy verdict per intermediate node.
 * 3. **No decryptor/Tech II selection.** Desktop's `build_material_tree`
 *    picks a cost-minimizing decryptor per Tech II/III node
 *    (`_material_mult_for`/`t2_memo`) - there is no Invention data in this
 *    app's SDE cache at all (Manufacturing-only, see [unitBuildCost]'s
 *    module docstring), so no Tech II node is ever reachable here; it is
 *    priced/expanded as "Buy" exactly like a genuine raw material, the same
 *    narrowing every other Production port in this app documents.
 */

/** One node of a recursive BOM explosion - the Kotlin shape of desktop's
 * `build_material_tree` dict, minus its `type_name`/`decryptor` fields
 * (name resolution is a UI-layer concern here, via `SdeRepository`'s own
 * `type()` lookup; decryptor selection doesn't exist in this
 * Manufacturing-only port at all - see this file's module docstring,
 * point 3).
 *
 * `quantity` is the *gross* amount of `typeId` this node's parent(s) require
 * (already ME-batch-rounded via [materialQty] for anything below the root -
 * the root node's own `quantity` is exactly the caller-requested amount).
 * `children` is empty for a genuine leaf (no cached Manufacturing blueprint)
 * as well as for a node cut off by [MAX_BOM_DEPTH] - [isExpanded]
 * distinguishes the two for display, but both are treated identically by
 * [aggregateLeaves] (see this file's module docstring on the depth cap). */
data class MaterialTreeNode(
    val typeId: Int,
    val quantity: Double,
    /** True if this node has a cached Manufacturing blueprint AND was
     * actually recursed into (i.e. `children` reflects its real BOM). False
     * for a genuine raw-material leaf, and also false for a manufacturable
     * node that [MAX_BOM_DEPTH] cut off before it could be expanded - see
     * this file's module docstring on why that case is intentionally
     * indistinguishable from a real leaf to [aggregateLeaves]. */
    val isExpanded: Boolean,
    val children: List<MaterialTreeNode> = emptyList(),
)

/** Recursive, hierarchical material tree for one root item - a Kotlin port
 * of `engine.build_material_tree`, narrowed to Manufacturing only (see this
 * file's module docstring). Stops at a leaf with no cached blueprint, at a
 * blueprint with no cached material rows (an incomplete/stale cache - see
 * [unitBuildCost]'s identical fallback for why this collapses to "buy"
 * rather than a "free" build), or at [MAX_BOM_DEPTH].
 *
 * Pure function, no memoization across siblings: a shared sub-component
 * (e.g. Tritanium needed by two different intermediate parts) is
 * legitimately expanded twice, once per branch, each at its own branch's
 * quantity - [aggregateLeaves] is what later sums those branches together,
 * the same "expand fully, pool afterwards" split desktop's own
 * `build_material_tree` (always expands) vs. `_expand_all`/`_base_runs`
 * (pools demand across targets) makes. */
suspend fun buildMaterialTree(
    typeId: Int,
    quantity: Double,
    cfg: ProductionConfig,
    bom: ProductionBomSource,
    depth: Int = 0,
): MaterialTreeNode {
    val bp = if (depth < MAX_BOM_DEPTH) bom.blueprintForProduct(typeId) else null
    if (bp == null || bp.productQuantity <= 0.0) {
        return MaterialTreeNode(typeId, quantity, isExpanded = false)
    }

    val materials = bom.blueprintMaterials(bp.blueprintTypeId)
    if (materials.isEmpty()) {
        // No cached BOM rows for a blueprint id this lookup itself returned
        // - mirrors unitBuildCost's identical guard against an incomplete/
        // stale cache: nothing to explode further, so treat as a leaf.
        return MaterialTreeNode(typeId, quantity, isExpanded = false)
    }

    val materialMult = 1.0 - cfg.materialEfficiency / 100.0
    val runs = ceil(quantity / bp.productQuantity)
    val children = materials.map { (materialId, baseQty) ->
        buildMaterialTree(materialId, materialQty(baseQty, materialMult, runs), cfg, bom, depth + 1)
    }
    return MaterialTreeNode(typeId, quantity, isExpanded = true, children = children)
}

/** Flattens a [buildMaterialTree] result into "what actually has to be
 * bought": one `typeId -> total quantity` entry per leaf (see
 * [MaterialTreeNode.isExpanded]), summed across every branch that needs it.
 * A shared raw material or sub-component required by two different
 * sibling/cousin assemblies therefore appears exactly once here, at the sum
 * of both branches' quantities - this is the "aggregate demand" half of a
 * BOM explosion, the direct equivalent (scoped to one item's own tree
 * rather than desktop's cross-target pooling) of `engine._expand_all`'s
 * demand-pooling.
 *
 * The root node itself is included as a (typeId -> quantity) entry when it
 * is not expanded (i.e. the requested item has no cached Manufacturing
 * blueprint at all) - exploding a non-manufacturable item degrades
 * gracefully to "you just need to buy N of it", not an empty result. */
fun aggregateLeaves(root: MaterialTreeNode): Map<Int, Double> {
    if (!root.isExpanded) return mapOf(root.typeId to root.quantity)
    val totals = LinkedHashMap<Int, Double>()
    for (child in root.children) {
        for ((typeId, qty) in aggregateLeaves(child)) {
            totals[typeId] = (totals[typeId] ?: 0.0) + qty
        }
    }
    return totals
}

/** One aggregated "you need to buy this" line - [aggregateLeaves]'s output
 * paired with a real landed buy price via `buyPrice` (`ProductionPricing.kt`),
 * so the Planner screen can show a total cost per leaf without
 * reimplementing price comparison. `unitCost`/`totalCost` are null when
 * neither market has a sell order for `typeId` - the same "no price
 * anywhere" case [unitBuildCost] surfaces as a null build cost. */
data class LeafRequirement(
    val typeId: Int,
    val quantity: Double,
    val unitCost: Double?,
    val totalCost: Double?,
)

/** Builds the [LeafRequirement] list for a completed explosion: one row per
 * [aggregateLeaves] entry, priced via `buyPrice` against the given
 * home/Jita quote maps (caller-supplied, the same "fetch once for every
 * type_id involved" precedent `scanBuildCandidates` and
 * `structuralMaterialClosure` already set - a caller should price every
 * `typeId` this map's keys plus, if home volumes are needed for the Jita
 * haul term, each one's [ProductionBomSource.volumeOf]). Sorted by total
 * cost descending (falling back to quantity descending when cost is
 * unknown), so the priciest requirement leads. */
suspend fun priceLeafRequirements(
    leaves: Map<Int, Double>,
    home: Map<Int, OrderStats>,
    jita: Map<Int, OrderStats>,
    cfg: ProductionConfig,
    bom: ProductionBomSource,
): List<LeafRequirement> {
    val rows = leaves.map { (typeId, qty) ->
        val volume = bom.volumeOf(typeId)
        val unit = buyPrice(typeId, home, jita, volume, cfg)
        LeafRequirement(typeId, qty, unit, unit?.let { it * qty })
    }
    return rows.sortedWith(
        compareByDescending<LeafRequirement> { it.totalCost ?: -1.0 }.thenByDescending { it.quantity },
    )
}
