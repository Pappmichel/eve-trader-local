package com.pappmichel.evetraderlocal.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
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
import com.pappmichel.evetraderlocal.data.db.AppDatabase
import com.pappmichel.evetraderlocal.data.doctrine.DoctrineFittingRepository
import com.pappmichel.evetraderlocal.data.doctrine.DoctrineSdeResolver
import com.pappmichel.evetraderlocal.data.doctrine.EftFittingParser
import com.pappmichel.evetraderlocal.data.doctrine.FittingParseException
import com.pappmichel.evetraderlocal.data.doctrine.ParsedFitting
import com.pappmichel.evetraderlocal.data.doctrine.SavedFitting
import com.pappmichel.evetraderlocal.data.sde.SdeRepository
import kotlinx.coroutines.launch

/** Doctrine -> Fittings: paste an EFT-format fitting export, preview how it
 * parses, and save it under a name - ported from the desktop build's
 * `do_parse_fitting`/`do_add_fitting` actions (`doctrine/actions.py`).
 *
 * **Scope: parse-preview + save/list/delete. No edit-in-place, no per-slot
 * targets editor.** [DoctrineFittingRepository] now gives this screen (and
 * Stockpile Status/Shopping List downstream) real persistence - see that
 * file's own docstring - so "paste -> review -> Save" is a real save, not a
 * dead end. What's still not ported: re-parsing a saved fit on edit
 * (`do_update_fitting`'s re-parse-and-revalidate path - this screen's Save
 * only ever creates a new saved fitting, never edits raw_eft in place;
 * delete-and-re-add is the workaround), and the Fuel Bay/Ship Maintenance
 * Bay separate-paste fields (GitHub issue #18 on desktop - no capital-ship
 * doctrine has exercised this port yet).
 *
 * **Slot sections are frequently wrong on this build, and the screen says
 * so.** The local SDE cache has no `dgmTypeEffects` table (see
 * `DoctrineSdeResolver`'s docstring), so `resolveSlot` always returns null
 * and most fitted modules parse into "cargo" rather than their real
 * low/med/high/rig/subsystem/service slot. Item names, quantities, and
 * parse issues (unresolved names, ambiguous splits, malformed lines) are
 * still fully accurate - only the *slot section* label is degraded - so
 * this is still useful for "does this paste even resolve, and what does it
 * actually contain", just not for a real per-slot fit review. Since
 * [com.pappmichel.evetraderlocal.data.doctrine.DoctrineValidation]'s Soll
 * math only distinguishes "exact" (low/med/high/rig/subsystem/service)
 * from "consume" (drone/cargo/charge/fuelbay/shipmaintenancebay) - never a
 * specific slot - a module misclassified as "cargo" here is silently
 * treated as consume-class (tolerance-eligible) rather than exact-class
 * (zero tolerance) by Stockpile Status, the concrete downstream effect of
 * this SDE gap worth knowing about before trusting a saved fit's ampel. */
@Composable
fun DoctrineFittingsScreen(database: AppDatabase) {
    val scope = rememberCoroutineScope()
    val sdeRepo = remember { SdeRepository(database) }
    val resolver = remember { DoctrineSdeResolver(sdeRepo) }
    val fittingRepo = remember { DoctrineFittingRepository(database) }

    var eftText by remember { mutableStateOf("") }
    var fitNameOverride by remember { mutableStateOf("") }
    var contractTargetText by remember { mutableStateOf("0") }
    var stockpileTargetText by remember { mutableStateOf("0") }
    var result by remember { mutableStateOf<ParsedFitting?>(null) }
    var savedFittings by remember { mutableStateOf<List<SavedFitting>>(emptyList()) }
    var status by remember {
        mutableStateOf(
            "Paste an EFT fitting export (from the game client's \"Copy as EFT\") and press Parse."
        )
    }
    var busy by remember { mutableStateOf(false) }

    suspend fun refreshSaved() {
        savedFittings = fittingRepo.list()
    }

    LaunchedEffect(Unit) { refreshSaved() }

    fun parse() {
        busy = true
        status = "Parsing..."
        result = null
        scope.launch {
            try {
                val hullCandidates = resolver.hullNameCandidates()
                val parsed = EftFittingParser.parseFitting(
                    eftText, resolver.resolveName, resolver.resolveSlot, hullCandidates,
                )
                result = parsed
                status = if (parsed.issues.isEmpty()) {
                    "Parsed '${parsed.hullName}, ${parsed.fitName}' - ${parsed.items.size} item(s), no issues."
                } else {
                    "Parsed '${parsed.hullName}, ${parsed.fitName}' - ${parsed.items.size} item(s), " +
                        "${parsed.issues.size} issue(s)."
                }
            } catch (e: FittingParseException) {
                status = "Parse failed: ${e.message}"
            } catch (e: Exception) {
                status = "Error: ${e.message}"
            } finally {
                busy = false
            }
        }
    }

    fun save() {
        val parsed = result ?: return
        val contractTarget = contractTargetText.trim().toIntOrNull()
        val stockpileTarget = stockpileTargetText.trim().toIntOrNull()
        if (contractTarget == null || contractTarget < 0 || stockpileTarget == null || stockpileTarget < 0) {
            status = "Targets must be whole numbers, zero or greater."
            return
        }
        busy = true
        scope.launch {
            try {
                val saved = fittingRepo.save(
                    name = fitNameOverride.trim(), rawEft = eftText, parsed = parsed,
                    contractTarget = contractTarget, stockpileTarget = stockpileTarget,
                )
                refreshSaved()
                status = "Saved '${saved.name}' (${saved.items.size} item(s))."
                eftText = ""
                fitNameOverride = ""
                contractTargetText = "0"
                stockpileTargetText = "0"
                result = null
            } catch (e: Exception) {
                status = e.message ?: "Save failed."
            } finally {
                busy = false
            }
        }
    }

    fun deleteSaved(fittingId: String) {
        scope.launch {
            fittingRepo.delete(fittingId)
            refreshSaved()
        }
    }

    Column(modifier = Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("Doctrine - Fittings", style = MaterialTheme.typography.titleLarge)
        Text(status, style = MaterialTheme.typography.bodyMedium)
        if (busy) LinearProgressIndicator(modifier = Modifier.fillMaxWidth())

        OutlinedTextField(
            value = eftText,
            onValueChange = { eftText = it },
            label = { Text("EFT fitting text") },
            modifier = Modifier.fillMaxWidth().height(180.dp),
        )
        Button(onClick = { parse() }, enabled = !busy && eftText.isNotBlank()) {
            Text("Parse")
        }

        val parsed = result
        if (parsed != null) {
            HorizontalDivider()
            Text(
                "${parsed.hullName} - ${parsed.fitName} (hull type ${parsed.hullTypeId})",
                style = MaterialTheme.typography.titleMedium,
            )
            if (parsed.items.isEmpty() && parsed.issues.isEmpty()) {
                Text("No items parsed - hull-only fitting.", style = MaterialTheme.typography.bodyMedium)
            }

            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(
                    value = fitNameOverride,
                    onValueChange = { fitNameOverride = it },
                    label = { Text("Save as (default: ${parsed.fitName})") },
                    modifier = Modifier.weight(2f),
                )
                OutlinedTextField(
                    value = contractTargetText,
                    onValueChange = { contractTargetText = it },
                    label = { Text("Contract target") },
                    modifier = Modifier.width(120.dp),
                )
                OutlinedTextField(
                    value = stockpileTargetText,
                    onValueChange = { stockpileTargetText = it },
                    label = { Text("Stockpile target") },
                    modifier = Modifier.width(120.dp),
                )
            }
            Button(onClick = { save() }, enabled = !busy) { Text("Save Fitting") }

            LazyColumn(modifier = Modifier.fillMaxWidth().height(240.dp)) {
                if (parsed.items.isNotEmpty()) {
                    item { Text("Items", style = MaterialTheme.typography.labelLarge) }
                    items(parsed.items) { i ->
                        ListItem(
                            headlineContent = { Text("type ${i.typeId} x${i.quantity.toInt()}${if (i.isOffline) " (offline)" else ""}") },
                            supportingContent = { Text("line ${i.lineNo} - section: ${i.slotSection}") },
                        )
                    }
                }
                if (parsed.issues.isNotEmpty()) {
                    item {
                        HorizontalDivider()
                        Text("Issues", style = MaterialTheme.typography.labelLarge)
                    }
                    items(parsed.issues) { i ->
                        ListItem(
                            headlineContent = { Text(i.message) },
                            supportingContent = { Text("line ${i.lineNo} - ${i.issueKind}") },
                        )
                    }
                }
            }
        }

        HorizontalDivider()
        Text("Saved Fittings (${savedFittings.size})", style = MaterialTheme.typography.titleMedium)
        LazyColumn(modifier = Modifier.fillMaxWidth()) {
            items(savedFittings) { f ->
                ListItem(
                    headlineContent = { Text(f.name) },
                    supportingContent = {
                        Text("${f.hullName} - contract target ${f.contractTarget}, stockpile target ${f.stockpileTarget}")
                    },
                    trailingContent = {
                        IconButton(onClick = { deleteSaved(f.fittingId) }) {
                            Icon(Icons.Filled.Close, contentDescription = "Delete")
                        }
                    },
                )
                HorizontalDivider()
            }
        }
    }
}
