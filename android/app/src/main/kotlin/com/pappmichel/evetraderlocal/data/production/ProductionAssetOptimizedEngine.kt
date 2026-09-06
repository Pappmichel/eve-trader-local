package com.pappmichel.evetraderlocal.data.production

import com.pappmichel.evetraderlocal.data.esi.OrderStats
import kotlin.math.ceil
import kotlin.math.floor
import kotlin.math.max
import kotlin.math.min

/**
 * Production's Asset-Optimized Planner - a Kotlin port of the desktop
 * build's `production/engine.py` `plan_asset_optimized`, now that
 * `ProductionEngine.kt` (the real `plan_production` port) exists to build
 * on. Read `engine.plan_asset_optimized`'s own docstring in full before
 * touching this file - it is reproduced here almost verbatim because this
 * port follows it step for step.
 *
 * ## What `plan_asset_optimized` actually does, and how it differs from
 * `plan_production`
 *
 * Both planners make *exactly* the same buy-vs-build calls
 * ([buyOrBuildDecision]) for the same type_id - "the two Baulisten never
 * disagree on *whether* to build something" (the desktop docstring's own
 * words). They differ only in *how much* of a level's demand gets netted
 * against owned stock:
 *
 * - `plan_production`'s [expandAll] nets a top-level stock target's own
 *   shortfall against stock once, then treats every level below it as pure
 *   gross demand until *that* level's own pooled total is netted - stock
 *   consumption is still tracked level by level via a shared `stockUsed`
 *   ledger, so this isn't "only the top level is stock-aware", but a single
 *   pooled demand number per level, not a per-job breakdown.
 * - `plan_asset_optimized` needs the *per-job* breakdown too, because its
 *   whole point is answering "which of these jobs can I click Start on
 *   right now" ([AssetPlanJob.runsReadyNow]) - a question `plan_production`
 *   never asks. When several jobs this round share a scarce material, the
 *   available stock is handed to the *smallest* claims first
 *   ([allocateScarceStock]) so as many whole jobs as possible become
 *   immediately startable, and each job's own `runsReadyNow` is bottlenecked
 *   by whichever of its direct materials is scarcest right now.
 *
 * Processed breadth-first, level by level (a "round" per BOM depth), exactly
 * like [expandAll] - a level's material demand is only known once the level
 * above it has been sized. Stock consumption for *sizing* (job_runs) is
 * tracked in `stockUsed`, a running ledger across rounds, so a component
 * needed at two different tree depths doesn't get the same physical stock
 * counted twice - same ledger discipline [expandAll] already uses.
 *
 * Readiness ([AssetPlanJob.runsReadyNow]) specifically nets against
 * `includeIncoming = false` stock (see [currentStock]'s own doc comment),
 * with its *own* parallel ledger (`stockUsedOnHand`) - mirrors the desktop
 * build's `_stock_on_hand`/`stock_used_on_hand` split exactly, and for the
 * exact same reason: `currentStock`'s incoming-inclusive number counts an
 * in-progress industry job's eventual output as available for *sizing*
 * purposes, but that output doesn't physically exist yet, so it can't make a
 * *different* job "Ready Now".
 *
 * Each material's per-parent claim also carries the same overbuild buffer
 * [expandAll] applies (`cfg.componentOverbuild x baseQty x materialMult x
 * that parent's whole-tree base run count`, via the shared
 * [parentBaseRunsForBuffer] helper it already exposes) - the two Baulisten
 * must agree on *how much* to build, not just *whether*.
 *
 * Result: [PlanAssetOptimizedResult.jobs], sorted by `jobRuns` descending,
 * same as the desktop build's own `sorted(jobs.values(), key=lambda j:
 * j.job_runs, reverse=True)`.
 *
 * ## Reused vs. new
 *
 * Every buy-vs-build/pricing/base-run primitive is reused directly from
 * `ProductionEngine.kt`/`ProductionBuildCost.kt` - [StockSource],
 * [currentStock], [buyOrBuildDecision], [buildMargin], [baseRuns],
 * [parentBaseRunsForBuffer], [materialQty], [unitBuildCost], [marginHome].
 * Nothing here re-derives any of that math. What's genuinely new is the
 * per-job round-by-round bookkeeping this file's functions implement:
 * [allocateScarceStock] (the smallest-claim-first split) and
 * [planAssetOptimized] itself (the round loop wiring the above together
 * into per-job readiness, ported from `engine.plan_asset_optimized`'s own
 * three-phase-per-round structure - phase A sizes this round's jobs and
 * their raw material claims, phase B allocates scarce stock for readiness
 * and nets buffered demand for sizing, phase C materializes/merges this
 * round's [AssetPlanJob] rows now that both are known).
 *
 * ## Scope decisions, cited against the real desktop function
 *
 * Deliberately narrower than the desktop version, matching every
 * simplification `ProductionEngine.kt`'s own `planProduction` port already
 * made for the identical reasons (see that file's module docstring for the
 * full citations - not re-derived here):
 *
 * - **No `recommended_slots`/free-job-slot-by-category split.** Same gap as
 *   `plan_production`: needs live character-skill/job-slot ESI data and a
 *   per-character "excluded from planning" flag this app has no sync job
 *   for. `runsReadyNow`/`jobRuns` is still a real, useful readiness signal
 *   without the slot split on top of it - matches the desktop docstring's
 *   own "still a real, useful readiness signal" conclusion.
 * - **No `job_category`/`decryptor` columns on [AssetPlanJob].** Same
 *   reasons `BuildJobEntry` (`plan_production`'s own result row) already
 *   drops both - no SDE group-id job-category table ported, and a
 *   Manufacturing-only SDE cache means `decryptor` would always be null
 *   (see `ProductionBuildCost.kt`'s own module docstring, point 1, and
 *   `ProductionEngine.kt`'s own module docstring for the identical
 *   `BuildJobEntry` narrowing).
 * - **`AssetPlanJob.margin` is always [marginHome], never [marginJita].**
 *   Matches `BuildJobEntry.margin`'s own comment ("Production sells only at
 *   home, never Jita") and the real desktop `AssetPlanJob.margin` field,
 *   which is also always `margin_home` regardless of whether the underlying
 *   stock target is a `jitaTarget` - only the *margin gate* (deciding
 *   whether a stock target's demand becomes a job at all) uses
 *   [buildMargin]'s home/Jita dispatch; the row's displayed margin does not.
 * - **`stockCoverage`'s denominator is this app's own single target
 *   quantity**, not the desktop's backup+home+Jita three-way sum - matches
 *   `plan_production`'s own already-simplified stock-target model (one
 *   quantity, one `jitaTarget` flag - see `StockPlan.kt`'s `StockTarget`).
 * - **No live system-cost-index/adjusted-price wiring into the cost math**
 *   - inherited from [unitBuildCost] itself (see `ProductionBuildCost.kt`'s
 *   own module docstring, point 4/5); this file adds no new pricing
 *   primitives of its own, so it carries exactly the same gap
 *   `plan_production`'s own port already documents and does not re-litigate.
 *
 * Everything else - Manufacturing-only BOM data, one flat ME level, no
 * structure/rig bonus, `MAX_BOM_DEPTH` cycle guard - is inherited unchanged
 * from `ProductionBuildCost.kt`/`ProductionEngine.kt`, exactly like
 * `plan_production`'s own port already inherits it.
 */

/** Splits `available` units of a scarce shared material across competing
 * claims (`parentTypeId` to `quantityNeeded`): smallest claim first, each
 * filled completely while stock lasts - maximizes the *count* of claims that
 * end up fully covered rather than spreading partial coverage across
 * everything. Mirrors `engine._allocate_scarce_stock` exactly, including its
 * "ported verbatim from the desktop build's confirmed allocation rule for
 * plan_asset_optimized" provenance. Returns `parentTypeId -> quantityCovered`,
 * one entry per input claim (`0.0` once stock runs out). */
internal fun allocateScarceStock(claims: List<Pair<Int, Double>>, available: Double): Map<Int, Double> {
    var remaining = available
    val covered = mutableMapOf<Int, Double>()
    for ((parentId, qty) in claims.sortedBy { it.second }) {
        val take = min(qty, remaining)
        covered[parentId] = take
        remaining -= take
    }
    return covered
}

/** One row of the asset-aware Bauliste - mirrors `models.AssetPlanJob`
 * minus `jobCategory`/`decryptor` (see this file's module docstring for
 * why both are dropped, matching `BuildJobEntry`'s own precedent). */
data class AssetPlanJob(
    val typeId: Int,
    val typeName: String,
    val blueprintTypeId: Int,
    val quantity: Double,
    val jobRuns: Int,
    /** Of [jobRuns], how many the *scarcest* direct material covers right
     * now - the rest still needs something upstream built or bought first. */
    val runsReadyNow: Int,
    val unitBuildCost: Double?,
    val margin: Double?,
    /** How much of this item's own current demand is already covered by
     * owned stock - 0 (nothing on hand) to 1 (fully covered), null if
     * there's nothing meaningful to compare against. Purely a display
     * signal, not used in the sourcing/queueing math above - see
     * `engine.plan_asset_optimized`'s own `stock_coverage_by_id` docstring
     * for the two denominators this can come from. */
    val stockCoverage: Double?,
)

/** [planAssetOptimized]'s full result - mirrors the dict `engine.
 * plan_asset_optimized` returns (`{"jobs": [...]}`). */
data class PlanAssetOptimizedResult(val jobs: List<AssetPlanJob>)

/** Mutable per-round accumulator for one [AssetPlanJob] while it's still
 * being merged across rounds (the same item can be a job at more than one
 * BOM depth - see `engine.plan_asset_optimized`'s own "merge rather than
 * overwrite" comment) - converted to an immutable [AssetPlanJob] once the
 * whole call finishes. */
private class AssetJobAccumulator(
    val typeId: Int,
    val typeName: String,
    val blueprintTypeId: Int,
    val unitBuildCost: Double?,
    val margin: Double?,
    val stockCoverage: Double?,
    var quantity: Double,
    var jobRuns: Int,
    var runsReadyNow: Int,
) {
    fun toAssetPlanJob() = AssetPlanJob(
        typeId = typeId, typeName = typeName, blueprintTypeId = blueprintTypeId, quantity = quantity,
        jobRuns = jobRuns, runsReadyNow = runsReadyNow, unitBuildCost = unitBuildCost, margin = margin,
        stockCoverage = stockCoverage,
    )
}

/** Runs the full asset-aware Bauliste - the direct port of `engine.
 * plan_asset_optimized` this file's module docstring describes step by
 * step. Callers own gathering the inputs, the same "one shared fetch up
 * front" precedent [planProduction] already sets: `home`/`jita` should
 * already cover `stockTargets`' full structural material closure
 * ([structuralMaterialClosure]). `typeName` resolves a display name for a
 * `typeId` - see [buildBuyList]'s own doc comment for why this is a lookup
 * lambda rather than a pre-built map. */
suspend fun planAssetOptimized(
    cfg: ProductionConfig,
    stockTargets: List<StockTarget>,
    manualStock: Map<Int, Double>,
    manualOverrides: Map<Int, String>,
    home: Map<Int, OrderStats>,
    jita: Map<Int, OrderStats>,
    bom: ProductionBomSource,
    stock: StockSource,
    typeName: suspend (Int) -> String,
): PlanAssetOptimizedResult {
    val costMemo = mutableMapOf<Int, Double?>()
    val base = baseRuns(cfg, home, jita, bom, costMemo, manualOverrides, stockTargets)

    // Same margin gate as plan_production: a stock target's demand is
    // dropped entirely (no job here) if building it doesn't clear
    // cfg.minMargin - accumulated across every top-level target first, then
    // reused for the recursive per-material decision below, so a low-margin
    // stock target that's *also* a raw material of another job can't be
    // excluded from plan_production but still "Build" here for the same
    // type_id (mirrors the desktop build's own confirmed bug guard).
    val marginExcluded = mutableSetOf<Int>()
    var jobsThisLevel = mutableMapOf<Int, Double>()
    val stockCoverageById = mutableMapOf<Int, Double?>()

    for (target in stockTargets) {
        if (bom.blueprintForProduct(target.typeId) == null) continue
        val current = currentStock(target.typeId, manualStock, stock, bom)
        val missing = max(0.0, target.quantity - current)
        if (missing <= 0.0) continue
        unitBuildCost(target.typeId, cfg, home, jita, bom, costMemo)

        if (target.typeId !in manualOverrides) {
            val margin = buildMargin(target.typeId, costMemo[target.typeId], target.jitaTarget, home, jita, cfg, bom)
            if (margin != null && margin < cfg.minMargin) marginExcluded += target.typeId
        }
        if (target.typeId in marginExcluded) continue
        val decision =
            buyOrBuildDecision(target.typeId, cfg, home, jita, bom, manualOverrides, costMemo, true)
        if (decision != BuildOrBuyDecision.BUILD) continue

        jobsThisLevel[target.typeId] = (jobsThisLevel[target.typeId] ?: 0.0) + missing
        stockCoverageById[target.typeId] = if (target.quantity > 0.0) min(1.0, current / target.quantity) else null
    }

    val jobs = mutableMapOf<Int, AssetJobAccumulator>()
    val stockUsed = mutableMapOf<Int, Double>()
    // Parallel ledger, on-hand-only (see currentStock's includeIncoming
    // param) - tracks readiness consumption separately from stockUsed's
    // incoming-inclusive planning consumption.
    val stockUsedOnHand = mutableMapOf<Int, Double>()
    val bufferedParents = mutableSetOf<Int>()
    val materialMult = 1.0 - cfg.materialEfficiency / 100.0

    var depth = 0
    while (jobsThisLevel.isNotEmpty() && depth < MAX_BOM_DEPTH) {
        // Phase A: this round's net production quantity -> runs + raw
        // material claims (not yet netted against stock).
        val runsThisRound = mutableMapOf<Int, Int>()
        val blueprintByType = mutableMapOf<Int, Int>()
        val nextLevelClaims = mutableMapOf<Int, MutableList<Pair<Int, Double>>>()
        val bufferedDemand = mutableMapOf<Int, Double>()

        for ((typeId, quantity) in jobsThisLevel) {
            val bp = bom.blueprintForProduct(typeId) ?: continue
            val runs = ceil(quantity / bp.productQuantity).toInt()
            runsThisRound[typeId] = runs
            blueprintByType[typeId] = bp.blueprintTypeId

            val parentBaseRuns = parentBaseRunsForBuffer(typeId, base, bufferedParents)
            for ((materialId, baseQty) in bom.blueprintMaterials(bp.blueprintTypeId)) {
                val bareNeeded = materialQty(baseQty, materialMult, runs.toDouble())
                if (bareNeeded <= 0.0) continue
                nextLevelClaims.getOrPut(materialId) { mutableListOf() } += typeId to bareNeeded
                val overbuild = if (materialId in manualOverrides) 0.0 else cfg.componentOverbuild
                val buffer = overbuild * baseQty * materialMult * parentBaseRuns
                bufferedDemand[materialId] = (bufferedDemand[materialId] ?: 0.0) + bareNeeded + buffer
            }
        }

        // Phase B: readiness (minFraction) is allocated across the *bare*
        // per-job claims, smallest first, against stock physically on hand
        // right now - purely informational. Actual consumption/shortfall
        // sizing uses the *buffered* pooled total against currentStock's
        // incoming-inclusive availability instead.
        val minFraction = runsThisRound.keys.associateWithTo(mutableMapOf()) { 1.0 }
        val nextJobsThisLevel = mutableMapOf<Int, Double>()

        for ((materialId, claims) in nextLevelClaims) {
            val mBp = bom.blueprintForProduct(materialId)
            val available =
                max(0.0, currentStock(materialId, manualStock, stock, bom) - (stockUsed[materialId] ?: 0.0))
            val availableOnHand = max(
                0.0,
                currentStock(materialId, manualStock, stock, bom, includeIncoming = false) -
                    (stockUsedOnHand[materialId] ?: 0.0),
            )
            val coveredByParent = allocateScarceStock(claims, availableOnHand)
            stockUsedOnHand[materialId] = (stockUsedOnHand[materialId] ?: 0.0) + coveredByParent.values.sum()

            for ((parentTypeId, qty) in claims) {
                val fraction = if (qty > 0.0) (coveredByParent[parentTypeId] ?: 0.0) / qty else 1.0
                if (fraction < (minFraction[parentTypeId] ?: 1.0)) minFraction[parentTypeId] = fraction
            }

            val bufferedTotal = bufferedDemand[materialId] ?: 0.0
            val consumed = min(bufferedTotal, available)
            stockUsed[materialId] = (stockUsed[materialId] ?: 0.0) + consumed
            val shortfall = bufferedTotal - consumed
            if (shortfall <= 0.0 || mBp == null) continue
            if (materialId !in marginExcluded &&
                buyOrBuildDecision(materialId, cfg, home, jita, bom, manualOverrides, costMemo, true, depth + 1) ==
                BuildOrBuyDecision.BUILD
            ) {
                nextJobsThisLevel[materialId] = (nextJobsThisLevel[materialId] ?: 0.0) + shortfall
                if (materialId !in stockCoverageById) {
                    stockCoverageById[materialId] = if (bufferedTotal > 0.0) available / bufferedTotal else null
                }
            }
        }

        // Phase C: materialize/merge this round's AssetPlanJob entries now
        // that readiness is known.
        for ((typeId, runs) in runsThisRound) {
            val blueprintTypeId = blueprintByType.getValue(typeId)
            val bp = bom.blueprintForProduct(typeId) ?: continue
            val readyIncrement = floor(runs * (minFraction[typeId] ?: 1.0)).toInt()

            val existing = jobs[typeId]
            if (existing == null) {
                jobs[typeId] = AssetJobAccumulator(
                    typeId = typeId,
                    typeName = typeName(typeId),
                    blueprintTypeId = blueprintTypeId,
                    unitBuildCost = costMemo[typeId],
                    margin = marginHome(typeId, costMemo[typeId], home, cfg),
                    stockCoverage = stockCoverageById[typeId],
                    quantity = runs * bp.productQuantity,
                    jobRuns = runs,
                    runsReadyNow = readyIncrement,
                )
            } else {
                // Same item is a job in more than one round (needed at two
                // different tree depths) - merge rather than overwrite, so
                // an earlier round's already-netted readiness isn't lost.
                existing.jobRuns += runs
                existing.quantity += runs * bp.productQuantity
                existing.runsReadyNow += readyIncrement
            }
        }

        jobsThisLevel = nextJobsThisLevel
        depth++
    }

    return PlanAssetOptimizedResult(jobs.values.map { it.toAssetPlanJob() }.sortedByDescending { it.jobRuns })
}
