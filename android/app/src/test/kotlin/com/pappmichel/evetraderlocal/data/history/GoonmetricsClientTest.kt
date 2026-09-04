package com.pappmichel.evetraderlocal.data.history

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.kxml2.io.KXmlParser

/** Parser-only tests against hand-written documents matching the real
 * Goonmetrics schema - no client and no network involved, the same way the
 * desktop build's tests/test_goonmetrics_client.py exercises
 * `_parse_history_xml` against a literal XML string.
 *
 * `android.util.Xml.newPullParser()` is a throwing framework stub on a plain
 * JVM unit-test classpath, so these hand a real implementation (kxml2 -
 * which is what the framework itself wraps on device) to `parseHistoryXml`'s
 * defaulted parser parameter. */
class GoonmetricsClientTest {
    private val jitaRegion = 10_000_002

    private fun parse(xml: String) = parseHistoryXml(xml, jitaRegion, KXmlParser())

    @Test
    fun `parses every history row under every type`() {
        val points = parse(
            """
            <evec_api version="2.0"><result><rowset name="history">
              <type id="34">
                <history date="2026-08-01" avgPrice="5.10" maxPrice="5.50" minPrice="4.90"
                         movement="1200000" numOrders="310"/>
                <history date="2026-08-02" avgPrice="5.20" maxPrice="5.60" minPrice="5.00"
                         movement="900000" numOrders="290"/>
              </type>
              <type id="35">
                <history date="2026-08-01" avgPrice="11.0" maxPrice="12.0" minPrice="10.0"
                         movement="500000" numOrders="140"/>
              </type>
            </rowset></result></evec_api>
            """.trimIndent()
        )

        assertEquals(3, points.size)
        val first = points.first()
        assertEquals(jitaRegion, first.regionId)
        assertEquals(34, first.typeId)
        assertEquals("2026-08-01", first.date)
        assertEquals(5.10, first.avgPrice, 1e-9)
        assertEquals(5.50, first.maxPrice, 1e-9)
        assertEquals(4.90, first.minPrice, 1e-9)
        assertEquals(1_200_000.0, first.movement, 1e-9)
        assertEquals(310, first.numOrders)

        // The type_id comes from the enclosing <type>, so the second type's
        // rows must not inherit the first one's id.
        assertEquals(listOf(34, 34, 35), points.map { it.typeId })
    }

    @Test
    fun `skips malformed rows instead of failing the document`() {
        val points = parse(
            """
            <evec_api><result><rowset name="history">
              <type id="34">
                <history date="2026-08-01" avgPrice="not-a-number" maxPrice="5.50" minPrice="4.90"
                         movement="1200000" numOrders="310"/>
                <history date="2026-08-02" maxPrice="5.60" minPrice="5.00" movement="900000" numOrders="290"/>
                <history date="2026-08-03" avgPrice="5.30" maxPrice="5.70" minPrice="5.10"
                         movement="800000" numOrders="280"/>
              </type>
            </rowset></result></evec_api>
            """.trimIndent()
        )

        assertEquals(1, points.size)
        assertEquals("2026-08-03", points.first().date)
    }

    @Test
    fun `returns nothing for an empty result set`() {
        assertTrue(parse("<evec_api><result><rowset name=\"history\"/></result></evec_api>").isEmpty())
    }
}
