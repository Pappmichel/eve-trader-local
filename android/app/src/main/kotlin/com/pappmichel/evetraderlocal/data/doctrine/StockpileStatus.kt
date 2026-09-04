package com.pappmichel.evetraderlocal.data.doctrine

import com.pappmichel.evetraderlocal.data.esi.CharacterAsset
import com.pappmichel.evetraderlocal.data.esi.EsiClient

/** Doctrine -> Stockpile Status: "how much of each fitting's items do I have
 * vs. need", the Android counterpart of `doctrine/engine.py`'s
 * `stockpile_rows_for_doctrine`/`aggregate_stockpile_rows`, built on top of
 * [DoctrineValidation]'s ported pure math and [DoctrineFittingRepository]'s
 * saved fittings.
 *
 * **Two deliberate simplifications vs. the desktop engine, both documented
 * here rather than silently baked in:**
 *
 * 1. **No contract-target multiplier.** Desktop's `build_stockpile_soll`
 *    multiplies Soll by `stockpileTarget + max(0, contractTarget -
 *    validContracts)` - the second term needs a live count of currently
 *    "valid" synced+matched contracts, which needs the full contract-sync/
 *    matching engine (`esi_sync.py` + `validation.py`'s contract-side
 *    functions) this platform does not port (see `ContractHistory.kt`'s own
 *    docstring for why). [DoctrineValidation.buildStockpileSoll] still
 *    accepts `contractTarget`/`validContracts` (so the math is ready the day
 *    contract sync lands), but this file always calls it with
 *    `validContracts = 0` - i.e. `contractTarget` is currently active demand
 *    same as desktop shows before any contract has ever synced. A
 *    `SavedFitting.contractTarget` set to 0 (this platform's every current
 *    fitting, since nothing writes a nonzero one yet) makes this a no-op in
 *    practice; it stops being a no-op the moment a future contract-sync port
 *    starts writing real target values.
 * 2. **`availableByType` is caller-supplied, not a fixed single-location
 *    ESI/SDE read.** Desktop reads `storage.esi_stock_at_location` for one
 *    configured `stockpile_location_id`. This platform has no persisted
 *    Doctrine location config yet, so [fetchAvailableQuantities] below sums
 *    a character's *entire* asset list by type_id (every location the
 *    character has visibility into) unless a `locationId` filter is passed -
 *    a real multi-location, multi-character deployment could double-count
 *    stock sitting somewhere that isn't the actual stockpile hangar. The
 *    screen surfaces this plainly and offers manual entry as a fallback for
 *    exactly the cases where that matters (see [DoctrineStockpileStatusScreen]). */
object StockpileStatus {

    /** One (fitting, type_id) requirement/availability row - mirrors
     * `doctrine/models.py`'s `StockpileRow`, minus `doctrineId`/
     * `doctrineName` (no doctrine-grouping screen exists on this platform
     * yet - see [SavedFitting]'s own docstring). */
    data class Row(
        val fittingId: String,
        val fittingName: String,
        val typeId: Int,
        val typeName: String,
        val slotSection: String,
        val requiredTotal: Double,
        val available: Double,
        val shortfall: Double,
        val severity: String?,
    )

    /** The same item required by several fittings, summed into one row -
     * mirrors `AggregatedStockpileRow`. Summing `available`/`shortfall`
     * across rows for the same type_id is safe because [allocate] already
     * partitions the physical stock across fittings by priority order
     * before this is computed - no row's `available` double-counts
     * another's (same reasoning `aggregate_stockpile_rows`'s own docstring
     * gives). */
    data class AggregatedRow(
        val typeId: Int,
        val typeName: String,
        val requiredTotal: Double,
        val available: Double,
        val shortfall: Double,
        val severity: String?,
        val fittingCount: Int,
    )

    /** Computes per-fitting Soll, allocates the shared `availableByType`
     * pool across fittings by list order (= priority, highest first - same
     * convention `engine.load_match_candidates`'s own docstring documents),
     * and returns one [Row] per (fitting, type_id) with a real requirement.
     * `typeName` resolves display names; a lookup miss falls back to the
     * bare type_id string. */
    fun computeRows(
        fittings: List<SavedFitting>,
        availableByType: Map<Int, Double>,
        typeName: (Int) -> String,
        cargoTolerancePctDefault: Double = 0.9,
    ): List<Row> {
        if (fittings.isEmpty()) return emptyList()

        val sollByFitting: List<Pair<String, Map<Int, Pair<Double, String>>>> = fittings.map { f ->
            f.fittingId to DoctrineValidation.buildStockpileSoll(
                items = f.items, hullTypeId = f.hullTypeId, stockpileTarget = f.stockpileTarget,
                contractTarget = f.contractTarget, validContracts = 0,
            )
        }
        val allocation = DoctrineValidation.allocateStockpile(sollByFitting, availableByType)

        val rows = mutableListOf<Row>()
        for (f in fittings) {
            val soll = sollByFitting.first { it.first == f.fittingId }.second
            val alloc = allocation.getValue(f.fittingId)
            val tolerance = f.cargoTolerancePct ?: cargoTolerancePctDefault
            for ((typeId, pair) in soll) {
                val (required, itemClass) = pair
                val allocated = alloc[typeId] ?: 0.0
                val (shortfall, severity) = DoctrineValidation.stockpileDeviation(required, allocated, itemClass, tolerance)
                val slotSection = if (typeId == f.hullTypeId) "hull" else itemClass
                rows.add(Row(
                    fittingId = f.fittingId, fittingName = f.name, typeId = typeId, typeName = typeName(typeId),
                    slotSection = slotSection, requiredTotal = required, available = allocated,
                    shortfall = shortfall, severity = severity,
                ))
            }
        }
        return rows
    }

    /** Mirrors `aggregate_stockpile_rows`: combines every fitting's row for
     * the same type_id, worst-severity-wins, sorted by shortfall descending
     * (biggest gap first). */
    fun aggregate(rows: List<Row>): List<AggregatedRow> {
        val byType = rows.groupBy { it.typeId }
        return byType.map { (typeId, group) ->
            AggregatedRow(
                typeId = typeId, typeName = group.first().typeName,
                requiredTotal = group.sumOf { it.requiredTotal },
                available = group.sumOf { it.available },
                shortfall = group.sumOf { it.shortfall },
                severity = DoctrineValidation.worstSeverity(group.map { it.severity }),
                fittingCount = group.size,
            )
        }.sortedByDescending { it.shortfall }
    }

    /** Fetches a character's owned quantity of every type_id, summed across
     * every asset location visible to that token unless `locationId` is
     * given (see this object's own docstring, simplification 2) - the
     * Android counterpart of desktop's `storage.esi_stock_at_location`,
     * built on the already-ported [EsiClient.characterAssets] rather than a
     * new endpoint. */
    suspend fun fetchAvailableQuantities(
        esi: EsiClient,
        characterId: Long,
        accessToken: String,
        locationId: Long? = null,
    ): Map<Int, Double> {
        val assets: List<CharacterAsset> = esi.characterAssets(characterId, accessToken)
        val filtered = if (locationId != null) assets.filter { it.locationId == locationId } else assets
        val result = mutableMapOf<Int, Double>()
        for (a in filtered) result[a.typeId] = (result[a.typeId] ?: 0.0) + a.quantity
        return result
    }
}
