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
import com.pappmichel.evetraderlocal.data.production.AssetPlanJob
import com.pappmichel.evetraderlocal.data.production.LiveStockSource
import com.pappmichel.evetraderlocal.data.production.PlanAssetOptimizedResult
import com.pappmichel.evetraderlocal.data.production.ProductionConfigRepository
import com.pappmichel.evetraderlocal.data.production.StockPlanRepository
import com.pappmichel.evetraderlocal.data.production.homePrices
import com.pappmichel.evetraderlocal.data.production.jitaPrices
import com.pappmichel.evetraderlocal.data.production.planAssetOptimized
import com.pappmichel.evetraderlocal.data.production.structuralMaterialClosure
import com.pappmichel.evetraderlocal.data.sde.SdeRepository
import com.pappmichel.evetraderlocal.data.trading.TradingConfigRepository
import kotlinx.coroutines.launch

private fun fmtIsk(value: Double?): String = value?.let { "%,.2f ISK".format(it) } ?: "not priceable"
private fun fmtQty(value: Double): String = "%,.2f".format(value)
private fun fmtPct(value: Double?): String = value?.let { "%.1f%%".format(it * 100) } ?: "n/a"
private fun fmtCoverage(value: Double?): String = value?.let { "%.1f%%".format(it * 100) } ?: "n/a"

/** Production -> Asset-Optimized Planner: the readiness-focused second
 * planner - a genuine port of `engine.plan_asset_optimized`, now that
 * `data/production/ProductionAssetOptimizedEngine.kt` exists (see that
 * file's own module docstring for the full algorithm and how it differs
 * from `plan_production`'s own already-ported Stock Planner). Replaces the
 * `PlaceholderScreen` this tool/view previously routed to.
 *
 * Same stock-target precondition as Stock Planner, but stock targets are
 * managed there, not duplicated here - this screen is planner-output-only,
 * mirroring desktop's own `production_asset_optimized.py` view exactly
 * ("Same stock-target precondition as the Planner view, but stock targets
 * are managed there, not duplicated here"). A single "Run Planner" button,
 * no configuration inputs of its own.
 *
 * Same live-stock/live-pricing plumbing [StockPlannerScreen] already
 * established: [LiveStockSource] for owned/incoming stock (character
 * assets/industry jobs only, no corp assets yet), `homePrices`/`jitaPrices`
 * for the structural material closure of every configured stock target. */
@Composable
fun AssetOptimizedPlannerScreen(database: AppDatabase, tokenManager: TokenManager) {
    val scope = rememberCoroutineScope()
    val stockPlanRepo = remember { StockPlanRepository(database) }
    val sdeRepo = remember { SdeRepository(database) }
    val productionConfigRepo = remember { ProductionConfigRepository(database) }
    val tradingConfigRepo = remember { TradingConfigRepository(database) }
    val esi = remember { EsiClient() }
    val stockSource = remember { LiveStockSource(tokenManager, esi) }

    var busy by remember { mutableStateOf(false) }
    var status by remember { mutableStateOf("Click Run Planner to see which jobs are startable right now.") }
    var plan by remember { mutableStateOf<PlanAssetOptimizedResult?>(null) }

    fun runPlanner() {
        busy = true
        plan = null
        status = "Planning readiness against configured stock targets (this can take a while)..."
        scope.launch {
            try {
                val stockTargets = stockPlanRepo.loadStockTargets()
                if (stockTargets.isEmpty()) {
                    status = "No stock targets configured. Add one in Stock Planner first."
                    return@launch
                }
                val cfg = productionConfigRepo.load()
                val tradingCfg = tradingConfigRepo.load()
                val manualStockMap = stockPlanRepo.loadManualStock()
                val manualOverrides = stockPlanRepo.loadManualBuildBuy()

                val seedTypeIds = stockTargets.map { it.typeId }
                val closure = structuralMaterialClosure(seedTypeIds, sdeRepo)
                val pricedTypeIds = (seedTypeIds.toSet() + closure).toList()
                val home = homePrices(pricedTypeIds, cfg, tokenManager, esi)
                val jita = jitaPrices(pricedTypeIds, tradingCfg.jitaRegionId, esi)

                val result = planAssetOptimized(
                    cfg, stockTargets, manualStockMap, manualOverrides, home, jita, sdeRepo, stockSource,
                ) { typeId -> sdeRepo.type(typeId)?.typeName ?: typeId.toString() }
                plan = result

                status = if (result.jobs.isEmpty()) {
                    "Nothing to build right now."
                } else {
                    "${result.jobs.size} job(s) planned."
                }
            } catch (e: Exception) {
                status = e.message ?: "Failed to compute the asset-optimized plan."
            } finally {
                busy = false
            }
        }
    }

    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        Text("Asset-Optimized Planner", style = MaterialTheme.typography.titleSmall)
        Button(enabled = !busy, onClick = { runPlanner() }) { Text("Run Planner") }
        if (busy) {
            LinearProgressIndicator(modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp))
        }
        Text(status, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(vertical = 8.dp))
        HorizontalDivider(modifier = Modifier.padding(vertical = 8.dp))

        LazyColumn(modifier = Modifier.weight(1f)) {
            plan?.let { result ->
                items(result.jobs, key = { it.typeId }) { job -> AssetPlanJobRow(job) }
            }
        }
    }
}

@Composable
private fun AssetPlanJobRow(job: AssetPlanJob) {
    Column(modifier = Modifier.padding(vertical = 4.dp)) {
        Text("${job.typeName}: ${job.jobRuns} run(s), ${job.runsReadyNow} ready now", style = MaterialTheme.typography.bodyMedium)
        Text(
            "Qty ${fmtQty(job.quantity)} @ ${fmtIsk(job.unitBuildCost)}/unit - margin ${fmtPct(job.margin)}" +
                " - stock coverage ${fmtCoverage(job.stockCoverage)}",
            style = MaterialTheme.typography.bodySmall,
        )
    }
    HorizontalDivider()
}
