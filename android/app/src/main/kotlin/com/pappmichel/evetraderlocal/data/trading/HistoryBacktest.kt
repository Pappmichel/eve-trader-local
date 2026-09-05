package com.pappmichel.evetraderlocal.data.trading

import com.pappmichel.evetraderlocal.data.history.HistoryPoint

/** Margin-momentum trends *and* hit-rate/avg-movement candidate scoring from
 * region price history - a Kotlin port of both halves of the desktop
 * build's history_backtest.py: `compute_margin_trends` (`computeMarginTrends`
 * below) and `_score_candidate`/`_latest_margin` (`scoreCandidate` below).
 * `select_candidate_window`'s rotating-offset "safe mode" batching is NOT
 * ported - see `scoreCandidate`'s own KDoc for why that's a reasonable
 * scope-down here, not a missing feature.
 *
 * The per-day formula is the same landed-cost/net-sell shape shared by
 * `computeMarginTrends`, `scoreCandidate`, and Shortlist.kt's live
 * order-book evaluation - just fed *daily region averages* here instead of
 * a live order-book snapshot:
 *
 *     landed  = jitaAvgPrice * (1 + jitaBuyBrokerFee) + volumeM3 * importCostPerM3
 *     netSell = refAvgPrice * structureSellHaircut
 *     margin  = (netSell - landed) / landed
 *
 * A region-wide daily average is a deliberately different (smoother) input
 * from a live order-book snapshot - it answers "is this item trending
 * better or worse than it has been" / "has this item been reliably
 * profitable", not "what can I buy it for right now".
 *
 * No storage coupling: history comes in from the caller (on Android, freshly
 * fetched by GoonmetricsClient on the user's button press - see
 * PriceHistoryScreen.kt for why this platform fetches instead of reading a
 * cache), volumes/candidates come in from the shortlist/Candidate Discovery,
 * results go back out. Same "caller wires the data in" split
 * history_backtest.py itself documents. */
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

    /** Indexes history points by (regionId, typeId, date) for O(1) paired
     * lookups - the Kotlin counterpart of `_index_history`. */
    fun indexHistory(points: List<HistoryPoint>): Map<Triple<Int, Int, String>, HistoryPoint> =
        points.associateBy { Triple(it.regionId, it.typeId, it.date) }

    /** Same landed/net-sell/margin formula as `scoreCandidate`'s per-day
     * loop, but for a single day only (`date`, normally the most recent one
     * with data) - used for the `add` gate below, which cares about
     * *current* profitability specifically, not the multi-day average
     * score. Returns 0.0 (not null) when either region has no price for
     * that day - the direct counterpart of `_latest_margin`'s own
     * `if not jita or not ref: return 0.0`. */
    private fun latestMargin(
        histIndex: Map<Triple<Int, Int, String>, HistoryPoint>, typeId: Int, date: String,
        volumeM3: Double, config: TradingConfig,
    ): Double {
        val jita = histIndex[Triple(config.jitaRegionId, typeId, date)] ?: return 0.0
        val ref = histIndex[Triple(config.referenceRegionId, typeId, date)] ?: return 0.0
        val landed = jita.avgPrice * (1 + config.jitaBuyBrokerFee) + volumeM3 * config.importCostPerM3
        val netSell = ref.avgPrice * config.structureSellHaircut
        return if (landed > 0) (netSell - landed) / landed else 0.0
    }

    /** Aggregates every paired (Jita, reference-region) day of history for
     * one candidate into a hit-rate/score, and decides whether to
     * recommend it - the Kotlin counterpart of `_score_candidate`, ported
     * case-for-case (see HistoryBacktestTest.kt).
     *
     * `score = avgProfitM3 x ln(1+avgMove) x hitRate`: profit per m3
     * rewards import efficiency, ln(1+avgMove) rewards liquidity without
     * letting a single very-high-volume day dominate, hitRate rewards
     * consistency over a lucky day. `add` (the actual recommendation)
     * requires *all* of: at least one profitable day, hitRate clearing
     * `config.minHitRate`, the *latest* day's margin (not just the
     * average) clearing `config.minMarginThreshold`, a positive score, and
     * average liquidity clearing `config.minAvgMovement` - a good
     * historical average alone isn't enough if the item isn't profitable
     * or liquid right now.
     *
     * Returns null when there's no Jita history for this candidate's
     * typeId at all - the direct counterpart of `_score_candidate`'s own
     * `if not dates: return None`.
     *
     * Deliberately NOT ported: `find_new_import_candidates`'s batching and
     * `select_candidate_window`'s rotating-offset "safe mode" (a persisted
     * cursor so repeated runs eventually cover a candidate universe too
     * large to score in one run/request budget). Android has no
     * counterpart of that persisted offset yet, and the callers here
     * (CandidateDiscoveryScreen.kt's "Add Recommended", ShortlistScreen.kt's
     * "Prune") score a bounded set on a single button press - the already-
     * discovered candidate list, or the current shortlist - rather than an
     * unbounded universe, so there is no unattended-search case to rotate a
     * window through the way the desktop CLI's `find-candidates --safe`
     * does. Worth porting if a caller ever needs to score the *entire*
     * live-ESI-walk candidate universe (thousands of items) in bounded
     * batches. */
    fun scoreCandidate(
        candidate: Candidate,
        histIndex: Map<Triple<Int, Int, String>, HistoryPoint>,
        config: TradingConfig,
    ): NewCandidateResult? {
        val dates = histIndex.keys
            .filter { (region, typeId, _) -> region == config.jitaRegionId && typeId == candidate.typeId }
            .map { it.third }
            .sorted()
        if (dates.isEmpty()) return null

        var days = 0
        var goodDays = 0
        var bestMargin = -999.0
        var sumProfitM3 = 0.0
        var sumMove = 0.0
        val latestDate = dates.last()

        for (d in dates) {
            val jita = histIndex[Triple(config.jitaRegionId, candidate.typeId, d)] ?: continue
            val ref = histIndex[Triple(config.referenceRegionId, candidate.typeId, d)] ?: continue
            val landed = jita.avgPrice * (1 + config.jitaBuyBrokerFee) + candidate.volumeM3 * config.importCostPerM3
            if (landed <= 0 || candidate.volumeM3 <= 0) continue
            val netSell = ref.avgPrice * config.structureSellHaircut
            val profit = netSell - landed
            val margin = profit / landed
            val profitM3 = profit / candidate.volumeM3
            days++
            sumProfitM3 += profitM3
            sumMove += ref.movement
            if (margin >= config.minMarginThreshold) goodDays++
            bestMargin = maxOf(bestMargin, margin)
        }

        if (days == 0) return null

        val hitRate = goodDays.toDouble() / days
        val avgProfitM3 = sumProfitM3 / days
        val avgMove = sumMove / days
        val score = avgProfitM3 * kotlin.math.ln(1 + avgMove) * hitRate
        val latestMarginValue = latestMargin(histIndex, candidate.typeId, latestDate, candidate.volumeM3, config)

        val add = goodDays > 0 && hitRate >= config.minHitRate && latestMarginValue >= config.minMarginThreshold &&
            score > 0 && avgMove >= config.minAvgMovement

        return NewCandidateResult(
            item = candidate.item, category = candidate.category, typeId = candidate.typeId,
            volumeM3 = candidate.volumeM3, pairedDays = days, profitableDays = goodDays,
            hitRate = hitRate, latestMargin = latestMarginValue, bestMargin = bestMargin,
            avgProfitM3 = avgProfitM3, avgSellMovement = avgMove, score = score,
            recommendation = if (add) "Consider import" else "Skip", add = add,
            metaLevel = candidate.metaLevel,
        )
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
