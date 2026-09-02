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

## Status: foundation layer only

This is a first foundational commit. **There are no trading or production
features here yet.** What exists:

- `storage.py` — SQLite persistence: OAuth tokens, ESI sync timestamps, config
  overrides. No `tenant_id` column anywhere; there is a test asserting that.
- `config.py` — dataclass config, layered defaults → `config.yaml` → stored
  overrides, with type/range validation that runs *before* anything is applied.
- `auth.py` — EVE SSO OAuth2 (authorization code + PKCE), ported nearly
  unchanged from the parent repo, storing tokens locally.
- `sde.py` — the EVE Static Data Export importer: downloads CCP's SDE as CSV
  from [Fuzzwork](https://www.fuzzwork.co.uk/dump/latest/csv/) and caches it in
  SQLite. Ported from the parent's `production/sde.py`. This is the data layer
  only — nothing reads it yet.
- `cli.py` — `init-db`, `auth`, `whoami`, `config`, `refresh-sde`,
  `sde-status`: enough to prove the layers work together.

Explicitly **not** done yet:

- Native GUI (PyQt/PySide) — planned, not started. The CLI is a smoke test, not
  the intended interface.
- Any business logic — no shortlist, no candidate discovery, no build-vs-buy,
  no ESI market client. None of the parent's `engine.py` / `pricing.py` /
  `shortlist.py` has been ported.
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
```

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
