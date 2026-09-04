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
import com.pappmichel.evetraderlocal.data.db.AppDatabase
import com.pappmichel.evetraderlocal.ui.screens.CandidateDiscoveryScreen
import com.pappmichel.evetraderlocal.ui.screens.CharactersScreen
import com.pappmichel.evetraderlocal.ui.screens.PlaceholderScreen
import com.pappmichel.evetraderlocal.ui.screens.ShortlistScreen
import kotlinx.coroutines.launch

private const val CANDIDATE_DISCOVERY_ROUTE = "trading/candidate-discovery"
private const val SHORTLIST_ROUTE = "trading/shortlist"

/** Every tool/view from `TOOL_MENUS` routes to `PlaceholderScreen` (see that
 * function's own docstring) except the ones a real screen has been built
 * for - currently Trading/Candidate Discovery and Trading/Shortlist (see
 * ROADMAP.md's Android section for what's ported so far). Add a route here
 * as each new screen replaces its placeholder. */
private fun routeFor(tool: String, view: String): String =
    if (tool == "Trading" && view == "Candidate Discovery") {
        CANDIDATE_DISCOVERY_ROUTE
    } else if (tool == "Trading" && view == "Shortlist") {
        SHORTLIST_ROUTE
    } else {
        "placeholder/${Uri.encode(tool)}/${Uri.encode(view)}"
    }

/** The mobile counterpart of main_window.py's menu-bar-plus-tab-workspace
 * shell: a nav drawer listing every tool/view from `TOOL_MENUS` (mirroring
 * `_TOOL_MENUS`), one screen visible at a time instead of closable tabs -
 * a phone has no room for a tab strip the way a desktop window does, so
 * this is the platform-appropriate equivalent of the same "menu lists
 * everything, opening one navigates to it" model, not the same widget. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AppNavHost(tokenManager: TokenManager, database: AppDatabase) {
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
                                navController.navigate(routeFor(tool, view))
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
                composable(CANDIDATE_DISCOVERY_ROUTE) { CandidateDiscoveryScreen(database) }
                composable(SHORTLIST_ROUTE) { ShortlistScreen(database, tokenManager) }
                composable("placeholder/{tool}/{view}") { backStackEntry ->
                    val tool = backStackEntry.arguments?.getString("tool") ?: ""
                    val view = backStackEntry.arguments?.getString("view") ?: ""
                    PlaceholderScreen(tool = tool, view = view)
                }
            }
        }
    }
}
