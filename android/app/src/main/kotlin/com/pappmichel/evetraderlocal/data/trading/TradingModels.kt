package com.pappmichel.evetraderlocal.data.trading

import kotlinx.serialization.Serializable

/** One row of "Candidate Universe" - same shape as the desktop build's
 * models.py `Candidate` dataclass. */
data class Candidate(
    val item: String,
    val typeId: Int,
    val volumeM3: Double,
    val category: String,
    val marketGroupPath: String,
    val metaLevel: Int? = null,
)

/** One shortlist entry: a candidate item actively tracked for import - the
 * persisted membership list, same shape as the desktop build's models.py
 * `ShortlistItem`. */
@Serializable
data class ShortlistItem(
    val item: String,
    val itemId: Int,
    val category: String,
    val volumeM3: Double,
    val active: Boolean = true,
    val metaLevel: Int? = null,
)

/** One fully evaluated shortlist item - see Shortlist.kt for the formula
 * behind each field, same shape as the desktop build's models.py
 * `ShortlistRow`. */
data class ShortlistRow(
    val item: String,
    val category: String,
    val landedCost: Double?,
    val netSell: Double?,
    val sellVolume: Double?,
    val ownOrdersRemaining: Double,
    val profitPerUnit: Double?,
    val margin: Double?,
    val profitPerM3: Double?,
    val decision: String,
    val active: Boolean,
    /** Real market-wide average daily traded quantity (Goonmetrics region
     * history's `movement`, see `averageMarketDailyVolume`) - never
     * order-book depth (`sellVolume`, which is current listed *depth*, not
     * traded quantity) and never this trader's own realized sales. null
     * means Goonmetrics has no history for this type yet. This is what
     * Profit / Day (`topImportsByDailyProfit`) is computed from. */
    val avgDailyVolume: Double? = null,
)

/** One candidate scored against its own daily Jita/reference-region price
 * history - the counterpart of the desktop build's models.py
 * `NewCandidateResult`, the output of `HistoryBacktest.scoreCandidate`'s
 * hit-rate/avg-movement filter (see that function's own KDoc). */
data class NewCandidateResult(
    val item: String,
    val category: String,
    val typeId: Int,
    val volumeM3: Double,
    val pairedDays: Int,
    val profitableDays: Int,
    val hitRate: Double,
    val latestMargin: Double,
    val bestMargin: Double,
    val avgProfitM3: Double,
    val avgSellMovement: Double,
    val score: Double,
    val recommendation: String,
    val add: Boolean,
    val metaLevel: Int? = null,
)
