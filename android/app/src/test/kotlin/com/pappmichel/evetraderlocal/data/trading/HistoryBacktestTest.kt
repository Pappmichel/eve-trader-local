package com.pappmichel.evetraderlocal.data.trading

import com.pappmichel.evetraderlocal.data.history.HistoryPoint
import kotlin.math.ln
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Ports the `compute_margin_trends` *and* `_score_candidate` cases from the
 * desktop build's tests/test_history_backtest.py.
 *
 * `compute_margin_trends` coverage: same fixtures (a flat Jita price, a list
 * of daily reference-region prices), same four behaviours: the
 * recent-vs-rolling-baseline window math, the thin-history and
 * near-zero-baseline exclusions, and the inner join dropping unpaired days.
 *
 * `scoreCandidate` coverage: the hit-rate/avg-movement/margin `add` gate -
 * same fixtures ((date, jitaAvg, refAvg, refMovement) tuples), same cases:
 * the score formula itself, cost application, unpaired/no-Jita-history
 * handling, and each of the four `add` gates failing independently. */
class HistoryBacktestTest {
    private val config = TradingConfig(
        // Zero out the fee/haircut/import terms so a fixture's margins are
        // exact round numbers (ref 150 vs jita 100 -> margin 0.5) and the
        // assertions are about the window math, not about arithmetic on
        // EVE's real broker/tax constants - same thing the Python fixture's
        // own cfg does.
        importCostPerM3 = 0.0,
        structureSellHaircut = 1.0,
        jitaBuyBrokerFee = 0.0,
    )

    private fun trendPoints(typeId: Int, jita: Double, refs: List<Double>): List<HistoryPoint> =
        refs.flatMapIndexed { index, ref ->
            val date = "2026-01-%02d".format(index + 1)
            listOf(
                point(config.jitaRegionId, typeId, date, jita),
                point(config.referenceRegionId, typeId, date, ref),
            )
        }

    private fun point(regionId: Int, typeId: Int, date: String, avgPrice: Double) = HistoryPoint(
        regionId = regionId, typeId = typeId, date = date, minPrice = avgPrice,
        maxPrice = avgPrice, avgPrice = avgPrice, movement = 10.0, numOrders = 1,
    )

    @Test
    fun `reports improving and eroding trends`() {
        // Baseline margin 0.5 (ref 150 vs jita 100) for 6 days, then the last
        // 3 days climb to ref 200 -> margin 1.0.
        val points = trendPoints(100, 100.0, List(6) { 150.0 } + List(3) { 200.0 })
        val trend = HistoryBacktest.computeMarginTrends(points, mapOf(100 to 1.0), config)[100]!!

        assertEquals(1.0, trend.recentAvgMargin, 1e-9)
        assertEquals((0.5 * 6 + 1.0 * 3) / 9, trend.baselineAvgMargin, 1e-9)
        assertTrue(trend.trendPct > 0)

        val eroding = trendPoints(100, 100.0, List(6) { 200.0 } + List(3) { 150.0 })
        assertTrue(HistoryBacktest.computeMarginTrends(eroding, mapOf(100 to 1.0), config)[100]!!.trendPct < 0)
    }

    @Test
    fun `omits thin history and near-zero baselines`() {
        assertTrue(HistoryBacktest.computeMarginTrends(emptyList(), mapOf(100 to 1.0), config).isEmpty())

        // 5 paired days, one short of MIN_TREND_HISTORY_DAYS.
        val thin = trendPoints(100, 100.0, List(HistoryBacktest.MIN_TREND_HISTORY_DAYS - 1) { 150.0 })
        assertTrue(HistoryBacktest.computeMarginTrends(thin, mapOf(100 to 1.0), config).isEmpty())

        // Baseline hovering at ~1% margin: inside the dead zone, so no trend
        // is reported even though there is plenty of history.
        val flat = trendPoints(100, 100.0, List(8) { 101.0 })
        assertTrue(HistoryBacktest.computeMarginTrends(flat, mapOf(100 to 1.0), config).isEmpty())

        // A type not in `volumes` has no landed cost to compute and is skipped.
        val other = trendPoints(200, 100.0, List(8) { 150.0 })
        assertNull(HistoryBacktest.computeMarginTrends(other, mapOf(100 to 1.0), config)[100])
        assertEquals(setOf(200), HistoryBacktest.computeMarginTrends(other, mapOf(200 to 1.0), config).keys)
    }

    @Test
    fun `ignores unpaired days`() {
        // Six extra Jita-only days can't make a trend on their own.
        val jitaOnly = (1..6).map { point(config.jitaRegionId, 300, "2026-02-%02d".format(it), 1.0) }
        val points = trendPoints(100, 100.0, List(8) { 150.0 }) + jitaOnly

        val trends = HistoryBacktest.computeMarginTrends(points, mapOf(100 to 1.0, 300 to 1.0), config)
        assertEquals(setOf(100), trends.keys)
    }

    @Test
    fun `baseline is a rolling window that includes the recent days`() {
        // 40 paired days: the baseline only looks at the last
        // BASELINE_WINDOW_DAYS (30) of them, so the 10 oldest - deliberately
        // given a wildly different margin - must not move it at all.
        val refs = List(10) { 1000.0 } + List(30) { 150.0 }
        val trend = HistoryBacktest.computeMarginTrends(
            trendPoints(100, 100.0, refs), mapOf(100 to 1.0), config,
        )[100]!!

        assertEquals(0.5, trend.baselineAvgMargin, 1e-9)
        assertEquals(0.5, trend.recentAvgMargin, 1e-9)
        assertEquals(0.0, trend.trendPct, 1e-9)
    }

    @Test
    fun `landed cost uses the broker fee and per-m3 import cost`() {
        // The formula itself, with the real constants in play: landed =
        // 100 * 1.0147 + 2 m3 * 900, net sell = 300 * 0.9463.
        val realConfig = TradingConfig()
        val landed = 100.0 * (1 + realConfig.jitaBuyBrokerFee) + 2.0 * realConfig.importCostPerM3
        val expected = (300.0 * realConfig.structureSellHaircut - landed) / landed

        val trend = HistoryBacktest.computeMarginTrends(
            trendPoints(100, 100.0, List(8) { 300.0 }), mapOf(100 to 2.0), realConfig,
        )[100]!!

        assertEquals(expected, trend.recentAvgMargin, 1e-9)
        assertEquals(expected, trend.baselineAvgMargin, 1e-9)
    }

    // ------------------------------------------------------- scoreCandidate
    // Round numbers so the expected margins below can be worked out by hand:
    // no broker fee, no haircut, no import cost - landed == jita avg price.
    private val scoreConfig = TradingConfig(
        jitaBuyBrokerFee = 0.0, structureSellHaircut = 1.0, importCostPerM3 = 0.0,
        minMarginThreshold = 0.05, minHitRate = 0.30, minAvgMovement = 0.0,
    )

    private fun candidate(typeId: Int = 100, volumeM3: Double = 1.0) =
        Candidate(item = "Item $typeId", typeId = typeId, volumeM3 = volumeM3, category = "Module/Rig", marketGroupPath = "modules")

    /** One day's worth of (date, jitaAvg, refAvg, refMovement) - the Kotlin
     * stand-in for the desktop fixture's plain tuples. */
    private data class Day(val date: String, val jitaAvg: Double, val refAvg: Double, val refMovement: Double)

    private fun scorePoints(typeId: Int, days: List<Day>): List<HistoryPoint> = days.flatMap { d ->
        listOf(
            HistoryPoint(scoreConfig.jitaRegionId, typeId, d.date, d.jitaAvg, d.jitaAvg, d.jitaAvg, 0.0, 1),
            HistoryPoint(scoreConfig.referenceRegionId, typeId, d.date, d.refAvg, d.refAvg, d.refAvg, d.refMovement, 1),
        )
    }

    @Test
    fun `score candidate margin math`() {
        // 3 days, all with ref = 2x jita -> margin 1.0 each, profit
        // 100/unit, volume_m3 1.0 -> profit_m3 100. movement 10 every day.
        val points = scorePoints(
            100,
            listOf(
                Day("2026-01-01", 100.0, 200.0, 10.0),
                Day("2026-01-02", 100.0, 200.0, 10.0),
                Day("2026-01-03", 100.0, 200.0, 10.0),
            ),
        )
        val r = HistoryBacktest.scoreCandidate(candidate(), HistoryBacktest.indexHistory(points), scoreConfig)!!

        assertEquals(3, r.pairedDays)
        assertEquals(3, r.profitableDays)
        assertEquals(1.0, r.hitRate, 1e-9)
        assertEquals(1.0, r.latestMargin, 1e-9)
        assertEquals(1.0, r.bestMargin, 1e-9)
        assertEquals(100.0, r.avgProfitM3, 1e-9)
        assertEquals(10.0, r.avgSellMovement, 1e-9)
        assertEquals(100.0 * ln(11.0) * 1.0, r.score, 1e-9)
        assertTrue(r.add)
        assertEquals("Consider import", r.recommendation)
    }

    @Test
    fun `score candidate costs are applied`() {
        val cfg = scoreConfig.copy(
            jitaBuyBrokerFee = 0.10, importCostPerM3 = 2.0, structureSellHaircut = 0.90,
        )
        // landed = 100 * 1.10 + 5 * 2 = 120, net_sell = 200 * 0.9 = 180.
        val points = scorePoints(100, listOf(Day("2026-01-01", 100.0, 200.0, 10.0)))
        val r = HistoryBacktest.scoreCandidate(candidate(volumeM3 = 5.0), HistoryBacktest.indexHistory(points), cfg)!!

        assertEquals((180.0 - 120.0) / 120.0, r.latestMargin, 1e-9)
        assertEquals((180.0 - 120.0) / 5.0, r.avgProfitM3, 1e-9)
    }

    @Test
    fun `score candidate ignores unpaired and returns null without jita history`() {
        val paired = scorePoints(100, listOf(Day("2026-01-01", 100.0, 200.0, 10.0))).toMutableList()
        // A Jita-only day: counted in `dates` but skipped for lack of a ref price.
        paired.add(HistoryPoint(scoreConfig.jitaRegionId, 100, "2026-01-02", 100.0, 100.0, 100.0, 0.0, 1))
        val histIndex = HistoryBacktest.indexHistory(paired)
        val r = HistoryBacktest.scoreCandidate(candidate(), histIndex, scoreConfig)!!
        assertEquals(1, r.pairedDays)
        // The latest date has no ref price at all, so the `add` gate's
        // current-profitability check sees 0.0 and refuses the
        // recommendation even though the one paired day was excellent.
        assertEquals(0.0, r.latestMargin, 1e-9)
        assertFalse(r.add)

        assertNull(HistoryBacktest.scoreCandidate(candidate(999), histIndex, scoreConfig))
    }

    @Test
    fun `score candidate add gates`() {
        // 1 good day out of 4 -> hit_rate 0.25, below min_hit_rate 0.30,
        // even though the *latest* day is profitable.
        var points = scorePoints(
            100,
            listOf(
                Day("2026-01-01", 100.0, 100.0, 10.0),
                Day("2026-01-02", 100.0, 100.0, 10.0),
                Day("2026-01-03", 100.0, 100.0, 10.0),
                Day("2026-01-04", 100.0, 200.0, 10.0),
            ),
        )
        var r = HistoryBacktest.scoreCandidate(candidate(), HistoryBacktest.indexHistory(points), scoreConfig)!!
        assertEquals(0.25, r.hitRate, 1e-9)
        assertEquals(1.0, r.latestMargin, 1e-9)
        assertFalse(r.add)
        assertEquals("Skip", r.recommendation)

        // Latest day unprofitable, everything else fine -> still no.
        points = scorePoints(
            100,
            listOf(
                Day("2026-01-01", 100.0, 200.0, 10.0),
                Day("2026-01-02", 100.0, 200.0, 10.0),
                Day("2026-01-03", 100.0, 100.0, 10.0),
            ),
        )
        r = HistoryBacktest.scoreCandidate(candidate(), HistoryBacktest.indexHistory(points), scoreConfig)!!
        assertEquals(2.0 / 3.0, r.hitRate, 1e-9)
        assertEquals(0.0, r.latestMargin, 1e-9)
        assertFalse(r.add)

        // Liquidity gate: profitable and consistent, but no movement at all.
        val liquidityCfg = scoreConfig.copy(minAvgMovement = 5.0)
        points = scorePoints(100, listOf(Day("2026-01-01", 100.0, 200.0, 1.0)))
        assertFalse(HistoryBacktest.scoreCandidate(candidate(), HistoryBacktest.indexHistory(points), liquidityCfg)!!.add)
    }
}
