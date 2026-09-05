package com.pappmichel.evetraderlocal.ui.screens

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material3.Button
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenu
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.LinearProgressIndicator
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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.pappmichel.evetraderlocal.data.auth.TokenManager
import com.pappmichel.evetraderlocal.data.db.AppDatabase
import com.pappmichel.evetraderlocal.data.esi.EsiClient
import com.pappmichel.evetraderlocal.data.production.DECRYPTORS
import com.pappmichel.evetraderlocal.data.production.InventionResult
import com.pappmichel.evetraderlocal.data.production.ProductionConfigRepository
import com.pappmichel.evetraderlocal.data.production.bestRecipeForDecryptor
import com.pappmichel.evetraderlocal.data.production.compareRecipesAndDecryptors
import com.pappmichel.evetraderlocal.data.production.homePrices
import com.pappmichel.evetraderlocal.data.production.jitaPrices
import com.pappmichel.evetraderlocal.data.production.reducibleMaterialCost
import com.pappmichel.evetraderlocal.data.sde.SdeRepository
import com.pappmichel.evetraderlocal.data.trading.TradingConfigRepository
import kotlinx.coroutines.launch

private fun fmtIsk(value: Double?): String = value?.let { "%,.0f".format(it) } ?: "n/a"
private fun fmtPct(value: Double): String = "%.1f%%".format(value * 100)

/** Not a real EVE decryptor, only this screen's own "let the comparison
 * table pick" option - the counterpart of the desktop GUI's
 * `_NO_DECRYPTOR` combo entry (`production_invention_estimator.py`). */
private const val COMPARE_ALL = "(compare all decryptors)"

/** Production -> Invention Estimator: a Kotlin port of the desktop build's
 * `InventionEstimatorView`/`do_estimate_invention` - resolves a T2/T3
 * blueprint's *product* name (e.g. "Damage Control II Blueprint") to its
 * invention recipe(s) and shows every (invention source, decryptor)
 * combination side by side, cheapest net cost per BPC run first
 * ([compareRecipesAndDecryptors]), or narrows to one decryptor across every
 * source grade if one is picked ([bestRecipeForDecryptor]) - same two modes
 * the desktop view's decryptor combo box offers.
 *
 * See `data/production/InventionEstimator.kt`'s own module docstring for
 * the underlying math and its real (not scoped-down) Tech III support - a
 * correction of an earlier, wrong conclusion that Tech III needed
 * infrastructure this platform doesn't have. The one genuine gap this
 * screen inherits is the same one every other Production screen already
 * documents: home-market pricing needs `ProductionConfig.homeLocationId`
 * configured and a Producer character logged in with structure-docking
 * access (see `ShipMarginScreen.kt`) - without either, only the Jita side
 * of every price is used, same as everywhere else in this app. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun InventionEstimatorScreen(database: AppDatabase, tokenManager: TokenManager) {
    val scope = rememberCoroutineScope()
    val sdeRepo = remember { SdeRepository(database) }
    val tradingConfigRepo = remember { TradingConfigRepository(database) }
    val productionConfigRepo = remember { ProductionConfigRepository(database) }
    val esi = remember { EsiClient() }

    var productText by remember { mutableStateOf("") }
    var decryptorExpanded by remember { mutableStateOf(false) }
    var selectedDecryptor by remember { mutableStateOf(COMPARE_ALL) }
    var busy by remember { mutableStateOf(false) }
    var status by remember { mutableStateOf("Enter a T2/T3 blueprint name and tap Estimate.") }
    var results by remember { mutableStateOf<List<InventionResult>>(emptyList()) }

    fun runEstimate() {
        val productName = productText.trim()
        if (productName.isEmpty()) {
            status = "Enter a product name first."
            return
        }
        busy = true
        results = emptyList()
        status = "Estimating invention for \"$productName\"..."
        scope.launch {
            try {
                val productTypeId = sdeRepo.resolveInventionProductTypeId(productName)
                if (productTypeId == null) {
                    status = "No invention recipe found for \"$productName\". Exact name? Refresh SDE first?"
                    return@launch
                }
                val cfg = productionConfigRepo.load()
                val tradingCfg = tradingConfigRepo.load()
                // Direct materials of the T2/T3 blueprint itself, plus its
                // decryptors/datacores/candidate sources, all get priced -
                // a flat type id list is enough since reducibleMaterialCost
                // only ever looks at direct (not recursive) materials, same
                // as invention.py's own call site.
                val directMaterials = sdeRepo.blueprintMaterials(productTypeId).map { it.first }
                val decryptorIds = DECRYPTORS.values.mapNotNull { it.typeId.takeIf { id -> id != 0 } }
                val candidateIds = sdeRepo.inventionRecipeCandidates(productTypeId)
                val recipeMaterialIds = candidateIds.flatMap { candidate ->
                    val recipe = sdeRepo.inventionRecipe(candidate)
                    (recipe?.datacores?.map { it.first } ?: emptyList()) + candidate
                }
                val typeIds = (directMaterials + decryptorIds + candidateIds + recipeMaterialIds).distinct()
                val home = homePrices(typeIds, cfg, tokenManager, esi)
                val jita = jitaPrices(typeIds, tradingCfg.jitaRegionId, esi)
                val reducibleCost = reducibleMaterialCost(productTypeId, home, jita, sdeRepo, cfg)

                results = if (selectedDecryptor == COMPARE_ALL) {
                    compareRecipesAndDecryptors(productTypeId, home, jita, cfg, sdeRepo, reducibleCost)
                } else {
                    listOfNotNull(
                        bestRecipeForDecryptor(productTypeId, selectedDecryptor, home, jita, cfg, sdeRepo, reducibleCost)
                    )
                }
                status = if (results.isEmpty()) {
                    "No invention recipe/decryptor combination found for that product."
                } else {
                    "${results.size} combination(s), cheapest net cost/run first."
                }
            } catch (e: Exception) {
                status = e.message ?: "Estimate failed."
            } finally {
                busy = false
            }
        }
    }

    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        OutlinedTextField(
            value = productText,
            onValueChange = { productText = it },
            label = { Text("T2/T3 blueprint (e.g. Damage Control II Blueprint)") },
            modifier = Modifier.fillMaxWidth(),
        )
        Row(
            modifier = Modifier.padding(top = 8.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            ExposedDropdownMenuBox(
                expanded = decryptorExpanded,
                onExpandedChange = { decryptorExpanded = it },
                modifier = Modifier.weight(1f),
            ) {
                OutlinedTextField(
                    value = selectedDecryptor,
                    onValueChange = {},
                    readOnly = true,
                    label = { Text("Decryptor") },
                    trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = decryptorExpanded) },
                    modifier = Modifier.menuAnchor().fillMaxWidth(),
                )
                ExposedDropdownMenu(expanded = decryptorExpanded, onDismissRequest = { decryptorExpanded = false }) {
                    (listOf(COMPARE_ALL) + DECRYPTORS.keys).forEach { name ->
                        DropdownMenuItem(
                            text = { Text(name) },
                            onClick = {
                                selectedDecryptor = name
                                decryptorExpanded = false
                            },
                        )
                    }
                }
            }
            Button(enabled = !busy, onClick = { runEstimate() }) { Text("Estimate") }
        }
        if (busy) {
            LinearProgressIndicator(modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp))
        }
        Text(status, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(vertical = 8.dp))

        if (results.isNotEmpty()) {
            HorizontalDivider()
            val best = results.first()
            Text(
                "Recommended: ${best.decryptor} on ${best.t1BlueprintName} " +
                    "(net ${fmtIsk(best.netCostPerRun)} ISK/run)",
                style = MaterialTheme.typography.titleSmall,
                modifier = Modifier.padding(vertical = 8.dp),
            )
            // The Row itself takes the remaining vertical space (weight(1f)
            // in the root Column below) so the LazyColumn nested inside it
            // has a real bounded height to scroll within, rather than an
            // unbounded one - horizontalScroll here is for the wide table
            // (many columns), the LazyColumn's own vertical scroll is for
            // the row count.
            Row(modifier = Modifier.weight(1f).horizontalScroll(rememberScrollState())) {
                Column {
                    ResultHeaderRow()
                    LazyColumn {
                        items(results) { r -> ResultRow(r) }
                    }
                }
            }
        }
    }
}

private val COLUMN_WIDTH = 110.dp
private val NAME_COLUMN_WIDTH = 200.dp

@Composable
private fun ResultHeaderRow() {
    Row {
        HeaderCell("Source", NAME_COLUMN_WIDTH)
        HeaderCell("Decryptor", COLUMN_WIDTH)
        HeaderCell("Probability", COLUMN_WIDTH)
        HeaderCell("Runs", COLUMN_WIDTH)
        HeaderCell("ME/TE", COLUMN_WIDTH)
        HeaderCell("Datacore", COLUMN_WIDTH)
        HeaderCell("Decryptor Cost", COLUMN_WIDTH)
        HeaderCell("Relic Cost", COLUMN_WIDTH)
        HeaderCell("Total Attempt", COLUMN_WIDTH)
        HeaderCell("Expected/Run", COLUMN_WIDTH)
        HeaderCell("Savings/Run", COLUMN_WIDTH)
        HeaderCell("Net/Run", COLUMN_WIDTH)
    }
    HorizontalDivider()
}

@Composable
private fun HeaderCell(text: String, width: androidx.compose.ui.unit.Dp) {
    Text(
        text,
        style = MaterialTheme.typography.labelSmall,
        fontWeight = FontWeight.Bold,
        modifier = Modifier.width(width).padding(4.dp),
    )
}

@Composable
private fun Cell(text: String, width: androidx.compose.ui.unit.Dp) {
    Text(text, style = MaterialTheme.typography.bodySmall, modifier = Modifier.width(width).padding(4.dp))
}

@Composable
private fun ResultRow(r: InventionResult) {
    Column {
        Row {
            Cell(r.t1BlueprintName, NAME_COLUMN_WIDTH)
            Cell(r.decryptor, COLUMN_WIDTH)
            Cell(fmtPct(r.probability), COLUMN_WIDTH)
            Cell(r.outputRuns.toString(), COLUMN_WIDTH)
            Cell("${r.me}/${r.te}", COLUMN_WIDTH)
            Cell(fmtIsk(r.datacoreCost), COLUMN_WIDTH)
            Cell(fmtIsk(r.decryptorCost), COLUMN_WIDTH)
            Cell(fmtIsk(r.relicCost), COLUMN_WIDTH)
            Cell(fmtIsk(r.totalAttemptCost), COLUMN_WIDTH)
            Cell(fmtIsk(r.expectedCostPerRun), COLUMN_WIDTH)
            Cell(fmtIsk(r.materialSavingsPerRun), COLUMN_WIDTH)
            Cell(fmtIsk(r.netCostPerRun), COLUMN_WIDTH)
        }
        HorizontalDivider()
    }
}
