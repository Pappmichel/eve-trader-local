package com.pappmichel.evetraderlocal.data.trading

import com.pappmichel.evetraderlocal.data.history.HistoryPoint

/** Margin-momentum trends from region price history - a Kotlin port of the
 * `compute_margin_trends` half of the desktop build's history_backtest.py.
 * The candidate-scoring/backtest half of that module (`_score_candidate`,
 * `select_candidate_window`) is a different feature and isn't ported here.
 *
 * The per-day formula is the same landed-cost/net-sell shape the desktop
 * module shares with `_latest_margin`/`_score_candidate` - and the same
 * shape shortlist evaluation uses against live order-book percentiles, just
 * fed *daily region averages* instead:
 *
 *     landed  = jitaAvgPrice * (1 + jitaBuyBrokerFee) + volumeM3 * importCostPerM3
 *     netSell = refAvgPrice * structureSellHaircut
 *     margin  = (netSell - landed) / landed
 *
 * A region-wide daily average is a deliberately different (smoother) input
 * from a live order-book snapshot - it answers "is this item trending
 * better or worse than it has been", not "what can I buy it for right now".
 *
 * No storage coupling: history comes in from the caller (on Android, freshly
 * fetched by GoonmetricsClient on the user's Refresh press - see
 * PriceHistoryScreen.kt for why this platform fetches instead of reading a
 * cache), volumes come in from the shortlist, results go back out. Same
 * "caller wires the data in" split history_backtest.py itself documents. */
object HistoryBacktest {
    // Minimum paired (jita+reference, same date) history days required before
    // a trend is reported at all - below this a "3-day vs 30-day average"
    // split is too noisy/coincidental to mean anything (e.g. 4 days total
    // gives a 3-day window that's almost the whole sample, not a real
    // "recent" slice).
    const val MIN_TREND_HISTORY_DAYS = 6
    const val RECENT_WINDOW_DAYS = 3
    const val BASELINE_WINDOW_DAYS = 30

    // trendPct divides by baselineAvgMargin - confirmed live in the parent
    // repo (2026-07-15) that an exact-zero-only guard isn't enough: items
    // whose baseline margin merely hovers near breakeven (e.g. 0.0006,
    // -0.001) produced trendPct in the thousands of percent (one live case:
    // +26,486%) even though both the recent and baseline margins
    // individually were perfectly ordinary numbers - the ratio itself is
    // what's unstable near zero, not the inputs. 0.02 is also the width of
    // the "stable" dead-zone the parent's UI already treats as noise; a
    // baseline inside that band has no meaningful direction to measure a
    // percentage change against, so it's omitted rather than shown.
    const val MIN_BASELINE_MARGIN_MAGNITUDE = 0.02

    /** Momentum signal inspired by comparable EVE trading tools (3-day vs
     * 30-day VWAP trend detection): for every type_id in `volumes`, compares
     * the average landed-cost margin over the most recent RECENT_WINDOW_DAYS
     * against the average over the last BASELINE_WINDOW_DAYS (which includes
     * those same recent days - the same "rolling window" convention the
     * inspiring tools use, not a disjoint "recent vs everything before it"
     * split).
     *
     * Omits a type_id wherever there isn't enough paired history yet - a
     * brand-new shortlist item with only a day or two of history has no
     * trend to report, not a fabricated one - and omits one whose baseline
     * margin sits within MIN_BASELINE_MARGIN_MAGNITUDE of zero (see that
     * constant's own comment for the live incident behind it). A day only
     * counts when *both* regions have a price for it. */
    fun computeMarginTrends(
        history: List<HistoryPoint>,
        volumes: Map<Int, Double>,
        config: TradingConfig,
    ): Map<Int, MarginTrend> {
        val jitaPrice = mutableMapOf<Pair<Int, String>, Double>()
        val refPrice = mutableMapOf<Pair<Int, String>, Double>()
        for (point in history) {
            if (point.typeId !in volumes) continue
            when (point.regionId) {
                config.jitaRegionId -> jitaPrice[point.typeId to point.date] = point.avgPrice
                config.referenceRegionId -> refPrice[point.typeId to point.date] = point.avgPrice
            }
        }

        // Inner join on (type_id, date), grouped by type_id - the parent repo
        // does this with a pandas merge/groupby over its persisted history
        // table; at this size (a shortlist's worth of type_ids x ~28 days)
        // plain maps are just as fast and keep this port dependency-light,
        // same call history_backtest.py's own comment makes.
        val marginsByType = mutableMapOf<Int, MutableList<Pair<String, Double>>>()
        for ((key, jita) in jitaPrice) {
            val ref = refPrice[key] ?: continue
            val (typeId, date) = key
            val volumeM3 = volumes[typeId] ?: continue
            val landed = jita * (1 + config.jitaBuyBrokerFee) + volumeM3 * config.importCostPerM3
            if (landed <= 0) continue
            val netSell = ref * config.structureSellHaircut
            marginsByType.getOrPut(typeId) { mutableListOf() }.add(date to (netSell - landed) / landed)
        }

        val results = mutableMapOf<Int, MarginTrend>()
        for ((typeId, rows) in marginsByType) {
            if (rows.size < MIN_TREND_HISTORY_DAYS) continue
            // Goonmetrics dates are ISO (YYYY-MM-DD), so a lexical sort is
            // chronological - no date parsing needed to take the "last N days".
            val margins = rows.sortedBy { it.first }.map { it.second }
            val recent = margins.takeLast(RECENT_WINDOW_DAYS)
            val baseline = margins.takeLast(BASELINE_WINDOW_DAYS)
            val recentAvg = recent.sum() / recent.size
            val baselineAvg = baseline.sum() / baseline.size
            if (kotlin.math.abs(baselineAvg) < MIN_BASELINE_MARGIN_MAGNITUDE) continue
            results[typeId] = MarginTrend(
                typeId = typeId,
                recentAvgMargin = recentAvg,
                baselineAvgMargin = baselineAvg,
                trendPct = (recentAvg - baselineAvg) / kotlin.math.abs(baselineAvg),
            )
        }
        return results
    }
}

/** One item's margin momentum - the desktop version returns this as a plain
 * `{type_id: {"recent_avg_margin", ...}}` dict; a data class is the
 * idiomatic Kotlin equivalent and keeps the field names checked at compile
 * time. `trendPct` = (recent - baseline) / abs(baseline): positive means the
 * margin is improving, negative means it's eroding. */
data class MarginTrend(
    val typeId: Int,
    val recentAvgMargin: Double,
    val baselineAvgMargin: Double,
    val trendPct: Double,
)
