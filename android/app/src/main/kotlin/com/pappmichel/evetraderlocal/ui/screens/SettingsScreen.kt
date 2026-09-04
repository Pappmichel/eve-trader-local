package com.pappmichel.evetraderlocal.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
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
import com.pappmichel.evetraderlocal.data.db.AppDatabase
import com.pappmichel.evetraderlocal.data.trading.TradingConfig
import com.pappmichel.evetraderlocal.data.trading.TradingConfigRepository
import kotlinx.coroutines.launch

/** Trading settings, the Android counterpart of one tab of the desktop
 * build's `SettingsDialog` (`gui/dialogs/settings_dialog.py`) - that
 * dialog builds its form generically off each tool's config dataclass via
 * reflection, which Kotlin has no equivalent of; this is a hand-written
 * form instead, over just the `TradingConfig` fields the Android build
 * currently reads (see that class's own docstring - the same "only the
 * fields this platform actually uses so far" scoping the class itself
 * already follows). Only Trading has a config on this platform yet, so
 * unlike the desktop dialog there is nothing to tab between. */
@Composable
fun SettingsScreen(database: AppDatabase) {
    val scope = rememberCoroutineScope()
    val configRepo = remember { TradingConfigRepository(database) }

    var jitaRegionId by remember { mutableStateOf("") }
    var referenceRegionId by remember { mutableStateOf("") }
    var structureId by remember { mutableStateOf("") }
    var importCostPerM3 by remember { mutableStateOf("") }
    var structureSellHaircut by remember { mutableStateOf("") }
    var jitaBuyBrokerFee by remember { mutableStateOf("") }
    var minProfitThreshold by remember { mutableStateOf("") }
    var minMarginThreshold by remember { mutableStateOf("") }
    var minHitRate by remember { mutableStateOf("") }
    var minAvgMovement by remember { mutableStateOf("") }
    var excludedPathPrefixes by remember { mutableStateOf("") }
    var status by remember { mutableStateOf("Loading...") }

    fun fill(cfg: TradingConfig) {
        jitaRegionId = cfg.jitaRegionId.toString()
        referenceRegionId = cfg.referenceRegionId.toString()
        structureId = cfg.structureId?.toString() ?: ""
        importCostPerM3 = cfg.importCostPerM3.toString()
        structureSellHaircut = cfg.structureSellHaircut.toString()
        jitaBuyBrokerFee = cfg.jitaBuyBrokerFee.toString()
        minProfitThreshold = cfg.minProfitThreshold.toString()
        minMarginThreshold = cfg.minMarginThreshold.toString()
        minHitRate = cfg.minHitRate.toString()
        minAvgMovement = cfg.minAvgMovement.toString()
        excludedPathPrefixes = cfg.excludedPathPrefixes.joinToString("\n")
    }

    LaunchedEffect(Unit) {
        fill(configRepo.load())
        status = "Loaded."
    }

    // Parses every field before saving anything, rather than silently
    // falling back to a default per-field the way an earlier version of
    // this screen did - a typo (e.g. "0.,5") used to be discarded without
    // telling the user, saving (and displaying back) TradingConfig()'s
    // default for that field instead of what they actually typed.
    fun save() {
        val invalid = mutableListOf<String>()
        // .trim() so stray whitespace (an easy copy-paste artifact) doesn't
        // turn an otherwise-valid number into a rejected one - toIntOrNull/
        // toDoubleOrNull don't tolerate surrounding whitespace themselves.
        fun reqInt(label: String, text: String): Int = text.trim().toIntOrNull() ?: run { invalid.add(label); 0 }
        fun reqDouble(label: String, text: String): Double = text.trim().toDoubleOrNull() ?: run { invalid.add(label); 0.0 }

        val newJitaRegionId = reqInt("Jita Region ID", jitaRegionId)
        val newReferenceRegionId = reqInt("Reference Region ID", referenceRegionId)
        val trimmedStructureId = structureId.trim()
        val newStructureId = if (trimmedStructureId.isEmpty()) null else trimmedStructureId.toLongOrNull()
            ?: run { invalid.add("Structure ID"); null }
        val newImportCostPerM3 = reqDouble("Import Cost per m3", importCostPerM3)
        val newStructureSellHaircut = reqDouble("Structure Sell Haircut", structureSellHaircut)
        val newJitaBuyBrokerFee = reqDouble("Jita Buy Broker Fee", jitaBuyBrokerFee)
        val newMinProfitThreshold = reqDouble("Min Profit Threshold", minProfitThreshold)
        val newMinMarginThreshold = reqDouble("Min Margin Threshold", minMarginThreshold)
        val newMinHitRate = reqDouble("Min Hit Rate", minHitRate)
        val newMinAvgMovement = reqDouble("Min Avg Movement", minAvgMovement)

        if (invalid.isNotEmpty()) {
            status = "Not saved - invalid value for: ${invalid.joinToString(", ")}."
            return
        }

        val cfg = TradingConfig(
            jitaRegionId = newJitaRegionId, referenceRegionId = newReferenceRegionId, structureId = newStructureId,
            importCostPerM3 = newImportCostPerM3, structureSellHaircut = newStructureSellHaircut,
            jitaBuyBrokerFee = newJitaBuyBrokerFee, minProfitThreshold = newMinProfitThreshold,
            minMarginThreshold = newMinMarginThreshold, minHitRate = newMinHitRate, minAvgMovement = newMinAvgMovement,
            excludedPathPrefixes = excludedPathPrefixes.lines().map { it.trim() }.filter { it.isNotEmpty() },
        )
        scope.launch {
            configRepo.save(cfg)
            fill(cfg)
            status = "Saved."
        }
    }

    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text("Trading", style = MaterialTheme.typography.titleMedium)
        OutlinedTextField(value = jitaRegionId, onValueChange = { jitaRegionId = it }, label = { Text("Jita Region ID") })
        OutlinedTextField(
            value = referenceRegionId, onValueChange = { referenceRegionId = it },
            label = { Text("Reference Region ID") },
        )
        OutlinedTextField(
            value = structureId, onValueChange = { structureId = it },
            label = { Text("Structure ID (blank = not set)") },
        )
        OutlinedTextField(
            value = importCostPerM3, onValueChange = { importCostPerM3 = it },
            label = { Text("Import Cost per m3") },
        )
        OutlinedTextField(
            value = structureSellHaircut, onValueChange = { structureSellHaircut = it },
            label = { Text("Structure Sell Haircut") },
        )
        OutlinedTextField(
            value = jitaBuyBrokerFee, onValueChange = { jitaBuyBrokerFee = it },
            label = { Text("Jita Buy Broker Fee") },
        )
        OutlinedTextField(
            value = minProfitThreshold, onValueChange = { minProfitThreshold = it },
            label = { Text("Min Profit Threshold") },
        )
        OutlinedTextField(
            value = minMarginThreshold, onValueChange = { minMarginThreshold = it },
            label = { Text("Min Margin Threshold") },
        )
        OutlinedTextField(value = minHitRate, onValueChange = { minHitRate = it }, label = { Text("Min Hit Rate") })
        OutlinedTextField(
            value = minAvgMovement, onValueChange = { minAvgMovement = it },
            label = { Text("Min Avg Movement") },
        )
        OutlinedTextField(
            value = excludedPathPrefixes, onValueChange = { excludedPathPrefixes = it },
            label = { Text("Excluded Market-Group Path Prefixes (one per line)") },
            minLines = 4,
        )
        Button(onClick = { save() }) { Text("Save") }
        Text(status, style = MaterialTheme.typography.bodySmall)
    }
}
