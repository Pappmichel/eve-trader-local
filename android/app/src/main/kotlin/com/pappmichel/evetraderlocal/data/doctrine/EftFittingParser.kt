package com.pappmichel.evetraderlocal.data.doctrine

/** Kotlin port of the desktop build's `doctrine/parser.py` - EFT fitting-text
 * parsing (Phase 3 spec section A there, CCP's official Fitting Formats
 * doc). Ported case-for-case, including its test suite
 * (`EftFittingParserTest`, matching `tests/test_doctrine_parser.py`'s fake-
 * resolver layer line for line): a pure, stateless text -> [ParsedFitting]
 * translator with no storage and no network of its own. Every SDE lookup it
 * needs is injected by the caller as a plain function, exactly like the
 * desktop version's `resolve_name`/`resolve_slot` callables - see
 * `DoctrineSdeResolver.kt` for the Android-specific bridge onto the local
 * SDE cache.
 *
 * One deliberate shape difference from the desktop port: both resolver
 * callbacks are `suspend` functions here, not plain ones. Desktop's
 * `resolve_name`/`resolve_slot` are backed by direct SQLite queries
 * (`storage.resolve_sde_type_by_name`/`get_type_slot`), which are ordinary
 * synchronous calls in Python; Android's SDE cache lives behind Room's
 * suspend DAO, so the equivalent per-line queries have to be suspend calls
 * too. Making the parse functions themselves `suspend` (rather than forcing
 * every candidate name to be resolved up front, before the comma-splitting
 * logic below even knows what candidate strings it will try) keeps the
 * exact one-query-per-lookup shape the desktop version has, just async.
 * Unit tests below pass plain in-memory suspend lambdas (no real IO)
 * wrapping a fixed `Map`, the same fidelity as the desktop test suite's own
 * fake resolvers - see that file's own docstring.
 *
 * Grammar (Phase 3 spec A.2, unchanged from the Python docstring):
 *
 *     fitting        := ws-prefix header LF body
 *     header         := "[" hull-name "," WS? fit-name "]"
 *     body           := { block | blank-line }
 *     block          := item-line { LF item-line }
 *     item-line      := empty-marker | typed-line
 *     empty-marker   := "[Empty" WS slot-word WS? "slot]"      (case-insensitive)
 *     typed-line     := names [ qty-suffix ] [ offline-suffix ]
 *     names          := type-name [ "," WS? charge-name ]
 *     qty-suffix     := WS "x" integer
 *     offline-suffix := WS "/offline"                           (case-insensitive)
 */

// ------------------------------------------------------------- output shapes
// Mirrors doctrine/models.py's ParsedItem/ParsedIssue/ParsedFitting. Only the
// parser-output shapes are ported here - the master-data/sync/status
// dataclasses further down that file have no consumer yet on this platform
// (no Doctrine/Fitting persistence layer exists on Android - see this
// package's own README note in DoctrineSdeResolver.kt).
data class ParsedItem(
    val lineNo: Int,
    val slotSection: String,
    val typeId: Int,
    val quantity: Double,
    val isOffline: Boolean = false,
)

data class ParsedIssue(
    val lineNo: Int,
    val rawLine: String,
    val issueKind: String,
    val message: String,
)

data class ParsedFitting(
    val hullTypeId: Int,
    val hullName: String,
    val fitName: String,
    val items: List<ParsedItem>,
    val issues: List<ParsedIssue>,
)

/** What an injected name resolver hands back - mirrors parser.py's own
 * `ResolvedType`. Only `typeId`/`categoryId` are read by parsing logic
 * itself; `groupId`/`metaGroupId`/`metaLevel` are carried through for
 * callers that might need them without a second lookup (desktop's B.6
 * variant-pairing in engine.py, not ported here - see this package's
 * README note on scope). */
data class ResolvedType(
    val typeId: Int,
    val groupId: Int?,
    val categoryId: Int?,
    val metaGroupId: Int?,
    val metaLevel: Int?,
    val typeName: String,
)

typealias NameResolver = suspend (String) -> ResolvedType?
typealias SlotResolver = suspend (Int) -> String?

/** Hard parse failure (Phase 3 A.7's "hard errors" table) - header missing/
 * invalid, or the hull name doesn't resolve to a real ship/structure. */
class FittingParseException(message: String) : Exception(message)

object EftFittingParser {

    // ---------------------------------------------------------- constants
    // Duplicated from DoctrineConstants.kt's own values rather than imported
    // as a single shared object, matching the desktop constants.py's own
    // "no cross-package import for values that happen to coincide" stance -
    // see that file's docstring. Kept local here since only the parser reads
    // them.
    private const val CHARGE_CATEGORY_ID = 8
    private const val DRONE_CATEGORY_ID = 18
    private const val FIGHTER_CATEGORY_ID = 87
    private val VALID_HULL_CATEGORY_IDS = setOf(6, 65) // Ship, Upwell structure

    const val ISSUE_KIND_UNRESOLVED_NAME = "unresolved_name"
    const val ISSUE_KIND_AMBIGUOUS_SPLIT = "ambiguous_split"
    const val ISSUE_KIND_UNKNOWN_SECTION = "unknown_section"
    const val ISSUE_KIND_MALFORMED = "malformed"

    // Levenshtein-suggestion cutoff (Phase 3 A.7: "no suggestion if distance
    // > 1/3 of the name length").
    private const val LEVENSHTEIN_SUGGESTION_MAX_RATIO = 1.0 / 3.0

    // Positional block order (Phase 3 A.2/A.4) - only used for the
    // marker-export position-vs-SDE gegenprobe (A.4's last rule), never to
    // *decide* a section.
    private val EFT_SECTION_ORDER =
        listOf("low", "med", "high", "rig", "subsystem", "service", "drone", "cargo")

    private val EMPTY_MARKER_RE = Regex("^\\[\\s*empty\\s+\\S+(?:\\s+\\S+)*\\s+slot\\s*]$", RegexOption.IGNORE_CASE)
    private val OFFLINE_SUFFIX_RE = Regex("\\s*/offline\\s*$", RegexOption.IGNORE_CASE)
    private val QTY_SUFFIX_RE = Regex("\\s+x(\\d+)\\s*$", RegexOption.IGNORE_CASE)
    private val HEADER_RE = Regex("^\\[(.*)]$")

    /** Standard edit-distance DP - no external dependency, used only for
     * A.7's "did you mean" hull-name suggestion, never for auto-correction
     * (Phase 3 A.3: no fuzzy matching in real resolution, ever). */
    private fun levenshtein(a0: String, b0: String): Int {
        val a = a0.lowercase()
        val b = b0.lowercase()
        if (a == b) return 0
        var prev = IntArray(b.length + 1) { it }
        for (i in 1..a.length) {
            val cur = IntArray(b.length + 1)
            cur[0] = i
            val ca = a[i - 1]
            for (j in 1..b.length) {
                val cost = if (ca == b[j - 1]) 0 else 1
                cur[j] = minOf(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            }
            prev = cur
        }
        return prev[b.length]
    }

    private fun suggest(name: String, candidates: Iterable<String>): String? {
        var best: String? = null
        var bestDist: Int? = null
        for (candidate in candidates) {
            val dist = levenshtein(name, candidate)
            if (bestDist == null || dist < bestDist) {
                best = candidate
                bestDist = dist
            }
        }
        if (best == null || bestDist == null) return null
        if (bestDist > name.length * LEVENSHTEIN_SUGGESTION_MAX_RATIO) return null
        return best
    }

    /** Phase 3 A.3: CRLF/CR -> LF defines line-number counting (done first,
     * nothing before this point may change line counts); per-line
     * whitespace normalization (tabs, repeated spaces, trim) happens after,
     * for parsing only - callers keep the raw text verbatim on their own
     * side, this function never mutates its input. */
    private fun normalize(rawText: String): List<String> {
        var text = rawText.removePrefix("\uFEFF") // strip BOM if present
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        return text.split("\n").map { line ->
            line.replace("\t", " ").replace(Regex(" {2,}"), " ").trim()
        }
    }

    /** Comma-ambiguity resolution driver (Phase 3 A.5): splits at each comma
     * from *right to left* - the rightmost valid split is the most
     * conservative one, since module names are more likely to themselves
     * contain comma-adjacent-looking text than charge names. */
    private fun commaSplitCandidates(text: String): List<Pair<String, String>> {
        val positions = text.indices.filter { text[it] == ',' }
        return positions.reversed().map { i -> text.substring(0, i).trim() to text.substring(i + 1).trim() }
    }

    private data class NameResolution(val primary: ResolvedType?, val charge: ResolvedType?, val issueKind: String?)

    /** Phase 3 A.5. `issueKind` is null on success (1 or 2 resolved items),
     * otherwise `ISSUE_KIND_AMBIGUOUS_SPLIT` or `ISSUE_KIND_UNRESOLVED_NAME`. */
    private suspend fun resolveNames(text: String, resolveName: NameResolver): NameResolution {
        val whole = resolveName(text)
        if (whole != null) return NameResolution(whole, null, null)
        for ((left, right) in commaSplitCandidates(text)) {
            if (left.isEmpty() || right.isEmpty()) continue
            val leftT = resolveName(left)
            val rightT = resolveName(right)
            if (leftT != null && rightT != null && rightT.categoryId == CHARGE_CATEGORY_ID) {
                return NameResolution(leftT, rightT, null)
            }
        }
        // Any split where both halves resolve, but the right half isn't a
        // charge, is a real ambiguity (Phase 3 A.5 case 3) - distinguished
        // from "resolves nowhere" (case 4) so the two get different issue
        // kinds/messages.
        for ((left, right) in commaSplitCandidates(text)) {
            if (left.isEmpty() || right.isEmpty()) continue
            if (resolveName(left) != null && resolveName(right) != null) {
                return NameResolution(null, null, ISSUE_KIND_AMBIGUOUS_SPLIT)
            }
        }
        return NameResolution(null, null, ISSUE_KIND_UNRESOLVED_NAME)
    }

    /** Phase 3 A.4 - SDE wins over position, with the qty-suffix override (a
     * quantity line is never a slot-fitted module, regardless of what
     * `resolveSlot` says - real gear never carries an xN suffix). */
    private suspend fun classifySection(resolved: ResolvedType, hasQty: Boolean, resolveSlot: SlotResolver): String {
        if (hasQty) {
            return if (resolved.categoryId == DRONE_CATEGORY_ID || resolved.categoryId == FIGHTER_CATEGORY_ID) "drone" else "cargo"
        }
        val slot = resolveSlot(resolved.typeId)
        if (slot != null) return slot
        if (resolved.categoryId == DRONE_CATEGORY_ID || resolved.categoryId == FIGHTER_CATEGORY_ID) return "drone"
        return "cargo" // includes the "bare Charge line" case - see caller for its own issue
    }

    /** Phase 3 spec A - full entry point. Throws [FittingParseException] for
     * the two hard-fail cases (A.7); everything else becomes a [ParsedItem]
     * or a [ParsedIssue], never an exception, so a fitting with warnings is
     * still fully parseable and (by the caller's choice) storable. */
    suspend fun parseFitting(
        rawText: String,
        resolveName: NameResolver,
        resolveSlot: SlotResolver,
        hullNameCandidates: Iterable<String> = emptyList(),
    ): ParsedFitting {
        val lines = normalize(rawText)

        var headerIdx = -1
        for (i in lines.indices) {
            if (lines[i].isNotEmpty()) {
                headerIdx = i
                break
            }
        }
        if (headerIdx == -1) {
            throw FittingParseException("No valid EFT header - first line must be `[Ship Name, Fitting Name]`.")
        }
        val headerLine = lines[headerIdx]
        val m = HEADER_RE.matchEntire(headerLine)
            ?: throw FittingParseException("No valid EFT header - first line must be `[Ship Name, Fitting Name]`.")
        val inner = m.groupValues[1]
        if (!inner.contains(",")) {
            throw FittingParseException("Header has no comma - expected `[Ship Name, Fitting Name]`.")
        }
        val commaIdx = inner.indexOf(",")
        val hullName = inner.substring(0, commaIdx).trim()
        val fitName = inner.substring(commaIdx + 1).trim()
        if (hullName.isEmpty()) {
            throw FittingParseException("Empty ship name in header.")
        }

        val hull = resolveName(hullName)
        if (hull == null) {
            val suggestion = suggest(hullName, hullNameCandidates)
            var msg = "Ship '$hullName' not found."
            if (suggestion != null) msg += " Did you mean '$suggestion'?"
            throw FittingParseException(msg)
        }
        if (hull.categoryId !in VALID_HULL_CATEGORY_IDS) {
            throw FittingParseException("'$hullName' is not a ship.")
        }

        val bodyLines = lines.drop(headerIdx + 1)

        // Blocks: contiguous runs of non-blank lines, separated by 1+ blank
        // lines - used only for the position-vs-SDE gegenprobe (A.4's last
        // rule), never to decide a section outright. hasMarkers gates
        // whether the gegenprobe fires at all (A.4: only for exports that
        // actually encode position via [Empty ... slot] markers).
        val blocks = mutableListOf<MutableList<Pair<Int, String>>>()
        var current = mutableListOf<Pair<Int, String>>()
        for ((offset, line) in bodyLines.withIndex()) {
            val lineNo = headerIdx + 2 + offset // 1-based; body starts right after the header line
            if (line.isEmpty()) {
                if (current.isNotEmpty()) {
                    blocks.add(current)
                    current = mutableListOf()
                }
                continue
            }
            current.add(lineNo to line)
        }
        if (current.isNotEmpty()) blocks.add(current)

        val hasMarkers = blocks.any { block -> block.any { (_, line) -> EMPTY_MARKER_RE.matches(line) } }
        val expectedSectionByLine = mutableMapOf<Int, String>()
        if (hasMarkers) {
            for ((blockIdx, block) in blocks.withIndex()) {
                if (blockIdx >= EFT_SECTION_ORDER.size) continue
                val expected = EFT_SECTION_ORDER[blockIdx]
                for ((lineNo, _) in block) expectedSectionByLine[lineNo] = expected
            }
        }

        val items = mutableListOf<ParsedItem>()
        val issues = mutableListOf<ParsedIssue>()

        for (block in blocks) {
            for ((lineNo, line) in block) {
                if (EMPTY_MARKER_RE.matches(line)) continue // A.7: no item, no issue

                var remainder = line
                var isOffline = false
                if (OFFLINE_SUFFIX_RE.containsMatchIn(remainder)) {
                    remainder = OFFLINE_SUFFIX_RE.replace(remainder, "")
                    isOffline = true
                }

                var hasQty = false
                var qty = 1
                val qtyMatch = QTY_SUFFIX_RE.find(remainder)
                if (qtyMatch != null) {
                    qty = qtyMatch.groupValues[1].toInt()
                    if (qty >= 1) {
                        remainder = QTY_SUFFIX_RE.replace(remainder, "")
                        hasQty = true
                    }
                    // qty == 0 is nonsensical (grammar requires >=1) - fall
                    // through and let name resolution run on the untouched
                    // line, which will most likely just fail to resolve as
                    // unresolved_name.
                }

                remainder = remainder.trim()
                if (remainder.startsWith("[") && !EMPTY_MARKER_RE.matches(line)) {
                    issues.add(ParsedIssue(lineNo, line, ISSUE_KIND_MALFORMED, "Unrecognized line: '$line'"))
                    continue
                }
                if (remainder.isEmpty()) {
                    issues.add(ParsedIssue(lineNo, line, ISSUE_KIND_MALFORMED, "Empty line after suffix removal: '$line'"))
                    continue
                }

                val (primary, charge, issueKind) = resolveNames(remainder, resolveName)
                if (issueKind == ISSUE_KIND_AMBIGUOUS_SPLIT) {
                    issues.add(ParsedIssue(lineNo, line, ISSUE_KIND_AMBIGUOUS_SPLIT, "Ambiguous line, multiple valid splits: '$line'"))
                    continue
                }
                if (issueKind == ISSUE_KIND_UNRESOLVED_NAME || primary == null) {
                    val suggestion = suggest(remainder, hullNameCandidates)
                    var msg = "Unknown item: '$remainder'."
                    msg += if (suggestion != null) " Did you mean '$suggestion'?" else " Possibly a mutated (Abyssal) module - these can't be validated."
                    issues.add(ParsedIssue(lineNo, line, ISSUE_KIND_UNRESOLVED_NAME, msg))
                    continue
                }

                val section = classifySection(primary, hasQty, resolveSlot)
                val bareChargeWithoutQty = !hasQty && charge == null &&
                    primary.categoryId == CHARGE_CATEGORY_ID && section == "cargo"
                var finalSection = section
                if (bareChargeWithoutQty) {
                    finalSection = "charge"
                    issues.add(ParsedIssue(lineNo, line, ISSUE_KIND_UNKNOWN_SECTION, "Bare charge line without module/quantity: '$line'"))
                } else if (!hasQty && charge == null &&
                    primary.categoryId !in setOf(DRONE_CATEGORY_ID, FIGHTER_CATEGORY_ID, CHARGE_CATEGORY_ID) &&
                    section == "cargo" && resolveSlot(primary.typeId) == null
                ) {
                    issues.add(ParsedIssue(lineNo, line, ISSUE_KIND_UNKNOWN_SECTION, "Unexpected item without slot/quantity, treated as cargo: '$line'"))
                } else if (hasQty) {
                    // A.7's "xN suffix outside Drones/Cargo" case: only a
                    // genuine surprise if this type otherwise *has* a real
                    // slot (a fitted-module type carrying a quantity suffix).
                    if (resolveSlot(primary.typeId) != null) {
                        issues.add(ParsedIssue(lineNo, line, ISSUE_KIND_UNKNOWN_SECTION, "Quantity suffix on a fittable module: '$line'"))
                    }
                }

                if (hasMarkers && !hasQty && expectedSectionByLine.containsKey(lineNo)) {
                    val expected = expectedSectionByLine.getValue(lineNo)
                    if (expected in setOf("low", "med", "high", "rig", "subsystem", "service") &&
                        expected != finalSection && resolveSlot(primary.typeId) != null
                    ) {
                        issues.add(ParsedIssue(lineNo, line, ISSUE_KIND_UNKNOWN_SECTION, "Expected section '$expected', SDE says '$finalSection': '$line'"))
                    }
                }

                items.add(ParsedItem(lineNo = lineNo, slotSection = finalSection, typeId = primary.typeId,
                    quantity = (if (hasQty) qty else 1).toDouble(), isOffline = isOffline))
                if (charge != null) {
                    items.add(ParsedItem(lineNo = lineNo, slotSection = "charge", typeId = charge.typeId, quantity = 1.0, isOffline = false))
                }
            }
        }

        return ParsedFitting(hullTypeId = hull.typeId, hullName = hull.typeName, fitName = fitName, items = items, issues = issues)
    }

    /** GitHub issue #18 - Fuel Bay / Ship Maintenance Bay contents, entered
     * as a separate plain-text list rather than through [parseFitting]'s own
     * EFT grammar above (which has no syntax for either bay at all - CCP's
     * Fitting Formats spec only covers slots/drones/cargo). One item per
     * line: plain "Item Name" (qty 1) or "Item Name xN" - the same
     * qty-suffix grammar `parseFitting`'s own body lines use, just without
     * any slot/section classification, since every resolved line here
     * becomes exactly `slotSection` (the caller passes "fuelbay" or
     * "shipmaintenancebay").
     *
     * Deliberately no comma/charge-pairing logic (Ship Maintenance Bay holds
     * plain ships/haulers, Fuel Bay holds a single fuel material - neither
     * ever pairs a module with a loaded charge the way a fitted slot can)
     * and no offline-suffix handling (bay contents are never "/offline",
     * that only applies to fitted modules). `lineNoStart` lets the caller
     * number these continuing on from wherever `parseFitting`'s own item
     * line numbers left off, so a bay-content issue's `lineNo` never
     * collides with a main EFT body issue's. */
    suspend fun parseBayItems(
        text: String,
        resolveName: NameResolver,
        slotSection: String,
        lineNoStart: Int = 1,
    ): Pair<List<ParsedItem>, List<ParsedIssue>> {
        val items = mutableListOf<ParsedItem>()
        val issues = mutableListOf<ParsedIssue>()
        var lineNo = lineNoStart
        for (rawLine in normalize(text)) {
            if (rawLine.isEmpty()) continue
            var remainder = rawLine
            var hasQty = false
            var qty = 1
            val qtyMatch = QTY_SUFFIX_RE.find(remainder)
            if (qtyMatch != null) {
                qty = qtyMatch.groupValues[1].toInt()
                if (qty >= 1) {
                    remainder = QTY_SUFFIX_RE.replace(remainder, "")
                    hasQty = true
                }
            }
            remainder = remainder.trim()
            val resolved = if (remainder.isNotEmpty()) resolveName(remainder) else null
            if (resolved == null) {
                issues.add(ParsedIssue(lineNo, rawLine, ISSUE_KIND_UNRESOLVED_NAME, "Unknown item: '$remainder'."))
            } else {
                items.add(ParsedItem(lineNo = lineNo, slotSection = slotSection, typeId = resolved.typeId,
                    quantity = (if (hasQty) qty else 1).toDouble(), isOffline = false))
            }
            lineNo++
        }
        return items to issues
    }
}
