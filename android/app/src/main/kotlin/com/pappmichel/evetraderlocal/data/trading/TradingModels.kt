package com.pappmichel.evetraderlocal.data.trading

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
