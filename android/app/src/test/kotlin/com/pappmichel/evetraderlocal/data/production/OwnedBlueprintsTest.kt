package com.pappmichel.evetraderlocal.data.production

import com.pappmichel.evetraderlocal.data.esi.CharacterBlueprint
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Ported case-for-case from the desktop build's tests/test_production_gaps
 * .py Owned Blueprints section (`test_engine_list_owned_blueprints_
 * aggregates_identical_groups`), against this port's live-ESI-shaped
 * [CharacterBlueprint] input instead of desktop's synced `character_
 * blueprints`/`corp_blueprints` table rows - see `OwnedBlueprints.kt`'s own
 * module docstring for why that's the one structural difference. */
class OwnedBlueprintsTest {
    private val COMPONENT_BP = 900
    private val FINISHED_BP = 901

    // ----------------------------------------------------- isBlueprintOriginal
    @Test
    fun `runs of minus one is a BPO`() {
        assertTrue(isBlueprintOriginal(-1))
    }

    @Test
    fun `any other runs value is a BPC`() {
        assertFalse(isBlueprintOriginal(-2))
        assertFalse(isBlueprintOriginal(5))
        assertFalse(isBlueprintOriginal(0))
    }

    // ----------------------------------------------------- groupOwnedBlueprints
    @Test
    fun `identical BPOs from two characters aggregate into one group`() {
        val blueprints = listOf(
            CharacterBlueprint(itemId = 1, typeId = COMPONENT_BP, quantity = -1, materialEfficiency = 10, timeEfficiency = 20, runs = -1),
            CharacterBlueprint(itemId = 2, typeId = COMPONENT_BP, quantity = -1, materialEfficiency = 10, timeEfficiency = 20, runs = -1),
        )
        val rows = groupOwnedBlueprints(blueprints, mapOf(COMPONENT_BP to "Widget Component Blueprint"))

        assertEquals(1, rows.size)
        val row = rows[0]
        assertEquals(COMPONENT_BP, row.typeId)
        assertEquals("Widget Component Blueprint", row.typeName)
        assertTrue(row.isOriginal)
        assertEquals(2, row.quantity)
        assertEquals(10, row.materialEfficiency)
        assertEquals(20, row.timeEfficiency)
        assertNull(row.runs)
    }

    @Test
    fun `a BPO and a BPC of the same type stay separate groups`() {
        val blueprints = listOf(
            CharacterBlueprint(itemId = 1, typeId = COMPONENT_BP, quantity = -1, materialEfficiency = 10, timeEfficiency = 20, runs = -1),
            CharacterBlueprint(itemId = 2, typeId = COMPONENT_BP, quantity = -2, materialEfficiency = 0, timeEfficiency = 0, runs = 5),
        )
        val rows = groupOwnedBlueprints(blueprints, mapOf(COMPONENT_BP to "Widget Component Blueprint"))

        assertEquals(2, rows.size)
        val bpo = rows.first { it.isOriginal }
        val bpc = rows.first { !it.isOriginal }
        assertEquals(1, bpo.quantity)
        assertNull(bpo.runs)
        assertEquals(1, bpc.quantity)
        assertEquals(5, bpc.runs)
    }

    @Test
    fun `a positive quantity is added as a real stack count, not treated as a sentinel`() {
        // Real EVE data never actually reports a positive quantity for a
        // blueprint item (see CharacterBlueprint's own doc comment), but the
        // grouping guard should still honor it rather than always folding to
        // 1, matching engine.list_owned_blueprints's own identical guard.
        val blueprints = listOf(
            CharacterBlueprint(itemId = 1, typeId = COMPONENT_BP, quantity = 3, materialEfficiency = 10, timeEfficiency = 20, runs = -1),
        )
        val rows = groupOwnedBlueprints(blueprints, mapOf(COMPONENT_BP to "Widget Component Blueprint"))
        assertEquals(1, rows.size)
        assertEquals(3, rows[0].quantity)
    }

    @Test
    fun `rows are sorted by type name`() {
        val blueprints = listOf(
            CharacterBlueprint(itemId = 1, typeId = FINISHED_BP, quantity = -1, materialEfficiency = 0, timeEfficiency = 0, runs = -1),
            CharacterBlueprint(itemId = 2, typeId = COMPONENT_BP, quantity = -1, materialEfficiency = 0, timeEfficiency = 0, runs = -1),
        )
        val typeNames = mapOf(FINISHED_BP to "Zeta Blueprint", COMPONENT_BP to "Alpha Blueprint")
        val rows = groupOwnedBlueprints(blueprints, typeNames)

        assertEquals(listOf("Alpha Blueprint", "Zeta Blueprint"), rows.map { it.typeName })
    }

    @Test
    fun `an unresolved type name falls back to the bare type id`() {
        val blueprints = listOf(
            CharacterBlueprint(itemId = 1, typeId = COMPONENT_BP, quantity = -1, materialEfficiency = 0, timeEfficiency = 0, runs = -1),
        )
        val rows = groupOwnedBlueprints(blueprints, emptyMap())
        assertEquals(COMPONENT_BP.toString(), rows[0].typeName)
    }
}
