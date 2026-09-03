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
| UI | React web app | CLI today, native GUI planned |
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
needed vs. on hand), and Special Orders (ad-hoc one-off build orders priced
with the same buy-vs-build engine, optionally netted against synced stock).
Only the parent's multi-structure Logistik-tab helpers
(`logistics_status`/`distribution_recommendations`) are left out, as a
deliberate single-structure simplification rather than a gap — see `SYNC.md`.

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
  `t1-bpc-invention-needs`, `set-manual-stock`, `remove-manual-stock`,
  `list-manual-stock`, `create-special-order`, `list-special-orders`,
  `update-special-order`, `remove-special-order`, `compute-special-order`,
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
was deliberately left out, and why.

Explicitly **not** done yet:

- Native GUI (PyQt/PySide) — planned, not started. The CLI is a smoke test
  and a working end-to-end proof, not the intended interface — see
  `ROADMAP.md` for the planned menu/tab navigation model.
- Production's multi-structure Logistik-tab helpers
  (`logistics_status`/`distribution_recommendations`) — per-category
  structure assignments, a genuine multi-structure routing concept with no
  single-structure equivalent; judged out of scope rather than deferred, see
  `SYNC.md`.
- Packaging/installer.
- Schema migrations. Tables are created with `CREATE TABLE IF NOT EXISTS`;
  adding a column to an existing table later will need real migration handling.

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
eve-trader-local sde-status         # when was it refreshed; is a newer dump out?
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
eve-trader-local remove-stock-target <item>              # stop tracking a stock target
eve-trader-local list-stock-targets                      # show every configured stock target
eve-trader-local plan-production            # stock-aware buy/build plan against your stock targets
eve-trader-local create-special-order <item:qty> [<item:qty> ...] [--note] [--net-against-stock]
eve-trader-local list-special-orders                     # show every special order
eve-trader-local update-special-order <order_id> [--status open|done] [--note]
eve-trader-local remove-special-order <order_id>         # delete a special order
eve-trader-local compute-special-order <order_id>        # buy/build plan for one order's line items
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
nets the order off currently-synced ESI assets/incoming jobs instead of
planning it entirely from scratch (the default), and also flags any item
shared with your configured stock targets that already has stock on hand —
a heads-up that the same physical units might get claimed by both. Unlike
`plan-production`, an order is never dropped for falling below
`min_margin` — a special order must be fulfilled regardless of margin.

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
