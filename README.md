# eve-trader-local

A **local-first, server-less** variant of [eve-trader](https://github.com/Pappmichel/eve-trader) —
EVE Online trading/production tooling that runs entirely on your own machine.

The parent project is a multi-user web app: FastAPI backend, React frontend,
Postgres with per-tenant Row-Level Security, an access-gate login, a background
scheduler thread. That is the right shape for something hosted for several
people. It is a lot of machinery for one person running a tool on their own PC.

This repo is the other shape — closer to [jEveAssets](https://github.com/GoldenGnu/jeveassets):
an embedded database file, one user, no server, no login wall, eventually a
native desktop GUI and a plain installer.

| | eve-trader | eve-trader-local |
|---|---|---|
| Storage | Postgres + RLS | embedded SQLite (WAL) |
| Users | multi-tenant | exactly one, no tenant concept |
| Process model | uvicorn + Vite dev server | none — commands run and exit |
| UI | React web app | CLI + native GUI (PySide6, in progress) |
| EVE SSO | shared hosted callback route | throwaway loopback server per login |

## Status: Trading pipeline works end to end (CLI); Production backend fully done (CLI); Doctrine works end to end (CLI); Ore & Minerals works end to end (CLI); Station Trading works end to end (CLI)

The **Trading** tool (buy in Jita, sell at your own structure) is fully
ported and wired up — discovery, backtesting, shortlist, order checks and
reconciliation all run for real from the CLI, not just as isolated modules.

The **Production** tool (Tech I/II/Reaction manufacturing planning) has its
whole backend ported and runnable from the CLI: SDE-driven classification,
buy-vs-build cost/margin math, invention math, producer ESI sync
(blueprints/assets/industry jobs, including real owned-BPO ME/TE),
build-candidate discovery, the Margin page's ship browser, a stock-aware
planner (plain CLI stock-target entry, netted against synced assets/incoming
jobs, plus a real manual-stock override and an invention-needs list), a
second readiness-focused asset-optimized planner (which jobs are startable
right now vs. blocked on an upstream shortfall), cheap market-status/
stock-value reads, invention-location logistics (datacores/decryptors/T1 BPCs
needed vs. on hand), the multi-structure Logistik-tab helpers (per-category
material logistics status with pull-from hints, plus distribution
recommendations for moving stock from a configured warehouse — or another
category's own surplus — to whichever category is short), Special Orders
(ad-hoc one-off build orders priced with the same buy-vs-build engine,
optionally netted against synced stock), a search-any-item Margin lookup and
full recursive material-tree browser, an asset-location search ("where is
this item currently sitting, across every synced character/corp", with
ESI-backed structure-name resolution), a standalone invention cost/decryptor
estimator, a live system-cost-index display, a manual Build/Buy override
(force an item to always build or always buy, wired into every planner), an
in-place stock-target editor, an owned-blueprint browser (every BPO/BPC,
aggregated by ME/TE/runs), and a current-industry-jobs list with a
per-character job-slot usage summary (usage counts from synced jobs, plus
optional manual totals/free via `set-character-job-slots` — still no ESI
character-skills pull; see `PHASE_E_EXPANSION.md` E.5). The Special-Order
core is semantically frozen (SF-1..SF-8 in `PRODUCTION_SEMANTICS.md`) and
certified as package `0.4.0rc1` (`PHASE_H_RELEASE.md`, `RELEASE_NOTES.md`,
`KNOWN_LIMITATIONS.md`).

The **Doctrine** tool (fitted-ship contract/stockpile tracking against EFT
fittings) is fully ported and runnable from the CLI too: paste-and-store EFT
fittings under a named doctrine, sync outstanding contracts + synced-asset
stock from ESI, match each contract against your fitting definitions, and get
a contract/stockpile deviation report with red/yellow/green ampel status,
a Shopping List (every stockpile shortfall priced Build vs. Buy-C-J vs.
Buy-Jita) and a permanent contract-history log of finished sales.

The **Ore & Minerals** tool (ore/ice import-refine-sell, reprocessing quotes,
mineral shopping list) is fully ported and runnable from the CLI too: an
SDE-derived Ore Shortlist you can add-to and live-refresh (Jita buy price vs.
C-J mineral sell value, after real reprocessing-yield math and the structure's
refining tax), a Reprocessing-tab-style quote for a pasted inventory list
(sell-as-is vs. reprocess, per line), and a Mineral Shopping List solved by a
real linear program (`scipy.optimize.linprog`) across every compressed ore/ice
type at once, picking whichever mix of buy-ore-and-refine vs. buy-mineral-
outright is cheapest for a saved (or ad-hoc) list of required minerals.

The **Station Trading** tool (market-making at Jita's own trade hub — buying
and selling *within the same station*, unlike the other four tools' Jita-
import-to-your-own-structure model) is fully ported and runnable from the CLI
too: a Goonmetrics-driven candidate scan across the whole Jita market
(bid-ask spread + real average daily traded volume, never order-book depth),
a persisted shortlist that live-confirms its prices against the real ESI
order book on every read, a bidirectional undercut/outbid check across every
registered trader character's own buy and sell orders, and a live trade-skill
summary (order-slot count derived from Trade/Retail/Wholesale/Tycoon).

What exists:

- `storage.py` — SQLite persistence for everything below: OAuth tokens, ESI
  sync timestamps, config overrides, the SDE cache, candidate universe,
  shortlist + snapshots, realized trades, new-candidate search state. No
  `tenant_id` column anywhere; there is a test asserting that.
- `config.py` — dataclass config, layered defaults → `config.yaml` → stored
  overrides, with type/range validation that runs *before* anything is applied.
- `auth.py` — EVE SSO OAuth2 (authorization code + PKCE), ported nearly
  unchanged from the parent repo, storing tokens locally.
- `sde.py` — the EVE Static Data Export importer: downloads CCP's SDE as CSV
  from [Fuzzwork](https://www.fuzzwork.co.uk/dump/latest/csv/) and caches it
  in SQLite.
- `esi_client.py` / `goonmetrics_client.py` — the full ESI HTTP layer (retry/
  backoff, error-budget handling, order-book stats) and Goonmetrics current
  prices + region price-history, ported from the parent.
- `candidate_discovery.py`, `history_backtest.py`, `shortlist.py`,
  `trade_reconciliation.py`, `own_orders.py` — the Trading business logic:
  finding importable items, backtesting them against price history, scoring
  a live shortlist (Profit/Day computed correctly — see `SYNC.md` for the
  two historical bugs this deliberately avoids reintroducing), catching
  undercuts/unlisted stock, and matching realized buy/sell pairs.
- `actions.py` — the orchestration layer wiring all of the above together
  (`do_pipeline`, `do_build_universe`, `do_find_new_candidates`,
  `do_refresh_and_prune_candidates`, `do_check_undercut`, `do_reconcile_trades`,
  …), each step isolated so one failure doesn't block the others.
- `portfolio.py` — the one cross-cutting view spanning Trading and
  Production: `portfolio_overview` combines Trading's realized P&L with
  Production's stock value into one read-only summary, plus a day-to-day
  profit volatility signal, degrading each half independently to zero/None
  when that tool has no data yet.
- `logging_setup.py` — a small rotating-file-handler log under the same data
  directory as the SQLite DB, so a `pipeline`/`sync-esi` run's best-effort
  swallowed errors leave a real trail after the fact (a much simpler cut of
  the parent's version — see `SYNC.md` for why the console-handler half
  doesn't apply here).
- `production/` — `constants.py`, `engine.py` (classification, buy-vs-build
  cost/margin, build-candidate discovery), `pricing.py`, `invention.py`,
  `esi_sync.py` (producer character blueprints/assets/industry jobs),
  `models.py`, `config.py` (`ProductionConfig`) and `actions.py`.
- `doctrine/` — the Doctrine tool (fitted-ship contract/stockpile tracking
  against EFT fittings): `constants.py` (slot sections, SDE category IDs,
  parse/deviation/ampel vocabularies), `models.py` (the parser-output and
  master-data dataclasses), `parser.py` (the EFT fitting-format text parser —
  pure, storage-free, its SDE lookups injected as callables by the caller),
  `validation.py` (pure contract-matching/stockpile-deviation scoring — Soll/
  Ist multisets, missing/short/extra/wrong-variant deviations, and ampel
  aggregation), `esi_sync.py` (one combined contract + asset sync for every
  registered Doctrine character), `engine.py` (the real-SDE resolver wiring,
  contract matching/validation, and Soll/Ist stockpile computation that ties
  parser.py/validation.py to real synced data), `config.py`
  (`DoctrineConfig`) and `actions.py`.
- `refining/` — the Ore & Minerals tool (ore/ice import-refine-sell,
  reprocessing quotes, mineral shopping list): `constants.py` (reprocessing-
  yield game constants), `models.py`, `reprocessing.py` (the real ore/ice +
  scrapmetal yield math), `paste_parser.py` (EVE inventory "Copy As" paste
  parsing), `candidate_discovery.py` (the fixed, SDE-derived compressed-ore/
  ice universe), `pricing.py` (the Ore Shortlist's per-row profit math),
  `quote.py` (the Reprocessing-tab's sell-as-is-vs-refine quote calculation),
  `optimizer.py` (the Mineral Shopping List's LP solver), `config.py`
  (`RefiningConfig`) and `actions.py`.
- `station_trading/` — the Station Trading tool (buy-low-sell-high within
  Jita's own trade hub station): `constants.py` (trade-skill type_ids and the
  order-slot-count formula), `candidate_discovery.py` (the Goonmetrics-spread/
  real-volume market scan, plus the bounded live-ESI shortlist confirmation),
  `undercut.py` (bidirectional own-order undercut/outbid checking, pooled
  across every registered trader character), `esi_sync.py` (trader character
  registration only — nothing here is worth caching ahead of time),
  `config.py` (`StationTradingConfig`) and `actions.py`.
- `cli.py` — every layer above has a command: `init-db`, `auth`, `whoami`,
  `config`, `refresh-sde`, `sde-status`, `check-update`, `update`,
  `build-universe`, `find-candidates`, `add-to-shortlist`,
  `refresh-shortlist`, `check-unlisted-stock`, `check-undercut`,
  `reconcile-trades`, `sync-esi`, `discover-build-candidates`,
  `discover-ship-margins`, `set-stock-target`, `remove-stock-target`,
  `list-stock-targets`, `plan-production`, `plan-asset-optimized`,
  `market-status`, `stock-value`, `invention-logistics`,
  `t1-bpc-invention-needs`, `logistics-status`, `distribution-recommendations`,
  `set-category-location`, `clear-category-location`,
  `add-category-location-option`, `remove-category-location-option`,
  `list-category-locations`, `set-manual-stock`, `remove-manual-stock`,
  `list-manual-stock`, `create-special-order`, `list-special-orders`,
  `update-special-order`, `remove-special-order`, `set-special-order-item`,
  `remove-special-order-item`, `compute-special-order`, `compute-combined-special-orders`,
  `pipeline`,
  `parse-fitting`, `create-doctrine`, `list-doctrines`, `add-fitting`,
  `list-fittings`, `sync-doctrine`, `validate-contracts`, `doctrine-status`,
  `stockpile-status`, `shopping-list`, `list-contracts`, `contract-history`,
  `add-ore-to-shortlist`,
  `refresh-ore-shortlist`, `list-ore-shortlist`, `quote-reprocessing`,
  `set-mineral-requirement`, `remove-mineral-requirement`,
  `list-mineral-requirements`, `solve-shopping-list`,
  `refresh-station-shortlist`, `list-station-shortlist`,
  `check-station-undercut`, `station-trading-skills`,
  `portfolio-overview`.

See `SYNC.md` for exactly what was ported from each parent-repo module, what
was deliberately left out, and why. Frozen Production/Special-Order rules
are in `PRODUCTION_SEMANTICS.md` (`pytest -m release`). Isolated Phase E
expansions (persistence, UX, auto-recompute wrappers, job-slot totals,
cost-index override *actions*) are specified in `PHASE_E_EXPANSION.md`.

## Native GUI (started 2026-09-04)

`eve_trader_local/gui/` — a PySide6 desktop app, `eve-trader-local-gui`
(`pip install -e ".[gui]"` to pull in PySide6, a deliberate optional extra
so the CLI/backend keep their otherwise dependency-light footprint). Follows
the jEveAssets-style navigation model from `ROADMAP.md`: one top-level menu
per tool, each listing its views as menu items; picking one opens it as a
closable tab in a shared workspace, and opening an already-open view
re-focuses its tab instead of duplicating it. Every view calls the exact
same `do_*` actions the CLI calls — never storage or the network clients
directly — so the CLI and GUI can never drift apart, matching the parent
repo's own CLI/API-both-call-actions.py rule.

Foundation: `main.py` (entry point), `main_window.py` (the
menu-bar/tab-workspace shell), `workers.py` (runs a `do_*` call on a
background `QThread` so a multi-second ESI/Goonmetrics round trip never
freezes the window — see its module docstring for the `_ResultBridge`
mechanism that makes the worker-thread-to-UI-thread handoff actually safe,
a real PySide gotcha it works around), and `views/base.py` (`BaseView`/
`TableView`, the shared "busy state + error display + table population"
shape most views need).

All five tools now have real views built out on that foundation, grouping
several of today's CLI commands into each tab per the navigation model
below (never a 1:1 CLI-command mirror):

- **Trading** — Shortlist (`views/trading_shortlist.py`, the live import/sell
  decision table); Candidate Discovery (`views/trading_candidate_discovery.py`,
  build-universe/build-focused plus find-new-candidates and Add Recommended
  To Shortlist — the "find new things to import" setup workflow that feeds
  the shortlist); Realized Trades & Transactions
  (`views/trading_realized_transactions.py`, matched buy/sell P&L plus raw
  per-character wallet transaction history — both "what actually happened"
  reads); Unlisted Stock & Undercut Check
  (`views/trading_unlisted_undercut.py`, two live one-shot seller-side
  checks); Price History (`views/trading_price_history.py`, margin-momentum
  trends over cached price history — `do_shortlist_trends` existed in
  `actions.py` with no caller until this view).
- **Production** — Build Candidates, Planner (now also carrying in-place
  stock-target editing, `update-stock-target` — GitHub issue #16, every
  field independently optional, unlike the existing Set Target/upsert
  control — and the manual-stock ledger, `set/remove/list-manual-stock` — a
  direct override of the on-hand quantities the planner's own Inventory
  table reads), Asset-
  Optimized Planner, Logistics (now also carrying manual Build/Buy override,
  structure-name resolution, and the single-location invention-logistics
  slice — `invention-logistics`/`t1-bpc-invention-needs`, ported earlier but
  previously unwired in any GUI), Special Orders, Ship Margins & Market
  Status (now also showing live system cost indices), Item Lookup
  (`views/production_item_lookup.py`, margin/material-tree/asset-location
  search sharing one item-name field), Invention Estimator
  (`views/production_invention_estimator.py`, standalone recipe/decryptor
  comparison), Owned Blueprints, and Current Jobs & Slots (active industry
  jobs plus per-character slot usage). This closes every gap the
  parent-vs-local GUI audit found — Production's GUI now covers the tool's
  full `do_*` backend surface, see `SYNC.md`'s Production section.
- **Doctrine** — Fittings, Stockpile Status, Shopping List, Contract History.
- **Ore & Minerals** — Ore Shortlist, Reprocessing Quote, Mineral Shopping List.
- **Station Trading** — Shortlist (`views/station_trading_shortlist.py`,
  discover/refresh + the live-ESI-confirmed persisted shortlist) and
  Undercut & Skills (`views/station_trading_undercut_skills.py`, the
  bidirectional own-orders undercut/outbid check plus the live trade-skill/
  order-slot summary — two small independent read-only reports grouped
  into one tab, same reasoning as Production's own Ship Margins & Market
  Status grouping).

Two app-level dialogs (`gui/dialogs/`), reachable from a new top-level "App"
menu in `main_window.py` (not one of the per-tool menus, since neither
belongs to just one tool):

- **Settings** (`dialogs/settings_dialog.py`) — one tab per tool's config
  dataclass, built generically off each dataclass's own fields (adding a
  field to `TradingConfig`/`ProductionConfig`/etc. shows up here
  automatically). Numbers get a ranged `QSpinBox`/`QDoubleSpinBox` (reusing
  `config._FIELD_RANGES`, the same bounds the backend itself enforces),
  bools a checkbox, the enum-style Production/Ore & Minerals structure/rig/
  implant fields a `QComboBox` over their real valid options, and
  `Optional[...]` fields a text box (blank = `None`). The tuple-typed
  `excluded_path_prefixes` field gets a multi-line text box (one entry per
  line); the dict-typed `ore_family_skill_levels` gets a small add/remove-row
  table (free-text family name, integer skill level — there's no fixed
  enum to validate the key against, matching the underlying field's own
  "unrecognized family defaults to skill level 5" tolerance).
- **Characters** (`dialogs/characters_dialog.py`) — lists every currently
  authorized character (`auth.TokenManager.list_records`, same data
  `eve-trader-local whoami` prints), starts a new SSO login for any of the
  five roles (Buyer/Seller/Producer/Doctrine/Trader — blocking, opens the
  system browser, so it always runs on a worker thread), and removes a
  registered character. `eve-trader-local auth`/`whoami` on the CLI still
  work exactly as before — this is an additional way in, not a replacement.

A new top-level **Portfolio** menu (`views/portfolio_overview.py`) — a tab,
not an "App"-menu dialog, since it's a live-refreshable cross-tool dashboard
(Trading realized profit, daily profit volatility, Production stock value,
combined value) rather than a one-shot settings form, matching every other
tool's own top-level-menu treatment. Wraps `portfolio.portfolio_overview()`
(previously CLI-only, `cmd_portfolio-overview`) with the same field
formatting the CLI command itself uses.

Both dialogs are the first real callers of any tool's `do_update_settings` -
there was no CLI command wiring those up before (`cmd_config` only ever
displayed the resolved config, read-only). Both mix in `gui/workers.py`'s
`BusyMixin` (the same busy-state/status-label/`run_action` machinery
`views/base.py`'s `BaseView` itself is now built on top of) rather than
subclassing `BaseView` directly, since a `QDialog` can't also inherit a
`QWidget`-based common base in PySide6.

General polish (2026-09-03): every table-based view now has per-column
default widths (long-text columns like Item/Category/Decision get more room
than a numeric ISK/percentage/quantity one) and a sensible default sort
applied right after populating — matching whatever order the underlying
`do_*`/storage call already documents as its own designed order (e.g. Build
Candidates by potential daily profit desc, the Planner's Buy/Build/Invention
lists by their own engine-documented sort keys) where one genuinely exists,
or a reasonable decision-relevant default (Margin desc) for the handful of
tables with no inherent backend order at all (Trading/Ore Shortlist,
Station Trading Shortlist) — see each view module's own comment for its
specific reasoning. `views/base.py`'s `_SortableTableWidgetItem` makes this
(and any later manual header-click sort) compare formatted numeric text
("1,234", "12.5%") by actual value rather than lexicographically. Window
geometry and which tabs were open (plus which was active) now persist
across restarts via `QSettings` (`main_window.py`'s `closeEvent`/
`restore_state`) — a saved tab whose view class no longer exists is skipped
with a logged warning, not a crash.

## Schema migrations (added 2026-09-04)

`storage.py`'s `SCHEMA` (`CREATE TABLE IF NOT EXISTS` everywhere) only ever
covers a brand-new table - it does nothing once a table already exists, so
adding a *column* to an existing table needed a real mechanism, not just
another `CREATE TABLE IF NOT EXISTS` line. This became a real (not
theoretical) problem once the self-update mechanism (see ROADMAP.md) exists:
`git reset --hard origin/main` pulls new code onto an old, already-populated
local database file, and a naive schema change would crash the next time
that older table's new column got touched.

`storage.MIGRATIONS` is an ordered list of `(version, description,
apply(conn))` entries, replayed against whatever `schema_version` a given
database file is currently at, every time `init_db()` runs (on every
startup, same as before). Two rules, always applied together when adding an
entry (documented at length in `storage.py`'s own comment above
`MIGRATIONS`): update `SCHEMA` itself too (a fresh install should never
"migrate" through history it never lived), and write the migration function
to check before it alters (`_column_exists`, or the equivalent) - because of
the first rule, the *same* migration runs once for real against an old
database and once as a guaranteed-safe no-op against every database created
after the change shipped. `schema_version` starts at 0 for any database that
doesn't have a row yet (fresh or pre-existing - there's deliberately no
"was this really fresh?" heuristic; idempotent migrations make that
question moot) and is never rewound.

No real migration has been needed yet - `MIGRATIONS` is currently empty,
with a commented-out example in `storage.py` showing the exact shape to
copy the first time one actually is.

## Packaging (added 2026-09-04)

`packaging/eve-trader-local.spec` builds the native GUI into a single
portable Windows `.exe` via [PyInstaller](https://pyinstaller.org/) (`pip
install -e ".[gui,build]"` for the new `build` extra); see
`packaging/README.md` for local build instructions and two real bugs that
setup already caught (an editable install's package silently not getting
bundled, and a windowed build's crash-report visibility). No installer yet -
a portable, no-install-step `.exe` is deliberately the first milestone; an
actual installer (Inno Setup, Start Menu entry + uninstaller) is future work.

`.github/workflows/build-windows.yml` builds and smoke-tests this on a real
Windows GitHub Actions runner on every `vX.Y.Z` tag push, uploads the result
as a build artifact, attaches it (plus a `.sha256` checksum file) to a
GitHub Release, and stamps that tag into `eve_trader_local/_version.py` so
the frozen build knows its own version.

ROADMAP.md's Stage-2 self-updater is built (2026-09-04): the App menu's
"Check for Updates..." (`gui/dialogs/update_dialog.py`) polls
`/releases/latest`, verifies the downloaded `.exe` against that release's
checksum, then hands off to a detached helper that replaces the running
binary and relaunches it - see `updater.py`'s
`download_and_apply_binary_update` for the full mechanics. The packaged
`.exe` is also portable now: `paths.py`'s `data_dir()` defaults to a folder
next to the `.exe` itself (not `%USERPROFILE%`) whenever the app is running
frozen, so the whole install - database, config, logs, window state,
update cache - travels together as one folder.

The GUI also now has a dark, EVE Online-inspired theme (`gui/theme.py`,
applied once in `gui/main.py`) instead of default Qt styling.

Explicitly **not** done yet: a real installer (Inno Setup or similar, Start
Menu entry + uninstaller) - the portable `.exe` plus in-app updater is
still the whole distribution story.

## Android (native, in progress - started 2026-09-04)

`android/` is a from-scratch Kotlin + Jetpack Compose app - not a Python
port via Chaquopy, see ROADMAP.md's "Android" section for that decision and
its trade-off. What exists so far: the same dark theme as the desktop GUI,
a nav drawer mirroring `main_window.py`'s tool/view menu structure, a local
Room database mirroring the desktop build's `tokens`/`settings` tables, a
real EVE SSO OAuth2 PKCE login flow, and one real (non-placeholder) tool
screen - Trading's Candidate Discovery, a Kotlin port of
`candidate_discovery.py`'s live-ESI-walk path. Everything else (the rest of
Trading, and all four other tools' business logic) is still a
`PlaceholderScreen`. See `android/README.md` for setup and this increment's
honest limitations (notably: written and reviewed by hand, never compiled -
no Android SDK/Gradle/JDK 17 was available in the environment it was built
in).

## Why the OAuth flow needed no rearchitecting

EVE SSO redirects a browser back to a URL you register. The parent repo serves
that URL from its always-running FastAPI backend — but its CLI already did the
other thing: bind a `http.server` on loopback, wait for the one redirect, shut
it down. That is a few seconds of listening, not a server. So it ports over
essentially as-is, which is why authentication was the right first thing to
build here.

## Setup

```bash
pip install -e ".[dev]"
cp .env.example .env      # then fill in EVE_SSO_CLIENT_ID
```

Register an application at <https://developers.eveonline.com> as an
*Authorization Code* client with PKCE (a public client — no secret is used) and
set its callback URL to match `.env`, by default `http://localhost:8000/callback`.

## Usage

```bash
eve-trader-local init-db            # create the SQLite file (idempotent)
eve-trader-local auth --role buyer  # opens a browser, stores the token
eve-trader-local whoami             # list authorized characters
eve-trader-local config             # show the resolved configuration
eve-trader-local refresh-sde        # download the EVE Static Data Export
eve-trader-local sde-status         # when was it refreshed; is a newer dump out; is Trading's candidate universe stale?
eve-trader-local check-update       # is there a newer commit on origin/main?
eve-trader-local update             # fetch + reset --hard + reinstall deps

eve-trader-local build-universe             # crawl ESI's market-group tree via SDE (occasional setup step)
eve-trader-local find-candidates --safe     # backtest candidates against Goonmetrics price history
eve-trader-local add-to-shortlist           # add the latest search's finds to the tracked shortlist
eve-trader-local refresh-shortlist          # recompute live Profit/Day for every shortlist item
eve-trader-local check-unlisted-stock       # stock at the structure with no sell order on it
eve-trader-local check-undercut             # your own listings a competitor has beaten on price
eve-trader-local reconcile-trades           # match realized buy/sell pairs, compute P&L
eve-trader-local auth --role producer       # authorize an industry character (assets/blueprints/jobs)
eve-trader-local sync-esi                   # refresh what your producers own: assets, BPOs, jobs
eve-trader-local discover-build-candidates  # scan the SDE for build-vs-buy opportunities
eve-trader-local set-stock-target <item> <qty> [--jita]  # keep <qty> units of an item in stock
eve-trader-local update-stock-target <item> [--quantity] [--jita|--home]  # edit an existing target in place
eve-trader-local remove-stock-target <item>              # stop tracking a stock target
eve-trader-local list-stock-targets                      # show every configured stock target
eve-trader-local plan-production            # stock-aware buy/build plan against your stock targets
eve-trader-local item-margin <item>                      # price/build cost/margin for any one item
eve-trader-local material-tree <item> [--quantity]       # full recursive bill-of-materials tree
eve-trader-local item-locations <item>                   # where a synced character/corp currently holds an item
eve-trader-local resolve-structure-name <location_id> [--force]  # resolve a location_id via ESI (cached)
eve-trader-local system-cost-indices        # live ESI manufacturing/reaction cost indices
eve-trader-local estimate-invention <product> [--decryptor]  # invention cost/probability by decryptor
eve-trader-local set-manual-build-buy <item> <Build|Buy>  # force an item to always Build or always Buy
eve-trader-local clear-manual-build-buy <item>            # remove a manual Build/Buy override
eve-trader-local list-manual-build-buy                    # list every manual Build/Buy override
eve-trader-local list-current-jobs          # every active/paused/ready character + corp industry job
eve-trader-local character-slots            # per-character, per-category running-job counts
eve-trader-local list-owned-blueprints       # every owned BPO/BPC, aggregated by ME/TE/runs
eve-trader-local create-special-order <item:qty> [<item:qty> ...] [--note] [--net-against-stock]
eve-trader-local list-special-orders                     # show every special order
eve-trader-local update-special-order <order_id> [--status open|done] [--note]
eve-trader-local remove-special-order <order_id>         # delete a special order
eve-trader-local set-special-order-item <order_id> <item> <quantity>  # upsert one line item (no plan)
eve-trader-local remove-special-order-item <order_id> <item>          # delete one line item (no plan)
eve-trader-local compute-special-order <order_id>        # buy/build plan for one order's line items
eve-trader-local compute-combined-special-orders <order_id> [<order_id> ...] [--net-against-stock]
eve-trader-local pipeline                   # the daily workflow: refresh+prune, then reconcile
eve-trader-local pipeline --rebuild-universe  # also re-crawl the market-group tree first

eve-trader-local parse-fitting <path>       # parse an EFT-format fitting file against the local SDE cache

eve-trader-local auth --role doctrine       # authorize a character for Doctrine contracts + assets
eve-trader-local create-doctrine <name> [--description]   # create a named doctrine
eve-trader-local list-doctrines                           # list every doctrine
eve-trader-local add-fitting <doctrine_id> <path> [--name] [--contract-target] [--stockpile-target]
eve-trader-local list-fittings [doctrine_id]               # list fittings, optionally for one doctrine
eve-trader-local sync-doctrine               # sync contracts + assets from ESI, match/validate them
eve-trader-local validate-contracts          # re-match/re-validate synced contracts, no ESI
eve-trader-local doctrine-status [doctrine_id]    # contract + stockpile ampel status
eve-trader-local stockpile-status [doctrine_id]   # aggregated stockpile shortfalls
eve-trader-local shopping-list [doctrine_id]      # shortfalls priced Build vs. Buy-C-J vs. Buy-Jita
eve-trader-local list-contracts [--status]        # list every synced Doctrine contract
eve-trader-local contract-history [doctrine_id]   # permanent log of finished contract sales - who bought what and when

eve-trader-local add-ore-to-shortlist        # add every compressed ore/ice type from the SDE
eve-trader-local refresh-ore-shortlist       # re-price every Ore Shortlist item against live market data
eve-trader-local list-ore-shortlist          # show the last Ore Shortlist evaluation run
eve-trader-local quote-reprocessing <path>   # quote a pasted inventory list: sell as-is vs. reprocess
eve-trader-local set-mineral-requirement <item> <qty>   # set/update a shopping-list requirement
eve-trader-local remove-mineral-requirement <item>      # remove a requirement
eve-trader-local list-mineral-requirements              # list configured requirements
eve-trader-local solve-shopping-list         # solve the cheapest buy-ore-and-refine-vs-buy-direct mix

eve-trader-local portfolio-overview          # combined Trading realized P&L + Production stock value
```

`<item>` above accepts either a numeric type_id or an exact (case-insensitive)
item name, e.g. `set-stock-target Tritanium 50000` or `set-stock-target 34
50000`. `--jita` marks a target as sold at Jita rather than at home - it
feeds the buy-vs-build margin gate (`margin_jita` vs `margin_home`), it
doesn't change where `plan-production` recommends sourcing materials from.
`plan-production` needs `sync-esi` to have run at least once for accurate
current-stock numbers (otherwise every target reads as fully unstocked) and
live ESI/Goonmetrics access for pricing, same as `discover-build-candidates`.

Special Orders are a one-off build order for an ad-hoc item list, tracked
separately from the permanent stock-target list above and priced with the
exact same buy-vs-build engine `plan-production` uses (`create-special-order`
takes one or more `NAME_OR_TYPE_ID:QUANTITY` pairs). `--net-against-stock`
on create is stored for that order's own `compute-special-order`. Combined
preview (`compute-combined-special-orders`) takes a fresh `--net-against-stock`
for that run only — it does not merge or rewrite stored order flags, and it
does not save the combined plan. In both modes, the ordered top-level
quantity is never reduced by hangar stock (B1). Unlike `plan-production`,
an order is never dropped for falling below `min_margin`. Frozen rules,
scope boundaries, and the missing-price policy are in
`PRODUCTION_SEMANTICS.md`.

`parse-fitting` needs `refresh-sde` to have run at least once (it resolves
every item/hull name and slot against the local SDE cache) and takes a plain
text file containing one EFT-format fitting export (the format EVE's
in-game "Export" ship-fitting action produces) — it's a standalone preview,
same parsing `add-fitting` uses, that never persists anything.

The Doctrine workflow: `create-doctrine`, then `add-fitting <doctrine_id>
<path>` for each fit you want tracked (`--contract-target`/`--stockpile-target`
set how many outstanding contracts/spare sets you want to maintain), then
`auth --role doctrine` once per character who should be scanned for
contracts + assets. `sync-doctrine` pulls outstanding item-exchange
contracts at your configured structure plus synced asset stock, matches each
contract against your fittings, and writes a deviation report; run
`doctrine-status`/`stockpile-status`/`list-contracts` afterward to see it.
`validate-contracts` re-runs just the matching/deviation logic against
whatever was synced last, without touching ESI — useful right after editing
a fitting's targets or tolerance. `doctrine_structure_id` (a plain top-level
key in `config.yaml`, same as every other config field - see `config`'s own
output for the full field list) falls back to Trading's own `structure_id`
if you already have one configured and leave Doctrine's own unset.

`update` is manual and confirmed interactively, and refuses to run unless the
checkout is exactly a clean install (on `main`, no uncommitted or untracked
changes, an `origin` pointing at this repository) — it ends in
`git reset --hard`, which would otherwise destroy local work. Your database and
`config.yaml` are never touched: they live outside the checkout by default
(see below), and if you have pointed `EVE_TRADER_LOCAL_DATA_DIR` inside it, the
update refuses unless git actually ignores them.

`refresh-sde` is a completely separate update cycle from `update` — CCP patches
the Static Data Export a few times a year, on a schedule unrelated to this
app's own code. It downloads ~19MB directly from Fuzzwork (a public third
party; nothing this project hosts) and replaces the cached tables wholesale in
one transaction, so a failed download leaves the previous cache intact.
`sde-status` answers "is there a newer dump?" with a single HEAD request
instead.

Authorizing twice under the same role registers a *second* character rather
than overwriting the first — tokens are keyed `<role>:<character_id>`.

## Where your data lives

`~/.eve-trader-local/` by default, holding `eve-trader-local.sqlite3` and an
optional hand-written `config.yaml`. Override with `EVE_TRADER_LOCAL_DATA_DIR`.
Nothing is written inside the checkout, so the app keeps working when installed
read-only.

## Tests

```bash
pytest
```

Covers storage round-tripping and config validation. The interactive login is
not tested — it can't run headlessly, and mocking it end to end would only test
the mock; the pieces around it (PKCE generation, token records, expiry, storage)
are.
