package com.pappmichel.evetraderlocal.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.ListItem
import androidx.compose.material3.MaterialTheme
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
import com.pappmichel.evetraderlocal.data.esi.EsiClient
import com.pappmichel.evetraderlocal.data.esi.OrderStats
import com.pappmichel.evetraderlocal.data.refining.ORE_IMPORT_DECISION
import com.pappmichel.evetraderlocal.data.refining.ORE_INACTIVE_DECISION
import com.pappmichel.evetraderlocal.data.refining.ORE_NO_MARKET_DATA_DECISION
import com.pappmichel.evetraderlocal.data.refining.OreCandidate
import com.pappmichel.evetraderlocal.data.refining.OreShortlistRepository
import com.pappmichel.evetraderlocal.data.refining.OreShortlistRow
import com.pappmichel.evetraderlocal.data.refining.RefiningConfigRepository
import com.pappmichel.evetraderlocal.data.refining.buildOreCandidateUniverse
import com.pappmichel.evetraderlocal.data.refining.evaluateOreItem
import com.pappmichel.evetraderlocal.data.sde.SdeRepository
import com.pappmichel.evetraderlocal.data.trading.TradingConfigRepository
import kotlinx.coroutines.launch

/** Ore & Minerals -> Ore Shortlist: a Kotlin port of the desktop build's
 * `refining/candidate_discovery.py` + `refining/pricing.py` (GitHub issue
 * #91). See `data/refining/OreShortlist.kt`'s own module docstring for the
 * full math and scope decision.
 *
 * "Discover" runs `buildOreCandidateUniverse` (a live query against the
 * local SDE cache for every published compressed ore/ice type - see
 * `SdeDao.oreIceCandidateTypes`) and persists the result, the same
 * insert-or-update-never-resets-active contract
 * `OreShortlistRepository.upsertFromDiscovery` documents. Every row is then
 * priced from a public Jita region read (the ore's own buy-in cost) plus,
 * when a seller character is registered, the home structure's own order
 * book (the refined minerals' sell price) - without a seller token this
 * screen still discovers/prices the ore side, it just can't complete the
 * mineral-value half of the calculation (same documented gap as
 * `ReprocessingQuoteScreen.kt`). */
@Composable
fun OreShortlistScreen(database: AppDatabase, tokenManager: TokenManager) {
    val scope = rememberCoroutineScope()
    val tradingConfigRepo = remember { TradingConfigRepository(database) }
    val refiningConfigRepo = remember { RefiningConfigRepository(database) }
    val shortlistRepo = remember { OreShortlistRepository(database) }
    val sdeRepo = remember { SdeRepository(database) }
    val esi = remember { EsiClient() }

    var rows by remember { mutableStateOf<List<OreShortlistRow>>(emptyList()) }
    var status by remember { mutableStateOf("Loading shortlist...") }
    var busy by remember { mutableStateOf(false) }

    suspend fun priceRows() {
        val stored = shortlistRepo.load()
        if (stored.isEmpty()) {
            rows = emptyList()
            status = "No shortlist items yet - Discover to scan the SDE for compressed ore/ice types."
            return
        }

        val tradingCfg = tradingConfigRepo.load()
        val refiningCfg = refiningConfigRepo.load()

        val typeIds = stored.map { it.itemId }
        val portionSizeById = HashMap<Int, Int?>()
        val materialsById = HashMap<Int, List<Pair<Int, Double>>>()
        val mineralIds = sortedSetOf<Int>()
        for (typeId in typeIds) {
            portionSizeById[typeId] = sdeRepo.portionSize(typeId)
            val materials = sdeRepo.typeMaterials(typeId)
            materialsById[typeId] = materials
            materials.forEach { (materialTypeId, _) -> mineralIds.add(materialTypeId) }
        }

        val jitaStatsById = esi.regionOrderStatsBulk(tradingCfg.jitaRegionId, typeIds)

        var mineralStatsById: Map<Int, OrderStats> = emptyMap()
        val sellerRecord = tokenManager.listRecords("seller").firstOrNull()
        val structureId = tradingCfg.structureId
        status = when {
            sellerRecord != null && structureId != null && mineralIds.isNotEmpty() -> {
                try {
                    val sellerToken = tokenManager.getToken(sellerRecord.role)
                    mineralStatsById = esi.structureOrderStatsBulk(structureId, mineralIds.toList(), sellerToken.accessToken)
                    "Pricing ${stored.size} item(s)..."
                } catch (e: Exception) {
                    "Could not fetch the structure's order book (${e.message}) - mineral values unavailable."
                }
            }
            structureId == null -> "No structure configured yet (see Settings) - mineral values unavailable."
            else -> "No seller character registered yet (see Characters) - mineral values unavailable."
        }

        val activeById = stored.associate { it.itemId to it.active }
        rows = stored.map { item ->
            // volumeM3 isn't stored on the persisted shortlist item (it's
            // SDE-derived, not something a user edits) - refetch it from the
            // SDE cache so landed-cost-per-m3 haul math stays correct after
            // a refresh.
            val candidate = OreCandidate(
                typeId = item.itemId, item = item.item, family = item.family, isIce = item.isIce,
                volumeM3 = sdeRepo.type(item.itemId)?.volume ?: 0.0,
            )
            evaluateOreItem(
                candidate = candidate,
                active = activeById[item.itemId] ?: true,
                portionSize = portionSizeById[item.itemId],
                materials = materialsById[item.itemId] ?: emptyList(),
                jitaStats = jitaStatsById[item.itemId],
                mineralStatsById = mineralStatsById,
                tradingCfg = tradingCfg,
                refiningCfg = refiningCfg,
            )
        }
        val importCount = rows.count { it.decision == ORE_IMPORT_DECISION }
        status = "${rows.size} shortlisted item(s), $importCount recommend Import. $status"
    }

    LaunchedEffect(Unit) {
        try {
            priceRows()
        } catch (e: Exception) {
            status = e.message ?: "Could not load the shortlist."
        }
    }

    fun discover() {
        busy = true
        status = "Scanning the SDE cache for compressed ore/ice types..."
        scope.launch {
            try {
                val candidates = buildOreCandidateUniverse(sdeRepo)
                shortlistRepo.upsertFromDiscovery(candidates)
                status = "Discovered ${candidates.size} candidate(s). Pricing..."
                priceRows()
            } catch (e: Exception) {
                status = e.message ?: "Discovery failed."
            } finally {
                busy = false
            }
        }
    }

    fun setActive(itemId: Int, active: Boolean) {
        scope.launch {
            shortlistRepo.setActive(setOf(itemId), active)
            priceRows()
        }
    }

    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        Text("Ore Shortlist", style = MaterialTheme.typography.titleMedium)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(vertical = 8.dp)) {
            Button(enabled = !busy, onClick = { discover() }) { Text("Discover") }
        }
        if (busy) {
            LinearProgressIndicator(modifier = Modifier.fillMaxWidth().padding(bottom = 8.dp))
        }
        Text(status, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(bottom = 8.dp))

        LazyColumn {
            items(rows) { row ->
                OreShortlistRowItem(row, onActiveChange = { checked -> setActive(row.itemId, checked) })
            }
        }
    }
}

@Composable
private fun OreShortlistRowItem(row: OreShortlistRow, onActiveChange: (Boolean) -> Unit) {
    val subtitle = buildString {
        append(row.family)
        if (row.isIce) append(" · ice")
        row.yieldPct?.let { append(" · yield %.1f%%".format(it * 100)) }
        row.profitPerUnit?.let { append(" · profit/unit %,.2f".format(it)) }
        row.margin?.let { append(" · margin %.1f%%".format(it * 100)) }
    }
    ListItem(
        headlineContent = { Text(row.item) },
        supportingContent = { Text(subtitle) },
        leadingContent = {
            Checkbox(checked = row.active, onCheckedChange = onActiveChange)
        },
        trailingContent = { Text(row.decision, color = decisionColor(row.decision)) },
    )
    HorizontalDivider()
}

@Composable
private fun decisionColor(decision: String) = when (decision) {
    ORE_IMPORT_DECISION -> MaterialTheme.colorScheme.primary
    ORE_INACTIVE_DECISION, ORE_NO_MARKET_DATA_DECISION -> MaterialTheme.colorScheme.outline
    else -> MaterialTheme.colorScheme.onSurface
}
