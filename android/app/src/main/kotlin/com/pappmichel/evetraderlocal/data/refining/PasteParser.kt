package com.pappmichel.evetraderlocal.data.refining

/** Parses EVE's standard inventory "Copy As" clipboard paste, ported from the
 * desktop build's `refining/paste_parser.py` (GitHub issue #92). Confirmed
 * against evepraisal.com's own open-source `evepaste` library
 * (`evepaste/parsers/assets.py`, the real, actively-maintained reference
 * implementation for this exact format) - the real column shape (Ctrl+A,
 * Ctrl+C from an Inventory window in "Assets"/list view) is 9 tab-separated
 * columns, no header row:
 *
 *     Name \t Quantity \t Group \t Category \t Size \t Slot \t Volume \t Meta Level \t Tech Level
 *
 * Size/Slot/Meta Level/Tech Level are frequently empty for many item types
 * (e.g. a T1 ammo stack has no Size/Slot at all) - trailing empty fields
 * still produce real tab characters in the real client's own copy, so a
 * short `split("\t")` result (missing trailing empties) is treated as
 * padding with empty strings, not a parse error. */

private val QUANTITY_CLEAN_RE = Regex("[^\\d]") // EVE item quantities are always whole numbers -
// any comma/period present is a thousands separator, never a decimal.
private val VOLUME_CLEAN_RE = Regex("[^\\d.]") // volume IS fractional ("5.0 m3") - strip the " m3" suffix/commas only.

private const val EXPECTED_COLUMNS = 9

data class ParsedPasteLine(
    val rawLine: String,
    val name: String,
    val quantity: Int,
    val category: String,
    val volumeM3: Double?,
    /** Set instead of throwing - a single bad line shouldn't abort the whole
     * paste. */
    val error: String? = null,
)

private fun parseQuantity(raw: String): Int? {
    val cleaned = QUANTITY_CLEAN_RE.replace(raw, "")
    return if (cleaned.isNotEmpty()) cleaned.toIntOrNull() else null
}

private fun parseVolume(raw: String): Double? {
    val stripped = raw.replace("m3", "").trim()
    val cleaned = VOLUME_CLEAN_RE.replace(stripped, "")
    return if (cleaned.isNotEmpty()) cleaned.toDoubleOrNull() else null
}

/** Parses every non-blank line of `text` independently - a malformed line is
 * recorded with its own `error` (still returned, not dropped, per #92's
 * "flag rather than silently drop" requirement) instead of aborting the
 * whole paste. */
fun parsePaste(text: String): List<ParsedPasteLine> {
    val results = mutableListOf<ParsedPasteLine>()
    for (rawLine in text.split("\n")) {
        val line = rawLine.trimEnd('\r', '\n')
        if (line.isBlank()) continue
        var fields = line.split("\t")
        if (fields.size < 2) {
            results.add(
                ParsedPasteLine(
                    rawLine = line, name = line.trim(), quantity = 0, category = "", volumeM3 = null,
                    error = "Not tab-separated - paste from an Inventory window's list view, not free text.",
                )
            )
            continue
        }
        fields = (fields + List(EXPECTED_COLUMNS) { "" }).take(EXPECTED_COLUMNS)
        val name = fields[0].trim()
        val quantity = parseQuantity(fields[1])
        val category = fields[3].trim()
        val volumeRaw = fields[6]
        if (name.isEmpty() || quantity == null) {
            results.add(
                ParsedPasteLine(
                    rawLine = line, name = name.ifEmpty { line.trim() }, quantity = 0, category = category,
                    volumeM3 = null, error = "Could not read a name/quantity from this line.",
                )
            )
            continue
        }
        results.add(ParsedPasteLine(rawLine = line, name = name, quantity = quantity, category = category, volumeM3 = parseVolume(volumeRaw)))
    }
    return results
}

/** Sums quantities for repeated names (multiple stacks of the same item in
 * different cargo slots, confirmed with the user during planning) -
 * case-insensitively, since EVE's own item names are unique regardless of
 * case and a paste could plausibly mix casing across separate copy
 * operations. */
fun mergeDuplicateStacks(lines: List<ParsedPasteLine>): List<ParsedPasteLine> {
    val merged = LinkedHashMap<String, ParsedPasteLine>()
    for (line in lines) {
        if (line.error != null) continue
        val key = line.name.lowercase()
        val existing = merged[key]
        merged[key] = if (existing != null) {
            existing.copy(quantity = existing.quantity + line.quantity)
        } else {
            line
        }
    }
    return merged.values.toList()
}
