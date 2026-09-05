package com.pappmichel.evetraderlocal.data.doctrine

import com.pappmichel.evetraderlocal.data.esi.CharacterAsset
import com.pappmichel.evetraderlocal.data.esi.EsiClient

/** Doctrine -> Stockpile Status: "how much of each fitting's items do I have
 * vs. need", the Android counterpart of `doctrine/engine.py`'s
 * `stockpile_rows_for_doctrine`/`aggregate_stockpile_rows`, built on top of
 * [DoctrineValidation]'s ported pure math and [DoctrineFittingRepository]'s
 * saved fittings.
 *
 * **One remaining deliberate simplification vs. the desktop engine (a second
 * one - no contract-target multiplier - was removed once [ContractSync]
 * landed; see [computeRows]'s own doc on how `validContractsByFitting` now
 * feeds that multiplier for real):**
 *
 * **`availableByType` is caller-supplied, not a fixed single-location
 * ESI/SDE read.** Desktop reads `storage.esi_stock_at_location` for one
 * configured `stockpile_location_id`. This platform has no persisted
 * Doctrine location config yet, so [fetchAvailableQuantities] below sums
 * a character's *entire* asset list by type_id (every location the
 * character has visibility into) unless a `locationId` filter is passed -
 * a real multi-location, multi-character deployment could double-count
 * stock sitting somewhere that isn't the actual stockpile hangar. The
 * screen surfaces this plainly and offers manual entry as a fallback for
 * exactly the cases where that matters (see [DoctrineStockpileStatusScreen]). */
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
     * bare type_id string.
     *
     * `validContractsByFitting` is the real contract-target multiplier
     * (GitHub issue #36, `DoctrineValidation.buildStockpileSoll`'s own
     * `validContracts` parameter) - a fitting's count of currently
     * outstanding-and-`valid`-status matched contracts, from
     * [ContractSync.SyncOutcome.activeContracts] (see
     * [DoctrineStockpileStatusScreen]'s wiring). Missing/never-synced
     * entries default to 0, same as `contractTarget`'s own "currently active
     * demand as if nothing has synced yet" fallback before this multiplier
     * existed. */
    fun computeRows(
        fittings: List<SavedFitting>,
        availableByType: Map<Int, Double>,
        typeName: (Int) -> String,
        cargoTolerancePctDefault: Double = 0.9,
        validContractsByFitting: Map<String, Int> = emptyMap(),
    ): List<Row> {
        if (fittings.isEmpty()) return emptyList()

        val sollByFitting: List<Pair<String, Map<Int, Pair<Double, String>>>> = fittings.map { f ->
            f.fittingId to DoctrineValidation.buildStockpileSoll(
                items = f.items, hullTypeId = f.hullTypeId, stockpileTarget = f.stockpileTarget,
                contractTarget = f.contractTarget, validContracts = validContractsByFitting[f.fittingId] ?: 0,
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
