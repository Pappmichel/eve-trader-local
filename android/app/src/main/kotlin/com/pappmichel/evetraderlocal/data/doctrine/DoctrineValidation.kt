package com.pappmichel.evetraderlocal.data.doctrine

import kotlin.math.ceil

/** Kotlin port of the desktop build's `doctrine/validation.py` - pure Soll/
 * Ist/deviation/stockpile-allocation/contract-matching math, ported
 * case-for-case against `tests/test_doctrine_validation.py` (see
 * `DoctrineValidationTest.kt`).
 *
 * This now ports validation.py in full, both halves: the stockpile-side
 * Soll/allocation math Stockpile Status/Shopping List already used
 * (`buildContractSoll`/`buildStockpileSoll`/`allocateStockpile`/
 * `stockpileDeviation`/`worstSeverity`/`stockpileAmpel`/`worstAmpel`), and
 * the contract-side matching math (`hullGateSatisfied`/`buildContractIst`/
 * `computeDeviations`/`pairWrongVariants`/`matchScore`/
 * `clearsMatchThreshold`/`contractStatus`/`contractAmpel`) that an earlier
 * pass on this platform deliberately left unported for lack of a caller
 * (no ESI contract sync existed yet). [ContractSync.kt] is that caller now -
 * see its own docstring for the orchestration this pure math feeds into
 * (hull-gate + Ist multiset + deviation table + wrong-variant pairing +
 * weighted match score + status, exactly desktop's B.2-B.7), and
 * `ContractHistory.kt`/`DoctrineContractHistoryEntity.kt` for the permanent
 * store its matched-and-finished output lands in.
 *
 * No storage, no network - same pure-function contract the desktop module's
 * own docstring documents. */
object DoctrineValidation {

    // ---------------------------------------------------------- contract-side models
    /** validation.py's `ContractItemRow` - one line of a contract's item
     * list, exactly [com.pappmichel.evetraderlocal.data.esi.EsiContractItem]'s
     * fields but as `Double` quantity (matching every other Soll/Ist map in
     * this object, which are all `Map<Int, Double>`) and carrying its own
     * `contractId` (ESI's item-list response doesn't repeat it per line, but
     * every deviation/matching function below is easier to call with it
     * already attached, same shape the desktop dataclass uses). */
    data class ContractItemRow(
        val contractId: Long,
        val recordId: Long,
        val typeId: Int,
        val quantity: Double,
        val isIncluded: Boolean,
        val isSingleton: Boolean,
    )

    /** validation.py's `DeviationRow` - one (typeId, kind) deviation on one
     * contract. */
    data class DeviationRow(
        val contractId: Long,
        val typeId: Int,
        val kind: String,
        val severity: String,
        val expectedQty: Double = 0.0,
        val actualQty: Double = 0.0,
    )

    const val DEVIATION_KIND_MISSING = "missing"
    const val DEVIATION_KIND_SHORT = "short"
    const val DEVIATION_KIND_EXTRA = "extra"
    const val DEVIATION_KIND_WRONG_VARIANT = "wrong_variant"

    const val VALIDATION_VALID = "valid"
    const val VALIDATION_TOLERABLE = "tolerable"
    const val VALIDATION_INVALID = "invalid"
    const val VALIDATION_UNMATCHED = "unmatched"

    // constants.py's MATCH_WEIGHT_EXACT/MATCH_WEIGHT_CONSUME/MATCH_THRESHOLD.
    const val MATCH_WEIGHT_EXACT = 0.8
    const val MATCH_WEIGHT_CONSUME = 0.2
    const val MATCH_THRESHOLD = 0.5

    // Slot-section vocabulary (doctrine/constants.py's EXACT_SECTIONS/
    // CONSUME_SECTIONS) - duplicated here rather than imported from
    // EftFittingParser's own private constants, matching the desktop
    // codebase's own "small curated constants, independently duplicated
    // rather than cross-imported" convention (see constants.py's docstring).
    val EXACT_SECTIONS = setOf("low", "med", "high", "rig", "subsystem", "service")
    val CONSUME_SECTIONS = setOf("drone", "cargo", "charge", "fuelbay", "shipmaintenancebay")

    /** validation.py's `_merge_charges_into_consume` + `build_contract_soll`:
     * returns (exactSoll, consumeSoll) for one fitting's items, hull
     * deliberately excluded (it's a separate matching gate on desktop, not
     * relevant to the stockpile Soll callers here - [buildStockpileSoll]
     * adds it back in itself). A "charge" line gets a Soll floor of 1,
     * merged with (not added to) any existing cargo/drone quantity for that
     * same type_id. */
    fun buildContractSoll(items: List<SavedFittingItem>): Pair<Map<Int, Double>, Map<Int, Double>> {
        val exact = mutableMapOf<Int, Double>()
        val consume = mutableMapOf<Int, Double>()
        val chargeTypeIds = mutableSetOf<Int>()
        for (item in items) {
            when {
                item.slotSection in EXACT_SECTIONS -> exact[item.typeId] = (exact[item.typeId] ?: 0.0) + item.quantity
                item.slotSection == "charge" -> chargeTypeIds.add(item.typeId)
                item.slotSection in CONSUME_SECTIONS -> consume[item.typeId] = (consume[item.typeId] ?: 0.0) + item.quantity
            }
        }
        for (typeId in chargeTypeIds) {
            if ((consume[typeId] ?: 0.0) < 1) consume[typeId] = 1.0
        }
        return exact to consume
    }

    /** What class (exact/consume) a required type_id belongs to, plus the
     * required quantity - the Android counterpart of validation.py's
     * `build_stockpile_soll`. `totalSets = stockpileTarget + max(0,
     * contractTarget - validContracts)` (GitHub issue #36's additive rule -
     * see the desktop function's own docstring): the spare-stock buffer and
     * whatever's still needed to close a contract-target shortfall are both
     * real, simultaneous demand. */
    fun buildStockpileSoll(
        items: List<SavedFittingItem>,
        hullTypeId: Int,
        stockpileTarget: Int,
        contractTarget: Int = 0,
        validContracts: Int = 0,
    ): Map<Int, Pair<Double, String>> {
        val totalSets = stockpileTarget + maxOf(0, contractTarget - validContracts)
        val (exact, consume) = buildContractSoll(items)
        val exactWithHull = exact.toMutableMap()
        exactWithHull[hullTypeId] = (exactWithHull[hullTypeId] ?: 0.0) + 1.0
        val result = mutableMapOf<Int, Pair<Double, String>>()
        for ((typeId, qty) in exactWithHull) result[typeId] = (qty * totalSets) to "exact"
        for ((typeId, qty) in consume) result[typeId] = (qty * totalSets) to "consume"
        return result
    }

    /** validation.py's `allocate_stockpile` (C.2): greedy priority
     * allocation - `orderedFittingSoll` is [(fittingId, {typeId: (required,
     * itemClass)})] in priority order (highest first, same order the caller
     * lists fittings in). Returns {fittingId: {typeId: allocatedQty}}. */
    fun allocateStockpile(
        orderedFittingSoll: List<Pair<String, Map<Int, Pair<Double, String>>>>,
        availableByType: Map<Int, Double>,
    ): Map<String, Map<Int, Double>> {
        val remaining = availableByType.toMutableMap()
        val allocation = mutableMapOf<String, MutableMap<Int, Double>>()
        for ((fittingId, soll) in orderedFittingSoll) {
            val forFitting = mutableMapOf<Int, Double>()
            for ((typeId, pair) in soll) {
                val (required, _cls) = pair
                val have = remaining[typeId] ?: 0.0
                val take = minOf(required, have)
                forFitting[typeId] = take
                remaining[typeId] = have - take
            }
            allocation[fittingId] = forFitting
        }
        return allocation
    }

    /** validation.py's `stockpile_deviation` (C.3): returns (shortfall,
     * severity) - severity is null when allocated fully covers the
     * tolerance-adjusted Soll. "exact" class items get zero cargo tolerance
     * (any shortfall is critical); "consume" class items get a
     * ceil(cargoTolerancePct * required) floor below which they're still
     * critical, and only "tolerable" between that floor and the full
     * required amount. */
    fun stockpileDeviation(
        requiredTotal: Double,
        allocated: Double,
        itemClass: String,
        cargoTolerancePct: Double,
    ): Pair<Double, String?> {
        val shortfall = maxOf(0.0, requiredTotal - allocated)
        if (shortfall <= 0.0) return 0.0 to null
        if (itemClass == "exact") return shortfall to SEVERITY_CRITICAL
        val threshold = ceil(cargoTolerancePct * requiredTotal)
        if (allocated <= 0.0) return shortfall to SEVERITY_CRITICAL
        if (allocated < threshold) return shortfall to SEVERITY_CRITICAL
        return shortfall to SEVERITY_TOLERABLE
    }

    const val SEVERITY_CRITICAL = "critical"
    const val SEVERITY_TOLERABLE = "tolerable"
    const val SEVERITY_INFO = "info"
    private val SEVERITY_RANK = mapOf(SEVERITY_CRITICAL to 2, SEVERITY_TOLERABLE to 1, SEVERITY_INFO to 0)

    const val AMPEL_GREEN = "green"
    const val AMPEL_YELLOW = "yellow"
    const val AMPEL_RED = "red"
    const val AMPEL_GRAY = "gray"
    private val AMPEL_SEVERITY_ORDER = listOf(AMPEL_GREEN, AMPEL_YELLOW, AMPEL_RED)

    /** validation.py's `worst_severity` - worst-of aggregation across
     * several fittings' rows for the same item (null never participates: no
     * deviation at all; if every input is null, the result is null). */
    fun worstSeverity(severities: List<String?>): String? {
        val real = severities.filterNotNull()
        if (real.isEmpty()) return null
        return real.maxByOrNull { SEVERITY_RANK.getValue(it) }
    }

    /** validation.py's `stockpile_ampel` (B.8). */
    fun stockpileAmpel(assetsAvailable: Boolean, stockpileTarget: Int, worstSeverity: String?): String {
        if (!assetsAvailable) return AMPEL_GRAY
        if (stockpileTarget == 0) return AMPEL_GRAY
        if (worstSeverity == SEVERITY_CRITICAL) return AMPEL_RED
        if (worstSeverity == SEVERITY_TOLERABLE) return AMPEL_YELLOW
        return AMPEL_GREEN
    }

    /** validation.py's `worst_ampel` - gray never participates; all-gray
     * input returns gray. */
    fun worstAmpel(ampels: List<String>): String {
        val nonGray = ampels.filter { it != AMPEL_GRAY }
        if (nonGray.isEmpty()) return AMPEL_GRAY
        return nonGray.maxByOrNull { AMPEL_SEVERITY_ORDER.indexOf(it) }!!
    }

    // ---------------------------------------------------------- contract matching gate + Ist (B.2/B.3)
    /** validation.py's `hull_gate_satisfied` (B.2): the hull must be present
     * and `isIncluded`. Deliberately does NOT also require `isSingleton` -
     * confirmed live on desktop (2026-08-19) against a real contract whose
     * genuinely assembled/fitted ship still came back `isSingleton=false`
     * from ESI's contract-items endpoint (unlike the asset endpoints, where
     * that field reliably means assembled-vs-packaged for ships) - a real
     * fitted hull with correctly slot-labeled modules was wrongly excluded
     * by that stricter check. No public ESI signal reliably distinguishes
     * "packaged kit" from "assembled ship" at the contract-items level, so
     * this only gates on the hull type actually being part of the deal. */
    fun hullGateSatisfied(contractItems: List<ContractItemRow>, hullTypeId: Int): Boolean =
        contractItems.any { it.typeId == hullTypeId && it.isIncluded }

    /** validation.py's `build_contract_ist` (B.3): Ist multiset from
     * `isIncluded=true` items, hull removed. ESI's contract-items response
     * has no damage/durability field at all (unlike the in-game contract
     * window's "NN% damaged" display), so several separately-stacked lines
     * of the same charge type_id (each a different in-game damage %) just
     * sum together here like any other stack - nothing to filter out. */
    fun buildContractIst(contractItems: List<ContractItemRow>, hullTypeId: Int): Map<Int, Double> {
        val ist = mutableMapOf<Int, Double>()
        for (ci in contractItems) {
            if (ci.isIncluded && ci.typeId != hullTypeId) ist[ci.typeId] = (ist[ci.typeId] ?: 0.0) + ci.quantity
        }
        return ist
    }

    // ---------------------------------------------------------- deviations (B.4)
    /** validation.py's `compute_deviations` (B.4), plus B.3's
     * `isIncluded=false` "requested consideration" extras. Returns at most
     * one [DeviationRow] per (typeId, kind) pair - a higher-severity find
     * for the same pair overwrites a lower one already recorded, same as
     * the desktop function's own `_add` helper. */
    fun computeDeviations(
        contractId: Long,
        exactSoll: Map<Int, Double>,
        consumeSoll: Map<Int, Double>,
        contractItems: List<ContractItemRow>,
        hullTypeId: Int,
        cargoTolerancePct: Double,
        strictExtras: Boolean = false,
    ): List<DeviationRow> {
        val ist = buildContractIst(contractItems, hullTypeId)
        val rows = mutableMapOf<Pair<Int, String>, DeviationRow>()
        val severityRank = mapOf(SEVERITY_CRITICAL to 2, SEVERITY_TOLERABLE to 1, SEVERITY_INFO to 0)

        fun add(typeId: Int, kind: String, severity: String, expected: Double, actual: Double) {
            val key = typeId to kind
            val existing = rows[key]
            if (existing == null || severityRank.getValue(severity) > severityRank.getValue(existing.severity)) {
                rows[key] = DeviationRow(contractId, typeId, kind, severity, expected, actual)
            }
        }

        val allTypeIds = exactSoll.keys + consumeSoll.keys + ist.keys
        for (typeId in allTypeIds) {
            val eExact = exactSoll[typeId] ?: 0.0
            val eConsume = consumeSoll[typeId] ?: 0.0
            val a = ist[typeId] ?: 0.0

            if (eExact > 0) {
                if (a <= 0) add(typeId, DEVIATION_KIND_MISSING, SEVERITY_CRITICAL, eExact, 0.0)
                else if (a < eExact) add(typeId, DEVIATION_KIND_SHORT, SEVERITY_CRITICAL, eExact, a)
            }

            if (eConsume > 0) {
                val threshold = ceil(cargoTolerancePct * eConsume)
                if (a <= 0) add(typeId, DEVIATION_KIND_MISSING, SEVERITY_CRITICAL, eConsume, 0.0)
                else if (a < threshold) add(typeId, DEVIATION_KIND_SHORT, SEVERITY_CRITICAL, eConsume, a)
                else if (a < eConsume) add(typeId, DEVIATION_KIND_SHORT, SEVERITY_TOLERABLE, eConsume, a)
            }

            if (eExact <= 0 && eConsume <= 0 && a > 0) {
                add(typeId, DEVIATION_KIND_EXTRA, if (strictExtras) SEVERITY_TOLERABLE else SEVERITY_INFO, 0.0, a)
            }
        }

        for (ci in contractItems) {
            if (!ci.isIncluded) add(ci.typeId, DEVIATION_KIND_EXTRA, SEVERITY_INFO, 0.0, ci.quantity)
        }

        return rows.values.toList()
    }

    /** validation.py's `pair_wrong_variants` (B.6): re-labels a
     * (missing/short, extra) pair as `wrong_variant` when both types share
     * the same `groupIdOf` AND the same `slotOf` classification. Greedy,
     * group-id-sorted pairing - a leftover in a group keeps its original
     * kind. The expected side stays critical (a different module is a
     * different product); the delivered side becomes info.
     *
     * **`slotOf` is honest about the same SDE gap [DoctrineSdeResolver]
     * already documents**: this platform's SDE cache has no
     * `dgmTypeEffects`-derived per-type slot table, so a real `slotOf`
     * would need the same not-yet-cached data the fitting parser's own
     * `resolveSlot` already always returns null for - callers here are
     * expected to pass an equally-always-null `slotOf` (see
     * `ContractSync.kt`'s own call site), which makes this pairing key off
     * `groupIdOf` alone in practice (both sides' `slotOf` compare
     * null==null, always true) rather than genuinely requiring the same
     * slot too. That's a strictly *more* permissive pairing than desktop's
     * (an occasional false "wrong_variant" between same-group items that
     * are not actually interchangeable in the same slot), not a crash or a
     * silently-wrong "yes they matched" - documented here rather than
     * quietly narrowed. */
    fun pairWrongVariants(
        deviations: List<DeviationRow>,
        groupIdOf: (Int) -> Int?,
        slotOf: (Int) -> String?,
    ): List<DeviationRow> {
        val missingLike = deviations.filter { it.kind == DEVIATION_KIND_MISSING || it.kind == DEVIATION_KIND_SHORT }
        val extras = deviations.filter { it.kind == DEVIATION_KIND_EXTRA && it.expectedQty == 0.0 }
        val others = deviations.filter { it !in missingLike && it !in extras }

        val usedExtraIds = mutableSetOf<Int>()
        val result = mutableListOf<DeviationRow>()
        for (m in missingLike.sortedBy { groupIdOf(it.typeId) ?: 0 }) {
            val mGroup = groupIdOf(m.typeId)
            val mSlot = slotOf(m.typeId)
            val paired = if (mGroup != null) {
                extras.firstOrNull { e -> e.typeId !in usedExtraIds && groupIdOf(e.typeId) == mGroup && slotOf(e.typeId) == mSlot }
            } else null
            if (paired != null) {
                usedExtraIds.add(paired.typeId)
                result.add(DeviationRow(m.contractId, m.typeId, DEVIATION_KIND_WRONG_VARIANT, SEVERITY_CRITICAL, m.expectedQty, m.actualQty))
                result.add(DeviationRow(paired.contractId, paired.typeId, DEVIATION_KIND_WRONG_VARIANT, SEVERITY_INFO, paired.expectedQty, paired.actualQty))
            } else {
                result.add(m)
            }
        }
        result.addAll(extras.filter { it.typeId !in usedExtraIds })
        result.addAll(others)
        return result
    }

    /** validation.py's `contract_status` (B.7). */
    fun contractStatus(deviations: List<DeviationRow>, matched: Boolean): String {
        if (!matched) return VALIDATION_UNMATCHED
        if (deviations.any { it.severity == SEVERITY_CRITICAL }) return VALIDATION_INVALID
        if (deviations.any { it.severity == SEVERITY_TOLERABLE }) return VALIDATION_TOLERABLE
        return VALIDATION_VALID
    }

    // ---------------------------------------------------------- matching score (B.5)
    private fun multisetOverlap(soll: Map<Int, Double>, ist: Map<Int, Double>): Double {
        if (soll.isEmpty()) return 1.0
        val totalExpected = soll.values.sum()
        if (totalExpected <= 0) return 1.0
        val overlap = soll.entries.sumOf { (typeId, qty) -> minOf(qty, ist[typeId] ?: 0.0) }
        return overlap / totalExpected
    }

    /** validation.py's `match_score` (B.5 step 2): weighted multiset-overlap
     * score for one fitting candidate against one contract's Ist multiset
     * (hull already gated/removed by the caller - see [hullGateSatisfied]/
     * [buildContractIst]). */
    fun matchScore(exactSoll: Map<Int, Double>, consumeSoll: Map<Int, Double>, ist: Map<Int, Double>): Double {
        val sExact = multisetOverlap(exactSoll, ist)
        val sConsume = multisetOverlap(consumeSoll, ist)
        return MATCH_WEIGHT_EXACT * sExact + MATCH_WEIGHT_CONSUME * sConsume
    }

    fun clearsMatchThreshold(score: Double): Boolean = score >= MATCH_THRESHOLD

    // ---------------------------------------------------------- contract ampel (B.8)
    /** validation.py's `contract_ampel` (B.8). GitHub issue #66's simplified
     * form (the redundant `>= contractTarget` disjunct removed on desktop -
     * ported already-simplified, there's no reason to reintroduce it here). */
    fun contractAmpel(lastSyncedAt: String?, contractTarget: Int, validContracts: Int, tolerableContracts: Int): String {
        if (lastSyncedAt == null) return AMPEL_GRAY
        if (contractTarget == 0) return AMPEL_GRAY
        if (validContracts >= contractTarget) return AMPEL_GREEN
        if (validContracts + tolerableContracts > 0) return AMPEL_YELLOW
        return AMPEL_RED
    }
}
