package com.pappmichel.evetraderlocal.data.production

import com.pappmichel.evetraderlocal.data.esi.IndustryJob
import com.pappmichel.evetraderlocal.data.esi.OrderStats
import java.time.Instant
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Ported case-for-case from the desktop build's
 * tests/test_production_gaps.py Current Jobs & Slots section
 * (`test_jobs_list_current_jobs_prices_output_and_sorts_by_remaining`,
 * `test_character_slot_overview_counts_active_jobs_per_category`), against
 * this port's live-ESI-shaped [IndustryJob] input instead of desktop's
 * synced `character_industry_jobs` table rows - see `ProductionJobs.kt`'s
 * own module docstring for why that's the one structural difference. */
class ProductionJobsTest {
    private val FINISHED = 500
    private val FINISHED_BP = 501

    private class FakeBom(private val products: Map<Int, BlueprintForProduct>) : ProductionBomSource {
        override suspend fun blueprintForProduct(productTypeId: Int): BlueprintForProduct? = products[productTypeId]
        override suspend fun blueprintMaterials(blueprintTypeId: Int): List<Pair<Int, Double>> = emptyList()
        override suspend fun volumeOf(typeId: Int): Double? = null
    }

    // ----------------------------------------------------- jobSlotsFromSkills
    @Test
    fun `job slots from skills is base 1 plus base and advanced skill levels`() {
        val slots = jobSlotsFromSkills(
            mapOf(SKILL_MASS_PRODUCTION to 4, SKILL_ADVANCED_MASS_PRODUCTION to 3)
        )
        assertEquals(1 + 4 + 3, slots["manufacturing"])
        // No reaction/science skills trained at all - base 1 slot only.
        assertEquals(1, slots["reaction"])
        assertEquals(1, slots["science"])
    }

    // ----------------------------------------------------- buildIndustryJobRows
    @Test
    fun `list current jobs prices output and reports runs and activity`() = runBlocking {
        val job = IndustryJob(
            jobId = 1, installerId = 1, activityId = 1, blueprintTypeId = FINISHED_BP,
            productTypeId = FINISHED, runs = 2, status = "active",
            startDate = "2026-01-01T00:00:00Z", endDate = "2099-01-01T00:00:00Z",
        )
        val bom = FakeBom(mapOf(FINISHED to BlueprintForProduct(FINISHED_BP, 1.0)))
        val installerNames = mapOf(1L to "Alice")
        val typeNames = mapOf(FINISHED to "Finished Widget")
        val home = mapOf(FINISHED to OrderStats(sellPercentile = 100.0, sellVolume = 1.0, buyPercentile = null, buyVolume = 0.0))

        val rows = buildIndustryJobRows(listOf(job), home, emptyMap(), bom, installerNames, typeNames)

        assertEquals(1, rows.size)
        val row = rows[0]
        assertEquals("Finished Widget", row.typeName)
        assertEquals(2, row.runs)
        assertEquals("Manufacturing", row.activity)
        assertEquals(2.0, row.quantity!!, 1e-9)
        assertEquals(200.0, row.outputValue!!, 1e-9) // 2 units x 100.0 home sell
        assertEquals("Alice", row.installerName)
    }

    @Test
    fun `a job with no product has no quantity or output value`() = runBlocking {
        val job = IndustryJob(
            jobId = 2, installerId = 1, activityId = 3, blueprintTypeId = FINISHED_BP,
            productTypeId = null, runs = 1, status = "active", startDate = "", endDate = "",
        )
        val bom = FakeBom(emptyMap())
        val typeNames = mapOf(FINISHED_BP to "Finished Widget Blueprint")

        val rows = buildIndustryJobRows(listOf(job), emptyMap(), emptyMap(), bom, mapOf(1L to "Alice"), typeNames)

        assertNull(rows[0].quantity)
        assertNull(rows[0].outputValue)
        assertEquals("TE Research", rows[0].activity)
    }

    @Test
    fun `jobs sort by soonest-completing first, unknown remaining last`() = runBlocking {
        val now = Instant.parse("2026-01-01T00:00:00Z")
        val soon = IndustryJob(
            jobId = 1, installerId = 1, activityId = 1, blueprintTypeId = FINISHED_BP,
            productTypeId = null, runs = 1, status = "active",
            startDate = "", endDate = "2026-01-01T01:00:00Z",
        )
        val later = IndustryJob(
            jobId = 2, installerId = 1, activityId = 1, blueprintTypeId = FINISHED_BP,
            productTypeId = null, runs = 1, status = "active",
            startDate = "", endDate = "2026-01-02T00:00:00Z",
        )
        val unknown = IndustryJob(
            jobId = 3, installerId = 1, activityId = 5, blueprintTypeId = FINISHED_BP,
            productTypeId = null, runs = 1, status = "active", startDate = "", endDate = "",
        )
        val bom = FakeBom(emptyMap())

        val rows = buildIndustryJobRows(
            listOf(later, unknown, soon), emptyMap(), emptyMap(), bom, mapOf(1L to "Alice"), emptyMap(), now,
        )

        assertEquals(listOf(1L, 2L, 3L), rows.map { it.jobId })
    }

    // ----------------------------------------------------- characterSlotOverview
    @Test
    fun `character slot overview counts active jobs per category`() {
        val jobs = listOf(
            IndustryJob(jobId = 1, installerId = 1, activityId = 1, blueprintTypeId = FINISHED_BP, runs = 1, status = "active"),
            IndustryJob(jobId = 2, installerId = 1, activityId = 1, blueprintTypeId = FINISHED_BP, runs = 1, status = "active"),
            IndustryJob(jobId = 3, installerId = 2, activityId = 8, blueprintTypeId = FINISHED_BP, runs = 1, status = "active"),
            IndustryJob(jobId = 4, installerId = 1, activityId = 1, blueprintTypeId = FINISHED_BP, runs = 1, status = "delivered"),
        )
        val installerNames = mapOf(1L to "Alice", 2L to "Bob")

        val rows = characterSlotOverview(jobs, installerNames)

        val byKey = rows.associate { (it.characterName to it.jobType) to it.usedSlots }
        assertEquals(2, byKey[("Alice" to "Manufacturing")])
        assertEquals(1, byKey[("Bob" to "Science")])
        assertFalse(byKey.containsKey("Alice" to "Science")) // never had any - not shown as a zero row
    }

    @Test
    fun `character slot overview reports real totals when skill levels are known`() {
        val jobs = listOf(
            IndustryJob(jobId = 1, installerId = 1, activityId = 1, blueprintTypeId = FINISHED_BP, runs = 1, status = "active"),
        )
        val installerNames = mapOf(1L to "Alice")
        val skillLevels = mapOf(1L to mapOf(SKILL_MASS_PRODUCTION to 5, SKILL_ADVANCED_MASS_PRODUCTION to 0))

        val rows = characterSlotOverview(jobs, installerNames, skillLevels)

        val row = rows.single()
        assertEquals(1, row.usedSlots)
        assertEquals(6, row.totalSlots) // base 1 + 5
        assertEquals(5, row.freeSlots)
    }

    @Test
    fun `character slot overview leaves totals null with no skill data`() {
        val jobs = listOf(
            IndustryJob(jobId = 1, installerId = 1, activityId = 1, blueprintTypeId = FINISHED_BP, runs = 1, status = "active"),
        )
        val rows = characterSlotOverview(jobs, mapOf(1L to "Alice"))

        assertNull(rows.single().totalSlots)
        assertNull(rows.single().freeSlots)
    }
}
