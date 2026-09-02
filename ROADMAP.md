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
data/settings are never a sync or update target).

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

## Deferred (not started, tracked here so it isn't lost)

- Native GUI (PyQt/PySide) replacing the CLI as the primary interface —
  discussed and decided in favor of "real native," not Electron/Tauri.
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
- Actual Trading/Production/Doctrine/Ore&Minerals business logic — see
  `SYNC.md`'s candidate table for what ports over and from where.
- Packaging/installer once there's a GUI to package (feeds directly into
  Stage 2 of the update mechanism above).
