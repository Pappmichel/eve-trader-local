package com.pappmichel.evetraderlocal.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
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
import com.pappmichel.evetraderlocal.data.doctrine.DoctrineFittingRepository
import com.pappmichel.evetraderlocal.data.doctrine.SavedFitting
import com.pappmichel.evetraderlocal.data.doctrine.StockpileStatus
import com.pappmichel.evetraderlocal.data.esi.EsiClient
import com.pappmichel.evetraderlocal.data.sde.SdeRepository
import kotlinx.coroutines.launch

/** Doctrine -> Stockpile Status: "how much of each saved fitting's items do
 * I have vs. need", ported from `doctrine/actions.py`'s
 * `do_get_stockpile_status` on top of [StockpileStatus]'s math (see that
 * file's own docstring for the two documented simplifications vs. the
 * desktop engine: no contract-target multiplier, and `availableByType` is
 * either an ESI asset-quantity read or manually entered here rather than a
 * single fixed stockpile location).
 *
 * **"Don't block on missing auth" pattern, same as [RealizedTradesScreen]/
 * [UnlistedUndercutScreen]:** pressing "Refresh from Character Assets"
 * without a "Doctrine" character logged in (see `CharactersScreen`) just
 * surfaces that in the status line - rows still compute from whatever
 * `available` quantities are already on screen (all zero on first load), so
 * a user with no ESI token at all can still type owned quantities into each
 * row's editable field and press "Recompute" to see real shortfalls/ampels,
 * exactly the manual-entry fallback [MineralShoppingListScreen] establishes
 * for a not-yet-authenticated view. */
@Composable
fun DoctrineStockpileStatusScreen(database: AppDatabase, tokenManager: TokenManager) {
    val scope = rememberCoroutineScope()
    val fittingRepo = remember { DoctrineFittingRepository(database) }
    val sdeRepo = remember { SdeRepository(database) }
    val esi = remember { EsiClient() }

    var fittings by remember { mutableStateOf<List<SavedFitting>>(emptyList()) }
    var availableByType by remember { mutableStateOf<Map<Int, Double>>(emptyMap()) }
    var availableEditText by remember { mutableStateOf<Map<Int, String>>(emptyMap()) }
    var typeNames by remember { mutableStateOf<Map<Int, String>>(emptyMap()) }
    var aggregated by remember { mutableStateOf<List<StockpileStatus.AggregatedRow>>(emptyList()) }
    var assetsAvailable by remember { mutableStateOf(false) }
    var status by remember {
        mutableStateOf("Loading saved fittings - Stockpile Status needs at least one saved fitting to compute anything.")
    }
    var busy by remember { mutableStateOf(false) }

    suspend fun resolveNames(fittingList: List<SavedFitting>) {
        val ids = fittingList.flatMap { f -> f.items.map { it.typeId } + f.hullTypeId }.distinct()
        typeNames = ids.associateWith { sdeRepo.typeName(it) ?: it.toString() }
    }

    fun recompute() {
        val rows = StockpileStatus.computeRows(
            fittings = fittings, availableByType = availableByType,
            typeName = { typeNames[it] ?: it.toString() },
        )
        aggregated = StockpileStatus.aggregate(rows)
    }

    fun loadFittings() {
        busy = true
        scope.launch {
            try {
                fittings = fittingRepo.listActive()
                resolveNames(fittings)
                val neededTypeIds = fittings.flatMap { f -> f.items.map { it.typeId } + f.hullTypeId }.distinct()
                availableEditText = neededTypeIds.associateWith { (availableByType[it] ?: 0.0).toInt().toString() }
                recompute()
                status = if (fittings.isEmpty()) {
                    "No saved fittings yet - save one on the Fittings screen first."
                } else {
                    "${fittings.size} saved fitting(s) loaded, ${neededTypeIds.size} distinct item(s) - " +
                        "enter owned quantities or Refresh from Character Assets."
                }
            } catch (e: Exception) {
                status = e.message ?: "Could not load saved fittings."
            } finally {
                busy = false
            }
        }
    }

    LaunchedEffect(Unit) { loadFittings() }

    fun refreshFromAssets() {
        busy = true
        status = "Fetching character assets..."
        scope.launch {
            try {
                val record = tokenManager.listRecords("doctrine").firstOrNull()
                    ?: error("No 'Doctrine' character is logged in yet (see Characters) - enter quantities manually below instead.")
                val token = tokenManager.getToken(record.role)
                availableByType = StockpileStatus.fetchAvailableQuantities(esi, token.characterId, token.accessToken)
                availableEditText = availableByType.mapValues { it.value.toInt().toString() }
                assetsAvailable = true
                recompute()
                status = "Refreshed from ${record.role}'s character assets (every location, see this screen's own note on location scope)."
            } catch (e: Exception) {
                status = e.message ?: "Could not fetch character assets."
            } finally {
                busy = false
            }
        }
    }

    fun applyManualEntry() {
        val parsed = availableEditText.mapNotNull { (typeId, text) ->
            text.trim().toDoubleOrNull()?.let { typeId to it }
        }.toMap()
        availableByType = parsed
        recompute()
        status = "Recomputed from manually entered quantities."
    }

    fun severityLabel(severity: String?): String = when (severity) {
        "critical" -> "CRITICAL"
        "tolerable" -> "tolerable"
        else -> "OK"
    }

    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        Text("Doctrine - Stockpile Status", style = MaterialTheme.typography.titleLarge)
        Text(status, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(vertical = 8.dp))
        if (busy) LinearProgressIndicator(modifier = Modifier.fillMaxWidth().padding(bottom = 8.dp))

        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(bottom = 8.dp)) {
            Button(onClick = { loadFittings() }, enabled = !busy) { Text("Reload Fittings") }
            Button(onClick = { refreshFromAssets() }, enabled = !busy) { Text("Refresh from Character Assets") }
        }

        Text(
            "Every saved fitting's required quantity, allocated across fittings by save order (priority) - " +
                "see this screen's own docstring for what's simplified vs. the desktop engine.",
            style = MaterialTheme.typography.bodySmall,
            modifier = Modifier.padding(bottom = 8.dp),
        )

        LazyColumn(modifier = Modifier.fillMaxWidth()) {
            items(aggregated) { row ->
                val editText = availableEditText[row.typeId] ?: row.available.toInt().toString()
                ListItem(
                    headlineContent = { Text("${row.typeName} - ${severityLabel(row.severity)}") },
                    supportingContent = {
                        Text("Required: ${row.requiredTotal.toInt()} · Shortfall: ${row.shortfall.toInt()} · across ${row.fittingCount} fitting(s)")
                    },
                    trailingContent = {
                        OutlinedTextField(
                            value = editText,
                            onValueChange = { availableEditText = availableEditText + (row.typeId to it) },
                            label = { Text("Owned") },
                            modifier = Modifier.width(90.dp),
                        )
                    },
                )
                HorizontalDivider()
            }
        }
        Button(onClick = { applyManualEntry() }, enabled = !busy && aggregated.isNotEmpty(),
            modifier = Modifier.padding(top = 8.dp)) {
            Text("Recompute from Owned Quantities")
        }
    }
}
