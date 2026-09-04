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
)
