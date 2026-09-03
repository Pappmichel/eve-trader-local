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

## Status: Trading pipeline works end to end (CLI); Production well underway; Doctrine just started (parser only); Ore&Minerals not started

The **Trading** tool (buy in Jita, sell at your own structure) is fully
ported and wired up — discovery, backtesting, shortlist, order checks and
reconciliation all run for real from the CLI, not just as isolated modules.

The **Production** tool (Tech I/II/Reaction manufacturing planning) has its
SDE-driven classification, buy-vs-build cost/margin math, invention math,
producer ESI sync (blueprints/assets/industry jobs, including real owned-BPO
ME/TE), build-candidate discovery, and a stock-aware planner (plain CLI
stock-target entry, netted against synced assets/incoming jobs) all ported
and runnable from the CLI. Still missing: the web-only logistics/
distribution views and the readiness-focused asset-optimized planner
variant.

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
- `production/` — `constants.py`, `engine.py` (classification, buy-vs-build
  cost/margin, build-candidate discovery), `pricing.py`, `invention.py`,
  `esi_sync.py` (producer character blueprints/assets/industry jobs),
  `models.py`, `config.py` (`ProductionConfig`) and `actions.py`.
- `doctrine/` — the start of the Doctrine tool (fitted-ship contract/
  stockpile tracking against EFT fittings): `constants.py` (slot sections,
  SDE category IDs, parse/deviation/ampel vocabularies), `models.py` (the
  parser-output and master-data dataclasses), and `parser.py` (the EFT
  fitting-format text parser — pure, storage-free, its SDE lookups injected
  as callables by the caller). Matching/validation, contract sync and the
  orchestration layer are not ported yet.
- `cli.py` — every layer above has a command: `init-db`, `auth`, `whoami`,
  `config`, `refresh-sde`, `sde-status`, `check-update`, `update`,
  `build-universe`, `find-candidates`, `add-to-shortlist`,
  `refresh-shortlist`, `check-unlisted-stock`, `check-undercut`,
  `reconcile-trades`, `sync-esi`, `discover-build-candidates`, `pipeline`,
  `parse-fitting`.

See `SYNC.md` for exactly what was ported from each parent-repo module, what
was deliberately left out, and why.

Explicitly **not** done yet:

- Native GUI (PyQt/PySide) — planned, not started. The CLI is a smoke test
  and a working end-to-end proof, not the intended interface — see
  `ROADMAP.md` for the planned menu/tab navigation model.
- Production's logistics/distribution views and the readiness-focused
  asset-optimized planner variant (per-category structure assignments —
  web-UI-shaped concepts with no local equivalent yet).
- The rest of Doctrine (matching a synced contract against a fitting,
  stockpile/deviation scoring, ESI contract sync, the shopping list) and the
  whole Ore & Minerals tool (the parent's `refining/*`) — none of their
  business logic has been ported.
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
eve-trader-local pipeline                   # the daily workflow: refresh+prune, then reconcile
eve-trader-local pipeline --rebuild-universe  # also re-crawl the market-group tree first

eve-trader-local parse-fitting <path>       # parse an EFT-format fitting file against the local SDE cache
```

`<item>` above accepts either a numeric type_id or an exact (case-insensitive)
item name, e.g. `set-stock-target Tritanium 50000` or `set-stock-target 34
50000`. `--jita` marks a target as sold at Jita rather than at home - it
feeds the buy-vs-build margin gate (`margin_jita` vs `margin_home`), it
doesn't change where `plan-production` recommends sourcing materials from.
`plan-production` needs `sync-esi` to have run at least once for accurate
current-stock numbers (otherwise every target reads as fully unstocked) and
live ESI/Goonmetrics access for pricing, same as `discover-build-candidates`.

`parse-fitting` needs `refresh-sde` to have run at least once (it resolves
every item/hull name and slot against the local SDE cache) and takes a plain
text file containing one EFT-format fitting export (the format EVE's
in-game "Export" ship-fitting action produces) — it's a smoke test proving
the parser resolves real data correctly, not the Doctrine tool itself (still
unported: matching against synced contracts, stockpile scoring, ESI sync).

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
