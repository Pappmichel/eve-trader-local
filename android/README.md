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
- **Settings** (`ui/screens/SettingsScreen.kt`), reachable from the drawer
  next to Characters (not per-tool, since Trading is the only tool with a
  config on this platform yet): a hand-written form over `TradingConfig`'s
  fields. The desktop build's own `SettingsDialog` builds its form
  generically off each config dataclass via Python reflection - Kotlin has
  no equivalent, so this is a plain fixed form instead, over the same field
  set `TradingConfig.kt` already scopes itself to. Save validates every
  numeric field before writing anything - an earlier version silently
  discarded an unparseable value (a typo) per field and saved (then
  displayed back) `TradingConfig()`'s default for it instead, with no
  indication anything had gone wrong.
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
  `_market_group_path` cases plus the string/volume fallback half of
  `guess_category` - the real-SDE-category-name half isn't ported, same
  reason `CandidateDiscovery.kt` itself doesn't have it),
  `EsiClientTest.kt` (a port of `tests/test_esi_client.py`'s
  `_percentile`/`_summarize_orders` cases - the order-book pricing math
  behind every `OrderStats`), and `TokenRecordTest.kt` (a port of
  `tests/test_auth.py`'s `test_is_expired_respects_skew` -
  `TokenRecord.isExpired` is the one pure, Android-independent piece of
  `data/auth/`). No lint step, no instrumented/UI tests (would need an
  emulator), no release signing, no Play Store upload.
