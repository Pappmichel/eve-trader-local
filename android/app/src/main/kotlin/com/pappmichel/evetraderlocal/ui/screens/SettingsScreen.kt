package com.pappmichel.evetraderlocal.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.pappmichel.evetraderlocal.data.db.AppDatabase
import com.pappmichel.evetraderlocal.data.production.ProductionConfig
import com.pappmichel.evetraderlocal.data.production.ProductionConfigRepository
import com.pappmichel.evetraderlocal.data.refining.RefiningConfig
import com.pappmichel.evetraderlocal.data.refining.RefiningConfigRepository
import com.pappmichel.evetraderlocal.data.refining.clampSkillLevel
import com.pappmichel.evetraderlocal.data.trading.StationTradingConfig
import com.pappmichel.evetraderlocal.data.trading.StationTradingConfigRepository
import com.pappmichel.evetraderlocal.data.trading.TradingConfig
import com.pappmichel.evetraderlocal.data.trading.TradingConfigRepository
import kotlinx.coroutines.launch

/** Settings, the Android counterpart of the desktop build's tabbed
 * `SettingsDialog` (`gui/dialogs/settings_dialog.py`) - that dialog builds
 * its form generically off each tool's config dataclass via reflection,
 * which Kotlin has no equivalent of, so each tab below is a hand-written
 * form instead, over just the fields that tool's Android port actually
 * reads (the same "only the fields this platform uses so far" scoping
 * each config class itself already follows - see `TradingConfig.kt`/
 * `StationTradingConfig.kt`/`ProductionConfig.kt`/`RefiningConfig.kt`'s
 * own docstrings for exactly which fields and why). One tab per tool with
 * a config on this platform so far - Doctrine has none yet (the EFT
 * parser reads nothing configurable). */
@Composable
fun SettingsScreen(database: AppDatabase) {
    var selectedTab by remember { mutableIntStateOf(0) }
    val tabs = listOf("Trading", "Station Trading", "Production", "Ore & Minerals")

    Column(modifier = Modifier.fillMaxSize()) {
        TabRow(selectedTabIndex = selectedTab) {
            tabs.forEachIndexed { index, label ->
                Tab(selected = selectedTab == index, onClick = { selectedTab = index }, text = { Text(label) })
            }
        }
        when (selectedTab) {
            0 -> TradingSettings(database)
            1 -> StationTradingSettings(database)
            2 -> ProductionSettings(database)
            3 -> RefiningSettings(database)
        }
    }
}

@Composable
private fun TradingSettings(database: AppDatabase) {
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
    var lookbackDays by remember { mutableStateOf("") }
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
        lookbackDays = cfg.lookbackDays.toString()
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
        val newLookbackDays = reqInt("Lookback Days", lookbackDays)

        if (invalid.isNotEmpty()) {
            status = "Not saved - invalid value for: ${invalid.joinToString(", ")}."
            return
        }

        val cfg = TradingConfig(
            jitaRegionId = newJitaRegionId, referenceRegionId = newReferenceRegionId, structureId = newStructureId,
            importCostPerM3 = newImportCostPerM3, structureSellHaircut = newStructureSellHaircut,
            jitaBuyBrokerFee = newJitaBuyBrokerFee, minProfitThreshold = newMinProfitThreshold,
            minMarginThreshold = newMinMarginThreshold, minHitRate = newMinHitRate, minAvgMovement = newMinAvgMovement,
            lookbackDays = newLookbackDays,
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
            value = lookbackDays, onValueChange = { lookbackDays = it },
            label = { Text("Realized Trades Lookback (days)") },
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

@Composable
private fun StationTradingSettings(database: AppDatabase) {
    val scope = rememberCoroutineScope()
    val configRepo = remember { StationTradingConfigRepository(database) }

    var stationId by remember { mutableStateOf("") }
    var brokerFeeRate by remember { mutableStateOf("") }
    var salesTaxRate by remember { mutableStateOf("") }
    var minSpreadThreshold by remember { mutableStateOf("") }
    var minDailyVolume by remember { mutableStateOf("") }
    var enforceShortlistCap by remember { mutableStateOf(false) }
    var maxActiveShortlistItems by remember { mutableStateOf("") }
    var status by remember { mutableStateOf("Loading...") }

    fun fill(cfg: StationTradingConfig) {
        stationId = cfg.stationId.toString()
        brokerFeeRate = cfg.brokerFeeRate.toString()
        salesTaxRate = cfg.salesTaxRate.toString()
        minSpreadThreshold = cfg.minSpreadThreshold.toString()
        minDailyVolume = cfg.minDailyVolume.toString()
        enforceShortlistCap = cfg.enforceShortlistCap
        maxActiveShortlistItems = cfg.maxActiveShortlistItems.toString()
    }

    LaunchedEffect(Unit) {
        fill(configRepo.load())
        status = "Loaded."
    }

    fun save() {
        val invalid = mutableListOf<String>()
        fun reqInt(label: String, text: String): Int = text.trim().toIntOrNull() ?: run { invalid.add(label); 0 }
        fun reqLong(label: String, text: String): Long = text.trim().toLongOrNull() ?: run { invalid.add(label); 0L }
        fun reqDouble(label: String, text: String): Double = text.trim().toDoubleOrNull() ?: run { invalid.add(label); 0.0 }

        val newStationId = reqLong("Station ID", stationId)
        val newBrokerFeeRate = reqDouble("Broker Fee Rate", brokerFeeRate)
        val newSalesTaxRate = reqDouble("Sales Tax Rate", salesTaxRate)
        val newMinSpreadThreshold = reqDouble("Min Spread Threshold", minSpreadThreshold)
        val newMinDailyVolume = reqDouble("Min Daily Volume", minDailyVolume)
        val newMaxActiveShortlistItems = reqInt("Max Active Shortlist Items", maxActiveShortlistItems)

        if (invalid.isNotEmpty()) {
            status = "Not saved - invalid value for: ${invalid.joinToString(", ")}."
            return
        }

        val cfg = StationTradingConfig(
            stationId = newStationId, brokerFeeRate = newBrokerFeeRate, salesTaxRate = newSalesTaxRate,
            minSpreadThreshold = newMinSpreadThreshold, minDailyVolume = newMinDailyVolume,
            enforceShortlistCap = enforceShortlistCap, maxActiveShortlistItems = newMaxActiveShortlistItems,
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
        OutlinedTextField(value = stationId, onValueChange = { stationId = it }, label = { Text("Station ID") })
        OutlinedTextField(
            value = brokerFeeRate, onValueChange = { brokerFeeRate = it },
            label = { Text("Broker Fee Rate") },
        )
        OutlinedTextField(value = salesTaxRate, onValueChange = { salesTaxRate = it }, label = { Text("Sales Tax Rate") })
        OutlinedTextField(
            value = minSpreadThreshold, onValueChange = { minSpreadThreshold = it },
            label = { Text("Min Spread Threshold") },
        )
        OutlinedTextField(
            value = minDailyVolume, onValueChange = { minDailyVolume = it },
            label = { Text("Min Daily Volume") },
        )
        Row(verticalAlignment = Alignment.CenterVertically) {
            Checkbox(checked = enforceShortlistCap, onCheckedChange = { enforceShortlistCap = it })
            Text("Enforce Shortlist Cap")
        }
        OutlinedTextField(
            value = maxActiveShortlistItems, onValueChange = { maxActiveShortlistItems = it },
            label = { Text("Max Active Shortlist Items") },
        )
        Button(onClick = { save() }) { Text("Save") }
        Text(status, style = MaterialTheme.typography.bodySmall)
    }
}

@Composable
private fun ProductionSettings(database: AppDatabase) {
    val scope = rememberCoroutineScope()
    val configRepo = remember { ProductionConfigRepository(database) }

    var jitaBuyBrokerFee by remember { mutableStateOf("") }
    var haulCostPerM3 by remember { mutableStateOf("") }
    var homeLocationId by remember { mutableStateOf("") }
    var status by remember { mutableStateOf("Loading...") }

    fun fill(cfg: ProductionConfig) {
        jitaBuyBrokerFee = cfg.jitaBuyBrokerFee.toString()
        haulCostPerM3 = cfg.haulCostPerM3.toString()
        homeLocationId = cfg.homeLocationId?.toString() ?: ""
    }

    LaunchedEffect(Unit) {
        fill(configRepo.load())
        status = "Loaded."
    }

    fun save() {
        val invalid = mutableListOf<String>()
        fun reqDouble(label: String, text: String): Double = text.trim().toDoubleOrNull() ?: run { invalid.add(label); 0.0 }

        val newJitaBuyBrokerFee = reqDouble("Jita Buy Broker Fee", jitaBuyBrokerFee)
        val newHaulCostPerM3 = reqDouble("Haul Cost per m3", haulCostPerM3)
        val trimmedHomeLocationId = homeLocationId.trim()
        val newHomeLocationId = if (trimmedHomeLocationId.isEmpty()) null else trimmedHomeLocationId.toLongOrNull()
            ?: run { invalid.add("Home Structure ID"); null }

        if (invalid.isNotEmpty()) {
            status = "Not saved - invalid value for: ${invalid.joinToString(", ")}."
            return
        }

        val cfg = ProductionConfig(
            jitaBuyBrokerFee = newJitaBuyBrokerFee, haulCostPerM3 = newHaulCostPerM3,
            homeLocationId = newHomeLocationId,
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
        OutlinedTextField(
            value = jitaBuyBrokerFee, onValueChange = { jitaBuyBrokerFee = it },
            label = { Text("Jita Buy Broker Fee") },
        )
        OutlinedTextField(
            value = haulCostPerM3, onValueChange = { haulCostPerM3 = it },
            label = { Text("Haul Cost per m3") },
        )
        OutlinedTextField(
            value = homeLocationId, onValueChange = { homeLocationId = it },
            label = { Text("Home Structure ID (blank = Jita-only)") },
        )
        Button(onClick = { save() }) { Text("Save") }
        Text(status, style = MaterialTheme.typography.bodySmall)
    }
}

@Composable
private fun RefiningSettings(database: AppDatabase) {
    val scope = rememberCoroutineScope()
    val configRepo = remember { RefiningConfigRepository(database) }

    var scrapmetalProcessingSkillLevel by remember { mutableStateOf("") }
    var refiningTaxRate by remember { mutableStateOf("") }
    var status by remember { mutableStateOf("Loading...") }

    fun fill(cfg: RefiningConfig) {
        scrapmetalProcessingSkillLevel = cfg.scrapmetalProcessingSkillLevel.toString()
        refiningTaxRate = cfg.refiningTaxRate.toString()
    }

    LaunchedEffect(Unit) {
        fill(configRepo.load())
        status = "Loaded."
    }

    fun save() {
        val invalid = mutableListOf<String>()
        fun reqInt(label: String, text: String): Int = text.trim().toIntOrNull() ?: run { invalid.add(label); 0 }
        fun reqDouble(label: String, text: String): Double = text.trim().toDoubleOrNull() ?: run { invalid.add(label); 0.0 }

        // Clamped on save (not silently on load) so a typo'd 15 is visible
        // as "we clamped this" right when it's entered, matching
        // clampSkillLevel's own doc on where clamping belongs.
        val newSkillLevel = clampSkillLevel(reqInt("Scrapmetal Processing Skill Level", scrapmetalProcessingSkillLevel))
        val newRefiningTaxRate = reqDouble("Refining Tax Rate", refiningTaxRate)

        if (invalid.isNotEmpty()) {
            status = "Not saved - invalid value for: ${invalid.joinToString(", ")}."
            return
        }

        val cfg = RefiningConfig(scrapmetalProcessingSkillLevel = newSkillLevel, refiningTaxRate = newRefiningTaxRate)
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
        OutlinedTextField(
            value = scrapmetalProcessingSkillLevel, onValueChange = { scrapmetalProcessingSkillLevel = it },
            label = { Text("Scrapmetal Processing Skill Level (0-5)") },
        )
        OutlinedTextField(
            value = refiningTaxRate, onValueChange = { refiningTaxRate = it },
            label = { Text("Refining Tax Rate") },
        )
        Button(onClick = { save() }) { Text("Save") }
        Text(status, style = MaterialTheme.typography.bodySmall)
    }
}
