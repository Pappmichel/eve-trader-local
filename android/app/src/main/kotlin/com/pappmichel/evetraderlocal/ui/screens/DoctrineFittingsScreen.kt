package com.pappmichel.evetraderlocal.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
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
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.pappmichel.evetraderlocal.data.db.AppDatabase
import com.pappmichel.evetraderlocal.data.doctrine.DoctrineSdeResolver
import com.pappmichel.evetraderlocal.data.doctrine.EftFittingParser
import com.pappmichel.evetraderlocal.data.doctrine.FittingParseException
import com.pappmichel.evetraderlocal.data.doctrine.ParsedFitting
import com.pappmichel.evetraderlocal.data.sde.SdeRepository
import kotlinx.coroutines.launch

/** Doctrine -> Fittings: paste an EFT-format fitting export and preview how
 * it parses, ported from the desktop build's `do_parse_fitting` action
 * (`doctrine/actions.py`) - "paste -> review preview" only, the same
 * preview-only contract that action itself has (it "never persists" on
 * desktop either, see its own docstring).
 *
 * **Scope: preview only, nothing is saved.** This is the one Doctrine view
 * this session ported to full depth (see `EftFittingParser.kt` for the
 * parsing algorithm and its docstring for why). Stockpile Status, Shopping
 * List, and Contract History all need a persisted Doctrine/Fitting/
 * stockpile/contract data model this Android build does not have yet, and
 * porting that storage layer just to give this screen a "Save" button was
 * judged out of scope for one pass - see `DoctrineSdeResolver.kt`'s
 * docstring for the full reasoning. Everything below is computed fresh
 * from the pasted text every time Parse is pressed; closing the screen
 * discards it, exactly like any other unsaved preview.
 *
 * **Slot sections are frequently wrong on this build, and the screen says
 * so.** The local SDE cache has no `dgmTypeEffects` table (see
 * `DoctrineSdeResolver`'s docstring), so `resolveSlot` always returns null
 * and most fitted modules parse into "cargo" rather than their real
 * low/med/high/rig/subsystem/service slot. Item names, quantities, and
 * parse issues (unresolved names, ambiguous splits, malformed lines) are
 * still fully accurate - only the *slot section* label is degraded - so
 * this is still useful for "does this paste even resolve, and what does it
 * actually contain", just not for a real per-slot fit review. */
@Composable
fun DoctrineFittingsScreen(database: AppDatabase) {
    val scope = rememberCoroutineScope()
    val sdeRepo = remember { SdeRepository(database) }
    val resolver = remember { DoctrineSdeResolver(sdeRepo) }

    var eftText by remember { mutableStateOf("") }
    var result by remember { mutableStateOf<ParsedFitting?>(null) }
    var status by remember {
        mutableStateOf(
            "Paste an EFT fitting export (from the game client's \"Copy as EFT\") and press Parse."
        )
    }
    var busy by remember { mutableStateOf(false) }

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
            LazyColumn(modifier = Modifier.fillMaxWidth()) {
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
    }
}
