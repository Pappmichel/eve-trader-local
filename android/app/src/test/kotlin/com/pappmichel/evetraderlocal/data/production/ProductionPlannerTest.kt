package com.pappmichel.evetraderlocal.data.production

import com.pappmichel.evetraderlocal.data.esi.OrderStats
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Tests for [buildMaterialTree]/[aggregateLeaves]/[priceLeafRequirements] -
 * see `ProductionPlanner.kt`'s own module docstring for why this ports
 * desktop's `engine.build_material_tree` (Item Lookup's "Material Tree"),
 * not `engine.plan_production` (the desktop feature actually named
 * "Planner"), and what's scoped out either way. Reuses the same
 * `FakeBom`/`stats` shape `ProductionBuildCostTest` already established, so
 * a reader who knows one test file's fixtures immediately knows the other's. */
class ProductionPlannerTest {
    private val TRITANIUM = 34
    private val COMPONENT = 100
    private val OTHER_COMPONENT = 101
    private val WIDGET = 200
    private val SHIP = 300
    private val BP_WIDGET = 900
    private val BP_SHIP = 901

    private fun stats(sell: Double): OrderStats =
        OrderStats(sellPercentile = sell, sellVolume = 1.0, buyPercentile = null, buyVolume = 0.0)

    private fun cfg(me: Int = 10) = ProductionConfig(materialEfficiency = me)

    private class FakeBom(
        private val products: Map<Int, BlueprintForProduct>,
        private val materials: Map<Int, List<Pair<Int, Double>>>,
        private val volumes: Map<Int, Double> = emptyMap(),
    ) : ProductionBomSource {
        override suspend fun blueprintForProduct(productTypeId: Int): BlueprintForProduct? = products[productTypeId]
        override suspend fun blueprintMaterials(blueprintTypeId: Int): List<Pair<Int, Double>> =
            materials[blueprintTypeId] ?: emptyList()
        override suspend fun volumeOf(typeId: Int): Double? = volumes[typeId]
    }

    // ------------------------------------------------------------ leaf vs. build
    @Test
    fun `a raw material with no blueprint is a leaf, not expanded`() = runBlocking {
        val bom = FakeBom(products = emptyMap(), materials = emptyMap())
        val node = buildMaterialTree(TRITANIUM, 100.0, cfg(), bom)
        assertFalse(node.isExpanded)
        assertTrue(node.children.isEmpty())
        assertEquals(100.0, node.quantity, 1e-9)
    }

    @Test
    fun `a manufacturable item is expanded into its blueprint materials`() = runBlocking {
        val bom = FakeBom(
            products = mapOf(WIDGET to BlueprintForProduct(BP_WIDGET, 1.0)),
            materials = mapOf(BP_WIDGET to listOf(TRITANIUM to 10.0)),
        )
        val node = buildMaterialTree(WIDGET, 1.0, cfg(me = 0), bom)
        assertTrue(node.isExpanded)
        assertEquals(1, node.children.size)
        assertEquals(TRITANIUM, node.children[0].typeId)
        assertFalse(node.children[0].isExpanded)
        // ME 0 -> no reduction, 1 run -> exactly the base qty.
        assertEquals(10.0, node.children[0].quantity, 1e-9)
    }

    // -------------------------------------------------------- quantity scaling
    @Test
    fun `quantity scales with the requested build count`() = runBlocking {
        val bom = FakeBom(
            products = mapOf(WIDGET to BlueprintForProduct(BP_WIDGET, 1.0)),
            materials = mapOf(BP_WIDGET to listOf(TRITANIUM to 10.0)),
        )
        val one = buildMaterialTree(WIDGET, 1.0, cfg(me = 0), bom)
        val five = buildMaterialTree(WIDGET, 5.0, cfg(me = 0), bom)
        assertEquals(one.children[0].quantity * 5.0, five.children[0].quantity, 1e-9)
    }

    @Test
    fun `product quantity per run reduces the run count needed`() = runBlocking {
        // A blueprint that outputs 10 units per run only needs 1 run to
        // cover a request for 10 units, so the material requirement is not
        // inflated by a spurious extra run.
        val bom = FakeBom(
            products = mapOf(WIDGET to BlueprintForProduct(BP_WIDGET, 10.0)),
            materials = mapOf(BP_WIDGET to listOf(TRITANIUM to 100.0)),
        )
        val node = buildMaterialTree(WIDGET, 10.0, cfg(me = 0), bom)
        assertEquals(100.0, node.children[0].quantity, 1e-9) // 1 run, not 10
    }

    // -------------------------------------------------------------- aggregation
    @Test
    fun `aggregateLeaves sums a shared raw material across sibling assemblies`() = runBlocking {
        // SHIP needs 2x WIDGET (built from Tritanium) plus 3x a second
        // component that ALSO reduces straight to Tritanium - the same leaf
        // reached from two different branches of the tree must sum, not
        // overwrite or double-count into two separate rows.
        val bom = FakeBom(
            products = mapOf(
                SHIP to BlueprintForProduct(BP_SHIP, 1.0),
                WIDGET to BlueprintForProduct(BP_WIDGET, 1.0),
            ),
            materials = mapOf(
                BP_SHIP to listOf(WIDGET to 2.0, OTHER_COMPONENT to 3.0),
                BP_WIDGET to listOf(TRITANIUM to 10.0),
            ),
        )
        val node = buildMaterialTree(SHIP, 1.0, cfg(me = 0), bom)
        val leaves = aggregateLeaves(node)

        // WIDGET branch: 2 runs of WIDGET -> 2 * 10 = 20 Tritanium.
        // OTHER_COMPONENT branch: itself a raw leaf, 3 units.
        assertEquals(2, leaves.size)
        assertEquals(20.0, leaves[TRITANIUM]!!, 1e-9)
        assertEquals(3.0, leaves[OTHER_COMPONENT]!!, 1e-9)
    }

    @Test
    fun `aggregateLeaves sums the same leaf reached at different quantities`() = runBlocking {
        // Two different intermediate parts both bottom out at Tritanium,
        // each needing a different amount of it.
        val partA = 401
        val partB = 402
        val bpA = 910
        val bpB = 911
        val bom = FakeBom(
            products = mapOf(
                SHIP to BlueprintForProduct(BP_SHIP, 1.0),
                partA to BlueprintForProduct(bpA, 1.0),
                partB to BlueprintForProduct(bpB, 1.0),
            ),
            materials = mapOf(
                BP_SHIP to listOf(partA to 1.0, partB to 1.0),
                bpA to listOf(TRITANIUM to 7.0),
                bpB to listOf(TRITANIUM to 13.0),
            ),
        )
        val node = buildMaterialTree(SHIP, 1.0, cfg(me = 0), bom)
        val leaves = aggregateLeaves(node)
        assertEquals(1, leaves.size)
        assertEquals(20.0, leaves[TRITANIUM]!!, 1e-9) // 7 + 13
    }

    @Test
    fun `a non-manufacturable root aggregates to itself`() = runBlocking {
        val bom = FakeBom(products = emptyMap(), materials = emptyMap())
        val node = buildMaterialTree(TRITANIUM, 250.0, cfg(), bom)
        val leaves = aggregateLeaves(node)
        assertEquals(mapOf(TRITANIUM to 250.0), leaves)
    }

    // ------------------------------------------------------------ cycle guard
    @Test
    fun `a cycle in the bom terminates at MAX_BOM_DEPTH instead of recursing forever`() = runBlocking {
        // Defensive: WIDGET's own blueprint lists WIDGET as a material -
        // should never happen in a real SDE cache, but the depth cap must
        // still terminate the walk rather than recursing indefinitely.
        val bom = FakeBom(
            products = mapOf(WIDGET to BlueprintForProduct(BP_WIDGET, 1.0)),
            materials = mapOf(BP_WIDGET to listOf(WIDGET to 1.0)),
        )
        val node = buildMaterialTree(WIDGET, 1.0, cfg(me = 0), bom)

        // Walk down the single-child chain and confirm it stops being
        // "expanded" at MAX_BOM_DEPTH, not beyond.
        var current = node
        var depth = 0
        while (current.isExpanded) {
            assertTrue(depth < MAX_BOM_DEPTH)
            current = current.children.single()
            depth++
        }
        assertEquals(MAX_BOM_DEPTH, depth)

        // The cut-off node still aggregates to a real, finite leaf
        // requirement rather than the walk never returning at all.
        val leaves = aggregateLeaves(node)
        assertEquals(1, leaves.size)
        assertTrue(leaves.getValue(WIDGET) > 0.0)
    }

    @Test
    fun `a manufacturable item that is itself the root does not infinite-loop`() = runBlocking {
        // A degenerate cycle one level shallower: the blueprint's own
        // product IS one of its listed materials.
        val bom = FakeBom(
            products = mapOf(SHIP to BlueprintForProduct(BP_SHIP, 1.0)),
            materials = mapOf(BP_SHIP to listOf(SHIP to 2.0, TRITANIUM to 5.0)),
        )
        val node = buildMaterialTree(SHIP, 1.0, cfg(me = 0), bom)
        assertTrue(node.isExpanded)
        // Terminates and aggregates without throwing/hanging.
        val leaves = aggregateLeaves(node)
        assertTrue(leaves.containsKey(TRITANIUM))
    }

    // ------------------------------------------------------------------ pricing
    @Test
    fun `priceLeafRequirements prices each aggregated leaf and totals it`() = runBlocking {
        val bom = FakeBom(products = emptyMap(), materials = emptyMap())
        val home = emptyMap<Int, OrderStats>()
        val jita = mapOf(TRITANIUM to stats(5.0), OTHER_COMPONENT to stats(2.0))
        val c = cfg().copy(jitaBuyBrokerFee = 0.0)
        val leaves = mapOf(TRITANIUM to 100.0, OTHER_COMPONENT to 50.0)

        val rows = priceLeafRequirements(leaves, home, jita, c, bom)

        assertEquals(2, rows.size)
        val tritaniumRow = rows.first { it.typeId == TRITANIUM }
        assertEquals(5.0, tritaniumRow.unitCost!!, 1e-9)
        assertEquals(500.0, tritaniumRow.totalCost!!, 1e-9)
        // Sorted by total cost descending: Tritanium (500) before the other
        // component (100).
        assertEquals(TRITANIUM, rows[0].typeId)
    }

    @Test
    fun `priceLeafRequirements leaves cost null when nothing prices the leaf`() = runBlocking {
        val bom = FakeBom(products = emptyMap(), materials = emptyMap())
        val rows = priceLeafRequirements(mapOf(TRITANIUM to 10.0), emptyMap(), emptyMap(), cfg(), bom)
        assertEquals(1, rows.size)
        assertNull(rows[0].unitCost)
        assertNull(rows[0].totalCost)
    }

    // --------------------------------------------------------- end-to-end shape
    @Test
    fun `a full explosion end to end matches the sum of hand-computed branches`() = runBlocking {
        // SHIP -> 3x WIDGET (10 Tritanium each at ME0) + 1x raw Mineral.
        val mineral = 500
        val bom = FakeBom(
            products = mapOf(
                SHIP to BlueprintForProduct(BP_SHIP, 1.0),
                WIDGET to BlueprintForProduct(BP_WIDGET, 1.0),
            ),
            materials = mapOf(
                BP_SHIP to listOf(WIDGET to 3.0, mineral to 1.0),
                BP_WIDGET to listOf(TRITANIUM to 10.0),
            ),
        )
        val node = buildMaterialTree(SHIP, 2.0, cfg(me = 0), bom) // build 2 ships
        val leaves = aggregateLeaves(node)

        // 2 ships -> 6 WIDGET runs -> 60 Tritanium; 2 units of raw mineral.
        assertEquals(60.0, leaves[TRITANIUM]!!, 1e-9)
        assertEquals(2.0, leaves[mineral]!!, 1e-9)
    }
}
