package com.pappmichel.evetraderlocal.data.sde

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** Unit tests for the hand-rolled CSV reader (SdeCsv).
 *
 * This is the one piece of the SDE port that is pure, network-free logic, and
 * it is also the piece most likely to be quietly wrong: a parser that
 * mishandles a quoted comma does not fail loudly, it silently shifts every
 * column after it by one, so `volume` ends up holding a market group id and
 * nothing ever throws. These cases are therefore about the *specific* shapes
 * the real Fuzzwork dumps contain - quoted commas in `typeName`, embedded
 * newlines in `description`, empty cells standing in for SQL NULL, a UTF-8
 * BOM on the header - rather than generic parser exercise. */
class SdeCsvTest {

    @Test
    fun `parses a plain comma-separated line`() {
        assertEquals(listOf("34", "18", "Tritanium"), SdeCsv.parseCsvLine("34,18,Tritanium"))
    }

    @Test
    fun `keeps commas inside a quoted field`() {
        // Real shape: type names with a comma in them, which a naive split
        // would turn into two columns and shift everything after.
        assertEquals(
            listOf("11393", "27", "Guardian-Vexor, Blueprint", "0.01"),
            SdeCsv.parseCsvLine("11393,27,\"Guardian-Vexor, Blueprint\",0.01"),
        )
    }

    @Test
    fun `unescapes a doubled quote inside a quoted field`() {
        assertEquals(
            listOf("1", "He said \"no\"", "2"),
            SdeCsv.parseCsvLine("1,\"He said \"\"no\"\"\",2"),
        )
    }

    @Test
    fun `reads empty fields as empty strings`() {
        assertEquals(listOf("34", "", "Tritanium", ""), SdeCsv.parseCsvLine("34,,Tritanium,"))
        assertEquals(listOf("", ""), SdeCsv.parseCsvLine(","))
    }

    @Test
    fun `parses an empty line as no fields`() {
        assertEquals(emptyList<String>(), SdeCsv.parseCsvLine(""))
    }

    @Test
    fun `maps rows onto header names`() {
        val rows = mutableListOf<Triple<Int?, String?, Double?>>()
        SdeCsv.readRows(
            """
            typeID,typeName,volume
            34,Tritanium,0.01
            35,Pyerite,0.01
            """.trimIndent()
        ) { row ->
            rows.add(Triple(row.intOrNull("typeID"), row.text("typeName"), row.doubleOrNull("volume")))
        }
        assertEquals(
            listOf(
                Triple(34, "Tritanium", 0.01),
                Triple(35, "Pyerite", 0.01),
            ),
            rows,
        )
    }

    @Test
    fun `treats an empty cell as null rather than an empty value`() {
        var row: SdeCsv.CsvRow? = null
        SdeCsv.readRows("typeID,groupID,typeName\n34,,Tritanium\n") { row = it }
        assertEquals(34, row!!.intOrNull("typeID"))
        assertNull(row!!.intOrNull("groupID"))
        assertNull(row!!.text("groupID"))
    }

    @Test
    fun `reads an absent column and an unparsable number as null`() {
        var row: SdeCsv.CsvRow? = null
        SdeCsv.readRows("typeID,volume\n34,not-a-number\n") { row = it }
        // metaLevel is absent from this dump - must read as null, not throw,
        // so a Fuzzwork column change degrades one field instead of the whole
        // refresh.
        assertNull(row!!.intOrNull("metaLevel"))
        assertNull(row!!.doubleOrNull("volume"))
    }

    @Test
    fun `keeps a row together across a newline inside a quoted field`() {
        // invTypes.csv's description column really does contain these; a
        // line-at-a-time parser desynchronises permanently on the first one.
        val rows = mutableListOf<Pair<Int?, String?>>()
        SdeCsv.readRows(
            "typeID,description,volume\n34,\"first line\nsecond line\",0.01\n35,plain,0.02\n"
        ) { row -> rows.add(row.intOrNull("typeID") to row.text("description")) }
        assertEquals(listOf(34 to "first line\nsecond line", 35 to "plain"), rows)
    }

    @Test
    fun `strips a UTF-8 BOM from the first header name`() {
        // Fuzzwork serves these with a BOM; left attached it would glue
        // itself to "typeID" and every lookup of that column would miss.
        var typeId: Int? = null
        SdeCsv.readRows("\uFEFFtypeID,typeName\n34,Tritanium\n") { typeId = it.intOrNull("typeID") }
        assertEquals(34, typeId)
    }

    @Test
    fun `handles CRLF line endings and a trailing newline`() {
        val ids = mutableListOf<Int?>()
        SdeCsv.readRows("typeID,typeName\r\n34,Tritanium\r\n35,Pyerite\r\n") { ids.add(it.intOrNull("typeID")) }
        assertEquals(listOf(34, 35), ids)
    }

    @Test
    fun `ignores blank lines between rows`() {
        val ids = mutableListOf<Int?>()
        SdeCsv.readRows("typeID\n34\n\n35\n") { ids.add(it.intOrNull("typeID")) }
        assertEquals(listOf(34, 35), ids)
    }

    @Test
    fun `reads the SDE published flag as one or zero`() {
        val flags = mutableListOf<Int>()
        SdeCsv.readRows("typeID,published\n34,1\n35,0\n36,\n") { flags.add(it.boolAsInt("published")) }
        assertEquals(listOf(1, 0, 0), flags)
    }

    @Test
    fun `reads ids too large for Int as Long`() {
        // Station ids are only 8 digits today, but the same accessor is what
        // an ESI location_id (structure ids run past Int.MAX_VALUE) would be
        // compared against - see SdeEntities' note on id widths.
        var stationId: Long? = null
        SdeCsv.readRows("stationID\n60003760\n") { stationId = it.longOrNull("stationID") }
        assertEquals(60003760L, stationId)
        assertEquals(1035466617946L, SdeCsv.parseCsvLine("1035466617946")[0].toLong())
    }
}
