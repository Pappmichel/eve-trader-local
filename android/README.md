# eve-trader-local — Android

Native Android counterpart of the desktop GUI (`eve_trader_local/gui/`),
same design principles: local-first, single-user, no backend server. See
`ROADMAP.md`'s "Android" section (repo root) for the decision history and
current scope.

## What's here (first increment, not a full port)

- Kotlin + Jetpack Compose (Material3), targeting minSdk 26 / compileSdk 34.
- **Theme** (`ui/theme/`): the same New Eden-inspired dark palette as the
  desktop build's `gui/theme.py` (near-black backgrounds, cyan/amber
  accents), kept in sync by eye - Compose and Qt QSS have no shared format
  to generate both themes from a single source.
- **Navigation** (`ui/nav/`): a nav drawer listing every tool/view from
  `ToolMenus.kt`'s `TOOL_MENUS`, mirroring `gui/main_window.py`'s
  `_TOOL_MENUS` structure. Every entry still opens `PlaceholderScreen`
  except the three Trading views below - production planning, doctrine
  stockpiles, refining, station trading, and the rest of Trading itself
  are real, substantial work still ahead (see ROADMAP.md's Android section
  for the current per-tool state). Drawer taps navigate via
  `AppNavHost.kt`'s `navigateFromDrawer` (`popUpTo(start) { saveState =
  true }` + `launchSingleTop` + `restoreState`, Navigation-Compose's own
  recommended drawer pattern) - without it, repeatedly picking drawer
  items pushes an unbounded pile of duplicate back-stack entries instead
  of returning to an existing one.
- **Trading -> Candidate Discovery** (`data/esi/EsiClient.kt`,
  `data/trading/`, `ui/screens/CandidateDiscoveryScreen.kt`): the first
  real (non-placeholder) tool screen. Walks EVE's market-group tree live
  via ESI, mirroring `candidate_discovery.py`'s `_build_candidate_universe_
  from_esi` path - the SDE-accelerated path (`_build_candidate_universe_
  from_sde`) isn't ported (no local SDE cache on this platform yet, see
  `sde.py`), so this always takes the slower ~2000-call live path, same as
  a fresh desktop install that hasn't run `refresh-sde`. `TradingConfig`
  (`data/trading/TradingConfig.kt`) mirrors the desktop build's
  `config.py` dataclass fields this screen actually reads, persisted the
  same way (`settings` table, one JSON blob per scope).
- **Trading -> Shortlist** (`data/trading/Shortlist.kt`,
  `data/trading/ShortlistRepository.kt`, `ui/screens/ShortlistScreen.kt`):
  the second real tool screen. Manual shortlist membership (add/remove/
  edit/toggle-active - tapping a row opens the same dialog Add uses,
  pre-filled, so fixing a typo no longer means delete-and-re-add; that
  dialog also rejects a type ID already used by a different entry rather
  than silently creating a duplicate row; persisted the same
  JSON-blob-per-scope way as `TradingConfig`) plus a
  "Refresh" action running `evaluateShortlist` - a straight Kotlin port of
  `shortlist.py`'s margin/decision formula -
  against live Jita region order stats and (when a Seller character is
  logged in and a structure id is configured) live structure order-book
  stats, plus (same conditions) the seller's own open sell orders at that
  structure (`EsiClient.characterOrders`, mirroring `esi_client.py`'s
  `character_orders` - the same source `own_orders.py`'s
  `fetch_own_sell_orders` reads on desktop). When a Buyer character is
  logged in, it also checks buyer coverage - an open buy order in Jita or
  at the structure (`characterOrders` again), or existing inventory at a
  Jita station or the structure (`EsiClient.characterAssets` +
  `EsiClient.solarSystemStationIds`, the latter a public ESI call that
  gets Jita's NPC station ids without needing the SDE cache this platform
  doesn't have yet - mirroring `own_orders.py`'s
  `fetch_buyer_already_covered`, which reads the same thing from a local
  SDE-backed table on desktop). Together these make "Already ordered"
  reachable through either signal, not just "Import" for everything.
  Best-effort throughout: a Seller/Buyer character authorized before these
  scopes existed falls back to "none known" for that one signal rather
  than failing the whole refresh - re-logging in picks up the new scopes
  (both roles now request the same flat scope set the desktop build's
  `config.py` does, rather than a role-trimmed subset). A stale/revoked
  refresh token on either character degrades the same way (that one
  character's data is just unavailable) rather than aborting the whole
  refresh - it used to take down even the Jita-only pricing above, which
  needs no login at all. Not ported yet:
  Profit / Day (needs Goonmetrics region history for real average daily
  volume), the Goonmetrics current-price fallback when no seller token is
  available, and auto-add/prune from Candidate Discovery
  (`refresh-and-prune` on desktop) - this screen is refresh-only,
  membership is manual (though Candidate Discovery's own screen can now
  add a candidate here directly - see below; there's just no automatic
  hit-rate/movement-threshold filtering behind that button the way
  `refresh-and-prune` has).
- **Candidate Discovery -> Shortlist** (`ui/screens/
  CandidateDiscoveryScreen.kt`'s per-row Add button): a manual stand-in for
  `refresh-and-prune`'s auto-add - adds one candidate to the Shortlist
  membership list directly, so it doesn't have to be retyped by hand into
  Shortlist's own Add dialog. No hit-rate/avg-movement filtering (desktop's
  `min_hit_rate`/`min_avg_movement` thresholds) happens here - every click
  adds unconditionally, same as manually typing it into Shortlist would.
  Goes through `ShortlistRepository.addIfAbsent`, which serializes its
  load-check-save sequence behind a `Mutex` - tapping Add on two different
  rows in quick succession used to race (both reading the same snapshot,
  the later save silently overwriting and dropping the earlier addition).
- **Trading -> Unlisted Stock & Undercut Check** (`data/trading/
  UnlistedUndercut.kt`, `ui/screens/UnlistedUndercutScreen.kt`): two
  independent, always-live one-shot checks (no saved snapshot to load on
  open, matching the desktop view's own restraint), ported from
  `own_orders.py`'s `check_undercut`/`fetch_seller_stock_without_order` -
  single-seller only, unlike those functions' actual desktop callers,
  which pool across every registered seller character sharing a
  structure's hangar (see `UnlistedUndercut.kt`'s own docstring). Undercut
  Check cross-references the seller's own sell orders (`EsiClient.
  characterOrders`) against the structure's full order book
  (`structureOrdersRaw`, already used by Shortlist) by order id, since
  ESI's structure order book carries no owning-character field. Unlisted
  Stock cross-references shortlist item_ids against the seller's assets
  (`EsiClient.characterAssets`, excluding `NON_STOCK_LOCATION_FLAGS`) minus
  their own open sell-order volume there. Item names come from the
  shortlist (no SDE cache exists yet), so a flagged type_id not on the
  shortlist shows its bare number.
- **Trading -> Price History** (`data/history/GoonmetricsClient.kt`,
  `data/trading/HistoryBacktest.kt`, `ui/screens/PriceHistoryScreen.kt`): a
  Kotlin port of `goonmetrics_client.py`'s `price_history`/
  `price_history_chunked` (parses the Goonmetrics/gnf.lt region-history XML
  rehost) and `history_backtest.py`'s `compute_margin_trends` (recent-3-day
  vs. rolling-30-day landed-cost margin per shortlist item). Unlike desktop,
  which reads a locally cached history table populated incidentally by past
  candidate searches, this fetches both regions' history live on every
  Refresh - there's no such cache on this platform and building one isn't a
  prerequisite for the screen (see `PriceHistoryScreen.kt`'s own docstring
  for the full tradeoff). Only the history endpoint is ported;
  `current_prices` (Goonmetrics' other endpoint) is out of scope here.
- **Trading -> Realized Trades & Transactions** (`data/trading/
  TradeReconciliation.kt`, `ui/screens/RealizedTradesScreen.kt`): a Kotlin
  port of `trade_reconciliation.py`'s FIFO matching of the buyer
  character's Jita buys against the seller character's structure sells.
  Ports the buy-must-predate-sell rule at the matcher's core (a buy dated
  after its matched sell is never used as that sale's cost basis - `break`,
  not `skip`, over the sorted buy queue) and the wallet-journal tax
  refinement (a sale's real post-tax ISK, looked up via its
  `journal_ref_id`, replaces the fully modeled `unit_price * haircut`
  estimate whenever the journal has it). New `EsiClient` methods:
  `characterWalletTransactions` (from_id cursor pagination over ESI's
  2500-per-page cap) and `characterWalletJournal` (page/X-Pages, same
  scheme as `characterAssets`). Simplified vs. desktop: one buyer and one
  seller character rather than pooled multi-character matching; the buy-side
  location filter prefers the whole Forge region via the local SDE cache
  when it's populated (`SdeRepository.stationIdsInRegion`), falling back to
  just Jita's own solar system via a live `solarSystemStationIds` call on
  an unrefreshed cache; item names/volumes for any traded type_id fall back
  to a live `/universe/types/`
  call (no shortlist coverage guarantee, unlike Price History); nothing is
  persisted between runs, so `average_daily_sold_by_type` (which needs a
  saved run to read back) isn't ported at all; there's no raw
  wallet-transaction listing, the other half of the desktop tab.
- **The SDE cache** (`data/sde/`: `SdeCsv.kt`, `SdeEntities.kt`,
  `SdeDownloader.kt`, `SdeRepository.kt`; `ui/screens/SdeDataScreen.kt`,
  reachable from the drawer as its own "SDE Data" entry next to Characters
  and Settings): a Kotlin port of `sde.py`'s Fuzzwork CSV download/parse
  pipeline and the `sde_*` half of `storage.py`'s schema, originally scoped
  to six of the desktop build's twelve tables - `invTypes`/`invGroups`/
  `invCategories`/`invMarketGroups` (the candidate universe and its real
  category names) and `staStations`/`mapSolarSystems` (station-in-region
  lookups, via a join since `staStations.csv` carries no region id of its
  own) - and now seven: `invTypeMaterials` joined once Ore & Minerals'
  Reprocessing Quote needed reprocessing yields (see below). The remaining
  five tables back Production/Doctrine-only features (full blueprint BOMs,
  `dgmTypeEffects`, Tech II/Faction detection), none of which exist on this
  platform yet, so fetching them would be pure waste. Faithful to `sde.py`:
  per-file retry/backoff, the UTF-8 BOM strip, the
  HEAD-request ETag staleness check (`checkForNewerSde`), the refresh-state
  row, row counts, and the atomicity guarantee (every file downloaded and
  parsed before the database is touched, the replace done in one Room
  transaction - a phone losing connectivity mid-download is routine, so a
  failed refresh must leave the previous cache exactly as it was). Kotlin
  has no `csv.DictReader` equivalent, so `SdeCsv.kt` is a small hand-rolled,
  unit-tested RFC4180 state machine instead of a new Gradle dependency -
  it has to handle embedded newlines inside `invTypes.csv`'s quoted
  `description` column, which a line-at-a-time parser would desynchronise
  on. Rows stream through a callback rather than materializing as
  `Map<String, String>` per row, so ~14MB of mostly-description text never
  sits on a phone's heap. Room moved to version 2 for the original six
  tables, then version 3 for `invTypeMaterials`/`portionSize`, both via
  `fallbackToDestructiveMigration()` - honest only while this app has
  never shipped or been installed anywhere real (see `AppDatabase.kt`'s own
  comment on when that must become a real `Migration`). The lookup API
  (`typeName`, `categoryNameFor`, `stationIdsInRegion`,
  `stationIdsInSystem`) plus new bulk-table reads (`marketGroups`,
  `typesWithMarketGroup`, `categoryNames`, `groupCategoryIds`) now back
  Candidate Discovery's SDE-backed fast path (see below), Realized Trades'
  buy-side region filter, Station Trading's item-name resolution, and
  Doctrine/Ore & Minerals' own lookups (see their respective entries
  below). Station Trading's Goonmetrics-based candidate discovery itself
  never needed station/category data, so there's nothing left to wire up
  there.
- **Candidate Discovery reads the SDE cache** (`CandidateDiscovery.
  buildCandidateUniverseFromSde`/`buildCandidateUniverse`): the same
  "prefer the local cache, fall back to the live ESI walk only when it's
  empty" branch desktop's own `build_candidate_universe` takes, now that
  the SDE cache itself exists on this platform. `guessCategory` grows the
  real-SDE-category-name half (optional `categoryId`/`categoryNames`/
  `groupId` params) alongside the string/volume heuristic, which remains
  the fallback for the live-ESI path (fetching a real category there would
  mean an extra ESI call per type). One documented simplification vs.
  desktop: the SDE path skips the capital-module packaged-volume
  correction (`resolve_effective_volume_bulk` on desktop) - applying it
  would mean an ESI round-trip per capital module even on the cache-only
  fast path, so a capital module's volume here is the raw (larger) SDE
  figure rather than its true packaged volume. Ships have the same quirk
  but are already excluded by `excludedPathPrefixes`.
- **Station Trading -> Shortlist and Undercut & Skills** (`data/trading/
  StationTradingConfig.kt`, `StationTradingCandidateDiscovery.kt`,
  `StationTradingUndercut.kt`, `StationTradingShortlistRepository.kt`,
  `StationTradingConstants.kt`; `ui/screens/StationTradingShortlistScreen.kt`,
  `StationTradingUndercutScreen.kt`): the first tool other than Trading to
  get real business logic, not just a placeholder. Kotlin ports of the
  desktop build's `station_trading/candidate_discovery.py` and
  `station_trading/undercut.py`:
  - *Shortlist*'s "Discover" is a two-stage scan, same shape as Trading's
    own history_backtest.py -> shortlist.py split: one Goonmetrics
    `current_prices` call for the *whole* Jita market (never ESI) ranks
    every item by bid-ask spread and real average daily traded volume
    (Goonmetrics region history, not order-book depth - depth is the wrong
    signal for "is this actually liquid"), then persists the result.
    Every row shown is live-confirmed against ESI's real order book
    (`confirmLive`/`EsiClient.regionOrderStatsBulk`), bounded to just the
    persisted shortlist - never a per-item ESI call across the whole
    market, since no bulk-region endpoint exists. `GoonmetricsClient.kt`
    grows `currentPrices` for this - the JSON best-bid/best-ask endpoint
    that Trading's own Price History screen never needed (that one only
    ever used the history/XML half of the same third-party API).
  - *Undercut & Skills* combines two independent checks into the one
    screen the drawer entry names: a bidirectional (sell *and* buy side)
    own-order-vs-order-id check at Jita's trade hub station, pooled across
    every registered "trader" character (`EsiClient.regionOrdersRaw`, a new
    method returning the *raw*, unsummarized region order book
    `checkUndercutPooled`/`checkBuyUndercutPooled` cross-reference by
    order_id - a structure/region order book carries no owning-character
    field, so price/type_id matching alone would false-positive on a
    coincidentally identical own price); and a live skill-level pull
    (`EsiClient.characterSkills`, a new method) turned into an order-slot
    count via `orderSlotsFromSkills` (`StationTradingConstants.kt` - the
    real skill type_ids and the 5-base + 4/8/16/32-per-level formula,
    checked against the SDE directly on desktop rather than trusted from
    memory, since "Margin Trading" - a skill some research names - does
    not actually exist in the game). Fee/tax discount skills (Accounting,
    Broker Relations, Advanced Broker Relations) are shown as raw levels
    only, never turned into a numeric discount, since those also depend on
    NPC corp standings this app has no way to read.
  - Station Trading is its own tool with its own config
    (`StationTradingConfig`/`StationTradingConfigRepository`, scope
    `"station_trading"` - separate from `TradingConfig`, the same way the
    desktop build keeps the two dataclasses apart) and its own shortlist
    persistence (`StationTradingShortlistRepository`, scope
    `"station_trading_shortlist_items"` - a different shape and a different
    type_id universe from Trading's own `ShortlistRepository`, so
    conflating the two would mean one type_id could only ever mean one
    thing across two unrelated tools). A new "Trader (Station Trading)"
    login role (`CharactersScreen.kt`) requests
    `esi-markets.read_character_orders.v1` + `esi-skills.read_skills.v1` -
    the same scope pair the desktop build's own
    `station_trading/esi_sync.py` requests, and pooled across every
    registered trader character rather than simplified to one, since
    Station Trading's single role has no reason not to pool the way
    Trading's buyer/seller split does.
  - Item names/categories in both screens are resolved from the local SDE
    cache (`SdeRepository.typeName`/`categoryNameFor`), falling back to
    the bare type_id/"Unknown" when the cache is unrefreshed or doesn't
    carry a given type.
- **Production -> Item Lookup** (`data/production/ProductionPricing.kt`,
  `ProductionConfig.kt`; `ui/screens/ItemLookupScreen.kt`): the first
  Production view, a Kotlin port of `production/pricing.py` scoped to just
  its buy-side comparison - "what does it cost to buy one unit of this
  item right now, home structure or Jita?" This was the one vertical slice
  portable before the SDE cache carried any blueprint data at all -
  Ship Margins & Market Status (below) is what unblocked the rest.
  Simplified vs. desktop: no Goonmetrics fallback (ESI-only for both sides
  - a failed/absent lookup just shows no quote for that side); lookup is
  by type_id only, since the SDE cache has no name-search index yet.
- **Production -> Ship Margins & Market Status** (`data/production/
  ProductionBuildCost.kt`, extends `ProductionConfig.kt`; `ui/screens/
  ShipMarginScreen.kt`): Production's first real *build*-cost view, a
  Kotlin port of `engine._unit_cost`/`_material_qty`/`margin_home`/
  `margin_jita`, scoped to a single-item build-cost/margin lookup
  (mirroring `engine.item_margin_detail`) rather than the desktop
  feature's whole-catalog scan. Unlocked by extending the SDE cache (Room
  version 3 -> 4) with Manufacturing-only blueprint data -
  `industryActivityMaterials.csv`/`industryActivityProducts.csv`
  (`sde_blueprint_materials`/`sde_blueprint_products`, `activityID = 1`
  only; Reactions/Invention/Copying rows are skipped, nothing reads them)
  - following the exact schema-addition shape `SdeRepository.kt`'s own
  docstring predicted for Reprocessing Quote's `sde_type_materials`
  table: a CSV, entities, and a Room version bump, not a redesign.
  `ProductionConfig` grows four fields for this slice (market fees rate,
  material efficiency, job-cost-index rate, facility tax rate) alongside
  the three `ProductionPricing.kt` already reads. Simplified vs. desktop:
  one flat, manually-entered ME level (no owned-BPO research tracking, no
  structure/rig bonus), no live ESI system-cost-index pull (uses the same
  flat fallback rate desktop itself falls back to), and EIV approximated
  from real material buy prices rather than ESI's own adjusted-price
  catalog.
- **Production -> Build Candidates** (`data/production/
  ProductionBuildCandidates.kt`; `ui/screens/
  ProductionBuildCandidatesScreen.kt`): a Kotlin port of `engine.
  discover_build_candidates`, reusing `ProductionBuildCost.kt`'s
  `unitBuildCost`/`marginHome` math (introduced for Ship Margins) across
  every manufacturable, published SDE type - a new `SdeDao.
  manufacturableTypes()` query joins `sde_blueprint_products` against
  `sde_types` (no new table). Simplified vs. desktop: no Jita
  cross-shopping during the scan (would mean hundreds of per-type ESI
  calls at catalog scale; home prices are one bulk download regardless of
  type count, and Jita has no equivalent bulk endpoint), margin-only
  ranking instead of desktop's Goonmetrics daily-movement weighting, and
  no `min_daily_profit` filter.
- **Production -> Current Jobs & Slots** (new `EsiClient.
  characterIndustryJobs`; `data/production/ProductionJobs.kt`; `ui/
  screens/ProductionCurrentJobsScreen.kt`): a live ESI read ported from
  `jobs.py`'s `list_current_jobs`/`character_slot_overview`, plus the
  existing skill-based job-slot formula. A new "Producer" login role
  requests `esi-industry.read_character_jobs.v1` alongside
  `esi-skills.read_skills.v1` - since `characterSkills` already existed
  (from Station Trading), this view shows a *real* total/free slot
  count, something the desktop build's own `character_slot_overview`
  explicitly can't do (see that function's own docstring - it has no
  such skill read wired up). Invention Estimator was evaluated and
  confirmed out of scope: `invention.py`'s recipe lookup needs
  `industryActivityProbabilities`/`industryActivitySkills` SDE tables
  this cache doesn't carry. Asset-Optimized Planner and Logistics remain
  `PlaceholderScreen` - each needs real additional infrastructure
  (character-asset cross-referencing, or ESI endpoints/business rules
  not yet modeled here).
- **Production -> Owned Blueprints** (new `EsiClient.characterBlueprints`
  + `CharacterBlueprint`; `data/production/OwnedBlueprints.kt`; `ui/
  screens/OwnedBlueprintsScreen.kt`): a live ESI read ported from
  `engine.py`'s `list_owned_blueprints`, grouping raw blueprint rows by
  `(type_id, is_original, ME, TE, runs)` exactly like that function's own
  grouping loop, including its "`quantity` is usually an ESI sentinel
  (-1/-2), not a real stack size" guard and its `runs == -1` BPO/BPC
  classification. Requires the new `esi-characters.read_blueprints.v1`
  scope, added to the existing "Producer" login role. Simplification:
  a live per-visit read across currently logged-in Producer characters,
  not desktop's synced local cache spanning every character ever synced
  - same tradeoff Current Jobs & Slots already made.
- **Production -> Planner** (`data/production/ProductionPlanner.kt`;
  `ui/screens/ProductionPlannerScreen.kt`): reading desktop's actual
  Planner tab (`production_planner.py`/`engine.plan_production`) in full
  found it isn't a recursive BOM-explosion feature at all - it's a
  stock-target-driven, ESI-asset-aware buy/build optimizer (nets
  configured stock targets against owned assets/industry jobs, pools
  demand across multiple targets, produces inventory/build/buy/
  invention-needs tables), infrastructure this platform has none of. The
  single-item recursive BOM explosion this menu slot actually needed is
  desktop's *Item Lookup -> Material Tree* view instead
  (`engine.build_material_tree`), so that's what got ported here:
  `buildMaterialTree` explodes a target item/quantity down through any
  manufacturable sub-component to raw materials, reusing the existing
  `ProductionBomSource`/`unitBuildCost` math per node, aggregating shared
  leaf-material demand across branches and pricing the result. Depth-
  capped as a defensive cycle guard even though desktop's own algorithm
  has no such cap (documented in the file's own docstring). Scoped out:
  manual-stock/ESI-asset offsetting and any per-node buy-vs-build
  decision (every manufacturable node is always expanded, matching
  `build_material_tree`'s own semantics) - and, as ever, no Tech II/
  invention modeling (no Invention data in this SDE cache).
- **Production -> Special Orders** (`data/production/SpecialOrders.kt`;
  `ui/screens/ProductionSpecialOrdersScreen.kt`): ports the desktop
  order-list half in full - create/list/mark-done/reopen/remove a named,
  one-off build order with its own line items - as a settings-blob
  repository, the same shape `DoctrineFittingRepository` already
  established. Does *not* port `engine.plan_special_order`'s real
  buy/build planner: that function reuses the entire `plan_production`
  engine (job-run batching, live cost indices/EIV, T2 invention,
  `manual_build_buy` overrides, and ESI-synced asset stock netted
  against `stock_targets` for `net_against_stock`, surfaced as a
  `stock_overlap_warning`) - none of which exists on this platform (same
  gap Planner just found). The screen's "Compute" button instead runs a
  much smaller, clearly-labeled per-line-item Buy-vs-Build estimate on
  top of the existing single-item `unitBuildCost`, not a narrowed port of
  the real planner.
- **Doctrine -> Fittings** (`data/doctrine/EftFittingParser.kt`,
  `DoctrineSdeResolver.kt`; `ui/screens/DoctrineFittingsScreen.kt`): a
  Kotlin port of `doctrine/parser.py`'s EFT fitting-text parser (paste a
  fit exported from the in-game client, see the parsed
  items/quantities/issues - unresolved names, ambiguous splits, malformed
  lines). Item names are resolved via two new lookup queries added to the
  existing SDE cache (`SdeRepository.resolveTypeByName`/`hullTypeNames`) -
  no new table and no Room-version bump, since both are plain queries over
  `sde_types`, already fully populated. A parsed fit can now be saved,
  listed, and deleted under a name (`DoctrineFittingRepository` - a
  settings-blob repository, same shape as `ShortlistRepository`),
  backing the three views below. One honest gap, surfaced in the screen
  itself: the SDE cache has no `dgmTypeEffects` table, so slot
  classification always returns null and most fitted modules parse into
  a generic "cargo" slot rather than their real low/med/high/rig section
  - item names, quantities, and parse issues are unaffected by this gap.
- **Doctrine -> Stockpile Status** (`data/doctrine/DoctrineValidation.kt`,
  `StockpileStatus.kt`; `ui/screens/DoctrineStockpileStatusScreen.kt`):
  `DoctrineValidation.kt` ports `validation.py`'s Soll/priority-allocation/
  deviation math case-for-case - the contract-side matching functions are
  deliberately not ported, since there's no consumer for them without a
  full ESI contract-sync engine. `StockpileStatus.kt` sums saved fittings'
  required quantities and compares them against `EsiClient.
  characterAssets` for a registered Doctrine character, falling back to
  manual entry when none is registered (same "don't block on missing
  auth" pattern `RealizedTradesScreen.kt`/`UnlistedUndercutScreen.kt`
  already use). Simplified vs. desktop: no contract-target multiplier
  (needs that same unported contract-sync engine) and no single fixed
  stockpile location (sums a character's whole asset list, or a location
  filter, rather than one hangar).
- **Doctrine -> Shopping List** (`data/doctrine/ShoppingList.kt`; `ui/
  screens/DoctrineShoppingListScreen.kt`): prices Stockpile Status's real
  shortfalls via `EsiClient.regionOrderStatsBulk`/`GoonmetricsClient.
  currentPrices` - no new price-fetch machinery, reusing exactly what
  Trading/Ore & Minerals already built. No "Build" column: this
  platform's Production port is a single-item build-cost estimate (Ship
  Margins/Build Candidates), not the desktop's recursive BOM-walk
  build-cost engine, so a buy-vs-build comparison per shortfall line
  isn't available here yet.
- **Doctrine -> Contract History** (new `EsiClient.characterContracts`/
  `characterContractItems`; `data/doctrine/ContractHistory.kt`; `ui/
  screens/DoctrineContractHistoryScreen.kt`): a simple read-only listing
  of currently-visible finished contracts with resolved item names -
  not the desktop's permanent fitting-matched history table, which needs
  the same not-yet-ported contract-sync/matching engine Stockpile
  Status's contract-target multiplier is also missing. A new "Doctrine"
  login role requests the two new ESI scopes this and Stockpile Status
  need (`esi-assets.read_assets.v1`, `esi-contracts.read_character_
  contracts.v1`) - kept as its own role/scope pair rather than folded
  into "Seller" (which already carries `esi-assets.read_assets.v1` for
  Ore & Minerals/Unlisted Stock), matching the desktop build's own
  separate `doctrine` role prefix.
- **Ore & Minerals -> Reprocessing Quote** (`data/refining/PasteParser.kt`,
  `ReprocessingYield.kt`, `ReprocessingQuote.kt`, `RefiningConfig.kt`;
  `ui/screens/ReprocessingQuoteScreen.kt`): a Kotlin port of
  `refining/quote.py` + `paste_parser.py` + `reprocessing.py` - paste an
  ore/item list (the same format the in-game inventory copies as text),
  see the reprocessed mineral yield and its priced value. Scrapmetal-path
  yield math only (`ReprocessingYield.kt`'s own docstring explains why: the
  real ore/ice formula needs structure/rig/security/implant/per-ore-family
  skill inputs, and `quote.py`'s Reprocessing tab always uses scrapmetal
  math regardless of what was pasted, so that fuller formula has no caller
  here yet). Skill levels are entered manually rather than pulled via ESI
  (no producer-role skill scope wired up for this yet) and clamped to
  EVE's real 0-5 range so a typo can't inflate a yield past what's
  achievable in-game. Grows the SDE cache to version 3
  (`invTypeMaterials.csv` -> `sde_type_materials`, plus `invTypes.csv`'s
  `portionSize` column) - exactly the addition `SdeRepository.kt`'s own
  docstring predicted once a reprocessing feature needed real yield data.
- **Ore & Minerals -> Ore Shortlist** (`data/refining/OreShortlist.kt`,
  extending `ReprocessingYield.kt`/`RefiningConfig.kt`; `ui/screens/
  OreShortlistScreen.kt`): the ore/ice yield formula Reprocessing Quote
  deliberately left out - structure type, rig tier, security status,
  implant, and per-ore-family reprocessing skill levels, all confirmed
  against wiki.eveuniversity.org/Reprocessing and all manually-entered
  config fields (this app still doesn't pull skills via ESI for
  refining, the same stance `RefiningConfig.kt` already documented for
  scrapmetal). Candidate discovery is a real, fully live SDE query
  (`SdeDao.oreIceCandidateTypes` - every published compressed ore/ice
  type, category 25 filtered by a `Compressed%` name match, since a
  compressed ore/ice type shares its *raw* ore's own group rather than
  having a dedicated "Compressed <Family>" group of its own in the real
  data), not scoped down to a manually-pasted list - no new SDE table
  needed, just a new query over data the Reprocessing Quote port already
  cached. Simplified vs. desktop: mineral-side pricing still needs a
  registered seller character with structure access to complete (the
  ore side prices from public Jita data alone, same documented gap
  Reprocessing Quote already has); no Goonmetrics fallback for the
  home-structure side.
- **Ore & Minerals -> Mineral Shopping List** (`data/refining/
  MineralShoppingList.kt`; `ui/screens/MineralShoppingListScreen.kt`):
  `refining/optimizer.py` turned out to be a genuine mixed-integer linear
  program (`scipy.optimize.linprog`, method `"highs"`, with both
  ore-portion and direct-mineral decision variables marked integer - its
  own docstring documents a real optimality-gap bug a relax-then-round
  approach used to have, which is exactly why it isn't relaxed-then-
  rounded here either). Porting an equivalent MIP solver into a
  phone-friendly Kotlin/JVM dependency was judged impractical for one
  pass, so this substitutes a documented simpler strategy instead: buy
  every required mineral outright at its current cheapest Jita listing,
  with no ore-refining alternative considered at all - that would need
  both a real solver and the ore/ice yield engine Ore Shortlist just
  added, a natural follow-up now that both exist. Further simplified vs.
  even that direct-buy half of the desktop behavior: no haul-cost term
  (the SDE cache doesn't carry mineral item-volume data in a form this
  screen reads) and no Goonmetrics home-market comparison.
- **Settings** (`ui/screens/SettingsScreen.kt`), reachable from the drawer
  next to Characters: a `TabRow` with one tab per tool that has a config on
  this platform - Trading, Station Trading, Production, Ore & Minerals
  (Doctrine has none yet; the EFT parser reads nothing configurable) - each
  a hand-written form over just that tool's config fields. The desktop
  build's own `SettingsDialog` builds its form generically off each config
  dataclass via Python reflection - Kotlin has no equivalent, so these are
  plain fixed forms instead, over the same field sets each `*Config.kt`
  already scopes itself to. Every tab validates every numeric field before
  writing anything - an earlier version of the Trading tab silently
  discarded an unparseable value (a typo) per field and saved (then
  displayed back) `TradingConfig()`'s default for it instead, with no
  indication anything had gone wrong; every tab added since follows the
  same "reject the whole save, name every bad field" rule from the start.
- **Local database** (`data/db/`): Room, mirroring the desktop build's
  `tokens`/`settings` SQLite tables (`storage.py`) - same JSON-blob-per-row
  shape, same table names' worth of meaning. App-private storage is this
  platform's own "local-first, no server" answer, the same way a portable
  `.exe`'s own folder is on desktop.
- **EVE SSO login** (`data/auth/TokenManager.kt`, `ui/screens/
  CharactersScreen.kt`): a real, working authorization-code + PKCE flow -
  same shape as the desktop build's `auth.py` (state check, PKCE
  verifier/challenge, code exchange, refresh, `/oauth/verify`) - but using
  a Chrome Custom Tab plus a custom URI scheme redirect
  (`eveauth-eve-trader-local://callback`, see `AndroidManifest.xml`)
  instead of a loopback `http.server`, since there's no port to bind on
  Android. Only the Trading roles (buyer/seller) are wired into
  `CharactersScreen`'s role list so far. Each authorized character also
  shows its live ISK wallet balance (`EsiClient.characterWalletBalance`,
  mirroring `esi_client.py`'s `character_wallet_balance`) - best-effort per
  character, so one with an expired/under-scoped token simply shows no
  balance rather than blocking the rest of the list. A logout button per
  row calls `TokenManager.removeToken` - that method existed from the
  first commit, but nothing in the UI ever called it, so there was
  previously no way to log a character out again short of clearing app
  data. Backing out of the Custom Tab without finishing login (rather than
  completing or explicitly cancelling it) used to leave `login()`
  suspended forever - `MainActivity.onResume()` now calls
  `TokenManager.cancelPendingLogin()`, which unblocks it with a clear
  "Login cancelled" error whenever the app resumes to a still-pending
  login (the redirect case is a no-op there, since `onNewIntent` already
  completed it moments earlier).

## Setup

1. Register a **second** application at
   https://developers.eveonline.com for this build (the desktop build's
   client id won't work here - its callback URL is `http://localhost:8000/
   callback`, not this one). Connection type: Authorization Code with
   PKCE. Callback URL: `eveauth-eve-trader-local://callback`.
2. `local.properties` already carries a working `EVE_SSO_CLIENT_ID` and is
   committed to this repo **for now** (2026-09-04, explicit instruction -
   this repo is private, so its client id isn't treated as sensitive
   here). This is a deliberate departure from the usual gitignored/
   per-machine pattern (`local.properties.example` still documents that
   normal shape, same idea as the desktop build's `.env.example`) - revisit
   before this repo is ever made public or the app distributed. Don't let
   Android Studio's auto-written `sdk.dir` line get committed here on your
   next sync (see the file's own comment).
3. Open the `android/` folder in Android Studio (Iguana/2023.2 or newer -
   needs JDK 17, which Android Studio bundles its own copy of).
4. **This project ships without a Gradle wrapper jar** - only
   `gradle/wrapper/gradle-wrapper.properties` (pinning Gradle 8.9) is
   committed, since the wrapper jar is a binary this repo's own tooling
   can't generate. Android Studio detects a missing/incomplete wrapper on
   first sync and offers to create it - accept that prompt (or run `gradle
   wrapper` yourself if you have a standalone Gradle install).
5. Sync and run on a device/emulator (API 26+).

## Honest limitations of this increment

- Most of this was originally built and reviewed by hand with no Android
  SDK, Gradle, or JDK 17 in the environment it was written in (only a bare
  JDK 8, per `java -version`) - never actually compiled until CI
  (`build-android.yml`) started building it for real, which is what
  caught five bugs "careful reading" alone had missed: a missing Compose
  Compiler Gradle plugin (Kotlin 2.0+ needs it whenever Compose is
  enabled), a missing `com.google.android.material:material` dependency
  (`themes.xml`'s `Theme.Material3.DayNight.NoActionBar` parent comes from
  it, not from Compose Material3), two missing
  `import kotlinx.serialization.encodeToString` lines, and two bad
  Compose scope-member imports in `CharactersScreen.kt`
  (`androidx.compose.foundation.layout.weight`,
  `androidx.compose.material3.ExposedDropdownMenu` - neither is a
  top-level symbol, so importing them by name shadowed the real
  implicit-receiver ones). CI is green as of this writing, but this still
  hasn't run on a device/emulator - expect a first-run round of smaller
  fixes once it actually opens in Android Studio or installs on a phone.
- No app icon (`android:icon` is deliberately omitted from
  `AndroidManifest.xml` rather than pointing at a nonexistent resource) -
  add one via Android Studio's Image Asset tool.
- Stored tokens are now encrypted at rest (`data/auth/TokenCrypto.kt`,
  AES-256-GCM, key held in the Android Keystore) - a real departure from
  the desktop build's own plain SQLite `tokens` table, since an EVE SSO
  refresh token is a genuine bearer credential worth protecting even in a
  single-user local app. What's still missing: nothing else in the
  database is encrypted (settings/shortlist membership aren't secrets),
  and this hasn't been exercised on a real device yet - Keystore behavior
  (especially key invalidation on lock-screen/biometric changes, which
  this doesn't opt into) is worth re-checking once it has been.
- CI (`.github/workflows/build-android.yml`) runs JVM unit tests
  (`app/src/test/`, JUnit 4) then assembles the debug APK. Test coverage is
  `ShortlistTest.kt` (a Kotlin port of `tests/test_shortlist.py`'s
  formula/decision-precedence cases - Profit/Day and Goonmetrics-history
  cases aren't ported, since that half of `shortlist.py` isn't ported to
  Kotlin yet), `UnlistedUndercutTest.kt` (a port of
  `tests/test_own_orders.py`'s single-seller `check_undercut`/
  `fetch_seller_stock_without_order` cases), and `CandidateDiscoveryTest.kt`
  (a port of `tests/test_candidate_discovery.py`'s `is_wanted_market_path`/
  `_market_group_path` cases plus both halves of `guess_category` - the
  real-SDE-category-name half, including the Booster/Drugs-vs-Implant
  split, and the string/volume fallback),
  `EsiClientTest.kt` (a port of `tests/test_esi_client.py`'s
  `_percentile`/`_summarize_orders` cases - the order-book pricing math
  behind every `OrderStats`), `TokenRecordTest.kt` (a port of
  `tests/test_auth.py`'s `test_is_expired_respects_skew` -
  `TokenRecord.isExpired` is the one pure, Android-independent piece of
  `data/auth/`), `GoonmetricsClientTest.kt` (parser-only tests against
  hand-written Goonmetrics history XML, mirroring
  `tests/test_goonmetrics_client.py`'s `_parse_history_xml` cases - a real
  `kxml2` `XmlPullParser` stands in for `android.util.Xml`, which is a
  throwing stub on the JVM unit-test classpath), `HistoryBacktestTest.kt`
  (a port of `tests/test_history_backtest.py`'s `compute_margin_trends`
  cases - the window math, the thin-history/near-zero-baseline exclusions,
  and the inner-join-drops-unpaired-days behavior), `TradeReconciliation
  Test.kt` (ported case-for-case from `tests/test_trade_reconciliation.py`
  where it still applies - FIFO matching, the buy-after-sell `break` rule,
  location filters, the journal-vs-modeled sell price, and the
  cost-basis-weighted summary; its SDE/storage-backed cases have no
  counterpart here), `SdeCsvTest.kt` (the hand-rolled CSV reader
  against hand-written documents matching the real Fuzzwork shapes -
  quoted commas, embedded newlines, doubled quotes, a BOM on the header -
  since this parser has no desktop equivalent to port tests from at all),
  `StationTradingCandidateDiscoveryTest.kt` and `StationTradingUndercutTest.kt`
  (ported case-for-case from `tests/test_station_trading_candidate_
  discovery.py` and `tests/test_station_trading_undercut.py`'s pure-logic
  cases against `rankCandidates`/`computeUndercuts`),
  `StationTradingConstantsTest.kt` (a port of
  `tests/test_station_trading_constants.py`'s `order_slots_from_skills`
  cases), `ProductionPricingTest.kt` (a port of
  `tests/test_production_pricing.py`'s pure `buy_price`/`buy_source`
  cases), `EftFittingParserTest.kt` (26 cases ported from
  `tests/test_doctrine_parser.py`'s fake-resolver layer - item/quantity
  parsing, unresolved-name and ambiguous-split issues, malformed lines),
  `PasteParserTest.kt`/`ReprocessingYieldTest.kt`/`ReprocessingQuoteTest.kt`
  (ported from `tests/test_refining_{paste_parser,reprocessing,quote}.py` -
  paste-line parsing, both the scrapmetal *and* ore/ice yield formulas
  (the latter's cases ported from `test_refining_reprocessing.py`'s
  ore/ice suite once Ore Shortlist added that path), and the priced-quote
  assembly), `OreShortlistTest.kt` (candidate discovery and pricing cases
  ported from `test_refining_pricing.py`/`test_refining_candidate_
  discovery.py`), `MineralShoppingListTest.kt` (freshly written, not a
  case-for-case port, since the desktop suite exercises LP behavior this
  simpler direct-buy substitute doesn't implement - covers whole-unit
  rounding, broker-fee markup, multiple independent lines, and the
  no-sell-orders case), `ProductionBuildCostTest.kt` (14 cases
  covering material-quantity rounding, buy-vs-build at every price level,
  an unpriceable material collapsing the whole estimate, product-quantity
  division, and both margin formulas), `ProductionBuildCandidatesTest.kt`
  and `ProductionJobsTest.kt` (the catalog-scan and job-slot-formula cases
  for Production's two newest views), and `DoctrineValidationTest.kt`/
  `StockpileStatusTest.kt`/`ShoppingListTest.kt`/`ContractHistoryTest.kt`
  (31 cases total, ported from `tests/test_doctrine_engine.py` and
  hand-written for the platform-specific orchestration around it). No
  lint step, no instrumented/UI tests (would need an emulator), no
  release signing, no Play Store upload.
