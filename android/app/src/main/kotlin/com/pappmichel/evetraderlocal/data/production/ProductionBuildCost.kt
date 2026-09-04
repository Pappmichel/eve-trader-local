package com.pappmichel.evetraderlocal.data.production

import com.pappmichel.evetraderlocal.data.esi.OrderStats
import kotlin.math.ceil
import kotlin.math.max
import kotlin.math.round

/** Production's first real *build* cost - a Kotlin port of the buy-vs-build
 * core of the desktop build's production/engine.py, now that the Android
 * SDE cache carries Manufacturing-only blueprint BOM data
 * (`sde_blueprint_products`/`sde_blueprint_materials` - see
 * `data/sde/SdeEntities.kt`). Backs the "Ship Margins & Market Status"
 * screen's single-item lookup (`ui/screens/ShipMarginScreen.kt`) - not that
 * view's full whole-catalog scan (`engine.discover_ship_margins`), the same
 * "one real vertical slice, not the whole feature" precedent
 * `ProductionPricing.kt`/`ItemLookupScreen.kt` already set for Item Lookup.
 *
 * Simplifications vs. desktop `engine.py`, documented here rather than
 * silently (this list is intentionally exhaustive - anything not mentioned
 * here is not a difference):
 *
 * 1. **Manufacturing only.** Reaction, Invention (Tech II/III) and Copying
 *    are not modeled at all - the SDE cache only carries activityID=1 rows
 *    (see `SdeBlueprintMaterialEntity`'s docstring for why). A type whose
 *    real blueprint is a Reaction formula or that is only buildable via an
 *    invented BPC has no row in `blueprintForProduct` at all, so it is
 *    priced as "Buy" here exactly like a genuine raw material would be -
 *    not classified differently the way desktop's `classify_activity`
 *    would (Reaction/Tech II/Faction/etc. labels). This is a real
 *    narrowing of what's "buildable" here, not a bug: every Tech I item
 *    manufactured directly from a T1 BPO (the large majority of the
 *    buildable catalog) still prices correctly.
 * 2. **One flat ME level, no owned-BPO research.** `cfg.materialEfficiency`
 *    (0-10) applies uniformly to every Manufacturing material, instead of
 *    desktop's real owned-BPO-or-perfect-research-baseline distinction
 *    (`engine._owned_bpo_mods`) - this app has no `character_blueprints`
 *    ESI sync (production/esi_sync.py) to read a real BPO's researched
 *    ME/TE from. TE (job time) is not modeled at all: this slice only
 *    prices ISK cost, never job duration, so there is nothing for a time
 *    multiplier to feed.
 * 3. **No structure/rig bonus.** Desktop stacks a structure+rig multiplier
 *    on top of the blueprint's own ME (`constants.structure_rig_multiplier`)
 *    - there is no structure/rig configuration on Android at all. Treat
 *    `cfg.materialEfficiency` as already being "your real effective ME",
 *    structure/rig bonus included, if you want to approximate one by hand.
 * 4. **No live system cost index.** `cfg.jobCostIndexRate` is always the
 *    flat `ACTIVITY_MODS["Tech I"].job_cost_rate` fallback value desktop
 *    itself falls back to when a live ESI system cost index can't be
 *    reached (production/constants.py) - no `system_cost_indices_for`
 *    ESI call exists in this app's `EsiClient` yet (see
 *    `ProductionPricing.kt`'s own docstring, which documents the same gap
 *    for the job-fee half of pricing.py).
 * 5. **EIV approximated from real material cost, not ESI adjusted
 *    prices.** Real EVE prices the job installation fee off "Estimated
 *    Item Value" - each material's *base* (ME 0) quantity times CCP's own
 *    `adjusted_price` valuation (`GET /markets/prices/`), a regulated
 *    number decoupled from live market prices. This app's `EsiClient` has
 *    no adjusted-price endpoint ported, so [unitBuildCost] uses each
 *    material's real ME-0 buy price instead - directionally the same
 *    "bigger, pricier materials mean a bigger job fee" shape, but not the
 *    real CCP-regulated number. A future adjusted-price port should
 *    replace this rather than layer on top of it.
 * 6. **No stock-aware planning, no buy-vs-build candidate discovery, no
 *    invention.** [unitBuildCost] answers exactly one question - "what
 *    would it cost to build one unit of this right now" - recursively
 *    picking whichever of buy/build is cheaper for every sub-material, the
 *    same as desktop's `_unit_cost`. Nothing here pools demand across
 *    multiple targets, plans a shopping list, or scans the whole catalog
 *    for candidates (`engine.plan_production`/`discover_build_candidates`
 *    are not ported).
 */

/** Guards against an SDE cycle (a blueprint whose materials transitively
 * include its own product) - mirrors `engine.MAX_DEPTH`. A real bill of
 * materials never legitimately goes this deep. */
const val MAX_BOM_DEPTH = 10

/** "Which blueprint makes this product, and how many per run" - the shape
 * [ProductionBomSource.blueprintForProduct] returns, mirroring the
 * (blueprint_type_id, quantity) half of `storage.get_blueprint_for_product`
 * this Manufacturing-only lookup still needs. */
data class BlueprintForProduct(val blueprintTypeId: Int, val productQuantity: Double)

/** What [unitBuildCost] needs from the SDE cache - kept as an interface
 * (rather than a direct `SdeRepository` dependency) so the recursive cost
 * math is testable in a plain Kotlin/JVM test without Room or an Android
 * runtime (see `src/test/kotlin/.../ProductionBuildCostTest.kt`).
 * `SdeRepository` implements this directly. */
interface ProductionBomSource {
    suspend fun blueprintForProduct(productTypeId: Int): BlueprintForProduct?
    suspend fun blueprintMaterials(blueprintTypeId: Int): List<Pair<Int, Double>>
    suspend fun volumeOf(typeId: Int): Double?
}

/** Real EVE material-quantity rounding (wiki.eveuniversity.org's Blueprint
 * research page, ported verbatim from `engine._material_qty`'s own
 * docstring): a job's material requirement is rounded UP once for the whole
 * batch of `runs`, and can never drop below `runs` itself - ME never
 * reduces a material below 1 unit/run, no matter how large the reduction.
 * Rounds the raw product to 2 decimal places first, matching desktop's own
 * `round(x, 2)` before `math.ceil`, so float noise just past a whole number
 * (e.g. 1.9999999999) doesn't spuriously ceil up an extra unit. */
fun materialQty(baseQty: Double, materialMult: Double, runs: Double): Double {
    val raw = round(baseQty * materialMult * runs * 100.0) / 100.0
    return max(runs, ceil(raw))
}

/** (sellPrice - buildCost) / buildCost, or null if buildCost isn't a real
 * positive cost - the shared core of [marginHome]/[marginJita], mirroring
 * `engine._sell_margin` exactly. */
fun sellMargin(sellPrice: Double, buildCost: Double?): Double? {
    if (buildCost == null || buildCost <= 0.0) return null
    return (sellPrice - buildCost) / buildCost
}

/** Margin from building one unit and selling it at the home structure's
 * current sell quote, net of `cfg.marketFeesRate` - mirrors
 * `engine.margin_home`. Null if there's no home sell quote to compare
 * against. */
fun marginHome(typeId: Int, buildCost: Double?, home: Map<Int, OrderStats>, cfg: ProductionConfig): Double? {
    val sell = home[typeId]?.sellPercentile
    if (sell == null || sell <= 0.0) return null
    return sellMargin(sell * (1 - cfg.marketFeesRate), buildCost)
}

/** Margin from building one unit, shipping it to Jita, and selling it
 * there, net of `cfg.marketFeesRate` and the export haul cost - mirrors
 * `engine.margin_jita`. Null if there's no Jita sell quote. */
fun marginJita(
    typeId: Int,
    buildCost: Double?,
    jita: Map<Int, OrderStats>,
    volumeM3: Double?,
    cfg: ProductionConfig,
): Double? {
    val sell = jita[typeId]?.sellPercentile
    if (sell == null || sell <= 0.0) return null
    val exportCost = cfg.haulCostPerM3 * (volumeM3 ?: 0.0)
    return sellMargin(sell * (1 - cfg.marketFeesRate) - exportCost, buildCost)
}

/** Pure (no side effects) recursive best-of-buy-or-build unit cost estimate
 * for `typeId` - a Kotlin port of `engine._unit_cost`/`_material_cost_and_eiv`
 * narrowed to Manufacturing only (see this file's module docstring for the
 * full list of simplifications). Memoized per type_id in `memo`, which the
 * caller owns and can reuse across items/sub-materials; `memo[id] = null`
 * is written *before* recursing into materials, the same cycle-breaking
 * trick `_unit_cost` uses, so a (defensive, shouldn't-happen) SDE cycle
 * degrades to that node's buy price rather than a stack overflow.
 *
 * Returns null only if `typeId` can neither be bought (no sell order on
 * either market) nor built (no cached Manufacturing blueprint, or a
 * sub-material that itself can't be costed) - a build with an unpriceable
 * input collapses to the item's own buy price rather than silently
 * treating the missing material as free, same as desktop. */
suspend fun unitBuildCost(
    typeId: Int,
    cfg: ProductionConfig,
    home: Map<Int, OrderStats>,
    jita: Map<Int, OrderStats>,
    bom: ProductionBomSource,
    memo: MutableMap<Int, Double?> = mutableMapOf(),
    depth: Int = 0,
): Double? {
    if (memo.containsKey(typeId)) return memo[typeId]
    memo[typeId] = null // cycle guard, refined below once computed

    val volume = bom.volumeOf(typeId)
    val buy = buyPrice(typeId, home, jita, volume, cfg)

    if (depth >= MAX_BOM_DEPTH) {
        memo[typeId] = buy
        return buy
    }

    val bp = bom.blueprintForProduct(typeId)
    if (bp == null || bp.productQuantity <= 0.0) {
        memo[typeId] = buy
        return buy
    }
    val materials = bom.blueprintMaterials(bp.blueprintTypeId)
    if (materials.isEmpty()) {
        // No cached BOM rows for a blueprint id this lookup itself returned
        // - either a genuinely material-less formula (none exist in real
        // EVE Manufacturing) or an incomplete/stale cache. Either way there
        // is nothing to build from, so fall back to buy rather than pricing
        // a "free" build.
        memo[typeId] = buy
        return buy
    }

    val materialMult = 1.0 - cfg.materialEfficiency / 100.0
    var materialCost = 0.0
    var eiv = 0.0
    for ((materialId, baseQty) in materials) {
        val materialCostPerUnit = unitBuildCost(materialId, cfg, home, jita, bom, memo, depth + 1)
        if (materialCostPerUnit == null) {
            // A sub-material with no price anywhere collapses the whole
            // build to this item's own buy price - matching
            // `engine._unit_cost`'s "a material with no sell order
            // anywhere and no blueprint" contract exactly.
            memo[typeId] = buy
            return buy
        }
        materialCost += materialQty(baseQty, materialMult, 1.0) * materialCostPerUnit
        // EIV approximation - see this file's module docstring, point 5.
        eiv += baseQty * (buyPrice(materialId, home, jita, null, cfg) ?: 0.0)
    }

    val jobCostRate = cfg.jobCostIndexRate + cfg.facilityTaxRate + SCC_SURCHARGE_RATE
    val buildCost = (materialCost + eiv * jobCostRate) / bp.productQuantity
    val best = if (buy == null) buildCost else minOf(buy, buildCost)
    memo[typeId] = best
    return best
}

/** CCP's fixed, structure-owner-independent job surcharge - matches
 * `production/constants.SCC_SURCHARGE_RATE` exactly. Not a `ProductionConfig`
 * field: like the desktop constant it mirrors, nobody can configure this
 * away, it's a flat EVE-wide rate. */
const val SCC_SURCHARGE_RATE = 0.04
