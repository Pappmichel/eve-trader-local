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
import com.pappmichel.evetraderlocal.data.production.buyPrice
import com.pappmichel.evetraderlocal.data.production.buySource
import com.pappmichel.evetraderlocal.data.production.candidatePrices
import com.pappmichel.evetraderlocal.data.production.homePrices
import com.pappmichel.evetraderlocal.data.production.jitaPrices
import com.pappmichel.evetraderlocal.data.sde.SdeRepository
import com.pappmichel.evetraderlocal.data.trading.TradingConfigRepository
import kotlinx.coroutines.launch

private fun fmtIsk(value: Double): String = "%,.2f".format(value)

/** Production -> Item Lookup: type in a type_id, see the cheapest place to
 * buy one unit of it right now - a Kotlin port of the buy-side half of the
 * desktop build's production/pricing.py (see
 * data/production/ProductionPricing.kt for the ported functions and,
 * importantly, why this is *not* a port of desktop's own Item Lookup/Item
 * Margin view).
 *
 * Desktop's Item Lookup (actions.do_get_item_margin) answers "what does it
 * cost to *build* this, and is that cheaper than buying it" - a full
 * build-vs-buy verdict that needs the item's blueprint bill of materials.
 * That data (industryActivityMaterials/industryActivityProducts SDE
 * tables) isn't in the Android SDE cache yet (see ProductionPricing.kt's
 * module docstring), so this screen answers a narrower but still real
 * question instead: "of the markets I can check, which is cheapest to buy
 * this from right now, and what would it cost me landed?" - the exact
 * buy-side comparison every build-vs-buy decision on desktop still has to
 * make for every raw material anyway.
 *
 * Lookup is by type_id only, not by name - the Android SDE cache has no
 * name-search index (see SdeRepository.kt), only a point lookup by ID.
 * Once a type_id resolves, its SDE name/volume are shown for confirmation.
 *
 * Home-market pricing needs a producer character's structure-docking
 * token (production/esi_sync.py's PRODUCER_ROLE_PREFIX = "producer"); no
 * login flow for that role exists in this app yet (same gap noted in
 * ProductionPricing.kt), so with no ProductionConfig.homeLocationId set
 * (the default) this screen only ever shows the Jita side - a real and
 * clearly-labeled reduction, not a silent one. */
@Composable
fun ItemLookupScreen(database: AppDatabase, tokenManager: TokenManager) {
    val scope = rememberCoroutineScope()
    val sdeRepo = remember { SdeRepository(database) }
    val tradingConfigRepo = remember { TradingConfigRepository(database) }
    val productionConfigRepo = remember { ProductionConfigRepository(database) }
    val esi = remember { EsiClient() }

    var typeIdText by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var status by remember { mutableStateOf("Enter a type ID and look it up.") }
    var itemName by remember { mutableStateOf<String?>(null) }
    // Landed unit price per source - candidatePrices' own output, so these
    // are exactly what buyPrice/buySource below compare (broker fee baked
    // in for both, haul cost baked in for jita), not the bare sell quote.
    var landedHome by remember { mutableStateOf<Double?>(null) }
    var landedJita by remember { mutableStateOf<Double?>(null) }
    var cheapest by remember { mutableStateOf<String?>(null) }
    var cheapestPrice by remember { mutableStateOf<Double?>(null) }
    var haveResult by remember { mutableStateOf(false) }

    fun lookup() {
        val typeId = typeIdText.trim().toIntOrNull()
        if (typeId == null) {
            status = "Enter a numeric type ID."
            return
        }
        busy = true
        haveResult = false
        status = "Looking up type $typeId..."
        scope.launch {
            try {
                val sdeType = sdeRepo.type(typeId)
                itemName = sdeType?.typeName
                val productionCfg = productionConfigRepo.load()
                val tradingCfg = tradingConfigRepo.load()
                val home = homePrices(listOf(typeId), productionCfg, tokenManager, esi)
                val jita = jitaPrices(listOf(typeId), tradingCfg.jitaRegionId, esi)
                val volume = sdeType?.volume
                val candidates = candidatePrices(typeId, home, jita, volume, productionCfg)
                landedHome = candidates["home"]
                landedJita = candidates["jita"]
                cheapest = buySource(typeId, home, jita, volume, productionCfg)
                cheapestPrice = buyPrice(typeId, home, jita, volume, productionCfg)
                haveResult = true
                status = if (cheapestPrice == null) {
                    "No sell orders found on either market for this item."
                } else {
                    "Looked up type $typeId."
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
            Button(enabled = !busy, onClick = { lookup() }) { Text("Look Up") }
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
                    "Home (landed): " + (landedHome?.let { "${fmtIsk(it)} ISK/unit" } ?: "no sell orders / not configured"),
                    style = MaterialTheme.typography.bodyMedium,
                )
                Text(
                    "Jita (landed): " + (landedJita?.let { "${fmtIsk(it)} ISK/unit" } ?: "no sell orders"),
                    style = MaterialTheme.typography.bodyMedium,
                )
                if (cheapestPrice != null && cheapest != null) {
                    Text(
                        "Cheapest: $cheapest at ${fmtIsk(cheapestPrice!!)} ISK/unit",
                        style = MaterialTheme.typography.titleSmall,
                    )
                }
            }
        }
    }
}
