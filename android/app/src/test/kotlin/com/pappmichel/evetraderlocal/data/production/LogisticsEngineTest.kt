package com.pappmichel.evetraderlocal.data.production

import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Tests for [logisticsStatus]/[distributionRecommendations] - a Kotlin
 * port, case for case, of desktop's `tests/test_production_logistics.py`
 * (GitHub issue #4's multi-structure Logistik-tab suite). Every fixture
 * below mirrors that file's own `_build_job`/`_stub_material_demand`
 * helpers: one material (id 2) at a flat base quantity of 10 (or 8, where
 * the desktop test itself varies it), material_mult effectively 1.0
 * (`materialEfficiency = 0`), one job run unless a test says otherwise -
 * same numbers, so this suite's assertions match the desktop file's own
 * worked-out expectations line for line. */
class LogisticsEngineTest {
    private val MATERIAL = 2
    private val BP_OFFSET = 100

    /** [FakeBom] here only ever needs to answer [ProductionBomSource.
     * blueprintMaterials] for [categoryMaterialDemand] - `blueprintForProduct`/
     * `volumeOf` are never called by anything in `LogisticsEngine.kt`, so
     * both are left empty like desktop's own stub (`_stub_material_demand`
     * never touches `classify_activity`'s blueprint id lookup either, just
     * `get_blueprint_materials`). */
    private class FakeBom(private val materials: Map<Int, List<Pair<Int, Double>>>) : ProductionBomSource {
        override suspend fun blueprintForProduct(productTypeId: Int): BlueprintForProduct? = null
        override suspend fun blueprintMaterials(blueprintTypeId: Int): List<Pair<Int, Double>> =
            materials[blueprintTypeId] ?: emptyList()
        override suspend fun volumeOf(typeId: Int): Double? = null
    }

    private fun bom(baseQty: Double = 10.0) = FakeBom(
        materials = mapOf(
            (1 + BP_OFFSET) to listOf(MATERIAL to baseQty),
            (2 + BP_OFFSET) to listOf(MATERIAL to baseQty),
        ),
    )

    private class FakeLocationStock(private val stock: Map<Long, Double>) : LocationStockSource {
        override suspend fun stockAt(typeId: Int, locationId: Long): Double = stock[locationId] ?: 0.0
    }

    private fun job(typeId: Int = 1, category: String = "Advanced Components", jobRuns: Int = 1) =
        LogisticsBuildJob(typeId = typeId, blueprintTypeId = typeId + BP_OFFSET, jobRuns = jobRuns, category = category)

    private val name: suspend (Int) -> String = { "Item$it" }

    // materialEfficiency = 0 -> a 1.0 material multiplier, matching desktop's
    // own `_stub_material_demand`'s `_direct_material_mult` stub (always
    // 1.0) - without this override, [ProductionConfig]'s real materialEfficiency
    // default (10, a 0.9 multiplier) would shrink every base_qty=10.0 demand
    // figure below to 9.0 and break every worked-out assertion ported from
    // the desktop suite.
    private fun cfg(distributionSourceLocationId: Long? = null, homeLocationId: Long? = null) = ProductionConfig(
        distributionSourceLocationId = distributionSourceLocationId, homeLocationId = homeLocationId,
        materialEfficiency = 0,
    )

    // ---------------------------------------------------------------- logisticsStatus

    @Test
    fun `logistics status nets needed against available at assigned location`() = runBlocking {
        val rows = logisticsStatus(
            listOf(job()), mapOf("Advanced Components" to 1001L), cfg(), bom(),
            FakeLocationStock(mapOf(1001L to 3.0)), name,
        )

        assertEquals(1, rows.size)
        val row = rows[0]
        assertEquals(10.0, row.needed, 0.0)
        assertEquals(3.0, row.available, 0.0)
        assertEquals(7.0, row.missing, 0.0)
        assertEquals(1001L, row.locationId)
    }

    @Test
    fun `logistics status skips categories with no assigned location`() = runBlocking {
        val rows = logisticsStatus(listOf(job()), emptyMap(), cfg(), bom(), FakeLocationStock(emptyMap()), name)
        assertTrue(rows.isEmpty())
    }

    @Test
    fun `logistics status pull-from hint picks richest surplus when no warehouse`() = runBlocking {
        val locations = mapOf("Advanced Components" to 1001L, "Capital Components" to 1002L, "Equipment" to 1003L)
        val stock = FakeLocationStock(mapOf(1001L to 3.0, 1002L to 20.0, 1003L to 5.0))

        val rows = logisticsStatus(listOf(job()), locations, cfg(), bom(), stock, name)

        val row = rows[0]
        assertEquals(7.0, row.missing, 0.0)
        assertEquals(1002L, row.pullFromLocationId) // 20 > 5, richest surplus (neither has its own demand here)
        assertEquals(20.0, row.pullFromAvailable)
    }

    @Test
    fun `logistics status prefers warehouse over surplus`() = runBlocking {
        val locations = mapOf("Advanced Components" to 1001L, "Capital Components" to 1002L)
        val stock = FakeLocationStock(mapOf(1001L to 3.0, 1002L to 20.0, 9000L to 6.0))

        val rows = logisticsStatus(listOf(job()), locations, cfg(homeLocationId = 9000L), bom(), stock, name)

        val row = rows[0]
        assertEquals(7.0, row.missing, 0.0)
        // Warehouse (9000) only has 6, less than Capital Components' surplus of 20,
        // but it's still preferred - GitHub issue #4's explicit ask.
        assertEquals(9000L, row.pullFromLocationId)
        assertEquals(6.0, row.pullFromAvailable)
    }

    @Test
    fun `logistics status falls back to surplus when warehouse empty`() = runBlocking {
        val locations = mapOf("Advanced Components" to 1001L, "Capital Components" to 1002L)
        val stock = FakeLocationStock(mapOf(1001L to 3.0, 1002L to 20.0, 9000L to 0.0))

        val rows = logisticsStatus(listOf(job()), locations, cfg(homeLocationId = 9000L), bom(), stock, name)

        val row = rows[0]
        assertEquals(1002L, row.pullFromLocationId)
        assertEquals(20.0, row.pullFromAvailable)
    }

    @Test
    fun `logistics status pull-from hint nets other locations own demand`() = runBlocking {
        // Capital Components has more raw stock (20) than Equipment (5), but
        // Capital Components' own demand (2 job runs * base 10 = 20) eats all
        // of it (surplus 0) - Equipment's smaller surplus (5, no demand of its
        // own here) should win instead. Desktop's own test uses a fractional
        // job_runs=1.8 (demand 18) for the same "almost all of it" shape;
        // [LogisticsBuildJob.jobRuns] is an Int here (matching [BuildJobEntry.
        // jobRuns]'s own type), so this fixture uses 2 whole runs (demand 20,
        // surplus exactly 0) instead - same winning pick, same reasoning.
        val locations = mapOf("Advanced Components" to 1001L, "Capital Components" to 1002L, "Equipment" to 1003L)
        val stock = FakeLocationStock(mapOf(1001L to 3.0, 1002L to 20.0, 1003L to 5.0))
        val jobs = listOf(
            job(typeId = 1, category = "Advanced Components"),
            job(typeId = 2, category = "Capital Components", jobRuns = 2),
        )

        val rows = logisticsStatus(jobs, locations, cfg(), bom(), stock, name)

        val row = rows.first { it.category == "Advanced Components" }
        assertEquals(1003L, row.pullFromLocationId)
        assertEquals(5.0, row.pullFromAvailable)
    }

    @Test
    fun `logistics status no pull-from hint when nothing missing`() = runBlocking {
        val locations = mapOf("Advanced Components" to 1001L, "Capital Components" to 1002L)
        val stock = FakeLocationStock(mapOf(1001L to 100.0, 1002L to 100.0))

        val rows = logisticsStatus(listOf(job()), locations, cfg(), bom(), stock, name)

        assertEquals(0.0, rows[0].missing, 0.0)
        assertNull(rows[0].pullFromLocationId)
    }

    // -------------------------------------------------- distributionRecommendations

    @Test
    fun `distribution recommendations moves from source to shortest category`() = runBlocking {
        val locations = mapOf("Advanced Components" to 1001L)
        val stock = FakeLocationStock(mapOf(1001L to 3.0, 2000L to 50.0))

        val rows = distributionRecommendations(
            listOf(job()), locations, cfg(distributionSourceLocationId = 2000L), bom(), stock, name,
        )

        assertEquals(1, rows.size)
        val row = rows[0]
        assertEquals(MATERIAL, row.typeId)
        assertEquals(2000L, row.fromLocationId)
        assertEquals("Advanced Components", row.toCategory)
        assertEquals(1001L, row.toLocationId)
        assertEquals(7.0, row.quantity, 0.0) // 10 needed - 3 on hand
    }

    @Test
    fun `distribution recommendations falls back to home location`() = runBlocking {
        val locations = mapOf("Advanced Components" to 1001L)
        val stock = FakeLocationStock(mapOf(1001L to 0.0, 3000L to 50.0))

        val rows = distributionRecommendations(listOf(job()), locations, cfg(homeLocationId = 3000L), bom(), stock, name)

        assertEquals(3000L, rows[0].fromLocationId)
    }

    @Test
    fun `distribution recommendations empty without any source configured`() = runBlocking {
        val locations = mapOf("Advanced Components" to 1001L)
        val rows = distributionRecommendations(listOf(job()), locations, cfg(), bom(), FakeLocationStock(emptyMap()), name)
        assertTrue(rows.isEmpty())
    }

    @Test
    fun `distribution recommendations covers largest shortfall first when source limited`() = runBlocking {
        // Advanced Components short by 8, Capital Components short by 3 - only 5 available at source, not enough for both.
        val locations = mapOf("Advanced Components" to 1001L, "Capital Components" to 1002L)
        val stock = FakeLocationStock(mapOf(1001L to 2.0, 1002L to 7.0, 2000L to 5.0))
        val jobs = listOf(job(typeId = 1, category = "Advanced Components"), job(typeId = 2, category = "Capital Components"))

        val rows = distributionRecommendations(jobs, locations, cfg(distributionSourceLocationId = 2000L), bom(), stock, name)

        assertEquals(1, rows.size) // only enough source stock for the larger shortfall
        assertEquals("Advanced Components", rows[0].toCategory)
        assertEquals(5.0, rows[0].quantity, 0.0)
    }

    @Test
    fun `distribution recommendations falls back to surplus when warehouse exhausted`() = runBlocking {
        // Advanced Components needs 8, Capital Components needs 3, warehouse only has 5
        // (covers Advanced Components' larger shortfall in full) - the leftover 3 for
        // Capital Components should come from Equipment's surplus.
        val locations = mapOf("Advanced Components" to 1001L, "Capital Components" to 1002L, "Equipment" to 1003L)
        val stock = FakeLocationStock(mapOf(1001L to 0.0, 1002L to 5.0, 1003L to 20.0, 2000L to 8.0))
        val jobs = listOf(job(typeId = 1, category = "Advanced Components"), job(typeId = 2, category = "Capital Components"))

        val rows = distributionRecommendations(jobs, locations, cfg(distributionSourceLocationId = 2000L), bom(8.0), stock, name)

        val warehouseRow = rows.first { it.fromLocationId == 2000L }
        assertEquals("Advanced Components", warehouseRow.toCategory)
        assertEquals(8.0, warehouseRow.quantity, 0.0)
        val surplusRow = rows.first { it.fromLocationId == 1003L }
        assertEquals("Capital Components", surplusRow.toCategory)
        assertEquals(3.0, surplusRow.quantity, 0.0)
    }

    @Test
    fun `distribution recommendations never double-books a surplus location`() = runBlocking {
        // Two categories both short 10 of the same material, no warehouse configured.
        // The only surplus location (Equipment) has just 12 spare - not enough for
        // both shortfalls in full. The combined recommended quantity out of Equipment
        // must never exceed its real surplus.
        val locations = mapOf("Advanced Components" to 1001L, "Capital Components" to 1002L, "Equipment" to 1003L)
        val stock = FakeLocationStock(mapOf(1001L to 0.0, 1002L to 0.0, 1003L to 12.0))
        val jobs = listOf(job(typeId = 1, category = "Advanced Components"), job(typeId = 2, category = "Capital Components"))

        val rows = distributionRecommendations(jobs, locations, cfg(), bom(), stock, name)

        val fromEquipment = rows.filter { it.fromLocationId == 1003L }
        assertTrue(fromEquipment.sumOf { it.quantity } <= 12.0)
    }
}
