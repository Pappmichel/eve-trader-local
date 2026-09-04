package com.pappmichel.evetraderlocal.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
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
import com.pappmichel.evetraderlocal.data.auth.TokenManager
import com.pappmichel.evetraderlocal.data.db.AppDatabase
import com.pappmichel.evetraderlocal.data.esi.EsiClient
import com.pappmichel.evetraderlocal.data.esi.OrderStats
import com.pappmichel.evetraderlocal.data.refining.NOT_REPROCESSABLE_DECISION
import com.pappmichel.evetraderlocal.data.refining.NO_MARKET_DATA_DECISION
import com.pappmichel.evetraderlocal.data.refining.REPROCESS_DECISION
import com.pappmichel.evetraderlocal.data.refining.ReprocessingQuoteRow
import com.pappmichel.evetraderlocal.data.refining.RefiningConfigRepository
import com.pappmichel.evetraderlocal.data.refining.evaluateReprocessingLine
import com.pappmichel.evetraderlocal.data.refining.mergeDuplicateStacks
import com.pappmichel.evetraderlocal.data.refining.parsePaste
import com.pappmichel.evetraderlocal.data.refining.reprocessingQuoteTotals
import com.pappmichel.evetraderlocal.data.refining.resolveTypeId
import com.pappmichel.evetraderlocal.data.sde.SdeRepository
import com.pappmichel.evetraderlocal.data.trading.TradingConfigRepository
import kotlinx.coroutines.launch

/** Ore & Minerals -> Reprocessing Quote: a Kotlin port of the desktop
 * build's `do_quote_reprocessing` (GitHub issue #92) - paste an EVE
 * Inventory-window "Copy As" list, get a sell-as-is-vs-reprocess
 * recommendation and totals for every line. See
 * `data/refining/ReprocessingQuote.kt`'s own module docstring for the full
 * scope decision (why this view and not Ore Shortlist/Mineral Shopping
 * List) and the one simplification vs. desktop (no Goonmetrics fallback
 * when no seller token is registered - same documented gap as this port's
 * own Shortlist/Undercut screens).
 *
 * Needs a registered "seller" character with docking access at the
 * configured structure (Settings' `structureId`, reused from Trading - see
 * `RefiningConfig.kt` for why Refining has no structure field of its own
 * yet) to price anything at all; without one this screen still parses and
 * resolves items, it just cannot quote a value for them. */
@Composable
fun ReprocessingQuoteScreen(database: AppDatabase, tokenManager: TokenManager) {
    val scope = rememberCoroutineScope()
    val tradingConfigRepo = remember { TradingConfigRepository(database) }
    val refiningConfigRepo = remember { RefiningConfigRepository(database) }
    val sdeRepo = remember { SdeRepository(database) }
    val esi = remember { EsiClient() }

    var pasteText by remember { mutableStateOf("") }
    var rows by remember { mutableStateOf<List<ReprocessingQuoteRow>>(emptyList()) }
    var status by remember { mutableStateOf("Paste an Inventory window's \"Copy As\" list, then Quote.") }
    var busy by remember { mutableStateOf(false) }

    fun quote() {
        val text = pasteText
        if (text.isBlank()) {
            status = "Paste is empty - copy items from an Inventory window's list view first."
            return
        }
        busy = true
        status = "Parsing..."
        scope.launch {
            try {
                val allLines = parsePaste(text)
                val errorLines = allLines.filter { it.error != null }
                val parsed = mergeDuplicateStacks(allLines)
                if (parsed.isEmpty() && errorLines.isEmpty()) {
                    status = "Could not parse any items from the paste."
                    rows = emptyList()
                    return@launch
                }

                status = "Resolving items against the SDE cache..."
                val typeIdByLine = parsed.associateWith { resolveTypeId(sdeRepo, it.name) }
                val resolvedTypeIds = typeIdByLine.values.filterNotNull()

                val mineralIds = sortedSetOf<Int>()
                val portionSizeByType = HashMap<Int, Int?>()
                val materialsByType = HashMap<Int, List<Pair<Int, Double>>>()
                for (typeId in resolvedTypeIds) {
                    portionSizeByType[typeId] = sdeRepo.portionSize(typeId)
                    val materials = sdeRepo.typeMaterials(typeId)
                    materialsByType[typeId] = materials
                    materials.forEach { (materialTypeId, _) -> mineralIds.add(materialTypeId) }
                }
                val allIds = (resolvedTypeIds + mineralIds).distinct()

                val tradingCfg = tradingConfigRepo.load()
                val refiningCfg = refiningConfigRepo.load()

                var statsById: Map<Int, OrderStats> = emptyMap()
                val sellerRecord = tokenManager.listRecords("seller").firstOrNull()
                val structureId = tradingCfg.structureId
                if (sellerRecord != null && structureId != null && allIds.isNotEmpty()) {
                    status = "Fetching structure order book..."
                    try {
                        val sellerToken = tokenManager.getToken(sellerRecord.role)
                        statsById = esi.structureOrderStatsBulk(structureId, allIds, sellerToken.accessToken)
                    } catch (e: Exception) {
                        status = "Could not fetch the structure's order book (${e.message}) - quoting without prices."
                    }
                } else if (structureId == null) {
                    status = "No structure configured yet (see Settings) - quoting without prices."
                } else {
                    status = "No seller character registered yet (see Characters) - quoting without prices."
                }

                val evaluated = errorLines.map { errLine ->
                    ReprocessingQuoteRow(
                        name = errLine.name, quantity = errLine.quantity, typeId = null, category = errLine.category,
                        sellAsIsValue = null, refinedValue = null, mineralValue = null, refiningTax = null,
                        decision = "Unknown item", error = errLine.error,
                    )
                } + parsed.map { pLine ->
                    val typeId = typeIdByLine[pLine]
                    evaluateReprocessingLine(
                        line = pLine,
                        typeId = typeId,
                        portionSize = typeId?.let { portionSizeByType[it] },
                        materials = typeId?.let { materialsByType[it] } ?: emptyList(),
                        itemStats = typeId?.let { statsById[it] },
                        mineralStatsById = statsById,
                        tradingCfg = tradingCfg,
                        refiningCfg = refiningCfg,
                    )
                }
                rows = evaluated
                val totals = reprocessingQuoteTotals(evaluated)
                status = "${evaluated.size} line(s) - ${totals.reprocessCount} recommend reprocessing " +
                    "(mineral value %,.2f, sell-as-is total %,.2f).".format(totals.totalRefinedValue, totals.totalSellAsIsValue)
            } catch (e: Exception) {
                status = e.message ?: "Quote failed."
            } finally {
                busy = false
            }
        }
    }

    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        Text("Reprocessing Quote", style = MaterialTheme.typography.titleMedium)
        OutlinedTextField(
            value = pasteText,
            onValueChange = { pasteText = it },
            label = { Text("Paste (Inventory window, Ctrl+A / Ctrl+C)") },
            modifier = Modifier.fillMaxWidth().height(120.dp),
        )
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(vertical = 8.dp)) {
            Button(enabled = !busy, onClick = { quote() }) { Text("Quote") }
        }
        if (busy) {
            LinearProgressIndicator(modifier = Modifier.fillMaxWidth().padding(bottom = 8.dp))
        }
        Text(status, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(bottom = 8.dp))

        LazyColumn {
            items(rows) { row -> ReprocessingQuoteRowItem(row) }
        }
    }
}

@Composable
private fun ReprocessingQuoteRowItem(row: ReprocessingQuoteRow) {
    val subtitle = row.error ?: buildString {
        append("qty ${row.quantity}")
        row.sellAsIsValue?.let { append(" · sell-as-is %,.2f".format(it)) }
        row.refinedValue?.let { append(" · refined %,.2f".format(it)) }
    }
    ListItem(
        headlineContent = { Text(row.name) },
        supportingContent = { Text(subtitle) },
        trailingContent = { Text(row.decision, color = decisionColor(row.decision)) },
    )
    HorizontalDivider()
}

@Composable
private fun decisionColor(decision: String) = when (decision) {
    REPROCESS_DECISION -> MaterialTheme.colorScheme.primary
    NOT_REPROCESSABLE_DECISION, NO_MARKET_DATA_DECISION -> MaterialTheme.colorScheme.outline
    else -> MaterialTheme.colorScheme.onSurface
}
