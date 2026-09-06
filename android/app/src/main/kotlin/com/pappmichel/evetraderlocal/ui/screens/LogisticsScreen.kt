package com.pappmichel.evetraderlocal.ui.screens

import androidx.compose.foundation.layout.Arrangement
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
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
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
import com.pappmichel.evetraderlocal.data.production.BuildOrBuyDecision
import com.pappmichel.evetraderlocal.data.production.CategoryLocationRepository
import com.pappmichel.evetraderlocal.data.production.DistributionRow
import com.pappmichel.evetraderlocal.data.production.JOB_CATEGORIES
import com.pappmichel.evetraderlocal.data.production.LiveLocationStockSource
import com.pappmichel.evetraderlocal.data.production.LiveStockSource
import com.pappmichel.evetraderlocal.data.production.LogisticsRow
import com.pappmichel.evetraderlocal.data.production.ManualBuildBuyEntity
import com.pappmichel.evetraderlocal.data.production.ProductionConfigRepository
import com.pappmichel.evetraderlocal.data.production.StockPlanRepository
import com.pappmichel.evetraderlocal.data.production.categorizeBuildList
import com.pappmichel.evetraderlocal.data.production.distributionRecommendations
import com.pappmichel.evetraderlocal.data.production.homePrices
import com.pappmichel.evetraderlocal.data.production.jitaPrices
import com.pappmichel.evetraderlocal.data.production.logisticsStatus
import com.pappmichel.evetraderlocal.data.production.planProduction
import com.pappmichel.evetraderlocal.data.production.structuralMaterialClosure
import com.pappmichel.evetraderlocal.data.sde.SdeRepository
import com.pappmichel.evetraderlocal.data.trading.TradingConfigRepository
import kotlinx.coroutines.launch

private fun fmtQty(value: Double): String = "%,.2f".format(value)

/** Production -> Logistics: the multi-structure "where is this category
 * short, and where should I pull it from" pair (GitHub issue #4), now that
 * [planProduction] (`ProductionEngine.kt`) exists to feed
 * [logisticsStatus]/[distributionRecommendations] a real build list -
 * replaces this menu entry's previous `PlaceholderScreen`.
 *
 * **Two of the desktop view's four report tabs are real ports here:
 * Logistics Status and Distribution Recommendations** - see
 * `data/production/LogisticsEngine.kt`'s own module docstring for exactly
 * what closed the two gaps that were blocking them (a `job_category`
 * classification for [BuildJobEntry], and a location-scoped stock read
 * `StockSource` itself can't answer) and why both are genuine ports, not
 * substitutes. Category-location assignments (the config both reports
 * consume) are real Room-backed CRUD ([CategoryLocationRepository]),
 * mirroring desktop's own `job_category_locations`/
 * `category_location_options` tables. Manual Build/Buy overrides reuses
 * [StockPlanRepository]'s existing table (unchanged from the Stock Planner
 * port) - listed here too since Logistics is where desktop keeps this
 * config, not because anything about the table itself is new.
 *
 * **Invention Logistics and T1 BPC Invention Needs are NOT ported** - both
 * still need `plan_production`'s `invention_list` (recommended invention
 * runs/decryptor per Tech II/III stock target), which [PlanProductionResult]
 * has no field for at all on a Manufacturing-only SDE cache (see
 * `ProductionEngine.kt`'s own module docstring). This screen says so
 * directly rather than rendering an empty/fake table for either.
 * "Resolve Structure Name" is likewise not wired - `EsiClient.kt` still has
 * no structure/corporation-name endpoint (re-checked for this task; see
 * `LogisticsEngine.kt`'s own module docstring for the exact grep). Location
 * IDs below are entered/shown as bare numeric structure IDs, same
 * limitation the desktop view's own "Resolve Structure Name" helper exists
 * to soften and this port still can't. */
@Composable
fun LogisticsScreen(database: AppDatabase, tokenManager: TokenManager) {
    val scope = rememberCoroutineScope()
    val categoryLocationRepo = remember { CategoryLocationRepository(database) }
    val stockPlanRepo = remember { StockPlanRepository(database) }
    val sdeRepo = remember { SdeRepository(database) }
    val productionConfigRepo = remember { ProductionConfigRepository(database) }
    val tradingConfigRepo = remember { TradingConfigRepository(database) }
    val esi = remember { EsiClient() }
    val stockSource = remember { LiveStockSource(tokenManager, esi) }
    val locationStockSource = remember { LiveLocationStockSource(tokenManager, esi) }

    var activeLocations by remember { mutableStateOf<Map<String, Long>>(emptyMap()) }
    var options by remember { mutableStateOf<Map<String, List<Long>>>(emptyMap()) }
    var locationInputs by remember { mutableStateOf<Map<String, String>>(emptyMap()) }
    var overrides by remember { mutableStateOf<List<ManualBuildBuyEntity>>(emptyList()) }
    var overrideNameText by remember { mutableStateOf("") }
    var overrideBuild by remember { mutableStateOf(true) }

    var busy by remember { mutableStateOf(false) }
    var status by remember { mutableStateOf("Assign at least one category to a location, then refresh a report.") }
    var logisticsRows by remember { mutableStateOf<List<LogisticsRow>?>(null) }
    var distributionRows by remember { mutableStateOf<List<DistributionRow>?>(null) }

    suspend fun reloadConfig() {
        activeLocations = categoryLocationRepo.loadCategoryLocations()
        options = categoryLocationRepo.loadCategoryLocationOptions()
        overrides = stockPlanRepo.listManualBuildBuy()
    }

    LaunchedEffect(Unit) { reloadConfig() }

    fun locationIdFor(category: String): Long? = locationInputs[category]?.trim()?.toLongOrNull()

    fun setActive(category: String) {
        val locationId = locationIdFor(category)
        if (locationId == null) {
            status = "Enter a whole-number location ID for '$category' first."
            return
        }
        scope.launch {
            categoryLocationRepo.setCategoryLocation(category, locationId)
            status = "'$category' -> $locationId."
            reloadConfig()
        }
    }

    fun clearActive(category: String) {
        scope.launch {
            categoryLocationRepo.clearCategoryLocation(category)
            status = "Cleared '$category''s active location."
            reloadConfig()
        }
    }

    fun addOption(category: String) {
        val locationId = locationIdFor(category)
        if (locationId == null) {
            status = "Enter a whole-number location ID for '$category' first."
            return
        }
        scope.launch {
            categoryLocationRepo.addCategoryLocationOption(category, locationId)
            status = "Added $locationId as an option for '$category'."
            reloadConfig()
        }
    }

    fun removeOption(category: String, locationId: Long) {
        scope.launch {
            categoryLocationRepo.removeCategoryLocationOption(category, locationId)
            status = "Removed $locationId from '$category''s options."
            reloadConfig()
        }
    }

    fun addOverride() {
        scope.launch {
            val trimmed = overrideNameText.trim()
            val typeId = trimmed.toIntOrNull() ?: sdeRepo.resolveTypeIdByName(trimmed)
            val typeName = typeId?.let { sdeRepo.type(it)?.typeName }
            if (typeId == null || typeName == null) {
                status = "No exact match for '$overrideNameText'."
                return@launch
            }
            stockPlanRepo.upsertManualBuildBuy(typeId, if (overrideBuild) BuildOrBuyDecision.BUILD else BuildOrBuyDecision.BUY)
            overrideNameText = ""
            status = "Added manual override: $typeName."
            reloadConfig()
        }
    }

    /** Shared by both refresh actions - runs [planProduction] (same calling
     * convention `StockPlannerScreen` established) and hands its build list
     * to [categorizeBuildList], the one step this screen adds on top:
     * resolving each build job's `job_category` via `SdeRepository.type(...)
     * ?.groupId`/`categoryIdOf(...)`, since [BuildJobEntry] itself carries
     * no such field (see `LogisticsEngine.kt`'s own module docstring). */
    suspend fun computeCategorizedBuildList() = run {
        val cfg = productionConfigRepo.load()
        val tradingCfg = tradingConfigRepo.load()
        val stockTargets = stockPlanRepo.loadStockTargets()
        val manualStockMap = stockPlanRepo.loadManualStock()
        val manualOverrides = stockPlanRepo.loadManualBuildBuy()

        val seedTypeIds = stockTargets.map { it.typeId }
        val closure = structuralMaterialClosure(seedTypeIds, sdeRepo)
        val pricedTypeIds = (seedTypeIds.toSet() + closure).toList()
        val home = homePrices(pricedTypeIds, cfg, tokenManager, esi)
        val jita = jitaPrices(pricedTypeIds, tradingCfg.jitaRegionId, esi)

        val plan = planProduction(
            cfg, stockTargets, manualStockMap, manualOverrides, home, jita, sdeRepo, stockSource,
        ) { typeId -> sdeRepo.type(typeId)?.typeName ?: typeId.toString() }

        val categorized = categorizeBuildList(
            plan.buildList,
            groupIdOf = { typeId -> sdeRepo.type(typeId)?.groupId },
            categoryIdOf = { typeId -> sdeRepo.categoryIdOf(typeId) },
        )
        Triple(cfg, categorized, plan.buildList.size)
    }

    fun refreshLogisticsStatus() {
        if (activeLocations.isEmpty()) {
            status = "Assign at least one category to a location first."
            return
        }
        busy = true
        logisticsRows = null
        status = "Computing logistics status (re-runs the planner)..."
        scope.launch {
            try {
                val (cfg, categorized, buildJobCount) = computeCategorizedBuildList()
                val rows = logisticsStatus(
                    categorized, activeLocations, cfg, sdeRepo, locationStockSource,
                ) { typeId -> sdeRepo.type(typeId)?.typeName ?: typeId.toString() }
                logisticsRows = rows
                status = if (rows.isNotEmpty()) "${rows.size} logistics row(s) ($buildJobCount build job(s) planned)."
                else "No category has both an assigned location and planned jobs right now."
            } catch (e: Exception) {
                status = e.message ?: "Failed to compute logistics status."
            } finally {
                busy = false
            }
        }
    }

    fun refreshDistribution() {
        if (activeLocations.isEmpty()) {
            status = "Assign at least one category to a location first."
            return
        }
        busy = true
        distributionRows = null
        status = "Computing distribution recommendations (re-runs the planner)..."
        scope.launch {
            try {
                val (cfg, categorized, _) = computeCategorizedBuildList()
                val rows = distributionRecommendations(
                    categorized, activeLocations, cfg, sdeRepo, locationStockSource,
                ) { typeId -> sdeRepo.type(typeId)?.typeName ?: typeId.toString() }
                distributionRows = rows
                status = if (rows.isNotEmpty()) "${rows.size} recommendation(s)."
                else "Nothing to move - either every category is covered, or no distribution source is configured " +
                    "(set homeLocationId/distributionSourceLocationId in Settings)."
            } catch (e: Exception) {
                status = e.message ?: "Failed to compute distribution recommendations."
            } finally {
                busy = false
            }
        }
    }

    LazyColumn(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        item {
            Text("Category Locations", style = MaterialTheme.typography.titleSmall)
            Text(
                "Not resolvable to a structure name here - EsiClient has no structure/corporation-name " +
                    "endpoint yet (\"Resolve Structure Name\" stays out of scope; enter a bare location ID).",
                style = MaterialTheme.typography.bodySmall,
            )
        }
        items(JOB_CATEGORIES) { category ->
            val active = activeLocations[category]
            val categoryOptions = options[category].orEmpty()
            ListItem(
                headlineContent = { Text(category) },
                supportingContent = {
                    Text(
                        (if (active != null) "Active: $active" else "Active: -") +
                            if (categoryOptions.isNotEmpty()) " | Options: ${categoryOptions.joinToString()}" else "",
                    )
                },
            )
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth().padding(bottom = 4.dp)) {
                OutlinedTextField(
                    value = locationInputs[category] ?: "",
                    onValueChange = { locationInputs = locationInputs + (category to it) },
                    label = { Text("Location ID") },
                    modifier = Modifier.weight(1f),
                )
                TextButton(onClick = { setActive(category) }) { Text("Set") }
                TextButton(onClick = { clearActive(category) }) { Text("Clear") }
                TextButton(onClick = { addOption(category) }) { Text("+Opt") }
            }
            if (categoryOptions.isNotEmpty()) {
                Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    categoryOptions.forEach { loc ->
                        TextButton(onClick = { removeOption(category, loc) }) { Text("-$loc") }
                    }
                }
            }
        }

        item {
            HorizontalDivider(modifier = Modifier.padding(vertical = 8.dp))
            Text("Manual Build/Buy Overrides", style = MaterialTheme.typography.titleSmall)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                OutlinedTextField(
                    value = overrideNameText, onValueChange = { overrideNameText = it },
                    label = { Text("Name or type ID") }, modifier = Modifier.weight(1f),
                )
                TextButton(onClick = { overrideBuild = !overrideBuild }) {
                    Text(if (overrideBuild) "Force: Build" else "Force: Buy")
                }
            }
            Button(enabled = !busy, onClick = { addOverride() }) { Text("Add Override") }
        }
        items(overrides, key = { it.typeId }) { o ->
            ListItem(
                headlineContent = { Text(o.typeId.toString()) },
                supportingContent = { Text("Always ${o.decision}") },
                trailingContent = {
                    TextButton(onClick = { scope.launch { stockPlanRepo.deleteManualBuildBuy(o.typeId); reloadConfig() } }) {
                        Text("Remove")
                    }
                },
            )
        }

        item {
            HorizontalDivider(modifier = Modifier.padding(vertical = 8.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(enabled = !busy, onClick = { refreshLogisticsStatus() }) { Text("Refresh Logistics Status") }
                Button(enabled = !busy, onClick = { refreshDistribution() }) { Text("Refresh Distribution") }
            }
            if (busy) LinearProgressIndicator(modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp))
            Text(status, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(vertical = 8.dp))
        }

        logisticsRows?.let { rows ->
            item {
                HorizontalDivider(modifier = Modifier.padding(vertical = 8.dp))
                Text("Logistics Status", style = MaterialTheme.typography.titleSmall)
            }
            items(rows) { row ->
                Text(
                    "[${row.category}@${row.locationId}] ${row.typeName}: ${fmtQty(row.available)} / " +
                        "${fmtQty(row.needed)} - missing ${fmtQty(row.missing)}" +
                        (row.pullFromLocationId?.let { " (pull ${fmtQty(row.pullFromAvailable ?: 0.0)} from $it)" } ?: ""),
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.padding(vertical = 2.dp),
                )
            }
        }

        distributionRows?.let { rows ->
            item {
                HorizontalDivider(modifier = Modifier.padding(vertical = 8.dp))
                Text("Distribution Recommendations", style = MaterialTheme.typography.titleSmall)
            }
            items(rows) { row ->
                Text(
                    "Move ${fmtQty(row.quantity)}x ${row.typeName}: ${row.fromLocationId} -> " +
                        "${row.toCategory}@${row.toLocationId}",
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.padding(vertical = 2.dp),
                )
            }
        }

        item {
            HorizontalDivider(modifier = Modifier.padding(vertical = 8.dp))
            Text("Invention Logistics", style = MaterialTheme.typography.titleSmall)
            Text(
                "Not ported - needs plan_production's invention_list (recommended invention runs per Tech " +
                    "II/III stock target), which this Manufacturing-only SDE cache cannot produce at all " +
                    "(see ProductionEngine.kt's own module docstring).",
                style = MaterialTheme.typography.bodySmall,
            )
            HorizontalDivider(modifier = Modifier.padding(vertical = 8.dp))
            Text("T1 BPC Invention Needs", style = MaterialTheme.typography.titleSmall)
            Text(
                "Not ported - same invention_list gap as Invention Logistics above.",
                style = MaterialTheme.typography.bodySmall,
            )
        }
    }
}
