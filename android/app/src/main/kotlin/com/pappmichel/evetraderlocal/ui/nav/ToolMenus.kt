package com.pappmichel.evetraderlocal.ui.nav

/** Same declarative (tool -> views) structure as the desktop build's
 * main_window.py `_TOOL_MENUS` - kept as plain data here too, so giving one
 * of these a real screen later is a matter of pointing its route at that
 * screen in AppNavHost.kt, not restructuring navigation. Every entry opens
 * PlaceholderScreen except Trading/Candidate Discovery, Trading/Shortlist,
 * Trading/Unlisted Stock & Undercut Check, Production/Item Lookup,
 * Production/Ship Margins & Market Status, Production/Build Candidates,
 * Production/Planner, Production/Current Jobs & Slots,
 * Production/Owned Blueprints, and Production/Special Orders (see
 * ROADMAP.md's Android section for what's actually ported vs. still
 * pending, and see AppNavHost.kt's own routing docstring for the fuller,
 * more current list this comment doesn't fully track).
 *
 * Production/Planner's real screen is a recursive BOM-explosion feature,
 * not a port of desktop's actual Planner tab (a stock-target-driven buy/
 * build optimizer this app has no infrastructure for) - see
 * `data/production/ProductionPlanner.kt`'s own module docstring. */
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
