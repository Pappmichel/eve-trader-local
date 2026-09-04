package com.pappmichel.evetraderlocal.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

/** Every tool-menu view routes here until it gets a real screen - see
 * ROADMAP.md's Android section for current scope. Not a dead end: swapping
 * one of these for a real screen is the entire integration point in
 * ui/nav/AppNavHost.kt, nothing else needs to change. */
@Composable
fun PlaceholderScreen(tool: String, view: String) {
    Column(
        modifier = Modifier.fillMaxSize().padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Text("$tool → $view", style = MaterialTheme.typography.titleMedium)
        Text("Not ported yet - see ROADMAP.md's Android section.", style = MaterialTheme.typography.bodyMedium)
    }
}
