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
}
