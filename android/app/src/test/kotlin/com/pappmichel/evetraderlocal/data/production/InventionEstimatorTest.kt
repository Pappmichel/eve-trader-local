package com.pappmichel.evetraderlocal.data.production

import com.pappmichel.evetraderlocal.data.esi.OrderStats
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

/** Ported case-for-case from the desktop build's
 * `tests/test_production_invention.py` where an equivalent exists on this
 * platform - the fixture (type ids, SDE rows, prices) is the exact same
 * Tech II/Tech III chain that file seeds via `storage.replace_sde_data`,
 * reproduced here as two small fakes ([FakeInventionSdeSource],
 * [FakeProductionBomSource]) instead of a real SQLite cache, the same
 * "no I/O, no Room" precedent `ProductionBuildCostTest`'s own fake
 * `ProductionBomSource` already set.
 *
 * Not ported: `test_skill_levels_are_range_checked_to_eves_own_zero_to_five`
 * - it exercises desktop's `validate_config_overrides`, a config-load-time
 * range check this Android port has no equivalent of (`ProductionConfig`
 * has no settings-screen field or loader validator at all yet - see that
 * class's own docstring on `encryptionSkillLevel`). There is no
 * [skillMultiplier] behavior to test without it. */
class InventionEstimatorTest {
    // Tech II chain: a T1 blueprint invents the T2 blueprint of a T2 module.
    private val T1_BLUEPRINT = 1002
    private val T2_BLUEPRINT = 1001
    private val DATACORE_A = 20410
    private val DATACORE_B = 20424

    // Tech III chain: three relic grades all invent the same subsystem blueprint.
    private val T3_BLUEPRINT = 1008
    private val INTACT_RELIC = 1010
    private val MALFUNCTIONING_RELIC = 1011
    private val WRECKED_RELIC = 1012

    // Manufacturing materials of the T2 module (for the ME-savings half).
    private val TRITANIUM = 34
    private val MORPHITE = 11399

    private val BASE_PROBABILITY = 0.34
    private val BASE_RUNS = 10
    // 4/4/4 skills: 1 + (4+4)/30 + 4/40.
    private val SKILL_MULTIPLIER = 1.0 + 8.0 / 30.0 + 4.0 / 40.0

    private val ACCELERANT = DECRYPTORS.getValue("Accelerant")

    private fun cfg(
        encryptionSkillLevel: Int = 4,
        datacoreSkill1Level: Int = 4,
        datacoreSkill2Level: Int = 4,
    ) = ProductionConfig(
        jitaBuyBrokerFee = 0.0,
        haulCostPerM3 = 0.0,
        encryptionSkillLevel = encryptionSkillLevel,
        datacoreSkill1Level = datacoreSkill1Level,
        datacoreSkill2Level = datacoreSkill2Level,
    )

    private fun stats(sell: Double): OrderStats =
        OrderStats(sellPercentile = sell, sellVolume = 1.0, buyPercentile = null, buyVolume = 0.0)

    private val decryptorPrices = mapOf(
        "Accelerant" to 500_000.0, "Attainment" to 400_000.0, "Augmentation" to 1_000_000.0,
        "Parity" to 600_000.0, "Process" to 300_000.0, "Symmetry" to 450_000.0,
        "Optimized Attainment" to 900_000.0, "Optimized Augmentation" to 1_200_000.0,
    )

    private val jita: Map<Int, OrderStats> = buildMap {
        put(DATACORE_A, stats(1_000.0))
        put(DATACORE_B, stats(2_000.0))
        for ((name, price) in decryptorPrices) put(DECRYPTORS.getValue(name).typeId, stats(price))
        put(INTACT_RELIC, stats(30_000_000.0))
        put(MALFUNCTIONING_RELIC, stats(8_000_000.0))
        put(WRECKED_RELIC, stats(1_000_000.0))
        put(TRITANIUM, stats(5.0))
        put(MORPHITE, stats(10_000.0))
    }
    private val noHome: Map<Int, OrderStats> = emptyMap()

    /** A tiny in-memory invention recipe book, standing in for the SDE
     * cache - the [InventionSdeSource] counterpart of
     * `ProductionBuildCostTest`'s own fake `ProductionBomSource`. Mutable
     * maps so individual tests can delete/override one row (mirroring the
     * desktop fixture's own `UPDATE`/`DELETE` on the real SQLite cache). */
    private class FakeInventionSdeSource(
        val recipes: MutableMap<Int, InventionRecipe>,
        val candidates: MutableMap<Int, List<Int>>,
        val names: Map<Int, String>,
        val categoryIds: Map<Int, Int>,
    ) : InventionSdeSource {
        override suspend fun inventionRecipe(t1BlueprintTypeId: Int): InventionRecipe? = recipes[t1BlueprintTypeId]
        override suspend fun inventionRecipeCandidates(productBlueprintTypeId: Int): List<Int> =
            candidates[productBlueprintTypeId] ?: emptyList()
        override suspend fun resolveInventionProductTypeId(productName: String): Int? = null
        override suspend fun typeName(typeId: Int): String? = names[typeId]
        override suspend fun categoryIdOf(typeId: Int): Int? = categoryIds[typeId]
        override suspend fun volumeOf(typeId: Int): Double? = 0.0
    }

    private class FakeBomSource(private val materials: Map<Int, List<Pair<Int, Double>>>) : ProductionBomSource {
        override suspend fun blueprintForProduct(productTypeId: Int): BlueprintForProduct? = null
        override suspend fun blueprintMaterials(blueprintTypeId: Int): List<Pair<Int, Double>> =
            materials[blueprintTypeId] ?: emptyList()
        override suspend fun volumeOf(typeId: Int): Double? = 0.0
    }

    private fun sde(
        probabilityOverride: Double? = BASE_PROBABILITY,
        includeProbabilityRow: Boolean = true,
    ): FakeInventionSdeSource {
        val recipes = mutableMapOf(
            T1_BLUEPRINT to InventionRecipe(
                productTypeId = T2_BLUEPRINT, baseRuns = BASE_RUNS,
                baseProbability = if (includeProbabilityRow) probabilityOverride else null,
                datacores = listOf(DATACORE_A to 2.0, DATACORE_B to 2.0),
            ),
            INTACT_RELIC to InventionRecipe(
                productTypeId = T3_BLUEPRINT, baseRuns = 20, baseProbability = 0.26,
                datacores = listOf(DATACORE_A to 3.0),
            ),
            MALFUNCTIONING_RELIC to InventionRecipe(
                productTypeId = T3_BLUEPRINT, baseRuns = 10, baseProbability = 0.21,
                datacores = listOf(DATACORE_A to 3.0),
            ),
            WRECKED_RELIC to InventionRecipe(
                productTypeId = T3_BLUEPRINT, baseRuns = 3, baseProbability = 0.14,
                datacores = listOf(DATACORE_A to 3.0),
            ),
        )
        val candidates = mutableMapOf(
            T2_BLUEPRINT to listOf(T1_BLUEPRINT),
            T3_BLUEPRINT to listOf(INTACT_RELIC, MALFUNCTIONING_RELIC, WRECKED_RELIC),
        )
        val names = mapOf(
            T1_BLUEPRINT to "Damage Control I Blueprint", T2_BLUEPRINT to "Damage Control II Blueprint",
            INTACT_RELIC to "Intact Armor Nanobot", MALFUNCTIONING_RELIC to "Malfunctioning Armor Nanobot",
            WRECKED_RELIC to "Wrecked Armor Nanobot",
        )
        val categoryIds = mapOf(
            T1_BLUEPRINT to 9, T2_BLUEPRINT to 9, T3_BLUEPRINT to 9,
            INTACT_RELIC to ANCIENT_RELIC_CATEGORY_ID, MALFUNCTIONING_RELIC to ANCIENT_RELIC_CATEGORY_ID,
            WRECKED_RELIC to ANCIENT_RELIC_CATEGORY_ID,
        )
        return FakeInventionSdeSource(recipes, candidates, names, categoryIds)
    }

    private val bom = FakeBomSource(mapOf(T2_BLUEPRINT to listOf(TRITANIUM to 1000.0, MORPHITE to 1.0)))

    // ------------------------------------------------------------- probability

    @Test fun `skill multiplier stacks the three skills additively`() {
        assertEquals(SKILL_MULTIPLIER, skillMultiplier(cfg()), 1e-9)
        assertEquals(1.0, skillMultiplier(cfg(0, 0, 0)), 1e-9)
        assertEquals(1.0 + 10.0 / 30.0 + 5.0 / 40.0, skillMultiplier(cfg(5, 5, 5)), 1e-9)
    }

    @Test fun `probability without a decryptor is base times skills`() = runBlocking {
        val result = estimate(T1_BLUEPRINT, "None", noHome, jita, cfg(), sde())

        assertEquals(BASE_PROBABILITY * SKILL_MULTIPLIER, result.probability, 1e-9)
        assertEquals(0.0, result.decryptorCost, 0.0)
        assertEquals(BASE_RUNS, result.outputRuns)
        assertEquals(2, result.me)
        assertEquals(4, result.te)
    }

    @Test fun `probability with a decryptor applies its multiplier and run bonus`() = runBlocking {
        val result = estimate(T1_BLUEPRINT, "Accelerant", noHome, jita, cfg(), sde())

        assertEquals(BASE_PROBABILITY * SKILL_MULTIPLIER * ACCELERANT.probabilityMultiplier, result.probability, 1e-9)
        assertEquals(BASE_RUNS + ACCELERANT.runBonus, result.outputRuns)
        assertEquals(500_000.0, result.decryptorCost, 0.0)
        assertEquals(ACCELERANT.meBonus, result.me)
        assertEquals(ACCELERANT.teBonus, result.te)
    }

    @Test fun `probability never exceeds one`() = runBlocking {
        val result = estimate(T1_BLUEPRINT, "Optimized Attainment", noHome, jita, cfg(), sde(probabilityOverride = 0.9))

        assertEquals(1.0, result.probability, 0.0)
    }

    // ------------------------------------------------------------ expected cost

    @Test fun `expected cost per run divides by probability then by output runs`() = runBlocking {
        val result = estimate(T1_BLUEPRINT, "None", noHome, jita, cfg(), sde())

        assertEquals(6_000.0, result.datacoreCost, 1e-6) // 2x1000 + 2x2000
        assertEquals(6_000.0, result.totalAttemptCost, 1e-6)
        val expectedPerSuccess = 6_000.0 / (BASE_PROBABILITY * SKILL_MULTIPLIER)
        assertEquals(expectedPerSuccess, result.expectedCostPerSuccess!!, 1e-6)
        assertEquals(expectedPerSuccess / BASE_RUNS, result.expectedCostPerRun!!, 1e-6)
        assertEquals(0.0, result.materialSavingsPerRun, 0.0)
        assertEquals(result.expectedCostPerRun, result.netCostPerRun)
    }

    @Test fun `decryptor cost is part of the attempt cost`() = runBlocking {
        val result = estimate(T1_BLUEPRINT, "Accelerant", noHome, jita, cfg(), sde())

        assertEquals(6_000.0 + 500_000.0, result.totalAttemptCost, 1e-6)
        assertEquals(506_000.0 / result.probability, result.expectedCostPerSuccess!!, 1e-6)
        assertEquals(
            506_000.0 / result.probability / (BASE_RUNS + ACCELERANT.runBonus),
            result.expectedCostPerRun!!, 1e-6,
        )
    }

    @Test fun `me bonus is netted off as material savings`() = runBlocking {
        val result = estimate(T1_BLUEPRINT, "Accelerant", noHome, jita, cfg(), sde(), reducibleMaterialCostPerRun = 100_000.0)

        assertEquals(ACCELERANT.meBonus / 100.0 * 100_000.0, result.materialSavingsPerRun, 1e-6)
        assertEquals(result.expectedCostPerRun!! - result.materialSavingsPerRun, result.netCostPerRun!!, 1e-9)
    }

    @Test fun `reducible material cost skips quantity one materials`() = runBlocking {
        val cost = reducibleMaterialCost(T2_BLUEPRINT, noHome, jita, bom, cfg())

        assertEquals(1000 * 5.0, cost, 1e-6)
    }

    @Test fun `an unpriced input makes the derived figures none not cheap`() = runBlocking {
        val partial = jita.filterKeys { it != DATACORE_B }

        val result = estimate(T1_BLUEPRINT, "None", noHome, partial, cfg(), sde())

        assertEquals(2_000.0, result.datacoreCost, 1e-6) // only DATACORE_A priced
        assertNull(result.expectedCostPerSuccess)
        assertNull(result.expectedCostPerRun)
        assertNull(result.netCostPerRun)
    }

    // ------------------------------------------------------------------ Tech III

    @Test fun `a relic is priced into the attempt but a t1 blueprint is not`() = runBlocking {
        val relic = estimate(INTACT_RELIC, "None", noHome, jita, cfg(), sde())
        val techTwo = estimate(T1_BLUEPRINT, "None", noHome, jita, cfg(), sde())

        assertEquals(30_000_000.0, relic.relicCost, 0.0)
        assertEquals(3 * 1_000.0 + 30_000_000.0, relic.totalAttemptCost, 1e-6)
        assertEquals(0.0, techTwo.relicCost, 0.0)
        assertEquals(techTwo.datacoreCost, techTwo.totalAttemptCost, 1e-9)
    }

    @Test fun `every relic grade is compared not just the best odds one`() = runBlocking {
        val results = compareRecipesAndDecryptors(T3_BLUEPRINT, noHome, jita, cfg(), sde())

        assertEquals(
            setOf(INTACT_RELIC, MALFUNCTIONING_RELIC, WRECKED_RELIC),
            results.map { it.t1BlueprintTypeId }.toSet(),
        )
        assertEquals(3 * DECRYPTORS.size, results.size)
    }

    @Test fun `best recipe and decryptor can prefer a cheaper lower odds grade`() = runBlocking {
        val best = bestRecipeAndDecryptor(T3_BLUEPRINT, noHome, jita, cfg(), sde())

        assertTrue(best != null)
        assertEquals(WRECKED_RELIC, best!!.t1BlueprintTypeId)
        assertEquals(T3_BLUEPRINT, best.productTypeId)
        assertEquals("Wrecked Armor Nanobot", best.t1BlueprintName)
        val perRun = compareRecipesAndDecryptors(T3_BLUEPRINT, noHome, jita, cfg(), sde()).map { it.netCostPerRun }
        assertEquals(perRun.filterNotNull().min(), best.netCostPerRun!!, 1e-9)
    }

    @Test fun `best recipe for decryptor keeps the decryptor but optimises the grade`() = runBlocking {
        val best = bestRecipeForDecryptor(T3_BLUEPRINT, "Attainment", noHome, jita, cfg(), sde())

        assertTrue(best != null)
        assertEquals("Attainment", best!!.decryptor)
        assertEquals(WRECKED_RELIC, best.t1BlueprintTypeId)
    }

    // --------------------------------------------------------------- selection

    @Test fun `compare decryptors covers every option cheapest first`() = runBlocking {
        val results = compareDecryptors(T1_BLUEPRINT, noHome, jita, cfg(), sde())

        assertEquals(DECRYPTORS.size, results.size)
        assertEquals(DECRYPTORS.keys, results.map { it.decryptor }.toSet())
        val costs = results.map { it.netCostPerRun ?: Double.POSITIVE_INFINITY }
        assertEquals(costs.sorted(), costs)
    }

    @Test fun `unpriceable combinations sort last rather than disappearing`() = runBlocking {
        val partial = jita.filterKeys { it != ACCELERANT.typeId }

        val results = compareDecryptors(T1_BLUEPRINT, noHome, partial, cfg(), sde())

        assertEquals("Accelerant", results.last().decryptor)
        assertNull(results.last().netCostPerRun)
        assertTrue(results.first().netCostPerRun != null)
    }

    @Test fun `best decryptor for item is the cheapest net cost option`() = runBlocking {
        val best = bestDecryptorForItem(T1_BLUEPRINT, noHome, jita, cfg(), sde())
        val allOptions = compareDecryptors(T1_BLUEPRINT, noHome, jita, cfg(), sde())

        assertTrue(best != null)
        assertEquals(allOptions.first().decryptor, best!!.decryptor)
        assertEquals(allOptions.first().netCostPerRun, best.netCostPerRun)
    }

    @Test fun `a big me bonus can beat a cheap decryptor`() = runBlocking {
        val cheapOnly = bestDecryptorForItem(T1_BLUEPRINT, noHome, jita, cfg(), sde())
        val withSavings = bestDecryptorForItem(
            T1_BLUEPRINT, noHome, jita, cfg(), sde(), reducibleMaterialCostPerRun = 500_000_000.0
        )

        assertTrue(withSavings != null && cheapOnly != null)
        assertTrue(withSavings!!.me >= cheapOnly!!.me)
        assertTrue(withSavings.netCostPerRun!! < 0) // savings exceed the invention cost
    }

    // ------------------------------------------------------------------ failures

    @Test fun `a type with no invention recipe raises`() = runBlocking {
        try {
            estimate(T2_BLUEPRINT, "None", noHome, jita, cfg(), sde())
            fail("expected InventionRecipeNotFoundException")
        } catch (e: InventionRecipeNotFoundException) {
            // expected
        }
    }

    @Test fun `a recipe without a probability row raises`() = runBlocking {
        try {
            estimate(T1_BLUEPRINT, "None", noHome, jita, cfg(), sde(includeProbabilityRow = false))
            fail("expected InventionRecipeNotFoundException")
        } catch (e: InventionRecipeNotFoundException) {
            // expected - "no probability data" must be "can't estimate", never a silent 0.
        }
    }

    @Test fun `the best helpers return null instead of raising`() = runBlocking {
        assertNull(bestDecryptorForItem(T2_BLUEPRINT, noHome, jita, cfg(), sde()))
        assertNull(bestRecipeAndDecryptor(999999, noHome, jita, cfg(), sde()))
        assertNull(bestRecipeForDecryptor(999999, "None", noHome, jita, cfg(), sde()))
    }

    @Test fun `an unknown decryptor name is a user facing error`() = runBlocking {
        try {
            estimate(T1_BLUEPRINT, "Acclerant", noHome, jita, cfg(), sde())
            fail("expected UnknownDecryptorException")
        } catch (e: UnknownDecryptorException) {
            assertTrue(e.message!!.contains("Unknown decryptor"))
        }
    }
}
