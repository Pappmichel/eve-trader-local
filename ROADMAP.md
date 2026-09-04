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
  roles are wired into the UI so far.
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
  Membership (which items are tracked) is manual add/remove/toggle-active
  here, not auto-populated/pruned from Candidate Discovery the way
  desktop's `refresh-and-prune` does it — and Profit / Day, the Goonmetrics
  current-price fallback, and own-orders/buyer-covered tracking (so
  "Already ordered" is currently unreachable) aren't ported either. See
  `android/README.md`'s own Shortlist entry for the full list.
- A Settings screen (`ui/screens/SettingsScreen.kt`), reachable from the
  drawer next to Characters - a hand-written form over `TradingConfig`'s
  fields, since Kotlin has no equivalent of the desktop `SettingsDialog`'s
  reflection-driven generic form and Trading is the only tool with a config
  on this platform yet.
- CI (`.github/workflows/build-android.yml`), the Android counterpart of
  `build-windows.yml` - assembles the debug APK on every push/PR touching
  `android/` as a compile gate. No wrapper jar is committed (see
  `android/README.md`), so it installs Gradle itself and invokes it
  directly rather than `./gradlew`. No lint/test step (no Android-side
  tests exist yet), no release signing, no Play Store upload.

Not started: the rest of Trading (realized-trade reconciliation, price
history, unlisted-stock/undercut checks, auto-add/prune from Candidate
Discovery, own-orders/buyer-covered tracking, Profit / Day, the Goonmetrics
fallback), all four other tools' business logic and their own Settings tabs,
the SDE cache itself, Android-side tests, an app icon, encryption at rest
for stored tokens, a Play Store listing.

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
