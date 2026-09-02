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
- `cli.py` — `init-db`, `auth`, `whoami`, `config`: enough to prove the three
  layers work together.

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
```

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
