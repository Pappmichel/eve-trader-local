package com.pappmichel.evetraderlocal.data.refining

import com.pappmichel.evetraderlocal.data.esi.OrderStats
import com.pappmichel.evetraderlocal.data.sde.SdeRepository
import com.pappmichel.evetraderlocal.data.trading.TradingConfig

/** Reprocessing-tab quote calculation, ported from the desktop build's
 * `refining/quote.py` (GitHub issue #92 in the parent repo). This is the one
 * Ore & Minerals view this port scopes in - see this file's own "scope"
 * section below for why, and `PasteParser.kt`/`ReprocessingYield.kt` for the
 * two other pieces it's built from.
 *
 *     Sell-as-is value = quantity x C-J sell percentile x structureSellHaircut
 *     Refined value     = mineral yield (scrapmetalYield, portion-size-rounded)
 *                          x mineral C-J sell percentile x structureSellHaircut - Refining Tax
 *     Recommendation    = "Reprocess" if Refined value > Sell-as-is value, else "Sell instead"
 *                          (both numbers always shown - nothing is auto-decided/excluded)
 *
 * Both options end with a C-J sell order, so both incur the same
 * `structureSellHaircut` (broker fee + sales tax + SCC surcharge) - the
 * desktop build's own confirmed fix (2026-08-29): sell-as-is used to be a
 * bare gross `quantity x price` with no haircut at all, while the refined
 * side already netted it out on the mineral side, systematically biasing
 * borderline items toward "Sell instead" even when reprocessing was
 * actually the better outcome. `ReprocessingQuoteTest` ports the desktop
 * regression test for this case-for-case.
 *
 * **Scope decision (why this view, not Ore Shortlist or Mineral Shopping
 * List):** this is the only one of the three Ore & Minerals views whose
 * desktop math is fully covered by real pure-function tests
 * (`test_refining_quote.py`, `test_refining_paste_parser.py`,
 * `test_refining_reprocessing.py`) and needs only one new piece of SDE data
 * (`invTypeMaterials`/`portionSize`, added in this same change - see
 * `SdeRepository.kt`) rather than a whole new candidate-discovery pipeline.
 * Ore Shortlist (`pricing.py`) needs the *ore/ice* yield path (structure/
 * rig/security/implant/per-family skills - deliberately not ported, see
 * `ReprocessingYield.kt`) plus `candidate_discovery.py`'s auto-derived
 * compressed-ore/ice universe; Mineral Shopping List
 * (`optimizer.py`/`models.py`'s `ShoppingListPlan`) needs a real LP solver
 * with no Kotlin/JVM-friendly equivalent evaluated yet. Both stay
 * `PlaceholderScreen` for now.
 *
 * **Simplification vs. desktop's own `do_quote_reprocessing`:** no
 * Goonmetrics current-price fallback when no seller token is available -
 * this port's own Shortlist/Undercut screens document the identical gap
 * (see `Shortlist.kt`'s module docstring) for the same reason: nothing on
 * this platform wires `structure_order_stats_bulk_or_goonmetrics`'s
 * fallback branch yet. Without a seller login this screen simply reports no
 * structure price data, same "unknown, not free" degrade as every other
 * priced row here. */

const val REPROCESS_DECISION = "Reprocess"
const val SELL_DECISION = "Sell instead"
const val NOT_REPROCESSABLE_DECISION = "Not reprocessable"
const val UNRESOLVED_DECISION = "Unknown item"
const val NO_MARKET_DATA_DECISION = "No market data"

data class ReprocessingQuoteRow(
    val name: String,
    val quantity: Int,
    val typeId: Int?,
    val category: String,
    val sellAsIsValue: Double?,
    val refinedValue: Double?,
    val mineralValue: Double?,
    val refiningTax: Double?,
    val decision: String,
    val error: String? = null,
)

/** Exact (case-insensitive) match against the SDE - a paste line's own name
 * field should already be a real EVE item name, so a fuzzy/substring match
 * risks silently resolving to the wrong item; better to report "unknown"
 * and let the user fix a typo than guess. */
suspend fun resolveTypeId(sde: SdeRepository, name: String): Int? = sde.resolveTypeIdByName(name.trim())

/** Every distinct `material_type_id` any of `typeIds`' portion-size batch
 * reprocesses into - a small, shared set worth fetching order-book stats for
 * once, rather than per-line. */
suspend fun mineralTypeIdsForLines(sde: SdeRepository, typeIds: List<Int>): List<Int> {
    val ids = sortedSetOf<Int>()
    for (typeId in typeIds) {
        sde.typeMaterials(typeId).forEach { (materialTypeId, _) -> ids.add(materialTypeId) }
    }
    return ids.toList()
}

/** Pure - `itemStats`/`mineralStatsById`, `portionSize` and `materials` are
 * all pre-fetched by the caller (see `ReprocessingQuoteScreen.kt`), no
 * network/SDE calls in here at all. This is the one difference from
 * desktop's `evaluate_reprocessing_line`, which is pure apart from two plain
 * SDE reads (`get_portion_size`/`get_type_materials`) done inline - those
 * are `suspend` on Android, so the caller does them first and hands the raw
 * results in, keeping this function itself fully synchronous and
 * table-driven for `ReprocessingQuoteTest`.
 *
 * `portionSize`/`materials` are deliberately the *raw* SDE table (not an
 * already-applied yield map): "not reprocessable at all" (no portion size,
 * no material rows) and "reprocessable, but this quantity/yield% happens to
 * floor to zero of every mineral" are different outcomes on desktop (the
 * first is `Not reprocessable`, the second falls through to
 * `No market data`) - collapsing them to one pre-applied map here would lose
 * that distinction. */
fun evaluateReprocessingLine(
    line: ParsedPasteLine,
    typeId: Int?,
    portionSize: Int?,
    materials: List<Pair<Int, Double>>,
    itemStats: OrderStats?,
    mineralStatsById: Map<Int, OrderStats>,
    tradingCfg: TradingConfig,
    refiningCfg: RefiningConfig,
): ReprocessingQuoteRow {
    if (line.error != null) {
        return ReprocessingQuoteRow(
            name = line.name, quantity = line.quantity, typeId = null, category = line.category,
            sellAsIsValue = null, refinedValue = null, mineralValue = null, refiningTax = null,
            decision = UNRESOLVED_DECISION, error = line.error,
        )
    }

    if (typeId == null) {
        return ReprocessingQuoteRow(
            name = line.name, quantity = line.quantity, typeId = null, category = line.category,
            sellAsIsValue = null, refinedValue = null, mineralValue = null, refiningTax = null,
            decision = UNRESOLVED_DECISION, error = "No exact match in the SDE for this item name.",
        )
    }

    val sellAsIsValue = itemStats?.sellPercentile?.let { it * line.quantity * tradingCfg.structureSellHaircut }

    if (portionSize == null || portionSize <= 0 || materials.isEmpty()) {
        return ReprocessingQuoteRow(
            name = line.name, quantity = line.quantity, typeId = typeId, category = line.category,
            sellAsIsValue = sellAsIsValue, refinedValue = null, mineralValue = null, refiningTax = null,
            decision = NOT_REPROCESSABLE_DECISION,
        )
    }

    val yieldPct = scrapmetalYield(refiningCfg)
    val minerals = applyYield(portionSize, materials, line.quantity, yieldPct)

    var mineralValue = 0.0
    var haveFullMineralData = minerals.isNotEmpty()
    for ((materialTypeId, qty) in minerals) {
        val stats = mineralStatsById[materialTypeId]
        if (stats?.sellPercentile == null) {
            haveFullMineralData = false
            continue
        }
        mineralValue += qty * stats.sellPercentile * tradingCfg.structureSellHaircut
    }

    if (sellAsIsValue == null || !haveFullMineralData) {
        return ReprocessingQuoteRow(
            name = line.name, quantity = line.quantity, typeId = typeId, category = line.category,
            sellAsIsValue = sellAsIsValue, refinedValue = null,
            mineralValue = if (haveFullMineralData) mineralValue else null, refiningTax = null,
            decision = NO_MARKET_DATA_DECISION,
        )
    }

    val refiningTax = mineralValue * refiningCfg.refiningTaxRate
    val refinedValue = mineralValue - refiningTax
    val decision = if (refinedValue > sellAsIsValue) REPROCESS_DECISION else SELL_DECISION

    return ReprocessingQuoteRow(
        name = line.name, quantity = line.quantity, typeId = typeId, category = line.category,
        sellAsIsValue = sellAsIsValue, refinedValue = refinedValue, mineralValue = mineralValue,
        refiningTax = refiningTax, decision = decision,
    )
}

/** Totals across every row that came back `Reprocess` (mineral/refined
 * value) plus the sell-as-is total across every priced row, whatever its
 * decision - the counterpart of `do_quote_reprocessing`'s inline `totals`
 * dict on desktop. */
data class ReprocessingQuoteTotals(
    val reprocessCount: Int,
    val totalMineralValue: Double,
    val totalRefinedValue: Double,
    val totalSellAsIsValue: Double,
)

fun reprocessingQuoteTotals(rows: List<ReprocessingQuoteRow>): ReprocessingQuoteTotals {
    val reprocessRows = rows.filter { it.decision == REPROCESS_DECISION }
    return ReprocessingQuoteTotals(
        reprocessCount = reprocessRows.size,
        totalMineralValue = reprocessRows.sumOf { it.mineralValue ?: 0.0 },
        totalRefinedValue = reprocessRows.sumOf { it.refinedValue ?: 0.0 },
        totalSellAsIsValue = rows.mapNotNull { it.sellAsIsValue }.sum(),
    )
}
