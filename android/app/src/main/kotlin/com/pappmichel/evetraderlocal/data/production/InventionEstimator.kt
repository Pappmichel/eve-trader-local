package com.pappmichel.evetraderlocal.data.production

import com.pappmichel.evetraderlocal.data.esi.OrderStats

/** Invention cost/probability math - a Kotlin port of the desktop build's
 * `production/invention.py`: for one invention source (a T1 blueprint for
 * Tech II, a Sleeper relic for Tech III) x one decryptor, estimate the
 * datacore + decryptor cost, the success probability, and the resulting
 * BPC's run count, and pick the "best" one by minimising *net* cost per BPC
 * run after accounting for the material savings the resulting ME gives on
 * every subsequent build:
 * `((datacore+decryptor cost)/probability)/outputRuns - (me/100)*reducibleMaterialCostPerRun`.
 *
 * The probability formula is EVE's actual invention mechanic (per
 * wiki.eveuniversity.org/Invention, same source desktop's own module
 * docstring cites) - two separate datacore/science skills plus one
 * encryption skill, contributing at different rates and stacking
 * additively, not multiplicatively:
 * ```
 * probability = baseProbability
 *             * (1 + (datacoreSkill1Level + datacoreSkill2Level)/30 + encryptionSkillLevel/40)
 *             * decryptorProbabilityMultiplier
 * ```
 *
 * **Tech III is NOT scoped down here** - a correction worth stating plainly,
 * because an earlier pass through this codebase concluded otherwise and was
 * wrong. Tech III hulls/subsystems use the exact same activity_id=8
 * Invention mechanic as Tech II (CCP removed the old relic-based "Reverse
 * Engineering" years ago - `sde.py`'s own comment on `_RELEVANT_ACTIVITIES`
 * confirms this against real SDE data), so probability/runs/materials work
 * identically either way; only the *input* consumed differs (a real,
 * ownable/reprintable T1 BPO/BPC for Tech II, a Sleeper relic - Ancient
 * Relics, category id 34 - for Tech III, which can only ever be bought or
 * looted). Every piece of data that distinction needs -
 * `sde_blueprint_products`/`sde_blueprint_materials` activityId=8 rows,
 * `sde_invention_probability`, and the category id join to tell a relic
 * apart from a genuine T1 blueprint - was already in the Android SDE cache's
 * reach the moment those two tables existed (see `SdeRepository.kt`'s own
 * docstring for the same correction from the other side). [estimate] prices
 * a Tech III relic into the attempt cost via the same `relicCost` field a T1
 * blueprint always reports as 0.0 for - see this file's own [estimate]
 * doc for why the two must differ there.
 *
 * Known simplifications, all inherited from what this Android build's
 * pricing/build-cost slices already document rather than being new to
 * Invention specifically:
 * - **No Goonmetrics fallback.** [estimate] prices every input through
 *   [buyPrice] (home-structure-or-Jita ESI order books only), matching
 *   `ProductionPricing.kt`'s own documented gap - desktop's
 *   `pricing.buy_price` additionally falls back to a Goonmetrics snapshot
 *   when ESI has no producer character logged in or docking fails.
 * - **The invention job's own installation fee (facility fee) is ignored**,
 *   and so is the T1 BPC's own copy cost (copy time + copy job fee) for a
 *   genuine T1 blueprint - matching `invention.py`'s own documented
 *   simplification exactly, including why it does NOT extend to a Tech III
 *   relic (a relic must be bought/looted fresh per attempt; a T1 BPC is
 *   usually a near-free reprint of an owned BPO).
 * - [reducibleMaterialCost] reuses [ProductionBomSource.blueprintMaterials],
 *   which is Manufacturing (activityId=1) only - exactly the activity
 *   `invention.py`'s own call site (`production/actions.do_estimate_
 *   invention`) passes, so this is a match, not a narrowing.
 */

// ------------------------------------------------------------- decryptors

/** One CCP decryptor's stats - the Kotlin counterpart of
 * `production/constants.Decryptor`. `typeId` 0 means "no decryptor" (not a
 * real EVE item, priced as 0 ISK and contributing no probability/run/ME/TE
 * change beyond EVE's own no-decryptor invention baseline). `meBonus`/
 * `teBonus` are the resulting BPC's *absolute* ME/TE stat, not a delta -
 * material multiplier = 1 - meBonus/100, matching `Decryptor`'s own
 * docstring. */
data class Decryptor(
    val typeId: Int,
    val probabilityMultiplier: Double,
    val runBonus: Int,
    val meBonus: Int,
    val teBonus: Int,
)

/** Official CCP decryptor stats, sourced from the SDE - a hardcoded
 * constant here exactly as `production/constants.DECRYPTORS` is on desktop
 * (a fixed, tiny 9-row table). `linkedMapOf` preserves insertion order,
 * matching the Python dict's own iteration order that
 * `compare_decryptors`/the desktop GUI's combo box both rely on. */
val DECRYPTORS: Map<String, Decryptor> = linkedMapOf(
    "None" to Decryptor(typeId = 0, probabilityMultiplier = 1.0, runBonus = 0, meBonus = 2, teBonus = 4),
    "Accelerant" to Decryptor(34201, 1.2, 1, 4, 14),
    "Attainment" to Decryptor(34202, 1.8, 4, 1, 8),
    "Augmentation" to Decryptor(34203, 0.6, 9, 0, 6),
    "Parity" to Decryptor(34204, 1.5, 3, 3, 2),
    "Process" to Decryptor(34205, 1.1, 0, 5, 10),
    "Symmetry" to Decryptor(34206, 1.0, 2, 3, 12),
    "Optimized Attainment" to Decryptor(34207, 1.9, 2, 3, 2),
    "Optimized Augmentation" to Decryptor(34208, 0.9, 7, 4, 4),
)

/** SDE category id for Ancient Relics (Sleeper Tech III invention sources) -
 * matches `production/constants.ANCIENT_RELIC_CATEGORY_ID` exactly. A bare
 * literal here rather than an import, same "this file doesn't reach into
 * another submodule's own constants for one id" precedent
 * `SdeDao.oreIceCandidateTypes`'s own docstring already sets for category
 * id 25 (Ore). */
const val ANCIENT_RELIC_CATEGORY_ID = 34

// --------------------------------------------------------------- failures

/** `decryptorName` isn't one of [DECRYPTORS]' keys - a caller/typo error,
 * not a missing-data one (mirrors `invention.estimate`'s own
 * `ActionError("Unknown decryptor ...")`). Never thrown by
 * [compareDecryptors]/[compareRecipesAndDecryptors], which only ever pass a
 * name straight out of [DECRYPTORS] itself. */
class UnknownDecryptorException(message: String) : RuntimeException(message)

/** `t1BlueprintTypeId` invents nothing at all, or the SDE has no success
 * probability cached for it - the Kotlin counterpart of `invention.
 * NoInventionRecipe`. Its own exception type (distinct from
 * [UnknownDecryptorException]) so the multi-candidate helpers below can
 * skip an unusable candidate without also swallowing a genuine
 * caller/config error. */
class InventionRecipeNotFoundException(message: String) : RuntimeException(message)

// -------------------------------------------------------------- SDE access

/** One invention (activity 8) job definition, as read from the SDE cache -
 * the Kotlin counterpart of `storage.get_invention_recipe`'s returned dict.
 * `baseProbability` is null when the SDE has the recipe (a real product row
 * exists) but no probability row for it - callers must treat that as "can't
 * estimate", never as 0, exactly like the desktop dict's own contract. */
data class InventionRecipe(
    val productTypeId: Int,
    val baseRuns: Int,
    val baseProbability: Double?,
    val datacores: List<Pair<Int, Double>>,
)

/** What [estimate] and the comparison helpers below need from the SDE cache
 * - kept as an interface (rather than a direct `SdeRepository` dependency)
 * so this file's math is testable in a plain Kotlin/JVM test without Room
 * or an Android runtime, the same reason [ProductionBomSource] exists.
 * `SdeRepository` implements this directly. */
interface InventionSdeSource {
    /** The full Invention job definition for `t1BlueprintTypeId` (a T1
     * blueprint or a Sleeper relic) - null if it invents nothing at all. */
    suspend fun inventionRecipe(t1BlueprintTypeId: Int): InventionRecipe?

    /** Every valid invention source for `productBlueprintTypeId` (the T2/T3
     * blueprint being invented), best-probability-first - empty if it isn't
     * an invented product at all. More than one candidate means Tech III's
     * up to three relic grades (Intact/Malfunctioning/Wrecked); Tech II
     * always has exactly one. */
    suspend fun inventionRecipeCandidates(productBlueprintTypeId: Int): List<Int>

    /** Given a T2/T3 blueprint's exact name, its own `productTypeId` - null
     * if `productName` isn't a real invented blueprint's name. */
    suspend fun resolveInventionProductTypeId(productName: String): Int?

    /** The SDE display name for a type id. */
    suspend fun typeName(typeId: Int): String?

    /** A type's SDE category id - used to tell a genuine T1 blueprint
     * (category 9) apart from a Tech III Sleeper relic
     * ([ANCIENT_RELIC_CATEGORY_ID]). */
    suspend fun categoryIdOf(typeId: Int): Int?

    /** A type's SDE volume (m3), for landed-price haul-cost math - the same
     * lookup [ProductionBomSource.volumeOf] already provides. */
    suspend fun volumeOf(typeId: Int): Double?
}

// -------------------------------------------------------------------- math

/** EVE's real invention skill bonus: each of the two datacore/science
 * skills contributes 1/30 (~3.33%) per level and the encryption skill 1/40
 * (2.5%) per level - all three additive, never multiplied. Matches
 * `invention.skill_multiplier` exactly. */
fun skillMultiplier(cfg: ProductionConfig): Double =
    1.0 + (cfg.datacoreSkill1Level + cfg.datacoreSkill2Level) / 30.0 + cfg.encryptionSkillLevel / 40.0

/** Cost of a blueprint's own build materials that actually scale with ME.
 * Materials with a base quantity of 1/run are excluded: EVE never reduces a
 * material below 1 unit per run, so no ME bonus can ever save anything on
 * them. Used to weigh a decryptor's ME bonus against its price - see this
 * file's module doc. Mirrors `invention.reducible_material_cost` exactly,
 * reusing [ProductionBomSource.blueprintMaterials] (Manufacturing/
 * activityId=1 only) for the material list - the same activity desktop's
 * own call site (`actions.do_estimate_invention`) passes. */
suspend fun reducibleMaterialCost(
    manufacturingBlueprintTypeId: Int,
    home: Map<Int, OrderStats>,
    jita: Map<Int, OrderStats>,
    bom: ProductionBomSource,
    cfg: ProductionConfig,
): Double {
    var total = 0.0
    for ((materialId, baseQty) in bom.blueprintMaterials(manufacturingBlueprintTypeId)) {
        if (baseQty <= 1.0) continue
        val volume = bom.volumeOf(materialId)
        val price = buyPrice(materialId, home, jita, volume, cfg)
        total += baseQty * (price ?: 0.0)
    }
    return total
}

/** One (invention source, decryptor) combination, priced end to end -
 * mirrors `InventionResult`. `relicCost` is `t1BlueprintTypeId`'s own price,
 * counted ONLY when it's a Tech III relic ([ANCIENT_RELIC_CATEGORY_ID]) -
 * always 0.0 for a genuine T1 blueprint, matching that case's deliberate
 * "ignore BPC copy cost" simplification (see this file's module doc). */
data class InventionResult(
    val t1BlueprintTypeId: Int,
    val t1BlueprintName: String,
    val productTypeId: Int,
    val productName: String,
    val decryptor: String,
    val probability: Double,
    val outputRuns: Int,
    val datacoreCost: Double,
    val decryptorCost: Double,
    val relicCost: Double,
    val totalAttemptCost: Double,
    // Null whenever any input price was unknown - see estimate's
    // allPricesKnown.
    val expectedCostPerSuccess: Double?,
    val expectedCostPerRun: Double?,
    val me: Int,
    val te: Int,
    val materialSavingsPerRun: Double,
    val netCostPerRun: Double?,
)

/** One invention source x one decryptor, priced end to end - the Kotlin
 * port of `invention.estimate`.
 *
 * `reducibleMaterialCostPerRun` is the invented item's own ME-scaling
 * build-material cost (see [reducibleMaterialCost]) - pass 0.0 to ignore
 * build-side ME savings entirely, e.g. when pricing invention itself rather
 * than deciding which decryptor to build with.
 *
 * Throws [UnknownDecryptorException] if `decryptorName` isn't in
 * [DECRYPTORS], or [InventionRecipeNotFoundException] if `sde` has no
 * recipe, or a recipe with no cached success probability, for
 * `t1BlueprintTypeId`. */
suspend fun estimate(
    t1BlueprintTypeId: Int,
    decryptorName: String,
    home: Map<Int, OrderStats>,
    jita: Map<Int, OrderStats>,
    cfg: ProductionConfig,
    sde: InventionSdeSource,
    reducibleMaterialCostPerRun: Double = 0.0,
): InventionResult {
    val decryptor = DECRYPTORS[decryptorName]
        ?: throw UnknownDecryptorException(
            "Unknown decryptor '$decryptorName'. Options: ${DECRYPTORS.keys.joinToString(", ")}"
        )
    val recipe = sde.inventionRecipe(t1BlueprintTypeId)
        ?: throw InventionRecipeNotFoundException("No invention recipe found for type ID $t1BlueprintTypeId.")
    val baseProbability = recipe.baseProbability
        ?: throw InventionRecipeNotFoundException(
            "No base success probability found for type ID $t1BlueprintTypeId."
        )

    val probability = minOf(1.0, baseProbability * skillMultiplier(cfg) * decryptor.probabilityMultiplier)
    val outputRuns = maxOf(0, recipe.baseRuns + decryptor.runBonus)

    // A datacore/decryptor with genuinely no sell order anywhere must not be
    // silently priced at 0 ISK - that understates the attempt and can make
    // an actually-expensive decryptor look cheaper than it is (the same real
    // bug `invention.estimate`'s own `all_prices_known` comment documents
    // fixing). The raw cost fields below still sum whatever *is* priced;
    // this flag gates only the decision-driving derived fields, so a
    // missing price surfaces as an honest "can't estimate" (null).
    var allPricesKnown = true

    suspend fun priced(typeId: Int): Double {
        val volume = sde.volumeOf(typeId)
        val price = buyPrice(typeId, home, jita, volume, cfg)
        if (price == null) allPricesKnown = false
        return price ?: 0.0
    }

    var datacoreCost = 0.0
    for ((materialId, qty) in recipe.datacores) datacoreCost += qty * priced(materialId)
    val decryptorCost = if (decryptor.typeId != 0) priced(decryptor.typeId) else 0.0

    // A Tech III relic is the "blueprint" being consumed here, exactly as a
    // T1 BPC is for Tech II - but unlike a T1 BPC (a near-free reprint of an
    // already-owned BPO, the documented reason its cost is deliberately not
    // modeled at all), a relic must be bought or looted fresh for every
    // attempt, so it IS priced, under the same allPricesKnown gate as every
    // other input. A genuine T1 blueprint (category 9) never reaches this
    // branch.
    val relicCost = if (sde.categoryIdOf(t1BlueprintTypeId) == ANCIENT_RELIC_CATEGORY_ID) {
        priced(t1BlueprintTypeId)
    } else {
        0.0
    }

    val totalAttemptCost = datacoreCost + decryptorCost + relicCost
    val expectedCostPerSuccess =
        if (probability > 0.0 && allPricesKnown) totalAttemptCost / probability else null
    val expectedCostPerRun =
        if (expectedCostPerSuccess != null && outputRuns > 0) expectedCostPerSuccess / outputRuns else null
    val materialSavingsPerRun = (decryptor.meBonus / 100.0) * reducibleMaterialCostPerRun
    val netCostPerRun = expectedCostPerRun?.let { it - materialSavingsPerRun }

    val t1Name = sde.typeName(t1BlueprintTypeId) ?: t1BlueprintTypeId.toString()
    val productName = sde.typeName(recipe.productTypeId) ?: recipe.productTypeId.toString()

    return InventionResult(
        t1BlueprintTypeId = t1BlueprintTypeId,
        t1BlueprintName = t1Name,
        productTypeId = recipe.productTypeId,
        productName = productName,
        decryptor = decryptorName,
        probability = probability,
        outputRuns = outputRuns,
        datacoreCost = datacoreCost,
        decryptorCost = decryptorCost,
        relicCost = relicCost,
        totalAttemptCost = totalAttemptCost,
        expectedCostPerSuccess = expectedCostPerSuccess,
        expectedCostPerRun = expectedCostPerRun,
        me = decryptor.meBonus,
        te = decryptor.teBonus,
        materialSavingsPerRun = materialSavingsPerRun,
        netCostPerRun = netCostPerRun,
    )
}

/** Sort key: an unpriceable combination ranks last rather than being
 * dropped, so the caller still sees it exists - mirrors `_net_cost_key`. */
private fun netCostKey(result: InventionResult): Double = result.netCostPerRun ?: Double.POSITIVE_INFINITY

/** One [InventionResult] per decryptor option (including "None"), cheapest
 * net cost per BPC run first. Mirrors `invention.compare_decryptors`. */
suspend fun compareDecryptors(
    t1BlueprintTypeId: Int,
    home: Map<Int, OrderStats>,
    jita: Map<Int, OrderStats>,
    cfg: ProductionConfig,
    sde: InventionSdeSource,
    reducibleMaterialCostPerRun: Double = 0.0,
): List<InventionResult> =
    DECRYPTORS.keys
        .map { name -> estimate(t1BlueprintTypeId, name, home, jita, cfg, sde, reducibleMaterialCostPerRun) }
        .sortedBy(::netCostKey)

/** The single cheapest decryptor choice for ONE fixed invention source, or
 * null if no recipe/probability data exists for it. Mirrors
 * `invention.best_decryptor_for_item`. */
suspend fun bestDecryptorForItem(
    t1BlueprintTypeId: Int,
    home: Map<Int, OrderStats>,
    jita: Map<Int, OrderStats>,
    cfg: ProductionConfig,
    sde: InventionSdeSource,
    reducibleMaterialCostPerRun: Double = 0.0,
): InventionResult? = try {
    compareDecryptors(t1BlueprintTypeId, home, jita, cfg, sde, reducibleMaterialCostPerRun).firstOrNull()
} catch (e: InventionRecipeNotFoundException) {
    null
}

/** Every (invention source, decryptor) combination for
 * `productBlueprintTypeId`, cheapest net cost per run first - generalises
 * [compareDecryptors], which only ever varies the decryptor for one fixed
 * source. Tech II always has exactly one source; Tech III's up to three
 * relic grades (Intact/Malfunctioning/Wrecked) are compared side by side
 * here rather than silently assuming the best-odds one. Mirrors
 * `invention.compare_recipes_and_decryptors`. */
suspend fun compareRecipesAndDecryptors(
    productBlueprintTypeId: Int,
    home: Map<Int, OrderStats>,
    jita: Map<Int, OrderStats>,
    cfg: ProductionConfig,
    sde: InventionSdeSource,
    reducibleMaterialCostPerRun: Double = 0.0,
): List<InventionResult> {
    val results = mutableListOf<InventionResult>()
    for (candidate in sde.inventionRecipeCandidates(productBlueprintTypeId)) {
        try {
            results += compareDecryptors(candidate, home, jita, cfg, sde, reducibleMaterialCostPerRun)
        } catch (e: InventionRecipeNotFoundException) {
            continue
        }
    }
    return results.sortedBy(::netCostKey)
}

/** The single globally-cheapest (source, decryptor) combination, or null if
 * no invention recipe/probability data exists for `productBlueprintTypeId`
 * at all. Mirrors `invention.best_recipe_and_decryptor`. */
suspend fun bestRecipeAndDecryptor(
    productBlueprintTypeId: Int,
    home: Map<Int, OrderStats>,
    jita: Map<Int, OrderStats>,
    cfg: ProductionConfig,
    sde: InventionSdeSource,
    reducibleMaterialCostPerRun: Double = 0.0,
): InventionResult? =
    compareRecipesAndDecryptors(productBlueprintTypeId, home, jita, cfg, sde, reducibleMaterialCostPerRun)
        .firstOrNull()

/** Like [bestRecipeAndDecryptor], but with the decryptor fixed - still
 * explores every source candidate (Tech III's three relic grades), picking
 * whichever grade is cheapest with that one decryptor. Mirrors
 * `invention.best_recipe_for_decryptor`. Propagates
 * [UnknownDecryptorException] for an unknown `decryptorName`, same as
 * [estimate] - only a missing recipe is treated as "skip this candidate"
 * here. */
suspend fun bestRecipeForDecryptor(
    productBlueprintTypeId: Int,
    decryptorName: String,
    home: Map<Int, OrderStats>,
    jita: Map<Int, OrderStats>,
    cfg: ProductionConfig,
    sde: InventionSdeSource,
    reducibleMaterialCostPerRun: Double = 0.0,
): InventionResult? {
    var best: InventionResult? = null
    for (candidate in sde.inventionRecipeCandidates(productBlueprintTypeId)) {
        val result = try {
            estimate(candidate, decryptorName, home, jita, cfg, sde, reducibleMaterialCostPerRun)
        } catch (e: InventionRecipeNotFoundException) {
            continue
        }
        if (best == null || netCostKey(result) < netCostKey(best)) best = result
    }
    return best
}
