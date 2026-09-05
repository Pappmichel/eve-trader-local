# ROADMAP.md — eve-trader-local

Planning notes for where this repo is headed, beyond what's already built
(see README for current status: SQLite storage + config + OAuth foundation
only, no business logic yet). This file tracks *decisions*, not a task
backlog — update it when a decision changes, not on every commit.

## Self-update mechanism (app code)

Two deliberately different stages — don't build stage 2 before stage 1 is
actually outgrown.

**Stage 1 (now): git-based, for a source checkout.**
The app ships as a plain git clone, so updating it can be exactly that: on
startup, compare the locally recorded "installed commit SHA" against
`origin/main`'s current HEAD (via the GitHub API, no auth needed for a
public read), and on mismatch offer to `git fetch && git reset --hard
origin/main` plus a dependency reinstall, then relaunch. No release
pipeline, no binary packaging, no signing — appropriate for the current
Python-checkout distribution model and cheap to build.

**Stage 2 (later, once packaged as a native GUI app): tagged releases +
binary updater.**
Once this ships as an installed binary (see the native-GUI item in the
"Deferred" list below) rather than a source checkout, `git reset --hard`
stops being meaningful to an end user. At that point: semantic-version Git
tags, GitHub Releases carrying the packaged asset, and a small updater
component that does `GET /repos/pappmichel/eve-trader-local/releases/latest`
→ compare version → download the asset → verify a checksum → replace the
installed files → relaunch. This is the same shape every desktop app with
auto-update uses (VS Code, Discord, Electron's/Tauri's own updaters) — no
need to invent something novel here, just implement the standard pattern
when the time comes.

**Either stage updates app code only.** Never touches the local SQLite
database or `config.yaml` overrides — those are the user's own data, out of
scope for an "update" by definition (see `SYNC.md`'s scoping rule: user
data/settings are never a sync or update target). New code running against
that old, untouched database file is exactly why `storage.py`'s schema
migration mechanism (`MIGRATIONS`, added 2026-09-04 - see README.md's own
"Schema migrations" section) exists: Stage 1's `git reset --hard` is what
turns "a table needs a new column someday" from a theoretical gap into a
real one, since it's specifically new code meeting an old database file.

**Known live bug, found 2026-09-05, not yet fixed:** `eve_trader_local/
updater.py` implements both stages already (Stage 1's commit-SHA check
plus Stage 2's `releases/latest`/checksummed-`.exe` download machinery for
the packaged Windows build - this section's own "doesn't exist yet" wording
above about Stage 2 is stale), but every GitHub API call it makes is
unauthenticated, on the stated assumption of "no auth needed for a public
read." The actual repo (`Pappmichel/eve-trader-local`) is **private**
(confirmed via the GitHub API), and a private repo returns a plain 404 to
an unauthenticated `/repos/.../commits/main` or `/repos/.../releases/latest`
request - indistinguishable from "repo doesn't exist" to this code, not a
clean auth failure. The update check is very likely silently broken against
the real repo right now. Fixing it needs a real decision (embed a read-only
token in a distributed client vs. make the repo/its releases public - GitHub
has no "public releases, private source" middle ground), deliberately not
made yet - see this same date's discussion for the options considered.
**Android has no updater of any kind** (git-based Stage 1 doesn't apply to
an installed APK with no source checkout or git binary; an Android Stage 2
would hit the identical private-repo auth problem) - building one is
deferred until the auth question above is settled, not attempted around it.

## SDE (Static Data Export) strategy — decoupled from app updates

This is the important design decision: **SDE refresh is its own,
independent update cycle, unrelated to app-code updates.** The parent repo
already works this way — `eve_trader/production/sde.py`'s `refresh_sde()`
downloads CCP's Static Data Export as republished CSVs directly from
Fuzzwork (fuzzwork.co.uk), not from eve-trader's own GitHub repo, and it
runs on-demand (a "Refresh SDE" action), not on every code deploy. CCP
patches — and therefore new SDE dumps — happen a few times a year, on a
schedule that has nothing to do with when this app's code changes.

Plan for this repo, once SDE-dependent features get ported (Production/
Doctrine/Ore&Minerals — none of that exists here yet, see `SYNC.md`'s
candidate list):

- Port `production/sde.py`'s fetch/parse logic near-verbatim (already
  listed in `SYNC.md` as a sync candidate) — same Fuzzwork CSV endpoints,
  same retry-with-backoff behavior (`_fetch_csv`), same ETag-based
  freshness check (`_dump_etag`/`sde_refresh_state`).
- Target SQLite instead of Postgres: a local `replace_sde_data()`
  equivalent, writing the same `sde_*` table shapes into this repo's own
  database (see `storage.py`).
- Trigger: a CLI command (`eve-trader-local refresh-sde`) now, a GUI action
  later — always user-initiated or a "new SDE dump available" notice on
  startup, never tied to the app's own version-check from the section
  above.
- Fully offline-first in the same sense the rest of this app already is:
  needs internet *during* the refresh (same requirement ESI/Goonmetrics
  calls already have), but zero dependency on any server this project
  operates — Fuzzwork is a public third party, reachable directly from the
  user's machine.

## Access gate: intentionally absent, not deferred

Confirmed decision, not an open question: this repo will **never** need an
`access_gate.py`/tool-grants equivalent. In the parent repo, a special
`DEFAULT_TENANT_ID` operator bypasses the access gate and tool-permission
checks entirely (see that repo's CLAUDE.md, "Tool permissions & Admin").
Here, that bypass isn't a special case at all — it's the *only* case: every
person running this tool owns the machine it runs on and the single local
database on it, so "whoever has the tool may use it" is just the plain
single-user reality, not a policy decision enforced in code. Already
reflected in `SYNC.md`'s "Never sync" list (`access_gate.py`, `admin.py`).

## Native GUI — started 2026-09-04

Decided in favor of "real native," not Electron/Tauri, and PySide6 over
PyQt6 specifically (same Qt bindings, LGPL rather than GPL/commercial —
matters once this is packaged and redistributed as a binary, see Stage 2
above). Foundation is in `eve_trader_local/gui/` — see README.md's own "Native
GUI" section for what exists (menu-bar/tab-workspace shell, the
background-thread action runner) and the full list of views now built for
all five tools (Trading, Production, Doctrine, Ore & Minerals, Station
Trading — Station Trading finished last, completing the GUI's tool-menu
coverage).

**Navigation model** (researched 2026-09-02, jEveAssets as the reference
  point — a Java out-of-game asset manager, embedded SQLite, no server,
  which is the closest existing example of this repo's own target shape):
  jEveAssets does *not* keep every view permanently visible as its own tab
  the way the parent repo's React frontend does (a fixed sidebar/tab per
  page, e.g. Production alone has Overview/Blueprints/BuildCandidates/
  Invention/Logistics/Margin/MaterialTree/Slots/UnlistedStock/AssetSearch/
  Settings all visible at once). Instead it has one **menu bar with a
  "Tools" menu** listing every available view (Assets, Values, Stockpile,
  Overview, Materials, Journal, Transactions, Market Orders, Industry Jobs,
  Contracts, Ship Fittings, Reprocessed, Routing, …); picking one opens it
  as a tab in a shared workspace, and only the tabs someone actually opened
  are ever on screen — closable, reorderable, none of them mandatory.
  For this repo's GUI: mirror that shape, not the parent's fixed-tab-per-
  page layout. A top-level menu per tool (Trading / Production / Doctrine /
  Ore & Minerals), each listing its sub-views as menu items that open as
  workspace tabs on demand — and, per the user's explicit ask, don't just
  mirror the parent's page list 1:1 into menu items: group several of
  today's separate pages into one combined view where that makes sense
  (e.g. Production's BuildCandidates + MaterialTree + Invention could
  reasonably be sub-sections of one "Blueprints" view rather than three
  separate menu entries) rather than reproducing every existing route as
  its own item.

## Deferred (not started, tracked here so it isn't lost)

- All five tools now have real GUI views (see README.md's "Native GUI"
  section), plus a Settings dialog and an in-app Characters (OAuth/login)
  dialog, both reachable from a new top-level "App" menu (`gui/dialogs/`,
  `gui/main_window.py`) — Settings changes and character login/removal no
  longer require dropping to the CLI. General polish (column widths/sorting
  defaults, remembered window/tab state, real tuple/dict Settings editors)
  is done too, as of 2026-09-03 — see README.md's "Native GUI" section for
  what each of those actually covers.
- A careful parent-vs-local GUI audit (2026-09-03) found real gaps even
  after the above: Production's backend had 12 `do_*` actions/CLI commands
  with no GUI at all (item margin search, standalone invention estimator,
  material-tree browser, item-location search, system cost indices,
  structure-name resolution, manual build/buy override, in-place stock-
  target edit, current-jobs list, per-character job-slot usage, owned-
  blueprint browser), two ported-but-unwired actions
  (`invention_logistics`/`t1_bpc_invention_needs`, the manual-stock ledger)
  sat unreachable from any view, and `portfolio_overview()` had no GUI at
  all despite being a real cross-cutting page in the parent's frontend.
  Closed 2026-09-03: four new Production views (Item Lookup, Invention
  Estimator, Owned Blueprints, Current Jobs & Slots), manual-stock wired
  into the Planner, manual build/buy override + structure-name resolution +
  invention-logistics wired into Logistics, system cost indices folded into
  Ship Margins & Market Status, and a new top-level Portfolio menu/view for
  `portfolio_overview()` — see README.md's "Native GUI" section for the
  full per-view breakdown. This is the point where every gap the audit
  found is resolved — Production's GUI now covers its full backend surface,
  cross-checked against `SYNC.md`'s current Production section.
- **Packaging — started 2026-09-04.** `packaging/eve-trader-local.spec`
  (PyInstaller) builds the GUI into a single portable Windows `.exe`, and
  `.github/workflows/build-windows.yml` builds + smoke-tests it on a real
  Windows runner on every `vX.Y.Z` tag push, attaching the result to a
  GitHub Release. See README.md's "Packaging" section and
  `packaging/README.md` for the two real bugs this setup already caught
  (an editable install's package not getting bundled by PyInstaller at all;
  a windowed build's crash silently having nowhere to print to). Still
  missing: an actual installer (Inno Setup or similar - Start Menu entry +
  uninstaller, vs. today's plain portable `.exe`), and the Stage-2
  self-updater component itself described above - this packaging step
  produces the tagged-release artifact that updater would need, but the
  updater that actually checks `/releases/latest` and applies it doesn't
  exist yet.

## Android (standalone, no server) — started 2026-09-04

Confirmed requirement: the tool should eventually also run as a standalone
Android app (no backend server involved — the same "whoever runs it owns
the data" model as desktop, just on a phone). Originally sequenced *after*
the desktop GUI so there'd be something concrete to base the Android
approach on (see the three options this section used to leave open, kept
below for the record) — that desktop GUI now exists (see "Native GUI"
above), so this has started.

**Decision: plain native Kotlin/Compose, not Chaquopy.** Of the three
previously-listed options, this is closest to the "Chaquopy" shape (native
Android UI, not a wrapped web frontend or BeeWare/Toga) but *without* the
embedded CPython runtime — `android/` is a from-scratch Kotlin app with no
dependency on `eve_trader_local` at all, business logic included. Chosen
for the best native Android feel and the simplest build (no CPython
packaging inside an APK) at a real, accepted cost: none of
`eve_trader_local`'s five tools' business logic (candidate discovery,
production planning, doctrine stockpiles, refining, station trading) is
reused — it gets ported to Kotlin by hand, tool by tool, same as the
desktop GUI itself was built view by view. Revisit Chaquopy specifically if
that porting cost turns out to dominate.

What exists in `android/` so far (see that folder's own README.md for
full detail and honest limitations — notably: most of it was originally
written and reviewed by hand with no Android SDK/Gradle/JDK 17 available
in the environment it was built in, and stayed uncompiled until CI
(`build-android.yml`) started actually building it, which surfaced and
fixed five real bugs — a missing Compose Compiler Gradle plugin, a missing
Material Components dependency, two missing `encodeToString` imports, and
two bad Compose scope-member imports in `CharactersScreen.kt` — none of
which "careful reading" alone had caught):
- The same New Eden-inspired dark theme as `gui/theme.py`, ported to
  Compose's `ColorScheme` (`ui/theme/`) — kept in sync by eye, no shared
  source of truth between Compose and Qt QSS.
- A nav-drawer shell mirroring `main_window.py`'s `_TOOL_MENUS` structure
  (`ui/nav/ToolMenus.kt`) — every tool/view listed; all but two still open a
  `PlaceholderScreen`.
- A local Room database mirroring the desktop build's `tokens`/`settings`
  SQLite tables (`storage.py`) — app-private storage is this platform's own
  "local-first, no server" answer, same role a portable `.exe`'s data
  folder plays on desktop.
- A real, working EVE SSO OAuth2 PKCE login flow (`data/auth/
  TokenManager.kt`, `ui/screens/CharactersScreen.kt`) mirroring `auth.py`'s
  flow exactly (state check, PKCE verifier/challenge, code exchange,
  refresh, `/oauth/verify`), adapted for the platform: a Custom Tab plus a
  custom URI scheme redirect instead of a loopback `http.server`, since
  there's no port to bind on Android — still no server anywhere in the
  flow, which is the property that actually matters. Only the Trading
  roles are wired into the UI so far. Each authorized character shows its
  live ISK wallet balance (`EsiClient.characterWalletBalance`) -
  best-effort per character - and can be logged out again via a per-row
  button calling `TokenManager.removeToken` (present since the first
  commit, but never wired to any UI control until now).
- Trading → Candidate Discovery, the first real (non-placeholder) tool
  screen (`data/esi/EsiClient.kt`, `data/trading/`, `ui/screens/
  CandidateDiscoveryScreen.kt`) — a Kotlin port of `candidate_discovery.py`'s
  live-ESI-walk path (`_build_candidate_universe_from_esi`) plus a
  `TradingConfig` mirroring the `config.py` dataclass fields it needs, both
  persisted through the same Room `settings` table. The SDE-accelerated
  path (`_build_candidate_universe_from_sde`) is not ported — no local SDE
  cache exists on this platform yet (`sde.py`'s Fuzzwork CSV pipeline is
  its own, separate porting effort) — so this always takes the slower
  ~2000-call live path, same as a fresh desktop install that hasn't run
  `refresh-sde`.
- Trading → Shortlist, the second real tool screen (`data/trading/
  Shortlist.kt`, `data/trading/ShortlistRepository.kt`, `ui/screens/
  ShortlistScreen.kt`) — a Kotlin port of `shortlist.py`'s margin/decision
  formula, plus new `EsiClient` methods for live Jita region order stats and
  (when a Seller character is logged in) live structure order-book stats.
  Membership (which items are tracked) is manual add/remove/edit/
  toggle-active here (tapping a row opens the same dialog Add uses,
  pre-filled), not auto-populated/pruned from Candidate Discovery the way
  desktop's `refresh-and-prune` does it (though a Candidate Discovery row
  can now add itself here directly — see below) — the Goonmetrics
  current-price fallback and buyer-covered tracking aren't ported. See
  `android/README.md`'s own Shortlist entry for the full list.
- Trading → Profit / Day and the hit-rate/avg-movement auto-prune half of
  `refresh-and-prune` are both now ported. Profit/Day
  (`Shortlist.kt`'s `averageMarketDailyVolume`/`profitPerUnit x
  avgDailyVolume`) sources its volume from Goonmetrics reference-region
  history — never order-book depth, never the trader's own realized
  sales, matching desktop's own two documented bug-fix issues (#51/#100)
  on this exact point. The auto-prune half (`HistoryBacktest.
  scoreCandidate`, ported from `history_backtest.py`'s
  `_score_candidate`/`_latest_margin`) is split across two buttons rather
  than one combined CLI action, matching how this platform already
  splits Discovery and Shortlist into separate screens: Candidate
  Discovery's own "Score & Add Recommended" button is the auto-*add*
  half (this replaces the old plain per-row "Add to Shortlist" button's
  no-filter description above — that manual override still exists
  alongside it), and Shortlist's new "Prune" button is the auto-remove/
  reactivate half. Missing vs. desktop: no skip-streak grace period and
  no `max_active_shortlist_items` rank cap, since both need a persisted
  skip-since table this platform doesn't have — pruning here acts
  immediately on today's numbers instead of after a grace period.
- Shortlist now reads both signals that make "Already ordered" reachable:
  the seller's own open sell orders at the structure, and buyer coverage
  (an open buy order in Jita/at the structure, or existing inventory at a
  Jita station/the structure) — `EsiClient.characterOrders`/
  `characterAssets`/`solarSystemStationIds`, mirroring `esi_client.py`'s
  `character_orders`/`character_assets` and `own_orders.py`'s
  `fetch_own_sell_orders`/`fetch_buyer_already_covered` (the Jita-station
  lookup uses a public ESI call instead of the SDE-backed table desktop
  reads, since there's no SDE cache on this platform yet). Best-effort per
  signal, falling back to "none known" for a Seller/Buyer character
  authorized before its scopes existed — both login roles now request the
  same flat scope set `config.py` does on desktop, rather than a
  role-trimmed subset.
- A Settings screen (`ui/screens/SettingsScreen.kt`), reachable from the
  drawer next to Characters - a `TabRow` with one hand-written form per
  tool that has a config so far (Trading, Station Trading, Production, Ore
  & Minerals), since Kotlin has no equivalent of the desktop
  `SettingsDialog`'s reflection-driven generic form.
- CI (`.github/workflows/build-android.yml`), the Android counterpart of
  `build-windows.yml` - runs JVM unit tests then assembles the debug APK on
  every push/PR touching `android/`. No wrapper jar is committed (see
  `android/README.md`), so it installs Gradle itself and invokes it
  directly rather than `./gradlew`. No lint step, no instrumented/UI tests
  (would need an emulator), no release signing, no Play Store upload.
- A generic placeholder app icon (`res/mipmap-anydpi-v26/ic_launcher*.xml`,
  an adaptive icon over two plain vector drawables - a flat
  `BgWindow`-colored background behind a blocky cyan "E" monogram
  foreground) - not designed artwork, just something other than the
  default Android icon. See `android/README.md`'s own entry.
- Encryption at rest for stored tokens (`data/auth/TokenCrypto.kt`,
  AES-256-GCM with an Android-Keystore-held key) - a real departure from
  the desktop build's plain SQLite `tokens` table, since an EVE SSO
  refresh token is a genuine bearer credential. See `android/README.md`'s
  own entry for what's still unexercised (a real device/Keystore
  invalidation behavior).
- A JVM unit test suite (`app/src/test/`, JUnit 4) - `ShortlistTest.kt`, a
  Kotlin port of `tests/test_shortlist.py`'s formula/decision-precedence
  cases (Profit/Day and Goonmetrics-history cases aren't ported, matching
  `Shortlist.kt` itself not covering that half yet), and
  `UnlistedUndercutTest.kt`, a port of `tests/test_own_orders.py`'s
  single-seller `check_undercut`/`fetch_seller_stock_without_order` cases,
  and `CandidateDiscoveryTest.kt`, a port of
  `tests/test_candidate_discovery.py`'s `is_wanted_market_path`/
  `_market_group_path` cases plus `guess_category`'s string/volume
  fallback half, `EsiClientTest.kt`, a port of `tests/test_esi_client.py`'s
  `_percentile`/`_summarize_orders` cases, and `TokenRecordTest.kt`, a
  port of `tests/test_auth.py`'s `test_is_expired_respects_skew`.
  Runs in CI as a `testDebugUnitTest` step before the APK assembles.
- Trading → Unlisted Stock & Undercut Check (`data/trading/
  UnlistedUndercut.kt`, `ui/screens/UnlistedUndercutScreen.kt`) - two
  independent, always-live checks ported from `own_orders.py`'s
  `check_undercut`/`fetch_seller_stock_without_order`. Undercut Check
  cross-references the seller's own sell orders against the structure's
  full order book by order id (ESI's structure book carries no
  owning-character field); Unlisted Stock cross-references shortlist
  item_ids against the seller's assets minus their own open sell-order
  volume. Item names come from the shortlist, since there's still no SDE
  cache on this platform. Now pooled across every registered seller
  character sharing the structure's hangar, matching `own_orders.py`'s
  actual `_pooled` desktop callers (`check_undercut_pooled`/
  `fetch_seller_stock_without_order_pooled`) instead of assuming exactly
  one - a cheaper order or covering listing from one of your *own* other
  sellers is no longer mistaken for a competitor undercutting you or
  missing stock (the same GitHub issue #46 desktop already fixed).
- Trading → Price History (`data/history/GoonmetricsClient.kt`,
  `data/trading/HistoryBacktest.kt`, `ui/screens/PriceHistoryScreen.kt`) - a
  Kotlin port of `goonmetrics_client.py`'s `price_history`/
  `price_history_chunked` (region daily history over the Goonmetrics/gnf.lt
  rehost) and `history_backtest.py`'s `compute_margin_trends` (recent-3-day
  vs. rolling-30-day landed-cost margin, the same momentum signal desktop
  computes). Deliberately fetches live on every Refresh instead of reading a
  cache: desktop's view is a pure read over a history table that gets
  populated incidentally by candidate searches, none of which exists here,
  and building that whole caching pipeline isn't what unblocks this screen -
  see the screen's own docstring for the full tradeoff. `current_prices`
  (the other Goonmetrics endpoint, needed later for Station Trading) is out
  of scope here.
- Trading → Realized Trades & Transactions (`data/trading/
  TradeReconciliation.kt`, `ui/screens/RealizedTradesScreen.kt`, new
  `EsiClient` wallet-transactions/wallet-journal methods) - a Kotlin port of
  `trade_reconciliation.py`'s FIFO matching of Jita buys against structure
  sells, including the buy-must-predate-sell rule (a matched buy dated after
  its sell is never used as that sale's cost basis - `break`, not `skip`,
  the same correctness fix already landed on desktop) and the
  wallet-journal tax refinement via `journal_ref_id`. Buys are filtered to
  the whole Forge region via the local SDE cache when it's populated
  (`SdeRepository.stationIdsInRegion`, wired up once the SDE cache itself
  landed - see below), falling back to just Jita's own solar system live
  via ESI on an unrefreshed cache; item names/volumes for any
  non-shortlisted traded type fall back to a live `/universe/types/` call.
  Now pools *every* registered buyer and seller character rather than
  assuming exactly one of each - `reconcile_realized_trades` pools ANY
  buyer's buys against ANY seller's sells (confirmed by reading the
  source; never paired 1:1 by character), and every seller's
  wallet-journal entries are unioned by ref id before the one combined
  FIFO match. `average_daily_sold_by_type` (matched-quantity-per-type
  divided by the lookback window) is also now ported - it turned out to
  need only the run's own trade list, not desktop's persisted-snapshot
  table (`storage.py`'s `save_realized_trades`/`latest_realized_trades`
  is just a single-row cache of the last run for the GUI's convenience on
  restart, not a real historical-runs store) - so it's a pure aggregation
  over the same live `List<RealizedTrade>` this screen already holds
  after every Reconcile press, with no new storage needed. There's still
  no raw wallet-transaction listing, the other half of the desktop tab.
- The SDE cache itself (`data/sde/` - `SdeCsv.kt`, `SdeEntities.kt`,
  `SdeDownloader.kt`, `SdeRepository.kt`; reachable from the drawer as its
  own "SDE Data" screen, `ui/screens/SdeDataScreen.kt`) - a Kotlin port of
  `sde.py`'s Fuzzwork CSV download/parse pipeline and the `sde_*` half of
  `storage.py`, originally scoped to six of the desktop build's twelve
  tables (invTypes, invGroups, invCategories, invMarketGroups, staStations,
  mapSolarSystems) and now seven (`invTypeMaterials` joined once Ore &
  Minerals' Reprocessing Quote needed it, see below - the remaining five
  are Production/Doctrine-only and still out of scope). Room moved to
  version 2 then 3 (`fallbackToDestructiveMigration()`, honest only while
  nothing is installed anywhere real - see `AppDatabase.kt`'s own comment).
  A hand-rolled, unit-tested CSV reader stands in for `csv.DictReader`
  (Kotlin has no equivalent, and one state machine handling embedded
  newlines/quoted commas beats a new dependency for a handful of
  fixed-shape files).
  The lookup API (`typeName`, `categoryNameFor`, `stationIdsInRegion`,
  `stationIdsInSystem`) now backs Candidate Discovery (see below),
  Realized Trades' buy-side filter (see above), and Station Trading's
  Shortlist/Undercut screens' item-name resolution. Station Trading's own
  candidate discovery (the Goonmetrics-based market scan) is the one
  tracked follow-up left that still doesn't read it - it never needed
  station or category data to begin with, so there's nothing concrete to
  wire up there yet.
- Candidate Discovery now reads the SDE cache when it's populated
  (`CandidateDiscovery.buildCandidateUniverseFromSde`, via new `SdeDao`/
  `SdeRepository` bulk-table reads) - the fast path desktop's own
  `build_candidate_universe` prefers, skipping the ~2000-call live-ESI walk
  entirely. `guessCategory` grows the real-SDE-category-name half
  (`categoryId`/`categoryNames`/`groupId` params, with the Booster/Drugs
  vs. Implant split `IMPLANT_CATEGORY_ID`/`BOOSTER_GROUP_ID` document) -
  the string/volume heuristic remains the fallback for the live-ESI path,
  which has no per-type category to give it. One documented simplification:
  the SDE path skips desktop's capital-module packaged-volume ESI
  correction (`resolve_effective_volume_bulk`), since applying it would
  mean an ESI round-trip per capital module even on the cache-only fast
  path - a capital module's volume here is the raw (larger) SDE figure.
- Station Trading -> Shortlist and Undercut & Skills (`data/trading/
  StationTradingConfig.kt`, `StationTradingCandidateDiscovery.kt`,
  `StationTradingUndercut.kt`, `StationTradingShortlistRepository.kt`,
  `StationTradingConstants.kt`; `ui/screens/StationTradingShortlistScreen.kt`,
  `StationTradingUndercutScreen.kt`) - the first tool other than Trading to
  get real business logic. A Kotlin port of
  station_trading/candidate_discovery.py (one Goonmetrics `current_prices`
  scan of the whole Jita market, ranked by spread * real daily volume, with
  live ESI confirmation bounded to whatever that scan already narrowed
  things down to - never a per-item ESI call across the whole market) and
  station_trading/undercut.py (bidirectional own-order monitoring at Jita's
  trade hub, pooled across every registered "trader" character - unlike
  Trading's own buyer/seller split, Station Trading's single role has no
  reason not to pool). `GoonmetricsClient.kt` grows `currentPrices` (the
  JSON best-bid/best-ask endpoint, the half of that client Price History
  never needed). Its own shortlist is a separate persisted list from
  Trading's Shortlist (different tool, different type_id universe - a new
  `station_trading` login role with its own scopes, since Station Trading
  needs `esi-skills.read_skills.v1` for the Skills half and neither of
  Trading's roles request it). Item names/categories in the Shortlist and
  Undercut screens now read from the local SDE cache
  (`SdeRepository.typeName`/`categoryNameFor`), falling back to the bare
  type_id when the cache is unrefreshed or doesn't carry that type. Skill-
  derived fee/tax discounts are shown as raw levels only, never turned into
  a numeric discount - those depend on NPC corp standings this app has no
  way to read (an omission the
  desktop build's own constants.py documents too).
- Production -> Item Lookup (`data/production/ProductionPricing.kt`,
  `ProductionConfig.kt`; `ui/screens/ItemLookupScreen.kt`) - the first
  Production view, deliberately scoped to `production/pricing.py`'s
  buy-side comparison (home structure order book vs. Jita landed price)
  rather than any feature needing a real build cost - the one vertical
  slice portable before the SDE cache carried any blueprint data at all.
  Simplified vs. desktop: no Goonmetrics fallback (ESI-only for both
  sides); lookup is by type_id only, since the SDE cache has no name-search
  index.
- Doctrine -> Fittings (`data/doctrine/EftFittingParser.kt`,
  `DoctrineSdeResolver.kt`; `ui/screens/DoctrineFittingsScreen.kt`) - a
  Kotlin port of `doctrine/parser.py`'s EFT fitting-text parser, wired onto
  the local SDE cache for item-name resolution (`SdeRepository.
  resolveTypeByName`/`hullTypeNames`, two new lookup queries - no schema or
  Room-version change), now with a save/list/delete UI over
  `DoctrineFittingRepository` (settings-blob persistence, same shape as
  `ShortlistRepository`) backing the three views below. Honest gap: the
  SDE cache has no `dgmTypeEffects` table, so slot classification always
  returns null and most fitted modules parse into "cargo" instead of
  their real low/med/high/rig section - item names, quantities, and parse
  issues themselves are unaffected, and the screen says so.
- Doctrine -> Stockpile Status (`data/doctrine/DoctrineValidation.kt`,
  `StockpileStatus.kt`; `ui/screens/DoctrineStockpileStatusScreen.kt`) -
  `DoctrineValidation.kt` ports `validation.py`'s Soll/priority-allocation/
  deviation math case-for-case; `StockpileStatus.kt` compares saved
  fittings' required quantities against `EsiClient.characterAssets` (or
  manual entry when no Doctrine character is registered). The
  contract-target multiplier (GitHub issue #36) is now wired for real via
  the contract-sync engine below, instead of hardcoded to 0. Still no
  single fixed stockpile location (sums a character's whole asset list,
  or a location filter).
- Doctrine -> Shopping List (`data/doctrine/ShoppingList.kt`; `ui/screens/
  DoctrineShoppingListScreen.kt`) - prices Stockpile Status's real
  shortfalls via `EsiClient.regionOrderStatsBulk`/`GoonmetricsClient.
  currentPrices` (no new price-fetch machinery). No "Build" column -
  this platform's Production port is a single-item build-cost estimate,
  not the desktop's recursive BOM-walk build-cost engine, so buy-vs-build
  comparison isn't available here.
- Doctrine -> Contract History (new `EsiClient.characterContracts`/
  `characterContractItems`; `data/doctrine/ContractHistory.kt`; `ui/
  screens/DoctrineContractHistoryScreen.kt`) - now backed by the real
  contract-sync engine below: `syncAndPersist`/`loadPermanentHistory`
  read a genuinely permanent, fitting-matched history instead of the
  original read-only "currently visible ESI contracts" listing (kept as
  a secondary "Show Live ESI Listing" view). A new "Doctrine" login role
  requests the two ESI scopes (`esi-assets.read_assets.v1`,
  `esi-contracts.read_character_contracts.v1`) this and Stockpile Status
  need.
- Doctrine -> the contract-sync/matching engine itself (`data/doctrine/
  ContractSync.kt`, `DoctrineContractHistoryEntity.kt`,
  `DoctrineContractHistoryRepository.kt`; extends `DoctrineValidation.kt`)
  - ports `doctrine/esi_sync.py`'s `sync_contracts`, `engine.py`'s
  `match_and_validate_contract`/`load_match_candidates`, and the
  contract-side half of `validation.py` (hull gate, Ist multiset,
  weighted exact/consume overlap score, 0.5 threshold, title-hint
  tiebreak, deviation table, wrong-variant pairing) that Stockpile
  Status's own initial port had deliberately left out for lack of a
  caller - 71 tests ported case-for-case against the desktop suite, all
  passing. `DoctrineContractHistoryEntity`/`Repository` is a real Room
  table (`doctrine_contract_history`, DB version 4 -> 5) rather than this
  app's usual settings-blob-JSON list, since a permanently-growing history
  log fits Room better - it's GitHub issue #19's answer to ESI's own
  ~30-day contract-visibility window. Three scope decisions, each forced
  by a concrete existing gap rather than invented: character contracts
  only (this platform's "Doctrine" login role never requested
  `esi-contracts.read_corporation_contracts.v1`, and `EsiClient` has no
  corporation-contracts endpoint); no persisted "active contracts"
  snapshot (a finished contract is re-matched directly from ESI's
  still-served item list on each sync instead of reusing a prior
  outstanding-match record - strictly more permissive than desktop, not
  less correct); no acceptor-name resolution (`EsiClient` has no POST
  request path at all, so `acceptorId` is stored but never resolved to a
  name).
- Ore & Minerals -> Reprocessing Quote (`data/refining/{PasteParser,
  ReprocessingYield,ReprocessingQuote,RefiningConfig}.kt`; `ui/screens/
  ReprocessingQuoteScreen.kt`) - a Kotlin port of `refining/quote.py` +
  `paste_parser.py` + `reprocessing.py`'s scrapmetal reprocessing path
  (paste an ore/item list, see the mineral yield and its priced value).
  Scrapmetal only, not the real ore/ice yield formula (structure/rig/
  security/implant/per-ore-family skills) - `quote.py`'s own Reprocessing
  tab always reprocesses via scrapmetal math regardless of what was pasted,
  so the ore/ice formula has no caller on this platform yet and isn't
  ported speculatively. Extends the SDE cache (Room version 2 -> 3) with
  `invTypeMaterials.csv` (`sde_type_materials` table) and `invTypes.csv`'s
  `portionSize` column - exactly the addition `SdeRepository`'s own
  docstring predicted ("a CSV, an entity, and a Room version bump") once a
  reprocessing feature needed it.
- Ore & Minerals -> Ore Shortlist (`data/refining/OreShortlist.kt`;
  extends `ReprocessingYield.kt`/`RefiningConfig.kt`; `ui/screens/
  OreShortlistScreen.kt`) - the ore/ice yield formula Reprocessing Quote
  deliberately left out (structure type, rig tier, security status,
  implant, per-ore-family skill levels - all manually-entered config
  fields, matching this app's existing "no ESI skill pulls for refining"
  stance), plus a real, fully live candidate-discovery pipeline: every
  published compressed ore/ice type is queried straight from the SDE
  cache (`SdeDao.oreIceCandidateTypes`, category 25 + a `Compressed%`
  name filter - no new table, just a new query), not scoped down to a
  manually-pasted list. Mineral-side pricing still needs a registered
  seller character with structure access to complete (same documented
  gap Reprocessing Quote already has); the ore side prices from public
  Jita data alone.
- Ore & Minerals -> Mineral Shopping List (`data/refining/
  MineralShoppingList.kt`, `MineralShoppingListOptimizer.kt`; `ui/screens/
  MineralShoppingListScreen.kt`) - `refining/optimizer.py` is a genuine
  mixed-integer linear program (`scipy.optimize.linprog`, both ore-portion
  and direct-mineral variables integer, with its own documented fix for a
  relax-then-round optimality-gap bug). The first pass at this screen
  called an equivalent solver impractical for a phone-friendly Kotlin/JVM
  dependency without actually checking - that call was revisited and
  found wrong: `org.ojalgo:ojalgo` resolves cleanly from Maven Central
  (pure JVM, zero runtime dependencies, Java 11 target, no JNI), and
  `MineralShoppingListOptimizer.kt` ports `optimizer.py`'s buy-vs-refine
  MIP onto ojAlgo's `ExpressionsBasedModel` case-for-case, reusing Ore
  Shortlist's candidate universe/yield formulas for the refining side.
  `MineralShoppingListOptimizerTest.kt` ports the desktop suite's own
  hand-computed cases (including its "greedy trap" case) plus random-plan
  and realistic-scale checks - all pass, and the realistic-scale case
  solves in well under a second. `MineralShoppingListScreen.kt` now wires
  up the optimizer too: an "Optimize" button next to the original
  "Price" button, kept side by side rather than replaced (Price stays a
  free, already-working direct-buy sanity check/fallback; Optimize's
  result subsumes it when the ore side can fully cover a plan). Still
  missing vs. desktop: no haul-cost term (the SDE cache has no mineral
  item-volume data cached in a form this screen reads yet) and no
  Goonmetrics home-market comparison.
- Production -> Ship Margins & Market Status (`data/production/
  ProductionBuildCost.kt`; extends `ProductionConfig.kt`; `ui/screens/
  ShipMarginScreen.kt`) - Production's first real *build*-cost view,
  unlocked by extending the SDE cache (Room version 3 -> 4) with
  Manufacturing-only blueprint data: `industryActivityMaterials.csv`/
  `industryActivityProducts.csv` (`sde_blueprint_materials`/
  `sde_blueprint_products`, activityID=1 only - Reactions/Invention/
  Copying are out of scope, nothing reads them). Scoped to a single-item
  build-cost/margin lookup (mirroring `engine.item_margin_detail`), not
  the desktop feature's whole-catalog scan. Simplified vs. desktop: one
  flat, manually-entered ME level (no owned-BPO research tracking, no
  structure/rig bonus), no live ESI system-cost-index pull (uses the same
  flat fallback rate the desktop build itself falls back to), and EIV
  approximated from real material buy prices rather than ESI's own
  adjusted-price catalog.
- Production -> Build Candidates (`data/production/
  ProductionBuildCandidates.kt`; `ui/screens/
  ProductionBuildCandidatesScreen.kt`) - a Kotlin port of `engine.
  discover_build_candidates`, reusing `ProductionBuildCost.kt`'s
  `unitBuildCost`/`marginHome` math across every manufacturable, published
  SDE type (`SdeDao.manufacturableTypes`, joining `sde_blueprint_products`
  against `sde_types` - a new query, no new table). Simplified vs.
  desktop: no Jita cross-shopping during the catalog scan (avoids
  hundreds of per-type ESI calls; home prices are one bulk download
  regardless of type count, and Jita has no equivalent bulk endpoint),
  ranked by margin alone rather than desktop's Goonmetrics
  daily-movement weighting, and no `min_daily_profit` filter.
- Production -> Current Jobs & Slots (new `EsiClient.
  characterIndustryJobs`; `data/production/ProductionJobs.kt`; `ui/
  screens/ProductionCurrentJobsScreen.kt`) - a live ESI read ported from
  `jobs.py`'s `list_current_jobs`/`character_slot_overview`, plus the
  existing skill-based job-slot formula. A new "Producer" login role
  requests `esi-industry.read_character_jobs.v1` alongside
  `esi-skills.read_skills.v1` - since this app's `characterSkills` method
  already existed (from Station Trading), this view can show a real
  total/free slot count, which the desktop build's own
  `character_slot_overview` explicitly cannot (see that function's own
  docstring). Invention Estimator was evaluated and confirmed out of
  scope: it needs `industryActivityProbabilities`/`industryActivitySkills`
  SDE tables this cache doesn't carry. Asset-Optimized Planner and
  Logistics remain `PlaceholderScreen` - each needs real additional
  infrastructure (character-asset cross-referencing, or ESI
  endpoints/business rules not yet modeled here).
- Production -> Owned Blueprints (new `EsiClient.characterBlueprints`;
  `data/production/OwnedBlueprints.kt`; `ui/screens/
  OwnedBlueprintsScreen.kt`) - a live ESI read ported from
  `engine.list_owned_blueprints`, grouping by `(type_id, is_original, ME,
  TE, runs)` case-for-case, including the `quantity`-is-usually-a-
  sentinel guard and `runs == -1` BPO/BPC classification. Needs a new
  `esi-characters.read_blueprints.v1` scope, added to the "Producer"
  role. Simplified vs. desktop: a live per-visit read across logged-in
  Producer characters, not a synced local cache spanning every character
  ever synced.
- Production -> Planner (`data/production/ProductionPlanner.kt`; `ui/
  screens/ProductionPlannerScreen.kt`) - reading desktop's actual Planner
  tab in full found it is a stock-target-driven, ESI-asset-aware
  buy/build optimizer (`production_planner.py`/`engine.plan_production`),
  not a recursive BOM-explosion feature - infrastructure this platform
  has none of. The single-item BOM explosion this menu slot needed
  instead is desktop's *Item Lookup -> Material Tree* view
  (`engine.build_material_tree`), so that's what was ported here:
  recursive explosion through manufacturable sub-components down to raw
  materials, reusing `ProductionBuildCost.kt`'s existing
  `ProductionBomSource`/`unitBuildCost` math, aggregating shared leaf
  demand across branches, depth-capped as a defensive cycle guard (not
  present in desktop's own algorithm, documented as a deliberate
  addition). No manual-stock/asset offsetting, no per-node buy-vs-build
  decision (every manufacturable node always expands), no T2/invention
  (no Invention SDE data at all here).
- Production -> Special Orders (`data/production/SpecialOrders.kt`; `ui/
  screens/ProductionSpecialOrdersScreen.kt`) - ports the order-list half
  in full (create/list/mark-done/reopen/remove a named order with line
  items) as a settings-blob repository, same shape as
  `DoctrineFittingRepository`. Does not port `engine.plan_special_order`'s
  real buy/build planner - it reuses the entire `plan_production` engine
  (job-run batching, live cost indices/EIV, T2 invention,
  `manual_build_buy` overrides, ESI-synced asset stock netted against
  `stock_targets` for `net_against_stock`'s overlap warning), none of
  which exists here (the same gap Planner just found). "Compute" instead
  runs a much smaller per-line-item Buy-vs-Build estimate on the existing
  single-item `unitBuildCost`, clearly not a narrowed port of the real
  planner.
- Production -> Logistics was investigated and confirmed genuinely
  blocked, not just left as a placeholder without checking: desktop's
  four Logistics report tabs (Logistics Status, Distribution
  Recommendations, Invention Logistics, T1 BPC Invention Needs) each
  call `engine.plan_production` first and feed its output into their own
  report function - the same missing stock-target/multi-character-asset
  buy/build engine already found blocking Planner and Special Orders.
  Its two "cheap local config" panels (category-location assignments,
  manual build/buy overrides) only exist as input to those
  `plan_production`-dependent reports, so porting them here with nothing
  on this platform to read them would be dead configuration. Its
  "Resolve Structure Name" helper would need ESI endpoints
  (`EsiClient.kt` has none for structure/corporation name resolution)
  that only exist to label that same unportable config UI. Remains
  `PlaceholderScreen`, correctly.
- Production -> Asset-Optimized Planner was investigated the same way and
  found blocked for the identical reason: `engine.plan_asset_optimized`
  is explicitly documented as reusing `plan_production`'s own buy-vs-
  build decision function, just netting real owned stock against demand
  at every level of the recursive bill of materials - so it needs the
  same `stock_targets`/`manual_stock`/`manual_build_buy` storage, the
  same recursive BOM/buy-vs-build engine, and the same live pricing/cost-
  index/asset-sync machinery already missing for Planner, Special
  Orders, and Logistics. No smaller portable subset exists - it isn't a
  standalone asset-vs-material-tree cross-reference, it's a variant of
  the same missing optimizer. Remains `PlaceholderScreen`, correctly.
- Every configured tool now has its own Settings tab (`ui/screens/
  SettingsScreen.kt` grew a `TabRow`: Trading, Station Trading,
  Production, Ore & Minerals - Doctrine has no config yet). Previously
  only Trading's fields were editable; Station Trading/Production/Ore &
  Minerals' config classes existed with no UI to change them outside a
  hand-edited settings blob.

Not started: Production's Invention Estimator (see correction note below -
being revisited, not actually blocked; Logistics and Asset-Optimized
Planner remain confirmed genuinely blocked on the same missing
`plan_production` stock-target engine as Planner/Special Orders - see
above for those three), tests for anything beyond the pure logic already
covered, a Play Store listing.

**Correction, same day:** the Invention Estimator "confirmed genuinely
blocked" conclusion above was wrong in its implication. `industryActivity
Probabilities.csv`/`industryActivitySkills.csv` are plain Fuzzwork CSVs
already fetched by this repo's own desktop Python code (`sde.py`) via the
exact same pipeline Android's `SdeDownloader.kt` already uses for every
other SDE table - not some unreachable data source, just two tables
nobody had gotten around to adding to the Android cache yet, same as
`invTypeMaterials`/`industryActivityMaterials`+`Products` before them.
Being ported for real now, same established pattern (new CSV entries,
new Room entities, a version bump, DAO/`SdeRepository` mirrors, then the
actual probability/decryptor math from `production/invention.py`).

At this point every ROADMAP item that was open going into this Android
push has either landed for real or been investigated and confirmed
genuinely blocked by a specific, documented missing piece of
infrastructure - never just left alone without checking. That includes
items that looked like they might be permanently out of reach going in:
multi-character pooling for Realized Trades and Unlisted Stock &
Undercut Check, and `average_daily_sold_by_type`, both turned out to be
straightforwardly portable once someone actually read the desktop source
instead of assuming from the ROADMAP's own earlier (and, in hindsight,
imprecise) description of what they needed. What remains above is
genuinely down to one confirmed-blocked Production view family and
non-feature work (broader test coverage, an icon, store packaging) - the
real feature backlog for this Android port is exhausted.

Options considered before deciding above, kept for the record:
- **BeeWare/Toga** — one Python codebase for desktop *and* Android, calling
  `eve_trader_local` directly with no porting; the only option with real
  code-sharing across both, but Toga is less mature/polished than PyQt.
- **Chaquopy** — native Android UI (Kotlin/Compose) with an embedded
  CPython (Chaquopy) calling `eve_trader_local` as a library; best native
  Android feel, but means maintaining two separate UI codebases (PyQt for
  desktop, Kotlin for Android) against the one shared Python core.
- **Web-frontend + on-device server** — reuse eve-trader's React frontend,
  run the FastAPI backend locally on the Android device itself (e.g. via
  Termux or an embedded Python server), wrap in a WebView (Capacitor).
  Reuses the most existing code but is the most fragile on Android
  (background-service/lifecycle restrictions make an always-on local server
  awkward on mobile).

Whichever was chosen inherits this repo's existing constraints unchanged:
no server this project operates, SQLite (via Room on Android) as the local
store, and the same offline-first/user-owns-their-data model already
established for desktop.
