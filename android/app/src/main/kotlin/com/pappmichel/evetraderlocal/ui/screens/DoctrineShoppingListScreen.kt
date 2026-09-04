package com.pappmichel.evetraderlocal.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.pappmichel.evetraderlocal.data.db.AppDatabase
import com.pappmichel.evetraderlocal.data.doctrine.DoctrineFittingRepository
import com.pappmichel.evetraderlocal.data.doctrine.ShoppingList
import com.pappmichel.evetraderlocal.data.doctrine.StockpileStatus
import com.pappmichel.evetraderlocal.data.esi.EsiClient
import com.pappmichel.evetraderlocal.data.history.GoonmetricsClient
import com.pappmichel.evetraderlocal.data.sde.SdeRepository
import com.pappmichel.evetraderlocal.data.trading.TradingConfigRepository
import kotlinx.coroutines.launch

/** Doctrine -> Shopping List: every item with a real Stockpile Status
 * shortfall, priced Buy-C-J-vs-Buy-Jita - ported from `doctrine/actions.py`'s
 * `do_get_shopping_list` on top of [ShoppingList]'s math (see that file's
 * own docstring for the documented "no Build column" scope cut vs. the
 * desktop engine's three-way comparison).
 *
 * This screen computes Stockpile Status fresh from every *saved* fitting
 * with `availableByType` all zero (i.e. "if I owned nothing at all, what
 * would restocking everything cost") unless the user has already refreshed
 * real quantities on the Stockpile Status screen first and comes here
 * right after - there is no cross-screen live state on this platform, so
 * this screen re-derives shortfalls from scratch each time it loads,
 * consistently with every other "always live, nothing cached" view already
 * on this platform ([UnlistedUndercutScreen], [RealizedTradesScreen]). A
 * character with genuinely full stock reads this page as "nothing to buy" -
 * that's an artifact of the all-zero starting point, not a real quote, so
 * this screen leans on the same manually-entered-quantities idea Stockpile
 * Status itself offers: the "Owned quantities" field below accepts a
 * type_id=qty per line, pre-filled from nothing, exactly mirroring what a
 * user would have already typed into Stockpile Status. */
@Composable
fun DoctrineShoppingListScreen(database: AppDatabase) {
    val scope = rememberCoroutineScope()
    val fittingRepo = remember { DoctrineFittingRepository(database) }
    val sdeRepo = remember { SdeRepository(database) }
    val tradingConfigRepo = remember { TradingConfigRepository(database) }
    val esi = remember { EsiClient() }
    val goonmetrics = remember { GoonmetricsClient() }

    var homeMarketSlug by remember { mutableStateOf("") }
    var ownedQuantitiesText by remember { mutableStateOf("") }
    var rows by remember { mutableStateOf<List<ShoppingList.Row>>(emptyList()) }
    var status by remember {
        mutableStateOf(
            "Optionally enter a home-market slug (a player structure's appraise.gnf.lt market, e.g. from " +
                "the URL of that market's own price page) and owned quantities, then Build List."
        )
    }
    var busy by remember { mutableStateOf(false) }

    /** "123=4\n456=10" -> {123: 4.0, 456: 10.0} - the same lightweight
     * manual-entry text shape Stockpile Status' per-row fields cover one at
     * a time; here it's one free-text box since this screen has no fixed
     * row list to attach per-row fields to before pricing runs. */
    fun parseOwnedQuantities(text: String): Map<Int, Double> =
        text.lines().mapNotNull { line ->
            val parts = line.split("=")
            if (parts.size != 2) return@mapNotNull null
            val typeId = parts[0].trim().toIntOrNull() ?: return@mapNotNull null
            val qty = parts[1].trim().toDoubleOrNull() ?: return@mapNotNull null
            typeId to qty
        }.toMap()

    fun buildList() {
        busy = true
        status = "Loading saved fittings and computing shortfalls..."
        rows = emptyList()
        scope.launch {
            try {
                val fittings = fittingRepo.listActive()
                if (fittings.isEmpty()) {
                    status = "No saved fittings yet - save one on the Fittings screen first."
                    return@launch
                }
                val owned = parseOwnedQuantities(ownedQuantitiesText)
                val stockpileRows = StockpileStatus.computeRows(
                    fittings = fittings, availableByType = owned,
                    typeName = { it.toString() }, // resolved for real below, once we know the shortfall set
                )
                val aggregated = StockpileStatus.aggregate(stockpileRows)
                    .filter { it.shortfall > 0 }
                if (aggregated.isEmpty()) {
                    status = "No shortfalls - nothing to buy (given the owned quantities entered above)."
                    return@launch
                }
                val typeIds = aggregated.map { it.typeId }
                val names = typeIds.associateWith { sdeRepo.typeName(it) ?: it.toString() }
                val renamedAggregated = aggregated.map { it.copy(typeName = names[it.typeId] ?: it.typeName) }

                status = "Fetching prices..."
                val tradingCfg = tradingConfigRepo.load()
                val home = ShoppingList.fetchHomePrices(goonmetrics, homeMarketSlug.trim().ifBlank { null })
                val jita = ShoppingList.fetchJitaPrices(esi, tradingCfg, typeIds)
                val volumes = ShoppingList.fetchVolumes(esi, typeIds)

                rows = ShoppingList.build(
                    aggregatedRows = renamedAggregated, home = home, jita = jita, volumeByType = volumes,
                    importCostPerM3 = tradingCfg.importCostPerM3, jitaBuyBrokerFee = tradingCfg.jitaBuyBrokerFee,
                )
                val totalCost = rows.sumOf { it.totalCost ?: 0.0 }
                val unpriceable = rows.count { it.recommendedSource == null }
                status = if (unpriceable == 0) {
                    "%,.2f ISK total across ${rows.size} shortfall item(s).".format(totalCost)
                } else {
                    "%,.2f ISK across priceable items - $unpriceable item(s) have no C-J or Jita sell orders right now."
                        .format(totalCost)
                }
            } catch (e: Exception) {
                status = e.message ?: "Could not build the shopping list."
            } finally {
                busy = false
            }
        }
    }

    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        Text("Doctrine - Shopping List", style = MaterialTheme.typography.titleLarge)
        Text(
            "Buy-C-J-vs-Buy-Jita only - no Build option, see this screen's own docstring.",
            style = MaterialTheme.typography.bodySmall,
            modifier = Modifier.padding(vertical = 4.dp),
        )
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(bottom = 8.dp)) {
            OutlinedTextField(
                value = homeMarketSlug,
                onValueChange = { homeMarketSlug = it },
                label = { Text("Home (C-J) market slug, optional") },
                modifier = Modifier.fillMaxWidth(),
            )
        }
        OutlinedTextField(
            value = ownedQuantitiesText,
            onValueChange = { ownedQuantitiesText = it },
            label = { Text("Owned quantities, one per line as type_id=qty (optional)") },
            modifier = Modifier.fillMaxWidth().padding(bottom = 8.dp),
        )
        Button(onClick = { buildList() }, enabled = !busy) { Text("Build List") }
        if (busy) LinearProgressIndicator(modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp))
        Text(status, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(vertical = 8.dp))

        LazyColumn(modifier = Modifier.fillMaxWidth()) {
            items(rows) { row ->
                val subtitle = buildString {
                    append("Shortfall ${row.shortfall.toInt()}")
                    row.cjPrice?.let { append(" · C-J %,.2f".format(it)) }
                    row.jitaLandedPrice?.let { append(" · Jita landed %,.2f".format(it)) }
                    append(" · recommended: ${row.recommendedSource ?: "none priced"}")
                }
                ListItem(
                    headlineContent = { Text(row.typeName) },
                    supportingContent = { Text(subtitle) },
                    trailingContent = {
                        Text(row.totalCost?.let { "%,.2f".format(it) } ?: "-")
                    },
                )
                HorizontalDivider()
            }
        }
    }
}
