package com.pappmichel.evetraderlocal.ui.screens

import androidx.compose.foundation.layout.Column
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
import com.pappmichel.evetraderlocal.data.production.BuildCandidateRow
import com.pappmichel.evetraderlocal.data.production.ProductionConfigRepository
import com.pappmichel.evetraderlocal.data.production.homePrices
import com.pappmichel.evetraderlocal.data.production.scanBuildCandidates
import com.pappmichel.evetraderlocal.data.production.structuralMaterialClosure
import com.pappmichel.evetraderlocal.data.sde.SdeRepository
import kotlinx.coroutines.launch

private fun fmtIsk(value: Double): String = "%,.2f ISK".format(value)
private fun fmtPct(value: Double): String = "%.1f%%".format(value * 100)

/** Production -> Build Candidates: a whole-catalog buy-vs-build scan - a
 * Kotlin port of the desktop build's `engine.discover_build_candidates`,
 * reusing [com.pappmichel.evetraderlocal.data.production.unitBuildCost]/
 * [com.pappmichel.evetraderlocal.data.production.marginHome] (the same
 * math `ShipMarginScreen` already exercises one item at a time) across
 * every manufacturable type the SDE cache knows about. See
 * `ProductionBuildCandidates.kt`'s own module docstring for the full list
 * of differences from desktop (no Jita cross-shopping in the scan, no
 * Goonmetrics daily-movement ranking, no stock-target exclusion).
 *
 * Needs `ProductionConfig.homeLocationId` configured and a Producer
 * character logged in with structure-docking access (same gap
 * `ShipMarginScreen`/`ProductionPricing.kt` already document) - with
 * neither, every candidate has no home quote to compare against and the
 * scan correctly returns nothing rather than guessing off Jita alone
 * (desktop's own scan is deliberately home-structure-only too, see that
 * function's own docstring). */
@Composable
fun ProductionBuildCandidatesScreen(database: AppDatabase, tokenManager: TokenManager) {
    val scope = rememberCoroutineScope()
    val sdeRepo = remember { SdeRepository(database) }
    val productionConfigRepo = remember { ProductionConfigRepository(database) }
    val esi = remember { EsiClient() }

    var busy by remember { mutableStateOf(false) }
    var status by remember { mutableStateOf("Tap Scan to look for build candidates.") }
    var results by remember { mutableStateOf<List<BuildCandidateRow>>(emptyList()) }

    fun scan() {
        busy = true
        results = emptyList()
        status = "Loading the manufacturable catalog..."
        scope.launch {
            try {
                val cfg = productionConfigRepo.load()
                if (cfg.homeLocationId == null) {
                    status = "Set a home structure ID under Settings first - this scan is home-market only."
                    return@launch
                }
                val candidates = sdeRepo.manufacturableTypes()
                if (candidates.isEmpty()) {
                    status = "No manufacturable types cached - refresh the SDE cache first."
                    return@launch
                }
                status = "Pricing ${candidates.size} manufacturable type(s)..."
                val closure = structuralMaterialClosure(candidates.map { it.typeId }, sdeRepo)
                val allIds = (candidates.map { it.typeId } + closure).distinct()
                val home = homePrices(allIds, cfg, tokenManager, esi)
                if (home.isEmpty()) {
                    status = "No home-structure quotes at all - check the Producer login and structure ID."
                    return@launch
                }
                results = scanBuildCandidates(candidates, home, cfg, sdeRepo)
                status = "${results.size} candidate(s) clearing ${fmtPct(cfg.minMargin)} margin."
            } catch (e: Exception) {
                status = e.message ?: "Scan failed."
            } finally {
                busy = false
            }
        }
    }

    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        Button(enabled = !busy, onClick = { scan() }) { Text("Scan") }
        if (busy) {
            LinearProgressIndicator(modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp))
        }
        Text(status, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(vertical = 8.dp))

        LazyColumn(modifier = Modifier.weight(1f)) {
            items(results) { row ->
                ListItem(
                    headlineContent = { Text(row.typeName) },
                    supportingContent = { Text("Build cost: ${fmtIsk(row.buildCost)}") },
                    trailingContent = { Text(fmtPct(row.margin)) },
                )
                HorizontalDivider()
            }
        }
    }
}
