package com.pappmichel.evetraderlocal.data.refining

import com.pappmichel.evetraderlocal.data.esi.OrderStats
import com.pappmichel.evetraderlocal.data.trading.TradingConfig
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Tests for `buildMineralShoppingList` - NOT a case-for-case port of the
 * desktop build's `tests/test_refining_optimizer.py`. That file exercises a
 * real mixed-integer LP (`optimize_shopping_list`, buy-vs-refine across
 * every ore/ice type at once - see `MineralShoppingList.kt`'s module
 * docstring for the full finding, and `MineralShoppingListOptimizerTest`
 * for that case-for-case port, now that `MineralShoppingListOptimizer.kt`
 * implements the real joint solver); this file's own `buildMineralShoppingList`
 * only ever covers the "buy each mineral outright at its own cheapest
 * current Jita listing" half - the `allDirectCost`/`savingsVsAllDirect`
 * baseline the real optimizer compares its own plan against - so the
 * desktop suite's ore-vs-direct/mixed-plan/LP-vs-greedy cases
 * (`test_prefers_ore_when_refining_is_cheaper`,
 * `test_greedy_per_mineral_ranking_would_lose_to_the_lp`, etc.) exercise
 * behavior that lives in that other file now, not this one. These cases
 * instead pin down THIS file's own documented behavior: whole-unit
 * rounding, the broker-fee markup, multiple independent lines, and the
 * "can't price it" (no Jita sell orders) case. */
class MineralShoppingListTest {
    private val tradingCfg = TradingConfig(jitaBuyBrokerFee = 0.0147)

    private fun stats(sellPercentile: Double?) =
        OrderStats(sellPercentile = sellPercentile, sellVolume = 0.0, buyPercentile = null, buyVolume = 0.0)

    @Test
    fun `single priceable requirement is bought outright with the broker fee applied`() {
        val plan = buildMineralShoppingList(
            listOf(MineralRequirement(typeId = 34, name = "Tritanium", requiredQty = 1000.0)),
            mapOf(34 to stats(5.0)),
            tradingCfg,
        )
        val line = plan.lines.single()
        val unitCost: Double = line.unitCost!!
        val totalCost: Double = line.totalCost!!
        assertEquals(1000, line.quantity)
        assertEquals(5.0 * 1.0147, unitCost, 1e-9)
        assertEquals(5.0 * 1.0147 * 1000, totalCost, 1e-6)
        assertEquals(totalCost, plan.totalCost, 1e-6)
        assertTrue(plan.unpriceable.isEmpty())
    }

    @Test
    fun `fractional requirement is rounded up to a whole unit`() {
        val plan = buildMineralShoppingList(
            listOf(MineralRequirement(typeId = 34, name = "Tritanium", requiredQty = 100.5)),
            mapOf(34 to stats(1.0)),
            tradingCfg,
        )
        assertEquals(101, plan.lines.single().quantity)
    }

    @Test
    fun `requirement with no jita sell orders is unpriceable but still listed`() {
        val plan = buildMineralShoppingList(
            listOf(MineralRequirement(typeId = 34, name = "Tritanium", requiredQty = 1000.0)),
            mapOf(34 to stats(null)),
            tradingCfg,
        )
        val line = plan.lines.single()
        assertNull(line.unitCost)
        assertNull(line.totalCost)
        assertEquals(0.0, plan.totalCost, 1e-9)
        assertEquals(listOf(line), plan.unpriceable)
    }

    @Test
    fun `requirement missing from stats entirely is the same as no sell orders`() {
        val plan = buildMineralShoppingList(
            listOf(MineralRequirement(typeId = 34, name = "Tritanium", requiredQty = 1000.0)),
            emptyMap(),
            tradingCfg,
        )
        assertNull(plan.lines.single().totalCost)
    }

    @Test
    fun `multiple minerals are priced independently and summed`() {
        val plan = buildMineralShoppingList(
            listOf(
                MineralRequirement(typeId = 34, name = "Tritanium", requiredQty = 1000.0),
                MineralRequirement(typeId = 35, name = "Pyerite", requiredQty = 500.0),
            ),
            mapOf(34 to stats(5.0), 35 to stats(2.0)),
            tradingCfg,
        )
        assertEquals(2, plan.lines.size)
        val expected = (5.0 * 1.0147 * 1000) + (2.0 * 1.0147 * 500)
        assertEquals(expected, plan.totalCost, 1e-6)
    }

    @Test
    fun `non-positive requirements are dropped`() {
        val plan = buildMineralShoppingList(
            listOf(
                MineralRequirement(typeId = 34, name = "Tritanium", requiredQty = 0.0),
                MineralRequirement(typeId = 35, name = "Pyerite", requiredQty = -5.0),
                MineralRequirement(typeId = 36, name = "Mexallon", requiredQty = 10.0),
            ),
            mapOf(36 to stats(3.0)),
            tradingCfg,
        )
        assertEquals(listOf(36), plan.lines.map { it.typeId })
    }

    @Test
    fun `empty requirement list produces an empty plan with zero total`() {
        val plan = buildMineralShoppingList(emptyList(), emptyMap(), tradingCfg)
        assertTrue(plan.lines.isEmpty())
        assertEquals(0.0, plan.totalCost, 1e-9)
        assertTrue(plan.unpriceable.isEmpty())
    }
}
