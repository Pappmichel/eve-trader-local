package com.pappmichel.evetraderlocal.data.sde

import java.io.Reader

/** A minimal RFC4180 CSV reader, written by hand rather than pulled in as a
 * Gradle dependency.
 *
 * The desktop build gets this for free (`csv.DictReader` in sde.py); Kotlin
 * has no CSV reader in either the standard library or anything already on
 * this app's classpath, and adding a third-party parser to read six
 * fixed-shape files from one known publisher is a poor trade - a new
 * dependency to audit and keep current, for ~100 lines of logic whose
 * entire contract is "commas separate, double quotes group". So: hand-rolled
 * and unit-tested (see SdeCsvTest) instead.
 *
 * Three properties of the real Fuzzwork dumps that a naive `String.split(",")`
 * gets wrong, and which this parser therefore has to handle:
 *  - `typeName`/`stationName` legitimately contain commas inside double
 *    quotes (e.g. ship names with a comma in the faction suffix).
 *  - `invTypes.csv` carries the SDE's free-text `description` column, whose
 *    quoted values contain *embedded newlines*. A line-at-a-time parser
 *    silently desynchronises the moment it hits one, so rows are parsed off
 *    a character stream and a newline only ends a row when it is outside
 *    quotes. This is the single most important reason not to split on lines.
 *  - `""` inside a quoted field is one literal double quote.
 *
 * Unquoted fields are taken literally, including any stray quote character
 * mid-field - the same forgiving behaviour Python's `csv` module has by
 * default, chosen so one oddly-escaped row in a 50k-row dump degrades to a
 * slightly wrong string rather than derailing the whole refresh.
 *
 * Rows are handed to a callback rather than returned as a list: `invTypes.csv`
 * alone is ~14MB of CSV, most of it `description` text this app never stores,
 * and materialising every row as a `Map<String, String>` first (the shape
 * `DictReader` hands sde.py) would hold tens of megabytes of strings live on
 * a phone for no reason. The callback sees each row exactly once, projects
 * the handful of columns it wants into an entity, and everything else is
 * immediately garbage. */
object SdeCsv {

    /** Fuzzwork serves these files with a UTF-8 BOM - the direct counterpart
     * of sde.py's `decode("utf-8-sig")`. Left unstripped it would end up
     * glued to the first header name, so `typeID` would silently never
     * resolve. */
    private const val BOM = '\uFEFF'

    /** Parses one physical line's worth of CSV - the convenience entry point
     * for callers (and tests) holding a single row as a string. A line that
     * would continue into a quoted newline simply ends here; the streaming
     * [readRows] path is the one that handles multi-line fields. */
    fun parseCsvLine(line: String): List<String> =
        RowParser(line.reader()).nextRow() ?: emptyList()

    /** Header-aware read of a whole CSV document held in memory. Only used by
     * tests and small inputs - the download path uses the [Reader] overload
     * so a ~14MB body is never materialised as a String on top of the rows
     * parsed out of it. */
    fun readRows(text: String, onRow: (CsvRow) -> Unit) = readRows(text.reader(), onRow)

    /** Reads `reader` to exhaustion, treating the first row as the header and
     * invoking `onRow` once per data row. Each [CsvRow] wraps a freshly
     * allocated value list, so retaining one is safe - but retaining 50k of
     * them is exactly the memory cost this streaming shape exists to avoid,
     * so project out the columns you need and let the row go. */
    fun readRows(reader: Reader, onRow: (CsvRow) -> Unit) {
        val parser = RowParser(reader)
        val header = parser.nextRow() ?: return
        val columns = HashMap<String, Int>(header.size * 2)
        header.forEachIndexed { index, name ->
            val key = name.trim()
            if (!columns.containsKey(key)) columns[key] = index
        }
        while (true) {
            val values = parser.nextRow() ?: break
            // A blank line (including the trailing newline dumps end with)
            // parses as a single empty field - not a row with a missing id.
            if (values.size == 1 && values[0].isEmpty()) continue
            onRow(CsvRow(columns, values))
        }
    }

    /** One parsed row, addressed by header name rather than by position.
     *
     * Every accessor is null-tolerant on purpose, in two directions at once:
     * a column that isn't in this dump at all reads as absent rather than
     * throwing, and a value that doesn't parse as the requested type reads as
     * null rather than throwing. sde.py can afford to blow up on a malformed
     * row (a desktop user re-runs `refresh-sde` and reads the traceback);
     * losing one row out of 50k is a much better outcome on a phone than
     * losing the whole ~19MB download that produced it. Rows missing a
     * *primary key* are the exception - the callers below drop those, since
     * a row with no id cannot be stored at all. */
    class CsvRow internal constructor(
        private val columns: Map<String, Int>,
        private val values: List<String>,
    ) {
        /** The raw cell, or null when the column is absent, the row is short,
         * or the cell is empty. Empty-as-null matches sde.py's own
         * `_int_or_none`/`_float_or_none`, which treat `""` as SQL NULL. */
        fun text(name: String): String? {
            val index = columns[name] ?: return null
            return values.getOrNull(index)?.takeIf { it.isNotEmpty() }
        }

        fun intOrNull(name: String): Int? = text(name)?.toIntOrNull()

        fun longOrNull(name: String): Long? = text(name)?.toLongOrNull()

        fun doubleOrNull(name: String): Double? = text(name)?.toDoubleOrNull()

        /** SQLite has no boolean type and neither does the SDE: `published`
         * is the string "1"/"0", stored as 1/0 exactly as sde.py stores
         * `int(r["published"] == "1")`. */
        fun boolAsInt(name: String): Int = if (text(name) == "1") 1 else 0
    }

    /** The actual character-level state machine. Deliberately not a
     * line-splitter - see this file's own docstring for why. */
    private class RowParser(reader: Reader) {
        private val input = reader.buffered()
        private var pushedBack: Int = NOTHING_PUSHED
        private var atStreamStart = true

        /** The next row's fields, or null once the stream is exhausted. */
        fun nextRow(): List<String>? {
            val values = ArrayList<String>()
            val field = StringBuilder()
            var inQuotes = false

            while (true) {
                val next = read()
                if (next == -1) {
                    // EOF with nothing buffered means "no further row" rather
                    // than "one empty row" - otherwise every file would yield
                    // a phantom trailing record.
                    if (values.isEmpty() && field.isEmpty()) return null
                    values.add(field.toString())
                    return values
                }
                val ch = next.toChar()

                if (inQuotes) {
                    if (ch != '"') {
                        field.append(ch)
                        continue
                    }
                    val after = read()
                    if (after == '"'.code) field.append('"') else { inQuotes = false; pushBack(after) }
                    continue
                }

                when (ch) {
                    // A quote only opens a quoted field at the field's start;
                    // anywhere else it is just a character (see docstring).
                    '"' -> if (field.isEmpty()) inQuotes = true else field.append(ch)
                    ',' -> { values.add(field.toString()); field.setLength(0) }
                    '\n' -> { values.add(field.toString()); return values }
                    '\r' -> {
                        // CRLF, and a lone CR, both end the row exactly once.
                        val after = read()
                        if (after != '\n'.code) pushBack(after)
                        values.add(field.toString())
                        return values
                    }
                    else -> field.append(ch)
                }
            }
        }

        private fun read(): Int {
            if (pushedBack != NOTHING_PUSHED) {
                val c = pushedBack
                pushedBack = NOTHING_PUSHED
                return c
            }
            val c = input.read()
            if (atStreamStart) {
                atStreamStart = false
                if (c == BOM.code) return read()
            }
            return c
        }

        private fun pushBack(c: Int) {
            if (c != -1) pushedBack = c
        }

        private companion object {
            const val NOTHING_PUSHED = -2
        }
    }
}
