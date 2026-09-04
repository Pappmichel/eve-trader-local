package com.pappmichel.evetraderlocal.ui.nav

import android.net.Uri
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Menu
import androidx.compose.material3.DrawerValue
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalDrawerSheet
import androidx.compose.material3.ModalNavigationDrawer
import androidx.compose.material3.NavigationDrawerItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.rememberDrawerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import com.pappmichel.evetraderlocal.data.auth.TokenManager
import com.pappmichel.evetraderlocal.ui.screens.CharactersScreen
import com.pappmichel.evetraderlocal.ui.screens.PlaceholderScreen
import kotlinx.coroutines.launch

private fun placeholderRoute(tool: String, view: String) =
    "placeholder/${Uri.encode(tool)}/${Uri.encode(view)}"

/** The mobile counterpart of main_window.py's menu-bar-plus-tab-workspace
 * shell: a nav drawer listing every tool/view from `TOOL_MENUS` (mirroring
 * `_TOOL_MENUS`), one screen visible at a time instead of closable tabs -
 * a phone has no room for a tab strip the way a desktop window does, so
 * this is the platform-appropriate equivalent of the same "menu lists
 * everything, opening one navigates to it" model, not the same widget. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AppNavHost(tokenManager: TokenManager) {
    val navController = rememberNavController()
    val drawerState = rememberDrawerState(DrawerValue.Closed)
    val scope = rememberCoroutineScope()

    ModalNavigationDrawer(
        drawerState = drawerState,
        drawerContent = {
            ModalDrawerSheet {
                Text(
                    "eve-trader-local",
                    modifier = Modifier.padding(16.dp),
                    style = MaterialTheme.typography.titleMedium,
                )
                HorizontalDivider()
                NavigationDrawerItem(
                    label = { Text("Characters") },
                    selected = false,
                    onClick = {
                        navController.navigate("characters")
                        scope.launch { drawerState.close() }
                    },
                    modifier = Modifier.padding(horizontal = 8.dp),
                )
                TOOL_MENUS.forEach { (tool, views) ->
                    Text(
                        tool,
                        style = MaterialTheme.typography.labelLarge,
                        modifier = Modifier.padding(start = 16.dp, top = 12.dp, bottom = 4.dp),
                    )
                    views.forEach { view ->
                        NavigationDrawerItem(
                            label = { Text(view) },
                            selected = false,
                            onClick = {
                                navController.navigate(placeholderRoute(tool, view))
                                scope.launch { drawerState.close() }
                            },
                            modifier = Modifier.padding(horizontal = 8.dp),
                        )
                    }
                }
            }
        },
    ) {
        Scaffold(
            topBar = {
                TopAppBar(
                    title = { Text("eve-trader-local") },
                    navigationIcon = {
                        IconButton(onClick = { scope.launch { drawerState.open() } }) {
                            Icon(Icons.Filled.Menu, contentDescription = "Menu")
                        }
                    },
                )
            },
        ) { padding ->
            NavHost(
                navController = navController,
                startDestination = "characters",
                modifier = Modifier.padding(padding),
            ) {
                composable("characters") { CharactersScreen(tokenManager) }
                composable("placeholder/{tool}/{view}") { backStackEntry ->
                    val tool = backStackEntry.arguments?.getString("tool") ?: ""
                    val view = backStackEntry.arguments?.getString("view") ?: ""
                    PlaceholderScreen(tool = tool, view = view)
                }
            }
        }
    }
}
