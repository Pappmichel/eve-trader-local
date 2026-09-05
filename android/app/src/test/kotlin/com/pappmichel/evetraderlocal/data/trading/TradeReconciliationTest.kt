package com.pappmichel.evetraderlocal.data.trading

import com.pappmichel.evetraderlocal.data.esi.CharacterWalletTransaction
import java.time.Instant
import java.time.temporal.ChronoUnit
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** FIFO reconciliation of buys against sells - no network, no ESI, no
 * Android: everything here runs against the pure functions in
 * TradeReconciliation.kt, which is exactly why they were kept pure (the
 * paging/journal fetch helpers in that same file need a real EsiClient and
 * are left to manual testing, same as CandidateDiscovery's own ESI walk).
 *
 * Ported case-for-case from the desktop build's
 * tests/test_trade_reconciliation.py where the case still applies - the
 * ones that don't are its SDE/storage tests (no SDE cache and no persisted
 * run on this platform, see TradeReconciliation.kt's own note).
 *
 * The pooled multi-character cases at the bottom (two buyers, two sellers)
 * are not ported from a desktop test - desktop's own test suite never
 * actually exercises `reconcile_realized_trades` with more than one
 * (character_id, role) pair per side, even though the function accepts
 * lists - so these are freshly written against this file's own docstring
 * claim (any buyer's purchase can fund any seller's sale, pooled rather
 * than paired 1:1) and against `RealizedTradesScreen.kt`'s actual pooling:
 * concatenate every buyer's buys, concatenate every seller's sells and
 * journal maps, then match once.
 *
 * The config below uses round numbers so every expected figure is workable
 * by hand: no broker fee, no haircut, no freight - landed cost is exactly
 * the buy price, net sell exactly the sell price. */
private val CONFIG = TradingConfig(
    structureId = STRUCTURE,
    jitaBuyBrokerFee = 0.0,
    structureSellHaircut = 1.0,
    importCostPerM3 = 0.0,
    lookbackDays = 30,
)

private const val JITA_STATION = 60003760L
private const val OTHER_REGION_STATION = 60011866L
private const val STRUCTURE = 1234567890123L
private const val OTHER_STRUCTURE = 9999999999999L
private const val TRITANIUM = 34
private const val PYERITE = 35

private val NAMES = mapOf(TRITANIUM to "Tritanium", PYERITE to "Pyerite")
private val VOLUMES = mapOf(TRITANIUM to 0.01, PYERITE to 0.01)

/** Dates are compared as raw strings by the matcher (see its own note), so
 * these are ordinary ESI-shaped timestamps counted back from a fixed
 * instant - nothing here depends on when the suite runs. */
private val NOW: Instant = Instant.parse("2026-09-04T00:00:00Z")

private fun daysAgo(days: Long): String =
    NOW.minus(days, ChronoUnit.DAYS).toString().replace(".000Z", "Z")

private fun buy(
    qty: Long,
    price: Double,
    ago: Long,
    transactionId: Long = 1,
    locationId: Long = JITA_STATION,
    typeId: Int = TRITANIUM,
) = CharacterWalletTransaction(
    transactionId = transactionId, typeId = typeId, isBuy = true, quantity = qty,
    unitPrice = price, date = daysAgo(ago), locationId = locationId,
    journalRefId = 1000 + transactionId,
)

private fun sell(
    qty: Long,
    price: Double,
    ago: Long,
    transactionId: Long = 1,
    locationId: Long = STRUCTURE,
    typeId: Int = TRITANIUM,
) = CharacterWalletTransaction(
    transactionId = transactionId, typeId = typeId, isBuy = false, quantity = qty,
    unitPrice = price, date = daysAgo(ago), locationId = locationId,
    journalRefId = 2000 + transactionId,
)

private fun reconcile(
    buys: List<CharacterWalletTransaction>,
    sells: List<CharacterWalletTransaction>,
    journal: Map<Long, Double> = emptyMap(),
    config: TradingConfig = CONFIG,
    names: Map<Int, String> = NAMES,
    volumes: Map<Int, Double> = VOLUMES,
): List<RealizedTrade> = reconcileRealizedTrades(
    buys = buysAtStations(buys, setOf(JITA_STATION)),
    sells = sellsAtStructure(sells, config.structureId!!),
    journalAmountByRefId = journal,
    itemNames = names,
    itemVolumes = volumes,
    config = config,
)

private const val EPSILON = 1e-6

// ------------------------------------------------------------- the match
class TradeReconciliationTest {

    @Test
    fun `a clean buy then sell matches in full`() {
        val trades = reconcile(listOf(buy(10, 100.0, ago = 5)), listOf(sell(10, 150.0, ago = 2)))

        assertEquals(1, trades.size)
        val trade = trades[0]
        assertEquals(TRITANIUM, trade.typeId)
        assertEquals("Tritanium", trade.item)
        assertEquals(10L, trade.matchedQty)
        assertEquals(500.0, trade.realizedProfit, EPSILON)
        assertEquals(0.5, trade.margin, EPSILON)
        assertEquals(10L, trade.buyQty)
        assertEquals(10L, trade.sellQty)
    }

    @Test
    fun `costs are applied to the right side`() {
        val config = CONFIG.copy(
            jitaBuyBrokerFee = 0.1, structureSellHaircut = 0.9, importCostPerM3 = 100.0,
        )
        val trade = reconcile(
            listOf(buy(1, 100.0, ago = 5)), listOf(sell(1, 200.0, ago = 2)),
            config = config, volumes = mapOf(TRITANIUM to 0.5),
        )[0]

        val landed = 100.0 * 1.1 + 0.5 * 100.0 // broker fee on the buy + per-m3 freight
        assertEquals(200.0 * 0.9 - landed, trade.realizedProfit, EPSILON)
        assertEquals((200.0 * 0.9 - landed) / landed, trade.margin, EPSILON)
    }

    @Test
    fun `one sell consumes several buys oldest first`() {
        val trades = reconcile(
            listOf(buy(4, 100.0, ago = 9, transactionId = 1), buy(6, 200.0, ago = 6, transactionId = 2)),
            listOf(sell(10, 300.0, ago = 2)),
        )

        assertEquals(listOf(4L, 6L), trades.map { it.matchedQty })
        assertEquals(listOf(100.0, 200.0), trades.map { it.buyUnitPrice }) // FIFO: oldest first
        assertEquals(4 * 200.0 + 6 * 100.0, trades.sumOf { it.realizedProfit }, EPSILON)
    }

    @Test
    fun `one buy covers several sells and keeps its remainder`() {
        val trades = reconcile(
            listOf(buy(10, 100.0, ago = 9)),
            listOf(sell(3, 150.0, ago = 5, transactionId = 1), sell(2, 160.0, ago = 3, transactionId = 2)),
        )

        assertEquals(listOf(3L, 2L), trades.map { it.matchedQty })
        assertTrue(trades.all { it.buyUnitPrice == 100.0 })
        // The buy still has 5 units left over; nothing fabricates a third row for them.
        assertEquals(5L, trades.sumOf { it.matchedQty })
    }

    @Test
    fun `a sell without an earlier buy is dropped not matched forward`() {
        // A buy dated *after* a sell can't be that sale's cost basis. The
        // sell is dropped (no row at all), and the buy stays available for a
        // later sell - the `break`, not `continue`, rule the matcher
        // documents at length.
        val trades = reconcile(
            listOf(buy(5, 100.0, ago = 4)),
            listOf(sell(5, 150.0, ago = 8, transactionId = 1), sell(5, 150.0, ago = 1, transactionId = 2)),
        )

        assertEquals(1, trades.size)
        assertTrue(trades[0].sellDate > trades[0].buyDate)
        assertEquals(5L, trades[0].matchedQty)
    }

    @Test
    fun `a sell with no buy at all produces nothing`() {
        assertEquals(emptyList<RealizedTrade>(), reconcile(emptyList(), listOf(sell(5, 150.0, ago = 2))))
    }

    @Test
    fun `a partial cover matches what it can`() {
        val trades = reconcile(listOf(buy(3, 100.0, ago = 6)), listOf(sell(10, 150.0, ago = 2)))

        assertEquals(1, trades.size)
        assertEquals(3L, trades[0].matchedQty)
        assertEquals(10L, trades[0].sellQty) // the whole transaction is still recorded
    }

    @Test
    fun `types are matched independently`() {
        val trades = reconcile(
            listOf(
                buy(5, 100.0, ago = 6),
                buy(5, 10.0, ago = 6, transactionId = 2, typeId = PYERITE),
            ),
            listOf(
                sell(5, 150.0, ago = 2),
                sell(5, 20.0, ago = 2, transactionId = 2, typeId = PYERITE),
            ),
        )

        assertEquals(
            mapOf(TRITANIUM to 250.0, PYERITE to 50.0),
            trades.associate { it.typeId to it.realizedProfit },
        )
    }

    @Test
    fun `results are sorted by sell date`() {
        val trades = reconcile(
            listOf(buy(1, 100.0, ago = 20, transactionId = 1), buy(1, 100.0, ago = 19, transactionId = 2)),
            listOf(sell(1, 150.0, ago = 2, transactionId = 1), sell(1, 150.0, ago = 10, transactionId = 2)),
        )
        assertEquals(trades.map { it.sellDate }.sorted(), trades.map { it.sellDate })
    }

    @Test
    fun `an unknown type falls back to its id and zero freight`() {
        val unknown = 4247
        val trade = reconcile(
            listOf(buy(1, 100.0, ago = 6, typeId = unknown)),
            listOf(sell(1, 500.0, ago = 2, typeId = unknown)),
            config = CONFIG.copy(importCostPerM3 = 100.0),
            names = emptyMap(), volumes = emptyMap(),
        )[0]

        assertEquals(unknown.toString(), trade.item)
        assertEquals(400.0, trade.realizedProfit, EPSILON) // no volume known -> no freight
    }

    // -------------------------------------------------- location filters
    @Test
    fun `buys outside the buy stations are ignored`() {
        val trades = reconcile(
            listOf(buy(5, 100.0, ago = 6, locationId = OTHER_REGION_STATION)),
            listOf(sell(5, 150.0, ago = 2)),
        )
        assertEquals(emptyList<RealizedTrade>(), trades)
    }

    @Test
    fun `sells at another structure are ignored`() {
        val trades = reconcile(
            listOf(buy(5, 100.0, ago = 6)),
            listOf(sell(5, 150.0, ago = 2, locationId = OTHER_STRUCTURE)),
        )
        assertEquals(emptyList<RealizedTrade>(), trades)
    }

    @Test
    fun `a sell on the buy side is not a buy`() {
        // is_buy, not just location, decides which side a transaction is on.
        val stray = sell(5, 100.0, ago = 6, locationId = JITA_STATION)
        assertEquals(emptyList<RealizedTrade>(), reconcile(listOf(stray), listOf(sell(5, 150.0, ago = 2))))
    }

    // --------------------------------------------------- the sell price
    @Test
    fun `a journal amount replaces the modeled tax`() {
        val config = CONFIG.copy(structureSellHaircut = 0.9463)
        val s = sell(10, 200.0, ago = 2)
        // 1800 ISK credited for 10 units = 180/unit, already net of real sales tax.
        val trade = reconcile(
            listOf(buy(10, 100.0, ago = 6)), listOf(s),
            journal = mapOf(s.journalRefId!! to 1800.0), config = config,
        )[0]

        val netSell = 180.0 * (0.9463 + ASSUMED_TAX_RATE_IN_DEFAULT_HAIRCUT)
        assertEquals((netSell - 100.0) * 10, trade.realizedProfit, EPSILON)
    }

    @Test
    fun `a missing journal entry falls back to the modeled formula`() {
        val trade = reconcile(
            listOf(buy(10, 100.0, ago = 6)), listOf(sell(10, 150.0, ago = 2)),
            journal = mapOf(999999L to 1.0), // some other trade's entry
        )[0]
        assertEquals(500.0, trade.realizedProfit, EPSILON) // modeled formula, haircut 1.0
    }

    // -------------------------------------------------------- summary
    @Test
    fun `summarize weights margin by cost basis`() {
        val trades = listOf(
            RealizedTrade(TRITANIUM, "Tritanium", "d1", 100, 10.0, "d2", 100, 20.0, 100, 1000.0, 1.0),
            RealizedTrade(PYERITE, "Pyerite", "d1", 1, 1000.0, "d2", 1, 1100.0, 1, 100.0, 0.1),
        )
        val summary = summarizeRealizedTrades(trades)

        assertEquals(1100.0, summary.totalRealizedProfit, EPSILON)
        // Not (1.0 + 0.1) / 2: the one-unit freak margin must not weigh as
        // much as the hundred-unit trade.
        assertEquals(1100.0 / (10.0 * 100 + 1000.0 * 1), summary.averageMargin, EPSILON)
        assertEquals(listOf("Tritanium" to 1000.0, "Pyerite" to 100.0), summary.top3ItemsByProfit)
    }

    @Test
    fun `summarize keeps only the top three items`() {
        val trades = (1..5).map { i ->
            RealizedTrade(i, "Item $i", "d1", 1, 1.0, "d2", 1, 2.0, 1, i.toDouble(), 1.0)
        }
        assertEquals(
            listOf("Item 5" to 5.0, "Item 4" to 4.0, "Item 3" to 3.0),
            summarizeRealizedTrades(trades).top3ItemsByProfit,
        )
    }

    @Test
    fun `summarize of nothing is zero not a division error`() {
        assertEquals(0.0, summarizeRealizedTrades(emptyList()).averageMargin, EPSILON)
    }

    // ------------------------------------------- average daily sold by type
    // Ported case-for-case from desktop's test_average_daily_sold_by_type,
    // minus the two storage-specific cases (only the latest run counts;
    // empty before any run) that don't apply here - this port takes the
    // already-live List<RealizedTrade> directly rather than reading a
    // persisted run back out of SQLite, so there is no "latest run" or
    // "before any run" state to lose track of (see TradeReconciliation.kt's
    // own note on why that substitution is exact for this metric).
    @Test
    fun `average daily sold sums matched qty per type over the lookback window`() {
        val trades = listOf(
            RealizedTrade(TRITANIUM, "Tritanium", "d1", 100, 10.0, "d2", 100, 20.0, 60, 600.0, 1.0),
            RealizedTrade(TRITANIUM, "Tritanium", "d1", 100, 10.0, "d2", 100, 20.0, 40, 400.0, 1.0),
            RealizedTrade(PYERITE, "Pyerite", "d1", 5, 10.0, "d2", 5, 20.0, 5, 50.0, 1.0),
        )
        assertEquals(
            mapOf(TRITANIUM to 10.0, PYERITE to 0.5),
            averageDailySoldByType(trades, lookbackDays = 10),
        )
    }

    @Test
    fun `average daily sold of nothing is empty not a division error`() {
        assertEquals(emptyMap<Int, Double>(), averageDailySoldByType(emptyList(), lookbackDays = 10))
    }

    @Test
    fun `average daily sold with a non-positive lookback is empty`() {
        val trades = listOf(
            RealizedTrade(TRITANIUM, "Tritanium", "d1", 1, 10.0, "d2", 1, 20.0, 1, 10.0, 1.0),
        )
        assertEquals(emptyMap<Int, Double>(), averageDailySoldByType(trades, lookbackDays = 0))
    }

    // ------------------------------------------------ pooled: multi-character
    @Test
    fun `two sellers' sells are pooled and both get matched, not just one`() {
        // Two different sellers each sold Tritanium at the same structure;
        // RealizedTradesScreen.kt concatenates both characters' fetched
        // transactions before calling reconcile - reproduced directly here.
        val sellerA = sell(4, 150.0, ago = 3, transactionId = 1)
        val sellerB = sell(6, 160.0, ago = 2, transactionId = 2)
        val trades = reconcile(listOf(buy(10, 100.0, ago = 9)), listOf(sellerA, sellerB))

        assertEquals(2, trades.size)
        assertEquals(setOf(4L, 6L), trades.map { it.matchedQty }.toSet())
        // No double counting: together the two sells consume exactly the
        // one buy's 10 units, no more.
        assertEquals(10L, trades.sumOf { it.matchedQty })
    }

    @Test
    fun `two buyers' buys are pooled - either can fund the same seller's sale`() {
        // Desktop's reconcile_realized_trades pools every buyer's buys
        // against every seller's sells - it does not pair a specific buyer
        // to a specific seller. Buyer B's earlier buy should be usable as
        // cost basis for a sale made by the (single) seller here, exactly
        // as if buyer A had bought it themselves.
        val buyerA = buy(4, 90.0, ago = 9, transactionId = 1)
        val buyerB = buy(6, 100.0, ago = 8, transactionId = 2)
        val trades = reconcile(listOf(buyerA, buyerB), listOf(sell(10, 150.0, ago = 2)))

        assertEquals(2, trades.size) // FIFO still matches oldest-first across the pooled queue
        assertEquals(listOf(4L, 6L), trades.map { it.matchedQty })
        assertEquals(listOf(90.0, 100.0), trades.map { it.buyUnitPrice })
        assertEquals(10L, trades.sumOf { it.matchedQty })
    }

    @Test
    fun `pooling two buyers and two sellers matches without double counting`() {
        val buyerA = buy(5, 80.0, ago = 9, transactionId = 1)
        val buyerB = buy(5, 120.0, ago = 8, transactionId = 2)
        val sellerA = sell(4, 200.0, ago = 3, transactionId = 1)
        val sellerB = sell(6, 210.0, ago = 2, transactionId = 2)

        val trades = reconcile(listOf(buyerA, buyerB), listOf(sellerA, sellerB))

        // Every unit bought is matched to exactly one unit sold - 10 in,
        // 10 out, never both fully re-used across sellers.
        assertEquals(10L, trades.sumOf { it.matchedQty })
        assertEquals(10L, trades.filter { it.buyUnitPrice == 80.0 }.sumOf { it.matchedQty } +
            trades.filter { it.buyUnitPrice == 120.0 }.sumOf { it.matchedQty })
        assertEquals(4L, trades.filter { it.sellUnitPrice == 200.0 }.sumOf { it.matchedQty })
        assertEquals(6L, trades.filter { it.sellUnitPrice == 210.0 }.sumOf { it.matchedQty })
    }

    @Test
    fun `journal amounts from two sellers are unioned by ref id`() {
        // RealizedTradesScreen.kt merges every seller's fetchRecentJournalAmounts
        // result into one map (`journalAmounts += ...`) before the single
        // reconcile call - a ref id collision across sellers is not expected
        // (ESI's journal entry ids are globally unique), so a plain union is
        // correct and each seller's own sale still finds its own entry.
        val sellerASale = sell(10, 200.0, ago = 3, transactionId = 1)
        val sellerBSale = sell(5, 300.0, ago = 2, transactionId = 2)
        val config = CONFIG.copy(structureSellHaircut = 0.9463)
        val journal = mapOf(
            sellerASale.journalRefId!! to 1800.0, // 180/unit net for seller A
            sellerBSale.journalRefId!! to 1400.0, // 280/unit net for seller B
        )
        val trades = reconcile(
            listOf(buy(15, 100.0, ago = 9)), listOf(sellerASale, sellerBSale),
            journal = journal, config = config,
        )

        assertEquals(2, trades.size)
        val netA = 180.0 * (0.9463 + ASSUMED_TAX_RATE_IN_DEFAULT_HAIRCUT)
        val netB = 280.0 * (0.9463 + ASSUMED_TAX_RATE_IN_DEFAULT_HAIRCUT)
        assertEquals((netA - 100.0) * 10, trades.first { it.sellQty == 10L }.realizedProfit, EPSILON)
        assertEquals((netB - 100.0) * 5, trades.first { it.sellQty == 5L }.realizedProfit, EPSILON)
    }
}
