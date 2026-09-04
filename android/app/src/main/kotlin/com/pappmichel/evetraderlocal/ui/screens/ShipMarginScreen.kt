package com.pappmichel.evetraderlocal.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
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
import androidx.compose.ui.unit.dp
import com.pappmichel.evetraderlocal.data.auth.TokenManager
import com.pappmichel.evetraderlocal.data.db.AppDatabase
import com.pappmichel.evetraderlocal.data.esi.EsiClient
import com.pappmichel.evetraderlocal.data.production.ProductionConfigRepository
import com.pappmichel.evetraderlocal.data.production.homePrices
import com.pappmichel.evetraderlocal.data.production.jitaPrices
import com.pappmichel.evetraderlocal.data.production.marginHome
import com.pappmichel.evetraderlocal.data.production.marginJita
import com.pappmichel.evetraderlocal.data.production.unitBuildCost
import com.pappmichel.evetraderlocal.data.sde.SdeRepository
import com.pappmichel.evetraderlocal.data.trading.TradingConfigRepository
import kotlinx.coroutines.launch

private fun fmtIsk(value: Double): String = "%,.2f".format(value)
private fun fmtPct(value: Double): String = "%.1f%%".format(value * 100)

/** Production -> Ship Margins & Market Status: a single-item build-cost and
 * margin lookup - a Kotlin port of the desktop build's
 * `engine.item_margin_detail`/`actions.do_get_item_margin` (Item Margin's
 * search box), narrowed to one arbitrary type_id at a time rather than
 * desktop's whole-catalog scan (`engine.discover_ship_margins`).
 *
 * That narrowing mirrors Item Lookup's own precedent
 * (`ItemLookupScreen.kt`): a full catalog scan needs a bulk price fetch
 * across thousands of SDE-listed types plus (for the real desktop feature)
 * a Goonmetrics movement lookup, neither of which this port needs to stand
 * up a real, testable build-cost vertical slice. See
 * `data/production/ProductionBuildCost.kt`'s module docstring for the full
 * list of math simplifications ([unitBuildCost] narrows to Manufacturing-
 * only, one flat ME level, no structure/rig bonus, no live cost index, and
 * an EIV approximated from real material prices rather than ESI's
 * adjusted-price catalog).
 *
 * Home-market pricing needs a producer character's structure-docking
 * token, same gap Item Lookup already documents - with no
 * `ProductionConfig.homeLocationId` configured this screen only ever shows
 * the Jita side, clearly labeled rather than silently blank. */
@Composable
fun ShipMarginScreen(database: AppDatabase, tokenManager: TokenManager) {
    val scope = rememberCoroutineScope()
    val sdeRepo = remember { SdeRepository(database) }
    val tradingConfigRepo = remember { TradingConfigRepository(database) }
    val productionConfigRepo = remember { ProductionConfigRepository(database) }
    val esi = remember { EsiClient() }

    var typeIdText by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var status by remember { mutableStateOf("Enter a type ID and look it up.") }
    var itemName by remember { mutableStateOf<String?>(null) }
    var buildCost by remember { mutableStateOf<Double?>(null) }
    var homePrice by remember { mutableStateOf<Double?>(null) }
    var jitaPrice by remember { mutableStateOf<Double?>(null) }
    var marginHomePct by remember { mutableStateOf<Double?>(null) }
    var marginJitaPct by remember { mutableStateOf<Double?>(null) }
    var haveResult by remember { mutableStateOf(false) }

    fun lookup() {
        val typeId = typeIdText.trim().toIntOrNull()
        if (typeId == null) {
            status = "Enter a numeric type ID."
            return
        }
        busy = true
        haveResult = false
        status = "Pricing type $typeId..."
        scope.launch {
            try {
                val sdeType = sdeRepo.type(typeId)
                itemName = sdeType?.typeName
                val productionCfg = productionConfigRepo.load()
                val tradingCfg = tradingConfigRepo.load()
                val home = homePrices(listOf(typeId), productionCfg, tokenManager, esi)
                val jita = jitaPrices(listOf(typeId), tradingCfg.jitaRegionId, esi)

                val cost = unitBuildCost(typeId, productionCfg, home, jita, sdeRepo)
                buildCost = cost
                homePrice = home[typeId]?.sellPercentile?.takeIf { it > 0 }
                jitaPrice = jita[typeId]?.sellPercentile?.takeIf { it > 0 }
                marginHomePct = marginHome(typeId, cost, home, productionCfg)
                marginJitaPct = marginJita(typeId, cost, jita, sdeType?.volume, productionCfg)
                haveResult = true
                status = if (cost == null) {
                    "No sell orders and no cached blueprint for this item - can't price it."
                } else {
                    "Priced type $typeId."
                }
            } catch (e: Exception) {
                status = e.message ?: "Lookup failed."
            } finally {
                busy = false
            }
        }
    }

    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedTextField(
                value = typeIdText,
                onValueChange = { typeIdText = it },
                label = { Text("Type ID") },
                modifier = Modifier.weight(1f),
            )
            Button(enabled = !busy, onClick = { lookup() }) { Text("Price It") }
        }
        if (busy) {
            LinearProgressIndicator(modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp))
        }
        Text(status, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(vertical = 8.dp))

        if (haveResult) {
            HorizontalDivider()
            Column(modifier = Modifier.padding(vertical = 8.dp)) {
                Text(itemName ?: "(unknown item name)", style = MaterialTheme.typography.titleMedium)
                Text(
                    "Build cost: " + (buildCost?.let { "${fmtIsk(it)} ISK/unit" } ?: "not priceable"),
                    style = MaterialTheme.typography.titleSmall,
                )
                Text(
                    "Home sell: " + (homePrice?.let { "${fmtIsk(it)} ISK" } ?: "no sell orders / not configured"),
                    style = MaterialTheme.typography.bodyMedium,
                )
                Text(
                    "Home margin: " + (marginHomePct?.let { fmtPct(it) } ?: "n/a"),
                    style = MaterialTheme.typography.bodyMedium,
                )
                Text(
                    "Jita sell: " + (jitaPrice?.let { "${fmtIsk(it)} ISK" } ?: "no sell orders"),
                    style = MaterialTheme.typography.bodyMedium,
                )
                Text(
                    "Jita margin (after export haul): " + (marginJitaPct?.let { fmtPct(it) } ?: "n/a"),
                    style = MaterialTheme.typography.bodyMedium,
                )
            }
        }
    }
}
