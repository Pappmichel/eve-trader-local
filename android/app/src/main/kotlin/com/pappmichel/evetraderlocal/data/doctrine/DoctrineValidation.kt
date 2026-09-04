package com.pappmichel.evetraderlocal.data.doctrine

import kotlin.math.ceil

/** Kotlin port of the desktop build's `doctrine/validation.py` - pure Soll/
 * Ist/deviation/stockpile-allocation math, ported case-for-case against
 * `tests/test_doctrine_validation.py` (see `DoctrineValidationTest.kt`).
 * Only the subset Stockpile Status/Shopping List actually need is ported
 * here: Soll construction (`buildContractSoll`/`buildStockpileSoll`),
 * priority allocation (`allocateStockpile`), per-item deviation severity
 * (`stockpileDeviation`), and the two worst-of aggregators
 * (`worstSeverity`/`stockpileAmpel`/`worstAmpel`). Contract-side matching
 * (`hull_gate_satisfied`/`build_contract_ist`/`compute_deviations`/
 * `pair_wrong_variants`/`match_score`/`contract_status`/`contract_ampel`) is
 * NOT ported - that half needs ESI contract sync + a persisted match result
 * this platform doesn't have (see `ContractHistory.kt`'s own docstring for
 * why Contract History stays a simple unmatched ESI listing instead), so
 * porting it now would be dead code with no caller.
 *
 * No storage, no network - same pure-function contract the desktop module's
 * own docstring documents. */
object DoctrineValidation {

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
}
