package com.pappmichel.evetraderlocal.data.doctrine

import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

/** Ported case-for-case from the desktop build's
 * `tests/test_doctrine_parser.py` fake-resolver layer (the real
 * storage-backed integration tests at the bottom of that file have no
 * counterpart here - this platform's SDE cache has no `dgmTypeEffects`
 * table, see `DoctrineSdeResolver`'s docstring, so there is no equivalent
 * "real cache" fixture to seed with slot data the way `_seed_sde` does on
 * desktop).
 *
 * Category ids match `doctrine/constants.py`, same as the Python fixture:
 * 6 = Ship, 7 = Module (arbitrary "not special" stand-in), 8 = Charge,
 * 18 = Drone. */
class EftFittingParserTest {
    private val SHIP = 6
    private val MODULE = 7
    private val CHARGE = 8
    private val DRONE = 18

    private val types: Map<String, ResolvedType> = mapOf(
        "rifter" to ResolvedType(1, 100, SHIP, null, null, "Rifter"),
        "damage control ii" to ResolvedType(2, 200, MODULE, 2, 5, "Damage Control II"),
        "200mm autocannon ii" to ResolvedType(3, 300, MODULE, 2, 5, "200mm AutoCannon II"),
        "antimatter charge s" to ResolvedType(4, 400, CHARGE, null, null, "Antimatter Charge S"),
        "nanofiber internal structure ii" to ResolvedType(5, 500, MODULE, 2, 5, "Nanofiber Internal Structure II"),
        "small ancillary current router i" to ResolvedType(6, 600, MODULE, null, null, "Small Ancillary Current Router I"),
        "warrior ii" to ResolvedType(7, 700, DRONE, 2, 5, "Warrior II"),
        "tritanium" to ResolvedType(8, 800, 4, null, null, "Tritanium"), // category 4 = Material, no slot
        "liquid ozone" to ResolvedType(9, 900, 4, null, null, "Liquid Ozone"),
    )
    private val slots: Map<Int, String> = mapOf(2 to "low", 3 to "high", 5 to "low", 6 to "rig")

    private val resolveName: NameResolver = { name -> types[name.trim().lowercase()] }
    private val resolveSlot: SlotResolver = { typeId -> slots[typeId] }

    private fun parse(text: String, candidates: List<String> = emptyList()): ParsedFitting = runBlocking {
        EftFittingParser.parseFitting(text, resolveName, resolveSlot, candidates)
    }

    // --------------------------------------------------------------- hard failures
    @Test
    fun `missing header is hard error`() {
        try {
            parse("just some text\nno header here\n")
            fail("expected FittingParseException")
        } catch (e: FittingParseException) {
            // expected
        }
    }

    @Test
    fun `header without comma is hard error`() {
        try {
            parse("[Rifter]\nDamage Control II\n")
            fail("expected FittingParseException")
        } catch (e: FittingParseException) {
            // expected
        }
    }

    @Test
    fun `unresolvable hull is hard error with suggestion`() {
        try {
            parse("[Rifer, Typo Fit]\n", listOf("Rifter"))
            fail("expected FittingParseException")
        } catch (e: FittingParseException) {
            assertTrue(e.message?.contains("Rifer") == true)
        }
    }

    @Test
    fun `hull wrong category is hard error`() {
        try {
            parse("[Tritanium, Not A Ship]\n")
            fail("expected FittingParseException")
        } catch (e: FittingParseException) {
            // expected
        }
    }

    @Test
    fun `empty ship name is hard error`() {
        try {
            parse("[, Fit]\n")
            fail("expected FittingParseException")
        } catch (e: FittingParseException) {
            // expected
        }
    }

    // --------------------------------------------------------------- basic parsing
    @Test
    fun `hull only fitting is valid with no items or issues`() {
        val result = parse("[Rifter, Empty Hull]\n")
        assertEquals(1, result.hullTypeId)
        assertEquals(emptyList<ParsedItem>(), result.items)
        assertEquals(emptyList<ParsedIssue>(), result.issues)
    }

    @Test
    fun `leading blank lines before header are tolerated`() {
        val result = parse("\n\n[Rifter, Padded]\nDamage Control II\n")
        assertEquals(1, result.hullTypeId)
        assertEquals(1, result.items.size)
    }

    @Test
    fun `module classified by sde slot`() {
        val result = parse("[Rifter, Fit]\nDamage Control II\n")
        assertEquals(1, result.items.size)
        val item = result.items[0]
        assertEquals(2, item.typeId)
        assertEquals("low", item.slotSection)
        assertEquals(1.0, item.quantity, 0.0)
        assertEquals(false, item.isOffline)
    }

    @Test
    fun `offline suffix sets flag and still resolves`() {
        val result = parse("[Rifter, Fit]\nDamage Control II /offline\n")
        assertTrue(result.items[0].isOffline)
        assertEquals(2, result.items[0].typeId)
    }

    @Test
    fun `offline suffix case insensitive`() {
        val result = parse("[Rifter, Fit]\nDamage Control II /OFFLINE\n")
        assertTrue(result.items[0].isOffline)
    }

    @Test
    fun `empty marker produces no item and no issue`() {
        val result = parse("[Rifter, Fit]\n[Empty Low slot]\nDamage Control II\n")
        assertEquals(1, result.items.size)
        assertEquals(emptyList<ParsedIssue>(), result.issues)
    }

    // --------------------------------------------------------------- comma / charges
    @Test
    fun `module charge comma line produces two items`() {
        val result = parse("[Rifter, Fit]\n200mm AutoCannon II, Antimatter Charge S\n")
        assertEquals(2, result.items.size)
        val (module, charge) = result.items
        assertEquals(3, module.typeId)
        assertEquals("high", module.slotSection)
        assertEquals(4, charge.typeId)
        assertEquals("charge", charge.slotSection)
        assertEquals(emptyList<ParsedIssue>(), result.issues)
    }

    @Test
    fun `bare charge line without module gets charge section and issue`() {
        val result = parse("[Rifter, Fit]\nAntimatter Charge S\n")
        assertEquals(1, result.items.size)
        assertEquals("charge", result.items[0].slotSection)
        assertEquals(4, result.items[0].typeId)
        assertEquals(1, result.issues.size)
        assertEquals("unknown_section", result.issues[0].issueKind)
    }

    @Test
    fun `ambiguous comma split produces ambiguous_split issue no item`() {
        // Both "Damage Control II" and "Nanofiber Internal Structure II"
        // resolve, but neither combination pairs a module with a real
        // charge - genuinely ambiguous (Phase 3 A.5 case 3), not "resolves
        // nowhere".
        val result = parse("[Rifter, Fit]\nDamage Control II, Nanofiber Internal Structure II\n")
        assertEquals(emptyList<ParsedItem>(), result.items)
        assertEquals(1, result.issues.size)
        assertEquals("ambiguous_split", result.issues[0].issueKind)
    }

    // --------------------------------------------------------------- quantity / drones / cargo
    @Test
    fun `quantity suffix on unfittable item is cargo`() {
        val result = parse("[Rifter, Fit]\nTritanium x100\n")
        assertEquals(1, result.items.size)
        val item = result.items[0]
        assertEquals(8, item.typeId)
        assertEquals("cargo", item.slotSection)
        assertEquals(100.0, item.quantity, 0.0)
        assertEquals(emptyList<ParsedIssue>(), result.issues)
    }

    @Test
    fun `drone category with quantity is drone section`() {
        val result = parse("[Rifter, Fit]\nWarrior II x5\n")
        assertEquals(1, result.items.size)
        assertEquals("drone", result.items[0].slotSection)
        assertEquals(5.0, result.items[0].quantity, 0.0)
    }

    @Test
    fun `quantity suffix on fittable module flags issue`() {
        val result = parse("[Rifter, Fit]\nDamage Control II x2\n")
        assertEquals(1, result.items.size)
        assertEquals("cargo", result.items[0].slotSection)
        assertEquals(1, result.issues.size)
        assertEquals("unknown_section", result.issues[0].issueKind)
    }

    // --------------------------------------------------------------- malformed / unresolved input
    @Test
    fun `unresolvable item name produces issue no item`() {
        val result = parse("[Rifter, Fit]\nSome Made Up Module\n")
        assertEquals(emptyList<ParsedItem>(), result.items)
        assertEquals(1, result.issues.size)
        assertEquals("unresolved_name", result.issues[0].issueKind)
    }

    @Test
    fun `unresolvable item name with close match suggests it`() {
        val result = parse("[Rifter, Fit]\nDamage Control I\n", listOf("Damage Control II"))
        assertEquals("unresolved_name", result.issues[0].issueKind)
        assertTrue(result.issues[0].message.contains("Damage Control II"))
    }

    @Test
    fun `stray bracket line is malformed`() {
        val result = parse("[Rifter, Fit]\n[Not An Empty Marker]\n")
        assertEquals(emptyList<ParsedItem>(), result.items)
        assertEquals(1, result.issues.size)
        assertEquals("malformed", result.issues[0].issueKind)
    }

    @Test
    fun `line that becomes empty after suffix removal is malformed`() {
        val result = parse("[Rifter, Fit]\n /offline\n")
        assertEquals(emptyList<ParsedItem>(), result.items)
        assertEquals(1, result.issues.size)
        assertEquals("malformed", result.issues[0].issueKind)
    }

    // --------------------------------------------------------------- position-vs-SDE gegenprobe
    @Test
    fun `marker position mismatch flags unknown_section issue`() {
        // hasMarkers only turns on (and the position-vs-SDE gegenprobe only
        // fires at all) once the export encodes position via at least one
        // [Empty ... slot] marker (A.4). Block 0 (the marker's own block) is
        // expected "low"; block 1 is expected "med" per EFT_SECTION_ORDER,
        // but the 200mm AutoCannon II's real SDE slot is "high" - a genuine
        // mismatch.
        val text = "[Rifter, Fit]\n[Empty Low slot]\n\n200mm AutoCannon II\n"
        val result = parse(text)
        assertEquals("high", result.items[0].slotSection)
        assertTrue(result.issues.any { it.issueKind == "unknown_section" && it.message.contains("Expected section 'med'") })
    }

    // --------------------------------------------------------------- CRLF / whitespace normalization
    @Test
    fun `crlf and tabs are normalized without shifting line numbers`() {
        val raw = "[Rifter,\tFit]\r\nDamage\t Control  II\r\n"
        val result = parse(raw)
        assertEquals("Fit", result.fitName)
        assertEquals(1, result.items.size)
        assertEquals(2, result.items[0].lineNo)
    }

    // --------------------------------------------------------------- fuel bay / ship maintenance bay (issue #18)
    @Test
    fun `parse bay items resolves plain and quantity lines`() = runBlocking {
        val (items, issues) = EftFittingParser.parseBayItems("Liquid Ozone x500\nTritanium\n", resolveName, "fuelbay")
        assertEquals(2, items.size)
        assertEquals(9, items[0].typeId)
        assertEquals("fuelbay", items[0].slotSection)
        assertEquals(500.0, items[0].quantity, 0.0)
        assertEquals(8, items[1].typeId)
        assertEquals(1.0, items[1].quantity, 0.0)
        assertEquals(emptyList<ParsedIssue>(), issues)
    }

    @Test
    fun `parse bay items unresolvable line produces issue`() = runBlocking {
        val (items, issues) = EftFittingParser.parseBayItems("Not A Real Item\n", resolveName, "shipmaintenancebay")
        assertEquals(emptyList<ParsedItem>(), items)
        assertEquals(1, issues.size)
        assertEquals("unresolved_name", issues[0].issueKind)
    }

    @Test
    fun `parse bay items line_no_start continues numbering`() = runBlocking {
        val (items, _) = EftFittingParser.parseBayItems("Tritanium\n", resolveName, "fuelbay", lineNoStart = 42)
        assertEquals(42, items[0].lineNo)
    }
}
