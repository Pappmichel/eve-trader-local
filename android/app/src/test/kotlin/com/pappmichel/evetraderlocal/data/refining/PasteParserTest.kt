package com.pappmichel.evetraderlocal.data.refining

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Ported case-for-case from the desktop build's
 * `tests/test_refining_paste_parser.py`. No network, no SDE - this module
 * does its own SDE-independent tab-column parsing, confirmed against
 * evepraisal.com's `evepaste` reference format. */
class PasteParserTest {
    // A realistic pasted "Inventory" list-view copy: some rows have every
    // column, some have several trailing columns empty (a real client's own
    // copy still emits the tab characters for those, so a naive fields[N]
    // index would throw rather than treat them as blank).
    private val samplePaste =
        "Veldspar\t1250\tVeldspar\tAsteroid\t\t\t0.1 m3\t\t\n" +
            "Tritanium\t500\tMineral\tMaterial\t\t\t0.01 m3\t\t\n" +
            "Damage Control II\t2\tDamage Controls\tModule\tMedium\tLow\t5 m3\t2\t2\n"

    @Test
    fun `parse paste reads all columns`() {
        val lines = parsePaste(samplePaste)
        assertEquals(listOf("Veldspar", "Tritanium", "Damage Control II"), lines.map { it.name })
        assertEquals(1250, lines[0].quantity)
        assertEquals(0.1, lines[0].volumeM3!!, 1e-9)
        assertEquals("Asteroid", lines[0].category)
        assertEquals(2, lines[2].quantity)
        assertEquals("Module", lines[2].category)
        assertTrue(lines.all { it.error == null })
    }

    @Test
    fun `parse paste skips blank lines`() {
        val text = "Veldspar\t1250\tVeldspar\tAsteroid\t\t\t0.1 m3\t\t\n\n\n"
        assertEquals(1, parsePaste(text).size)
    }

    @Test
    fun `parse paste pads short trailing columns`() {
        // A short split() result (client omitted trailing empty tabs) is
        // padded with empty strings rather than throwing.
        val lines = parsePaste("Veldspar\t1250\n")
        assertEquals("Veldspar", lines[0].name)
        assertEquals(1250, lines[0].quantity)
        assertNull(lines[0].error)
    }

    @Test
    fun `parse paste flags non tab separated line`() {
        // A single bad line is recorded with its own error, not dropped and
        // not aborting the whole paste.
        val lines = parsePaste("just some free text, not a paste\n")
        assertNotNull(lines[0].error)
        assertTrue(lines[0].error!!.contains("tab-separated"))
    }

    @Test
    fun `parse paste flags unreadable name or quantity`() {
        val lines = parsePaste("\t\tGroup\tCategory\t\t\t\t\t\n")
        assertNotNull(lines[0].error)
    }

    @Test
    fun `parse paste quantity strips thousands separators`() {
        val lines = parsePaste("Tritanium\t1,234,567\tMineral\tMaterial\t\t\t\t\t\n")
        assertEquals(1234567, lines[0].quantity)
    }

    @Test
    fun `parse paste volume strips m3 suffix and commas`() {
        val lines = parsePaste("Freighter\t1\tFreighter\tShip\t\t\t1,000,000.0 m3\t\t\n")
        assertEquals(1000000.0, lines[0].volumeM3!!, 1e-9)
    }

    @Test
    fun `merge duplicate stacks sums case insensitively`() {
        // Multiple stacks of the same item in different cargo slots sum
        // together, case-insensitively - EVE names are unique regardless of
        // case.
        val text = "veldspar\t100\tVeldspar\tAsteroid\t\t\t0.1 m3\t\t\n" +
            "Veldspar\t50\tVeldspar\tAsteroid\t\t\t0.1 m3\t\t\n"
        val merged = mergeDuplicateStacks(parsePaste(text))
        assertEquals(1, merged.size)
        assertEquals(150, merged[0].quantity)
        assertEquals("veldspar", merged[0].name) // first-seen casing wins
    }

    @Test
    fun `merge duplicate stacks drops errored lines`() {
        val merged = mergeDuplicateStacks(parsePaste(samplePaste + "bad line with no tabs\n"))
        assertTrue(merged.all { it.error == null })
        assertEquals(3, merged.size)
    }

    @Test
    fun `merge duplicate stacks preserves first seen order`() {
        val text = "B Item\t1\tG\tC\t\t\t\t\t\nA Item\t1\tG\tC\t\t\t\t\t\n"
        val merged = mergeDuplicateStacks(parsePaste(text))
        assertEquals(listOf("B Item", "A Item"), merged.map { it.name })
    }
}
