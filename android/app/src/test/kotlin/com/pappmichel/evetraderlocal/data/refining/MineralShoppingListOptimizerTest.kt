package com.pappmichel.evetraderlocal.data.refining

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.random.Random

private const val TRIT = 34
private const val PYE = 35
private const val MEX = 36
private val NAMES = mapOf(TRIT to "Tritanium", PYE to "Pyerite", MEX to "Mexallon")

private fun req(typeId: Int, qty: Double) = MineralRequirement(typeId = typeId, name = NAMES.getValue(typeId), requiredQty = qty)

/** `costPerPortion` is the readable number in each test's own arithmetic;
 * `OreOption` itself stores a per-unit cost, so it's divided back out here -
 * same convention as the desktop suite's own `_ore` helper. */
private fun ore(typeId: Int, costPerPortion: Double, yields: Map<Int, Int>, portionSize: Int = 100, volumeM3: Double = 0.01) =
    OreOption(
        typeId = typeId, item = "Compressed Ore $typeId", family = "Veldspar", isIce = false,
        volumeM3 = volumeM3, portionSize = portionSize, landedCostPerUnit = costPerPortion / portionSize,
        yieldPerPortion = yields,
    )

/** Tests for `optimizeShoppingList` - a case-for-case port of the desktop
 * build's `tests/test_refining_optimizer.py`, now that this file's own
 * investigation (see `MineralShoppingListOptimizer.kt`'s module docstring)
 * found ojAlgo makes a real solver practical here after all. Every
 * hand-computed expected number below is copied from the corresponding
 * desktop test's own docstring arithmetic, not re-derived - if these ever
 * diverge from `test_refining_optimizer.py`, that file is the one to trust. */
class MineralShoppingListOptimizerTest {

    // --------------------------------------------------------- basic picks
    @Test
    fun `prefers ore when refining is cheaper`() {
        // 1000 Tritanium. One ore: 500 ISK/portion -> 400 Trit = 1.25
        // ISK/unit, against 5.00 ISK/unit direct. LP optimum is 2.5
        // portions = 1250 ISK; rounded up to 3 whole portions = 1500 ISK,
        // which still beats trimming to 2 portions + 200 Trit direct
        // (1000 + 1000 = 2000).
        val plan = optimizeShoppingList(
            listOf(req(TRIT, 1000.0)),
            listOf(ore(1, 500.0, mapOf(TRIT to 400))),
            mapOf(TRIT to 5.0),
        )
        assertEquals(1, plan.orePurchases.size)
        val buy = plan.orePurchases[0]
        assertEquals(3, buy.portions)
        assertEquals(300, buy.units)
        assertTrue(plan.directPurchases.isEmpty())
        assertEquals(1500.0, plan.oreCost, 1e-6)
        assertEquals(1500.0, plan.totalCost, 1e-6)
        assertEquals(1250.0, plan.lpCost, 1e-6)
        assertEquals(5000.0, plan.allDirectCost!!, 1e-6)
        assertEquals(3500.0, plan.savingsVsAllDirect!!, 1e-6)
        assertEquals(3.0, plan.totalVolumeM3, 1e-6)

        val cov = plan.coverage.single { it.typeId == TRIT }
        assertEquals(1200, cov.fromOre)
        assertEquals(0, cov.fromDirect)
        assertEquals(1200, cov.delivered)
        assertEquals(200.0, cov.surplus, 1e-6)
    }

    @Test
    fun `prefers direct purchase when ore is dearer`() {
        // Same ore (1.25 ISK/unit refined) but Tritanium lists at 1.00
        // direct, so the whole 1000 units is bought outright for 1000 ISK
        // and no ore is touched at all.
        val plan = optimizeShoppingList(
            listOf(req(TRIT, 1000.0)),
            listOf(ore(1, 500.0, mapOf(TRIT to 400))),
            mapOf(TRIT to 1.0),
        )
        assertTrue(plan.orePurchases.isEmpty())
        assertEquals(1, plan.directPurchases.size)
        assertEquals(1000, plan.directPurchases[0].quantity)
        assertEquals(1000.0, plan.totalCost, 1e-6)
        assertEquals(1000.0, plan.lpCost, 1e-6)
        assertEquals(0.0, plan.savingsVsAllDirect!!, 1e-6)
        assertEquals(0.0, plan.totalVolumeM3, 1e-9)
    }

    // ---------------------------------------------------------- real mixes
    @Test
    fun `mixes ore and direct when the last portion is not worth it`() {
        // 1000 Tritanium; ore is 900 ISK/portion -> 900 Trit (1.00 ISK/unit)
        // against 1.20 direct. The honest whole-portion answer is 1 portion
        // + 100 direct = 900 + 120 = 1020 (beats 2 portions = 1800).
        val plan = optimizeShoppingList(
            listOf(req(TRIT, 1000.0)),
            listOf(ore(1, 900.0, mapOf(TRIT to 900))),
            mapOf(TRIT to 1.2),
        )
        assertEquals(1, plan.orePurchases.size)
        assertEquals(1, plan.orePurchases[0].portions)
        assertEquals(1, plan.directPurchases.size)
        assertEquals(100, plan.directPurchases[0].quantity)
        assertEquals(900.0, plan.oreCost, 1e-6)
        assertEquals(120.0, plan.directCost, 1e-6)
        assertEquals(1020.0, plan.totalCost, 1e-6)
        assertEquals(1000.0 / 900.0 * 900.0, plan.lpCost, 1e-6)

        val cov = plan.coverage.single { it.typeId == TRIT }
        assertEquals(900, cov.fromOre)
        assertEquals(100, cov.fromDirect)
        assertEquals(1000, cov.delivered)
        assertEquals(0.0, cov.surplus, 1e-6)
    }

    @Test
    fun `picks ore for one mineral and direct for another`() {
        // 1000 Trit + 1000 Pyerite. Ore A: 800/portion -> 1000 Trit
        // (0.80/unit) vs 1.50 direct -> ore wins. Ore B: 900/portion ->
        // 1000 Pyerite (0.90/unit) vs 0.85 direct -> direct wins. Optimum
        // 800 + 850 = 1650.
        val plan = optimizeShoppingList(
            listOf(req(TRIT, 1000.0), req(PYE, 1000.0)),
            listOf(ore(1, 800.0, mapOf(TRIT to 1000)), ore(2, 900.0, mapOf(PYE to 1000))),
            mapOf(TRIT to 1.5, PYE to 0.85),
        )
        assertEquals(listOf(1), plan.orePurchases.map { it.typeId })
        assertEquals(listOf(PYE), plan.directPurchases.map { it.typeId })
        assertEquals(1650.0, plan.totalCost, 1e-6)
        assertEquals(1500.0 + 850.0, plan.allDirectCost!!, 1e-6)
        assertEquals(700.0, plan.savingsVsAllDirect!!, 1e-6)
    }

    @Test
    fun `greedy per-mineral ranking would lose to the joint solve`() {
        // The case the solver exists for. 1000 Trit + 1000 Mexallon. Ore A
        // (2000/portion -> 1000 Trit + 1000 Mex) covers both at once for
        // 2000. Ore B (600/portion -> 1000 Trit) is the cheapest
        // *Tritanium* source per unit (0.60), and Mexallon direct is
        // 3.00/unit. A per-mineral greedy pick would take Ore B for Trit
        // (600) and then still owe 3000 for Mexallon = 3600. Buying the one
        // combined portion of Ore A is 2000.
        val plan = optimizeShoppingList(
            listOf(req(TRIT, 1000.0), req(MEX, 1000.0)),
            listOf(ore(1, 2000.0, mapOf(TRIT to 1000, MEX to 1000)), ore(2, 600.0, mapOf(TRIT to 1000))),
            mapOf(TRIT to 1.0, MEX to 3.0),
        )
        assertEquals(listOf(1), plan.orePurchases.map { it.typeId })
        assertEquals(1, plan.orePurchases[0].portions)
        assertTrue(plan.directPurchases.isEmpty())
        assertEquals(2000.0, plan.totalCost, 1e-6)
        assertEquals(2000.0, plan.lpCost, 1e-6)
    }

    @Test
    fun `shared yield is not double-counted or under-covered`() {
        // One ore yielding two wanted minerals at once: 2000/portion ->
        // 1000 Trit + 500 Mex. Requirement is exactly one portion - zero
        // surplus on both lines, not two portions "one per mineral".
        val plan = optimizeShoppingList(
            listOf(req(TRIT, 1000.0), req(MEX, 500.0)),
            listOf(ore(1, 2000.0, mapOf(TRIT to 1000, MEX to 500))),
            mapOf(TRIT to 1.0, MEX to 4.0),
        )
        assertEquals(1, plan.orePurchases.size)
        assertEquals(1, plan.orePurchases[0].portions)
        assertTrue(plan.directPurchases.isEmpty())
        assertEquals(2000.0, plan.totalCost, 1e-6)
        assertEquals(1000.0 + 2000.0, plan.allDirectCost!!, 1e-6)
        val cov = plan.coverage.associateBy { it.typeId }
        assertEquals(0.0, cov.getValue(TRIT).surplus, 1e-6)
        assertEquals(0.0, cov.getValue(MEX).surplus, 1e-6)
    }

    @Test
    fun `overlapping yield tops up the shortfall directly rather than over-buying ore`() {
        // Same overlapping ore (2000/portion -> 1000 Trit + 500 Mex), but
        // requirement is 2000 Trit + 500 Mex. Two portions = 4000 (500
        // surplus Mex, uncredited); one portion + 1000 Trit direct = 3000.
        val plan = optimizeShoppingList(
            listOf(req(TRIT, 2000.0), req(MEX, 500.0)),
            listOf(ore(1, 2000.0, mapOf(TRIT to 1000, MEX to 500))),
            mapOf(TRIT to 1.0, MEX to 10.0),
        )
        assertEquals(1, plan.orePurchases.size)
        assertEquals(1, plan.orePurchases[0].portions)
        assertEquals(listOf(TRIT), plan.directPurchases.map { it.typeId })
        assertEquals(1000, plan.directPurchases[0].quantity)
        assertEquals(3000.0, plan.totalCost, 1e-6)

        val cov = plan.coverage.associateBy { it.typeId }
        assertEquals(1000, cov.getValue(TRIT).fromOre)
        assertEquals(1000, cov.getValue(TRIT).fromDirect)
        assertEquals(500, cov.getValue(MEX).fromOre)
        assertEquals(0, cov.getValue(MEX).fromDirect)
    }

    // ------------------------------------------------------------ failures
    @Test
    fun `unsourceable mineral raises`() {
        // Mexallon has no direct price and no ore yields it - there is no
        // plan, and saying so beats returning one that quietly doesn't
        // cover the build.
        val exception = try {
            optimizeShoppingList(
                listOf(req(TRIT, 1000.0), req(MEX, 100.0)),
                listOf(ore(1, 500.0, mapOf(TRIT to 400))),
                mapOf(TRIT to 5.0, MEX to null),
            )
            null
        } catch (e: ShoppingListSolveException) {
            e
        }
        assertTrue(exception != null && exception.message!!.contains("Mexallon"))
    }

    @Test(expected = ShoppingListSolveException::class)
    fun `mineral missing from direct prices entirely is the same failure`() {
        optimizeShoppingList(listOf(req(MEX, 100.0)), listOf(ore(1, 500.0, mapOf(TRIT to 400))), mapOf(TRIT to 5.0))
    }

    @Test(expected = ShoppingListSolveException::class)
    fun `empty requirement list raises`() {
        optimizeShoppingList(emptyList(), listOf(ore(1, 500.0, mapOf(TRIT to 400))), mapOf(TRIT to 5.0))
    }

    @Test(expected = ShoppingListSolveException::class)
    fun `non-positive requirement raises`() {
        optimizeShoppingList(listOf(req(TRIT, 0.0)), listOf(ore(1, 500.0, mapOf(TRIT to 400))), mapOf(TRIT to 5.0))
    }

    @Test
    fun `unpriceable mineral is still solvable from ore alone`() {
        // No direct price is not by itself an error: the ore path can
        // carry it, and the plan must round up enough portions to cover it
        // (1000 / 400 -> 3).
        val plan = optimizeShoppingList(
            listOf(req(TRIT, 1000.0)),
            listOf(ore(1, 500.0, mapOf(TRIT to 400))),
            mapOf(TRIT to null),
        )
        assertEquals(3, plan.orePurchases[0].portions)
        assertNull(plan.allDirectCost)
        assertNull(plan.savingsVsAllDirect)
    }

    @Test
    fun `ore that yields nothing wanted is never bought`() {
        val plan = optimizeShoppingList(
            listOf(req(TRIT, 1000.0)),
            listOf(ore(1, 1.0, mapOf(PYE to 100_000)), ore(2, 500.0, mapOf(TRIT to 400))),
            mapOf(TRIT to 5.0),
        )
        assertEquals(listOf(2), plan.orePurchases.map { it.typeId })
    }

    // ------------------------------------------------- no under-covering, ever
    @Test
    fun `random plans always cover every requirement`() {
        // The guard the solver's own hard constraints give: whatever mix
        // of continuous solve, round, and repair runs, every requirement
        // must come out at least met, reported costs must add up, and the
        // whole-portion plan can never be cheaper than the relaxation it
        // came from.
        for (seed in 0 until 25) {
            val rng = Random(seed)
            val minerals = listOf(TRIT, PYE, MEX)
            val requirements = minerals.map { req(it, rng.nextInt(1, 50_000).toDouble()) }
            val ores = (0 until rng.nextInt(1, 6)).map { i ->
                val yields = minerals.associateWith { if (rng.nextBoolean()) rng.nextInt(1, 5_000) else 0 }.toMutableMap()
                if (yields.values.all { it == 0 }) yields[minerals.random(rng)] = rng.nextInt(1, 5_000)
                ore(i + 1, rng.nextDouble(100.0, 20_000.0), yields, portionSize = listOf(50, 100, 200).random(rng))
            }
            // Every mineral keeps a direct price here so the case is always
            // feasible; the unsourceable path has its own tests above.
            val prices = minerals.associateWith { rng.nextDouble(0.5, 20.0) }

            val plan = optimizeShoppingList(requirements, ores, prices)

            for (cov in plan.coverage) {
                assertTrue("${cov.name} under-covered (seed=$seed)", cov.delivered + 1e-9 >= cov.required)
                assertEquals(cov.delivered, cov.fromOre + cov.fromDirect)
                assertEquals(cov.delivered - cov.required, cov.surplus, 1e-6)
            }
            assertEquals(plan.oreCost + plan.directCost, plan.totalCost, 1e-6)
            assertEquals(plan.orePurchases.sumOf { it.totalCost }, plan.oreCost, 1e-6)
            assertEquals(plan.directPurchases.sumOf { it.totalCost }, plan.directCost, 1e-6)
            // A whole-portion plan can only ever cost more than the relaxed optimum...
            assertTrue("seed=$seed", plan.totalCost >= plan.lpCost - 1e-6)
            // ...and never more than simply buying everything outright.
            assertTrue("seed=$seed", plan.totalCost <= plan.allDirectCost!! + 1e-6)
        }
    }

    @Test
    fun `realistic scale solves quickly`() {
        // Sanity-checks solve time at the tool's realistic scale: dozens of
        // ore candidates, requirements never exceeding the 8 real EVE
        // minerals - mirrors test_refining_optimizer.py's own
        // test_realistic_scale_solves_quickly.
        val rng = Random(1)
        val minerals = listOf(34, 35, 36, 37, 38, 39, 40, 11399)
        val mineralPrice = minerals.associateWith { rng.nextDouble(1.0, 80.0) }
        val ores = (0 until 60).map { i ->
            val yields = minerals.associateWith { if (rng.nextDouble() < 0.4) rng.nextInt(50, 3000) else 0 }
                .filterValues { it > 0 }
                .ifEmpty { mapOf(minerals.random(rng) to rng.nextInt(50, 3000)) }
            val baseValue = yields.entries.sumOf { (m, qty) -> qty * mineralPrice.getValue(m) }
            val markup = rng.nextDouble(0.85, 1.15)
            ore(i + 1, baseValue * markup, yields, portionSize = 100)
        }
        val required = minerals.associateWith { rng.nextDouble(10_000.0, 2_000_000.0) }
        val requirements = minerals.map { MineralRequirement(typeId = it, name = it.toString(), requiredQty = required.getValue(it)) }
        val directPrices = minerals.associateWith { mineralPrice.getValue(it) * rng.nextDouble(0.95, 1.3) }

        val start = System.nanoTime()
        val plan = optimizeShoppingList(requirements, ores, directPrices)
        val elapsedSeconds = (System.nanoTime() - start) / 1e9

        for (cov in plan.coverage) assertTrue(cov.delivered + 1e-6 >= cov.required)
        // Generous ceiling - well above the sub-second solves this file's
        // own investigation observed at this scale; guards against a
        // future regression to multi-second territory going unnoticed.
        assertTrue("realistic-scale solve took ${elapsedSeconds}s", elapsedSeconds < 8.0)
    }
}
