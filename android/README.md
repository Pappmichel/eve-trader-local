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
  except Trading/Candidate Discovery and Trading/Shortlist (see below) -
  production planning, doctrine stockpiles, refining, station trading, and
  the rest of Trading itself are real, substantial work still ahead (see
  ROADMAP.md's Android section for the current per-tool state).
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
  toggle-active, persisted the same JSON-blob-per-scope way as
  `TradingConfig`) plus a "Refresh" action running `evaluateShortlist` - a
  straight Kotlin port of `shortlist.py`'s margin/decision formula -
  against live Jita region order stats and (when a Seller character is
  logged in and a structure id is configured) live structure order-book
  stats, both new `EsiClient` methods mirroring `esi_client.py`'s
  `region_order_stats(_bulk)` and `structure_order_stats_bulk`. Not ported
  yet: Profit / Day (needs Goonmetrics region history for real average
  daily volume), the Goonmetrics current-price fallback when no seller
  token is available, own-orders/buyer-covered tracking (so "Already
  ordered" is unreachable here - every Import candidate shows as
  "Import"), and auto-add/prune from Candidate Discovery
  (`refresh-and-prune` on desktop) - this screen is refresh-only,
  membership is manual.
- **Settings** (`ui/screens/SettingsScreen.kt`), reachable from the drawer
  next to Characters (not per-tool, since Trading is the only tool with a
  config on this platform yet): a hand-written form over `TradingConfig`'s
  fields. The desktop build's own `SettingsDialog` builds its form
  generically off each config dataclass via Python reflection - Kotlin has
  no equivalent, so this is a plain fixed form instead, over the same field
  set `TradingConfig.kt` already scopes itself to.
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
  `CharactersScreen`'s role list so far.

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
- No encryption at rest for stored tokens (Room writes a plain SQLite
  file, matching the desktop build's own plain SQLite `tokens` table) -
  fine for a single-user local app in the same sense it already is on
  desktop, but worth revisiting before this ever ships to a store.
- CI (`.github/workflows/build-android.yml`) only assembles the debug APK
  as a compile gate - no lint/test step (there are no Android-side tests
  yet), no release signing, no Play Store upload.
