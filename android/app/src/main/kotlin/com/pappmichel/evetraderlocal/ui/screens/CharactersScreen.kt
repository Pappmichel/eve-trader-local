package com.pappmichel.evetraderlocal.ui.screens

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Logout
import androidx.compose.material3.Button
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.ListItem
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.pappmichel.evetraderlocal.data.auth.TokenManager
import com.pappmichel.evetraderlocal.data.auth.TokenRecord
import com.pappmichel.evetraderlocal.data.esi.EsiClient
import kotlinx.coroutines.launch

private fun fmtIsk(value: Double): String = "%,.2f ISK".format(value)

/** Role -> (label, role prefix, scopes) - the same shape as the desktop
 * build's characters_dialog.py `_LOGIN_ROLES`. Only the Trading roles are
 * wired here so far; Production/Doctrine/Station Trading roles get added
 * to this list once those tools themselves are ported (same "structure
 * now, business logic later" scope as PlaceholderScreen - see ROADMAP.md's
 * Android section). */
// The desktop build's own config.py requests the same flat scope tuple for
// every character regardless of role - simpler than tracking which role
// needs which scope, and EVE SSO doesn't mind a character holding a scope
// it happens not to use. Mirrored here rather than trimmed per role.
private val TRADING_SCOPES = listOf(
    "esi-markets.read_character_orders.v1", "esi-markets.structure_markets.v1",
    "esi-wallet.read_character_wallet.v1", "esi-assets.read_assets.v1",
)

private val LOGIN_ROLES: List<Triple<String, String, List<String>>> = listOf(
    Triple("Buyer (Trading)", "buyer", TRADING_SCOPES),
    Triple("Seller (Trading / Ore & Minerals)", "seller", TRADING_SCOPES),
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CharactersScreen(tokenManager: TokenManager) {
    val scope = rememberCoroutineScope()
    var records by remember { mutableStateOf<List<TokenRecord>>(emptyList()) }
    var walletBalances by remember { mutableStateOf<Map<String, Double>>(emptyMap()) }
    var selectedRole by remember { mutableIntStateOf(0) }
    var status by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }

    fun refresh() {
        scope.launch {
            records = tokenManager.listRecords()
            if (records.isEmpty()) status = "No characters authorized yet - pick a role and log in."
            // Best-effort, one character at a time: a character authorized
            // before esi-wallet.read_character_wallet.v1 was requested (or
            // whose token happens to be mid-refresh-failure) simply shows no
            // balance rather than blocking the rest of the list.
            val esi = EsiClient()
            walletBalances = records.mapNotNull { record ->
                try {
                    val fresh = tokenManager.getToken(record.role)
                    record.role to esi.characterWalletBalance(fresh.characterId, fresh.accessToken)
                } catch (e: Exception) {
                    null
                }
            }.toMap()
        }
    }
    LaunchedEffect(Unit) { refresh() }

    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        LazyColumn(modifier = Modifier.weight(1f)) {
            items(records) { record ->
                ListItem(
                    headlineContent = { Text(record.characterName) },
                    supportingContent = {
                        Text("${record.role} - ${if (record.isExpired()) "expired" else "valid"}")
                    },
                    trailingContent = {
                        Row {
                            walletBalances[record.role]?.let { Text(fmtIsk(it)) }
                            IconButton(
                                onClick = {
                                    scope.launch {
                                        tokenManager.removeToken(record.role)
                                        status = "Logged out ${record.characterName} ('${record.role}')."
                                        refresh()
                                    }
                                },
                            ) { Icon(Icons.Filled.Logout, contentDescription = "Log out ${record.characterName}") }
                        }
                    },
                )
                HorizontalDivider()
            }
        }

        Spacer(Modifier.height(8.dp))

        var expanded by remember { mutableStateOf(false) }
        ExposedDropdownMenuBox(expanded = expanded, onExpandedChange = { expanded = it }) {
            OutlinedTextField(
                value = LOGIN_ROLES[selectedRole].first,
                onValueChange = {},
                readOnly = true,
                label = { Text("Role") },
                trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = expanded) },
                modifier = Modifier.menuAnchor().fillMaxWidth(),
            )
            ExposedDropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
                LOGIN_ROLES.forEachIndexed { index, (label, _, _) ->
                    DropdownMenuItem(text = { Text(label) }, onClick = { selectedRole = index; expanded = false })
                }
            }
        }

        Spacer(Modifier.height(8.dp))
        Row {
            Button(
                enabled = !busy,
                onClick = {
                    val (label, rolePrefix, scopes) = LOGIN_ROLES[selectedRole]
                    busy = true
                    status = "Opening the browser for EVE SSO login as $label..."
                    scope.launch {
                        try {
                            val record = tokenManager.login(rolePrefix, scopes)
                            status = "Authorized ${record.characterName} as '${record.role}'."
                            refresh()
                        } catch (e: Exception) {
                            status = e.message ?: "Login failed."
                        } finally {
                            busy = false
                        }
                    }
                },
            ) { Text("Log In...") }
        }

        Spacer(Modifier.height(8.dp))
        Text(status)
    }
}
