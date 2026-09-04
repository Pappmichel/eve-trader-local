package com.pappmichel.evetraderlocal.ui.nav

/** Same declarative (tool -> views) structure as the desktop build's
 * main_window.py `_TOOL_MENUS` - kept as plain data here too, so giving one
 * of these a real screen later is a matter of pointing its route at that
 * screen in AppNavHost.kt, not restructuring navigation. Every entry opens
 * PlaceholderScreen except Trading/Candidate Discovery,
 * Trading/Shortlist, and Trading/Unlisted Stock & Undercut Check (see
 * ROADMAP.md's Android section for what's actually ported vs. still
 * pending). */
val TOOL_MENUS: List<Pair<String, List<String>>> = listOf(
    "Trading" to listOf(
        "Shortlist", "Candidate Discovery", "Realized Trades & Transactions",
        "Unlisted Stock & Undercut Check", "Price History",
    ),
    "Production" to listOf(
        "Build Candidates", "Planner", "Asset-Optimized Planner", "Logistics",
        "Special Orders", "Ship Margins & Market Status", "Item Lookup",
        "Invention Estimator", "Owned Blueprints", "Current Jobs & Slots",
    ),
    "Portfolio" to listOf("Overview"),
    "Doctrine" to listOf("Fittings", "Stockpile Status", "Shopping List", "Contract History"),
    "Ore & Minerals" to listOf("Ore Shortlist", "Reprocessing Quote", "Mineral Shopping List"),
    "Station Trading" to listOf("Shortlist", "Undercut & Skills"),
)
