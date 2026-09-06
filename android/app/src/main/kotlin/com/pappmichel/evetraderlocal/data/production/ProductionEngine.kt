package com.pappmichel.evetraderlocal.data.production

import com.pappmichel.evetraderlocal.data.esi.OrderStats
import kotlin.math.ceil
import kotlin.math.max
import kotlin.math.min

/**
 * Production's real stock-aware planner - a Kotlin port of the desktop
 * build's `production/engine.py` `plan_production` (the function this
 * repository's own task history repeatedly found missing underneath
 * Planner, Special Orders, Logistics, and Asset-Optimized Planner - see
 * `ProductionPlanner.kt`'s own module docstring for the earlier substitute
 * this file now supersedes as the *real* stock-target-driven optimizer).
 *
 * ## What `plan_production` actually does (read in full, `eve_trader_local/
 * production/engine.py`, before writing a line of this port)
 *
 * For every configured stock target (`type_id`, target `quantity`, and a
 * `jitaTarget` flag saying whether it's priced/margin-gated against Jita or
 * the home structure):
 * 1. Compute `currentStock` = manual-override count + everything ESI says
 *    you own anywhere (character + corp, every location - "do I own this
 *    anywhere" is deliberately not scoped to one curated structure) + the
 *    eventual output of any industry job already in progress (its runs x
 *    product quantity - "as good as in stock" for *sizing* purposes, so the
 *    plan doesn't recommend building a second batch of something already
 *    cooking).
 * 2. `missing = max(0, target - currentStock)`; an `InventoryRow` is
 *    recorded for every target regardless of whether anything is missing.
 * 3. If something is missing, it isn't automatically queued: building it
 *    must first clear `cfg.minMargin` (`buildMargin` - `marginJita` for a
 *    `jitaTarget`, else `marginHome`), *unless* a manual Build/Buy override
 *    exists for that type_id (a manual override always wins, cost-based
 *    gates no longer apply). This mirrors the real desktop reasoning: "if
 *    building isn't profitable, buying isn't assumed to be either, since
 *    both ultimately compete on the same sell price" - a stock target that
 *    fails the margin gate is dropped from the buy/build lists entirely
 *    (though its `InventoryRow.totalMissing` still reports the real
 *    shortfall).
 * 4. Every target that survives the gate seeds two pooled maps:
 *    `seedMissing` (its own shortfall) and `grossDemand` (its full target
 *    quantity, for the Buy List's "% on hand" column).
 * 5. [expandAll] resolves `seedMissing` into a flat `buyTotals` map and a
 *    `buildRuns` map, breadth-first *level by level* across every target at
 *    once (not depth-first per target): a material shared by several
 *    targets (or reached at different tree depths) must be decided on and
 *    expanded exactly *once*, using its *total* pooled demand across every
 *    parent claiming it this round - never once per parent edge. At each
 *    level, each material's target quantity is its pooled gross demand
 *    *plus* an overbuild buffer (`cfg.componentOverbuild x baseQty x
 *    materialMult x that parent's whole-tree base run count`, from
 *    [baseRuns] - deliberately the parent's *base*, stock-oblivious run
 *    count rather than its actual (smaller, stock-netted) run count this
 *    round, and deliberately not re-derived from the pooled target itself:
 *    either would compound the buffer exponentially with recursion depth,
 *    since a lower level's target already carries the level above it's own
 *    buffer). Each level's material target is then netted against
 *    `currentStock` (via a shared, mutated-in-place `stockUsed` ledger, so
 *    the same physical unit is never counted against two different
 *    parents' demand), and whatever's still missing becomes next level's
 *    demand.
 * 6. [buildBuyList]/[buildBuildList] turn the two resolved maps into sorted
 *    result rows (Buy List by total price desc, Build List by job runs
 *    desc).
 *
 * ## Scope decisions, cited against the real desktop function
 *
 * This port reuses [ProductionBomSource]/[unitBuildCost]/[marginHome]/
 * [marginJita]/[buyPrice]/[materialQty] from `ProductionBuildCost.kt`/
 * `ProductionPricing.kt` rather than re-deriving any of that math, so it
 * inherits every simplification those files already document (Manufacturing
 * only - no Reaction/Invention/Copying activity in the SDE cache at all;
 * one flat `cfg.materialEfficiency` ME level, no owned-BPO research or
 * structure/rig bonus; EIV approximated from real material buy prices
 * rather than ESI's adjusted-price catalog - see the `jobCostRate`/
 * `EsiClient.getAdjustedPrices` bullet below for why this pass adds the live
 * ESI plumbing those two real numbers need without yet wiring it into the
 * cost math itself). Additional simplifications specific to the
 * stock-planner port itself,
 * beyond what `ProductionBuildCost.kt` already narrows:
 *
 * - **No Tech II/III invention-needs list.** `engine.plan_production`'s
 *   `invention_list` (recommended invention runs per Tech II/III stock
 *   target) needs `classify_activity`'s Reaction/Tech II/Faction
 *   distinction and the whole decryptor-selection machinery in
 *   `invention.py` (`_tech_ii_mods`) - neither exists on this platform
 *   (Manufacturing-only SDE cache, see `ProductionBuildCost.kt`'s own module
 *   docstring point 1). Every stock target here is priced/expanded as
 *   either "has a cached Manufacturing blueprint" or "must be bought",
 *   exactly like every other Production port in this app; a Tech II/III
 *   target with no cached Manufacturing formula is bought at whatever price
 *   is listed for it, never invented from BPCs. [PlanProductionResult] has
 *   no `inventionList` field at all rather than a permanently-empty one.
 * - **No `job_category`/`decryptor` columns on `BuildJobEntry`.**
 *   `job_category` needs SDE group-id tables (`SdeGroupEntity` exists, but
 *   the ship-size/component-group id constants it would be checked against
 *   are not ported here) this app has no other consumer for; `decryptor` is
 *   always null in a Manufacturing-only port (see previous point). Both are
 *   simply absent from [BuildJobEntry] rather than carried as an
 *   always-empty/always-null field.
 * - **`activity` on [InventoryRow] is "Tech I" or "Input" only** (never
 *   "Reaction"/"Tech II"/"Faction"/...), matching `classify_activity`'s own
 *   fallback label for anything with a real Manufacturing blueprint versus
 *   anything without one - the only two `classify_activity` outcomes this
 *   platform's SDE cache can ever actually produce.
 * - **Stock is read live, not from a synced local cache.** Desktop reads
 *   `production/esi_sync.py`'s pre-populated `character_assets`/
 *   `corp_assets`/`character_industry_jobs`/`corp_industry_jobs` tables.
 *   This port has no such sync job; [StockSource] is the seam a caller
 *   fills with a live per-screen ESI read (character + corp assets, active/
 *   paused/ready industry jobs, for every logged-in producer character,
 *   merged and deduplicated) - the same "live, not synced" pattern
 *   `ProductionJobs.kt`/`OwnedBlueprints.kt` already established for this
 *   app's other ESI-asset-shaped Production views, documented there rather
 *   than repeated here. `engine._stock_on_hand` (the desktop build's own
 *   incoming-job-exclusive "can I click start right now" signal, used only
 *   by `plan_asset_optimized`) is intentionally not ported as its own
 *   function in this pass -
 *   `plan_production` itself never calls it; it is exactly the seam the
 *   still-blocked Asset-Optimized Planner will need, and [StockSource]'s
 *   own two-method split ([StockSource.ownedQuantity]/
 *   [StockSource.incomingIndustryRuns]) already carries the information a
 *   future `_stockOnHand`-equivalent would need without recomputing
 *   anything.
 * - **Live ESI plumbing added, not yet wired into the cost math itself.**
 *   `EsiClient.getSystemCostIndices`/`getAdjustedPrices` (this task's own
 *   instruction to add whatever live ESI data `plan_production` needs
 *   beyond what already existed) are real, working ports of
 *   `pricing.system_cost_indices_for`/`esi_client.get_adjusted_prices`, and
 *   `ProductionConfig.manufacturingSystemId`/`manufacturingCostIndexOverride`
 *   are real, working config for them - but this pass deliberately does
 *   **not** thread either into [unitBuildCost]'s own job-cost-rate/EIV math,
 *   per this task's own "reuse `ProductionBuildCost.kt`'s cost math wherever
 *   it already does the same job rather than reimplementing it" instruction:
 *   [unitBuildCost] is shared with Ship Margins/Build Candidates, and
 *   changing its EIV formula out from under those two screens is real,
 *   separate scope this task's own instructions don't ask for. Both are
 *   genuine, ready-to-use infrastructure for that follow-up (mirroring
 *   `engine._job_cost_rate`'s real EVE formula minus the structure/rig cost-
 *   bonus multiplier this app has no config for, and the reaction/component
 *   2-way system split this app collapses to one system id - see
 *   `ProductionConfig.manufacturingSystemId`'s own comment) - not dead code,
 *   just not yet a caller of it.
 * - **No `plan_special_order`.** Out of scope for this task by its own
 *   instructions - it needs a Special Orders line-item table this app
 *   models separately in `SpecialOrders.kt` and its own from-scratch/
 *   net-against-stock mode switch.
 * - **`plan_asset_optimized` now lives in `ProductionAssetOptimizedEngine.kt`,
 *   not here.** It reuses [StockSource]/[currentStock]/[buyOrBuildDecision]/
 *   [buildMargin]/[baseRuns]/[parentBaseRunsForBuffer]/[materialQty] from
 *   this file directly (see that file's own module docstring for exactly
 *   what's new on top - per-level stock netting and the readiness/
 *   scarce-stock-allocation math `plan_production` itself never needed)
 *   rather than duplicating any of it.
 */

// ---------------------------------------------------------------- stock

/** What [currentStock] needs beyond the manual-override table: live,
 * per-screen ESI reads for one `typeId` at a time (kept as an interface, the
 * same "testable in a plain Kotlin/JVM test" reasoning [ProductionBomSource]
 * documents, so a fake stands in for real ESI calls in tests without Room
 * or an Android runtime). A real implementation should read every
 * character's *and* corp's assets across every location the way desktop's
 * `storage.esi_stock_at_location(type_id, None)` does ("do I own this
 * anywhere" is corp-wide, not scoped to one curated structure) and every
 * currently active/paused/ready industry job's run count for `typeId`'s
 * own blueprint, merged across every logged-in producer character. */
interface StockSource {
    /** Owned quantity of `typeId` right now, everywhere ESI can see it
     * (character + corp assets, every location) - the ESI half of
     * `engine._current_stock`/`_stock_on_hand`, before the manual-override
     * table is added on top. */
    suspend fun ownedQuantity(typeId: Int): Double

    /** Total blueprint *runs* currently in progress (active/paused/ready,
     * never delivered/cancelled/reverted) whose product is `typeId` -
     * mirrors `storage.esi_incoming_industry_qty(type_id)["runs"]`. Building
     * this into a quantity (`runs x productQuantity`) is [currentStock]'s
     * own job, not this method's, since only [currentStock] knows the
     * product quantity per run. */
    suspend fun incomingIndustryRuns(typeId: Int): Double
}

/** A [StockSource] that reports no owned/incoming stock at all - the
 * correct behavior for a caller with no logged-in producer character (same
 * as desktop's own graceful "no ESI data" degradation: a stock target's
 * `currentStock` then falls back to the manual-override table alone,
 * exactly the way [ProductionPricing.homePrices] degrades to no home quotes
 * with no producer character logged in), and a convenient default for
 * tests that only care about the manual-stock half. */
object NoStockSource : StockSource {
    override suspend fun ownedQuantity(typeId: Int): Double = 0.0
    override suspend fun incomingIndustryRuns(typeId: Int): Double = 0.0
}

/** Owned stock right now: manual entry (`manualStock`) + everything
 * [StockSource] reports owned + (when `includeIncoming`, the default) the
 * eventual output of any industry job already in progress, counted as
 * "as good as in stock" for demand-*sizing* purposes - mirrors
 * `engine._current_stock` exactly. `includeIncoming = false` gives the
 * desktop build's separate `_stock_on_hand` reading instead (an in-progress
 * job's output doesn't physically exist yet, so it can't make a *different*
 * job startable right now) - not consumed by [planProduction] itself (see
 * this file's module docstring), kept here as the one shared primitive a
 * future Asset-Optimized Planner port can call with `includeIncoming =
 * false` without re-deriving this function. */
suspend fun currentStock(
    typeId: Int,
    manualStock: Map<Int, Double>,
    stock: StockSource,
    bom: ProductionBomSource,
    includeIncoming: Boolean = true,
): Double {
    var total = manualStock[typeId] ?: 0.0
    total += stock.ownedQuantity(typeId)
    if (includeIncoming) {
        val runs = stock.incomingIndustryRuns(typeId)
        if (runs > 0) {
            val bp = bom.blueprintForProduct(typeId)
            if (bp != null) total += runs * bp.productQuantity
        }
    }
    return total
}

// ------------------------------------------------------- buy-vs-build

/** How to source `typeId`: a manual override if one is configured, else
 * whichever the modeled unit cost (already populated into `costMemo` by a
 * prior [unitBuildCost] call for this `typeId`) says is cheaper - mirrors
 * `engine._buy_or_build_decision` exactly, including "Buy" whenever there's
 * no cached blueprint or [MAX_BOM_DEPTH] was reached. */
suspend fun buyOrBuildDecision(
    typeId: Int,
    cfg: ProductionConfig,
    home: Map<Int, OrderStats>,
    jita: Map<Int, OrderStats>,
    bom: ProductionBomSource,
    manualOverrides: Map<Int, String>,
    costMemo: Map<Int, Double?>,
    hasBlueprint: Boolean,
    depth: Int = 0,
): String {
    manualOverrides[typeId]?.let { return it }
    if (!hasBlueprint || depth >= MAX_BOM_DEPTH) return BuildOrBuyDecision.BUY
    val volume = bom.volumeOf(typeId)
    val buy = buyPrice(typeId, home, jita, volume, cfg)
    val build = costMemo[typeId]
    return if (build != null && (buy == null || build < buy)) BuildOrBuyDecision.BUILD else BuildOrBuyDecision.BUY
}

/** Margin from building one unit of `typeId` and selling it right now -
 * gates whether a *stock target* is worth building at all, separate from
 * [buyOrBuildDecision]'s plain build-cheaper-than-buy sourcing comparison -
 * mirrors `engine._build_margin` exactly. A `jitaTarget` stock target uses
 * [marginJita]; everything else uses [marginHome]. */
suspend fun buildMargin(
    typeId: Int,
    buildCost: Double?,
    jitaTarget: Boolean,
    home: Map<Int, OrderStats>,
    jita: Map<Int, OrderStats>,
    cfg: ProductionConfig,
    bom: ProductionBomSource,
): Double? = if (jitaTarget) {
    marginJita(typeId, buildCost, jita, bom.volumeOf(typeId), cfg)
} else {
    marginHome(typeId, buildCost, home, cfg)
}

// --------------------------------------------------------- base runs

/** Pure structural run count per `typeId`, completely ignoring current
 * stock and never buffered by overbuild - "if every stock target's full
 * target quantity had to be built from absolute zero, how many runs of each
 * item would that take". Used only as a *stable* sizing baseline for
 * [expandAll]'s overbuild buffer - mirrors `engine._base_runs` exactly,
 * including why it must not itself compound level over level (see that
 * function's own docstring, carried into [expandAll]'s here). A manually-
 * forced Buy (`manualOverrides`) stops the walk exactly like a cost-based
 * "Buy" decision would, so this baseline never counts runs for something
 * the user has said never to build. */
suspend fun baseRuns(
    cfg: ProductionConfig,
    home: Map<Int, OrderStats>,
    jita: Map<Int, OrderStats>,
    bom: ProductionBomSource,
    costMemo: MutableMap<Int, Double?>,
    manualOverrides: Map<Int, String>,
    stockTargets: List<StockTarget>,
): Map<Int, Double> {
    val result = mutableMapOf<Int, Double>()

    suspend fun expand(typeId: Int, quantity: Double, depth: Int) {
        if (quantity <= 0.0 || depth >= MAX_BOM_DEPTH) return
        unitBuildCost(typeId, cfg, home, jita, bom, costMemo)
        val bp = bom.blueprintForProduct(typeId)
        val decision = buyOrBuildDecision(typeId, cfg, home, jita, bom, manualOverrides, costMemo, bp != null, depth)
        if (decision == BuildOrBuyDecision.BUY || bp == null) return

        val runs = ceil(quantity / bp.productQuantity)
        result[typeId] = (result[typeId] ?: 0.0) + runs
        val materialMult = 1.0 - cfg.materialEfficiency / 100.0
        for ((materialId, baseQty) in bom.blueprintMaterials(bp.blueprintTypeId)) {
            expand(materialId, materialQty(baseQty, materialMult, runs), depth + 1)
        }
    }

    for (target in stockTargets) expand(target.typeId, target.quantity, 0)
    return result
}

/** The overbuild-buffer baseline to use for `typeId`'s materials *this*
 * round: `baseRuns[typeId]` the *first* time `typeId` is processed as a
 * parent across the whole [expandAll] call, `0.0` every later time -
 * mirrors `engine._parent_base_runs_for_buffer` exactly, including why: a
 * widely-shared component can legitimately reappear as a parent in a
 * *later* round (reached at a different BOM depth from a different stock
 * target) - without this guard, that reappearance would read the same
 * whole-tree aggregate fresh again and add a second full buffer on top of
 * the first. Mutates `bufferedParents` as a side effect. */
internal fun parentBaseRunsForBuffer(typeId: Int, baseRuns: Map<Int, Double>, bufferedParents: MutableSet<Int>): Double {
    if (typeId in bufferedParents) return 0.0
    bufferedParents += typeId
    return baseRuns[typeId] ?: 0.0
}

// --------------------------------------------------------- expand all

/** One resolved build job: `runs` of `productTypeId`'s cached blueprint
 * (`blueprintTypeId`). Key shape for [expandAll]'s `buildRuns` result -
 * collapses desktop's `(blueprint_id, activity_id, product_type_id)` key to
 * two fields since this Manufacturing-only port has no second activity_id
 * to disambiguate against (see `ProductionBuildCost.kt`'s own module
 * docstring, point 1). */
data class BuildJobKey(val blueprintTypeId: Int, val productTypeId: Int)

/** Result of one [expandAll] pass: `buyTotals` (`typeId -> quantity` still
 * to buy after every level's stock netting) and `buildRuns` (`typeId ->
 * total job runs` pooled across every branch that needs it). */
data class ExpandResult(
    val buyTotals: Map<Int, Double>,
    val buildRuns: Map<BuildJobKey, Int>,
)

/** Resolves every stock target's `seedMissing` quantity into buy/build
 * totals, using the buy-vs-build decisions already primed in `costMemo` -
 * mirrors `engine._expand_all` exactly, including its breadth-first,
 * level-by-level (not depth-first per target) processing: a shared material
 * must be decided on and expanded *once* per round, using its *total*
 * pooled demand, not once per parent edge that happens to reach it. See
 * this file's module docstring, point 5, for the overbuild-buffer formula
 * and why it is sized off [baseRuns] rather than each round's own (already-
 * buffered) pooled demand.
 *
 * `grossDemand`, if given, is mutated in place with each material's total
 * *pre-stock-netting* pooled demand across every round - used for the Buy
 * List's "% on hand" column, seeded by the caller with each top-level stock
 * target's own gross quantity before calling this (so a type_id that's
 * *both* a stock target and a shared material downstream gets one
 * correctly-combined total, matching `_expand_all`'s own documented
 * contract).
 *
 * `stockUsed` is a running ledger, seeded by the caller for top-level stock
 * targets and mutated here for everything below them, so a component
 * needed by two different branches doesn't have the same physical stock
 * counted against both. */
suspend fun expandAll(
    seedMissing: Map<Int, Double>,
    cfg: ProductionConfig,
    home: Map<Int, OrderStats>,
    jita: Map<Int, OrderStats>,
    bom: ProductionBomSource,
    costMemo: MutableMap<Int, Double?>,
    manualStock: Map<Int, Double>,
    stock: StockSource,
    stockUsed: MutableMap<Int, Double>,
    baseRuns: Map<Int, Double>,
    manualOverrides: Map<Int, String>,
    grossDemand: MutableMap<Int, Double>? = null,
): ExpandResult {
    val buyTotals = mutableMapOf<Int, Double>()
    val buildRuns = mutableMapOf<BuildJobKey, Int>()
    val bufferedParents = mutableSetOf<Int>()
    val materialMult = 1.0 - cfg.materialEfficiency / 100.0

    var jobsThisLevel: Map<Int, Double> = seedMissing.filterValues { it > 0.0 }
    var depth = 0
    while (jobsThisLevel.isNotEmpty() && depth < MAX_BOM_DEPTH) {
        val nextLevel = mutableMapOf<Int, Double>()

        for ((typeId, quantity) in jobsThisLevel) {
            val bp = bom.blueprintForProduct(typeId)
            val decision =
                buyOrBuildDecision(typeId, cfg, home, jita, bom, manualOverrides, costMemo, bp != null, depth)
            if (decision == BuildOrBuyDecision.BUY || bp == null) {
                buyTotals[typeId] = (buyTotals[typeId] ?: 0.0) + quantity
                continue
            }

            val runs = ceil(quantity / bp.productQuantity)
            val key = BuildJobKey(bp.blueprintTypeId, typeId)
            buildRuns[key] = (buildRuns[key] ?: 0) + runs.toInt()

            val parentBaseRuns = parentBaseRunsForBuffer(typeId, baseRuns, bufferedParents)
            for ((materialId, baseQty) in bom.blueprintMaterials(bp.blueprintTypeId)) {
                val grossNeeded = materialQty(baseQty, materialMult, runs)
                if (grossNeeded <= 0.0) continue
                val overbuild = if (materialId in manualOverrides) 0.0 else cfg.componentOverbuild
                val buffer = overbuild * baseQty * materialMult * parentBaseRuns
                nextLevel[materialId] = (nextLevel[materialId] ?: 0.0) + grossNeeded + buffer
            }
        }

        val next = mutableMapOf<Int, Double>()
        for ((materialId, target) in nextLevel) {
            if (target <= 0.0) continue
            grossDemand?.let { it[materialId] = (it[materialId] ?: 0.0) + target }
            val available = max(0.0, currentStock(materialId, manualStock, stock, bom) - (stockUsed[materialId] ?: 0.0))
            val consumed = min(target, available)
            stockUsed[materialId] = (stockUsed[materialId] ?: 0.0) + consumed
            val netNeeded = target - consumed
            if (netNeeded > 0.0) next[materialId] = (next[materialId] ?: 0.0) + netNeeded
        }
        jobsThisLevel = next
        depth++
    }

    return ExpandResult(buyTotals, buildRuns)
}

// --------------------------------------------------------- result rows

/** Target vs. current stock for one configured stock target - mirrors
 * `models.InventoryRow`, minus the desktop dataclass's Reaction/Tech II/
 * Faction activity labels this Manufacturing-only port can never produce
 * (see this file's module docstring). */
data class InventoryRow(
    val typeId: Int,
    val typeName: String,
    val activity: String, // "Tech I" (has a cached blueprint) | "Input" (must be bought)
    val target: Double,
    val currentStock: Double,
    val totalMissing: Double,
)

/** One Buy List line - mirrors `models.BuyListEntry` exactly. */
data class BuyListEntry(
    val typeId: Int,
    val typeName: String,
    val quantity: Double,
    val unitPrice: Double?,
    val totalPrice: Double?,
    val onHandPct: Double,
    val buyFrom: String?,
)

/** One Build List line - mirrors `models.BuildJobEntry` minus `decryptor`/
 * `jobCategory` (see this file's module docstring for why both are dropped
 * rather than always-null/always-empty). */
data class BuildJobEntry(
    val typeId: Int,
    val typeName: String,
    val blueprintTypeId: Int,
    val quantity: Double,
    val jobRuns: Int,
    val unitBuildCost: Double?,
    val margin: Double?,
)

/** [planProduction]'s full result - mirrors the dict `engine.
 * plan_production` returns, minus `invention_list` (see this file's module
 * docstring). */
data class PlanProductionResult(
    val inventory: List<InventoryRow>,
    val buyList: List<BuyListEntry>,
    val buildList: List<BuildJobEntry>,
)

/** Turns [expandAll]'s `buyTotals` into sorted [BuyListEntry] rows -
 * mirrors `engine._build_buy_list` exactly. `typeName` resolves a display
 * name for a `typeId` (e.g. `{ id -> sdeRepo.type(id)?.typeName ?:
 * id.toString() }`) - a lambda rather than a pre-built map, since neither
 * this function nor [planProduction] (which doesn't know the resolved
 * `buyTotals`/`buildRuns` key set until [expandAll] has already run) can
 * build that map ahead of time the way `buildIndustryJobRows`' caller-
 * supplied `typeNames` map can for a fixed, already-known job list. */
suspend fun buildBuyList(
    buyTotals: Map<Int, Double>,
    grossDemand: Map<Int, Double>,
    cfg: ProductionConfig,
    home: Map<Int, OrderStats>,
    jita: Map<Int, OrderStats>,
    bom: ProductionBomSource,
    typeName: suspend (Int) -> String,
): List<BuyListEntry> {
    val rows = buyTotals.map { (typeId, quantity) ->
        val volume = bom.volumeOf(typeId)
        val unitPrice = buyPrice(typeId, home, jita, volume, cfg)
        val gross = grossDemand[typeId] ?: quantity
        val onHandPct = if (gross > 0.0) max(0.0, min(100.0, (gross - quantity) / gross * 100.0)) else 0.0
        BuyListEntry(
            typeId = typeId,
            typeName = typeName(typeId),
            quantity = quantity,
            unitPrice = unitPrice,
            totalPrice = unitPrice?.let { it * quantity },
            onHandPct = onHandPct,
            buyFrom = buySource(typeId, home, jita, volume, cfg),
        )
    }
    return rows.sortedByDescending { it.totalPrice ?: 0.0 }
}

/** Turns [expandAll]'s `buildRuns` into sorted [BuildJobEntry] rows -
 * mirrors `engine._build_build_list`, minus the `decryptor`/`jobCategory`
 * fields this port doesn't model (see this file's module docstring). */
suspend fun buildBuildList(
    buildRuns: Map<BuildJobKey, Int>,
    costMemo: Map<Int, Double?>,
    cfg: ProductionConfig,
    home: Map<Int, OrderStats>,
    bom: ProductionBomSource,
    typeName: suspend (Int) -> String,
): List<BuildJobEntry> {
    val rows = buildRuns.map { (key, runs) ->
        val bp = bom.blueprintForProduct(key.productTypeId)
        val productQty = bp?.productQuantity ?: 1.0
        val unitCost = costMemo[key.productTypeId]
        BuildJobEntry(
            typeId = key.productTypeId,
            typeName = typeName(key.productTypeId),
            blueprintTypeId = key.blueprintTypeId,
            quantity = runs * productQty,
            jobRuns = runs,
            unitBuildCost = unitCost,
            margin = marginHome(key.productTypeId, unitCost, home, cfg),
        )
    }
    return rows.sortedByDescending { it.jobRuns }
}

// --------------------------------------------------------- orchestrator

/** Runs the full Stock Targets -> Inventory -> Buy/Build pipeline - the
 * direct port of `engine.plan_production` this file's module docstring
 * describes step by step. Callers own gathering the inputs (mirroring every
 * other Production port's own "one shared fetch up front" precedent):
 * `home`/`jita` should already cover `stockTargets`' full structural
 * material closure ([structuralMaterialClosure]). `typeName` resolves a
 * display name for a `typeId` - see [buildBuyList]'s own doc comment for why
 * this is a lookup lambda rather than a pre-built map. */
suspend fun planProduction(
    cfg: ProductionConfig,
    stockTargets: List<StockTarget>,
    manualStock: Map<Int, Double>,
    manualOverrides: Map<Int, String>,
    home: Map<Int, OrderStats>,
    jita: Map<Int, OrderStats>,
    bom: ProductionBomSource,
    stock: StockSource,
    typeName: suspend (Int) -> String,
): PlanProductionResult {
    val costMemo = mutableMapOf<Int, Double?>()
    val stockUsed = mutableMapOf<Int, Double>()

    val base = baseRuns(cfg, home, jita, bom, costMemo, manualOverrides, stockTargets)

    val inventory = mutableListOf<InventoryRow>()
    val seedMissing = mutableMapOf<Int, Double>()
    val grossDemand = mutableMapOf<Int, Double>()

    for (target in stockTargets) {
        val bp = bom.blueprintForProduct(target.typeId)
        val current = currentStock(target.typeId, manualStock, stock, bom)
        val missing = max(0.0, target.quantity - current)
        stockUsed[target.typeId] = (stockUsed[target.typeId] ?: 0.0) + min(current, target.quantity)
        inventory += InventoryRow(
            typeId = target.typeId,
            typeName = target.typeName,
            activity = if (bp != null) "Tech I" else "Input",
            target = target.quantity,
            currentStock = current,
            totalMissing = missing,
        )

        if (missing <= 0.0) continue
        unitBuildCost(target.typeId, cfg, home, jita, bom, costMemo)

        var skipDueToMargin = false
        if (bp != null && target.typeId !in manualOverrides) {
            val margin = buildMargin(target.typeId, costMemo[target.typeId], target.jitaTarget, home, jita, cfg, bom)
            if (margin != null && margin < cfg.minMargin) skipDueToMargin = true
        }
        if (skipDueToMargin) continue

        seedMissing[target.typeId] = missing
        grossDemand[target.typeId] = (grossDemand[target.typeId] ?: 0.0) + target.quantity
    }

    val expanded = expandAll(
        seedMissing, cfg, home, jita, bom, costMemo, manualStock, stock, stockUsed, base, manualOverrides, grossDemand,
    )

    val buyList = buildBuyList(expanded.buyTotals, grossDemand, cfg, home, jita, bom, typeName)
    val buildList = buildBuildList(expanded.buildRuns, costMemo, cfg, home, bom, typeName)

    return PlanProductionResult(inventory, buyList, buildList)
}
