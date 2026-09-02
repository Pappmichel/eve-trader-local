# SYNC.md — keeping eve-trader-local (offline) and eve-trader (online) aligned

This repo and `eve-trader` (https://github.com/Pappmichel/eve-trader) are
two separate codebases, not a fork/branch pair — they deliberately diverge
at the storage/config layer (SQLite + a plain dataclass here vs. Postgres +
RLS + `ConfigProxy` there, see this repo's own README). There is no tooling
that syncs them automatically. This file is the manually-maintained
contract for *what* needs porting by hand when it changes, and *what never
should be*. The two copies of this file (one per repo) should stay
identical in content — update both together.

**In scope for syncing:** general business-logic/behavior — pricing
formulas, ESI/Goonmetrics parsing and math, classification rules, the OAuth
flow itself. **Out of scope, by design:** anything tenant-specific,
user-specific settings/data, or access-control/multi-tenant machinery — see
"Never sync" below. When in doubt: would a single local user's own settings
or a specific tenant's data ever appear in the diff? If yes, it's not a sync
candidate.

## Already ported

| File here | File in eve-trader | What's shared | Status |
|---|---|---|---|
| `eve_trader_local/auth.py` | `eve_trader/auth.py` | EVE SSO authorization-code+PKCE flow: state/PKCE generation, loopback callback handling, refresh logic | Ported 2026-09-02. Storage calls adapted here (no tenant scoping), locking removed (single-process, no shared thread pool to guard) — port algorithm changes only, not the storage glue. |
| `eve_trader_local/sde.py` | `eve_trader/production/sde.py` | Fuzzwork CSV fetch/parse: the file list, `_fetch_csv`'s retry-with-backoff, `_dump_etag`'s ETag freshness check, every row-shaping rule (the `_RELEVANT_ACTIVITIES` filter, the slot-defining dogma effect IDs, `metaGroupID`/`portionSize` handling) | Ported 2026-09-02, near-verbatim. **Data layer only.** Deliberately left out: the parent's `production/constants.py` import (the four activity IDs are inlined), `ProductionConfig.fuzzwork_csv_base` (a module constant here — no Production config exists yet), and most of the parent's `storage.py` SDE *read* helpers (`load_sde_*`, `get_type_category`, `get_blueprint_*`, `find_invention_recipe_candidates_*`, `get_type_slot`, `get_type_materials`, …) — those exist to serve Production/Doctrine/Refining business logic that isn't ported here yet; port each one when the feature that needs it arrives. Only `sde_row_counts`, `get_sde_type` and `search_sde_types` came over, to make `refresh-sde`/`sde-status` verifiable by a human. The parent's `lru_cache` memoisation on those reads was dropped with them (nothing here calls them in a hot loop, and a cache would need invalidation on every refresh). |

## Candidates — port from eve-trader when a real local feature needs it

None of these exist in this repo yet — only the storage/config/auth
foundation has been built so far (no Trading/Production business logic).
When one gets ported over, add it to the table above and keep it in sync
going forward. Each entry names *what part* of the parent file is
shareable — usually the computation, not the surrounding
storage/`TRADING_CONFIG` wiring:

| File in eve-trader | Shareable logic |
|---|---|
| `eve_trader/esi_client.py` | Order-book percentile stats, price-adjustment math |
| `eve_trader/goonmetrics_client.py` | Region price/history fetch shape, the caching *pattern* (not the cache itself — see the parent repo's CLAUDE.md "Caching pattern" section) |
| `eve_trader/shortlist.py` | `average_market_daily_volume`, Profit/Day computation (issue #100's fix) |
| `eve_trader/history_backtest.py` | Historical candidate-scoring logic |
| `eve_trader/candidate_discovery.py`, `station_trading/candidate_discovery.py` | Discovery heuristics |
| `eve_trader/trade_reconciliation.py` | Realized-sales matching |
| `eve_trader/own_orders.py` | Order-management logic |
| `eve_trader/production/engine.py` | `classify_activity`, `potential_daily_profit`/`daily_movement`, `ACTIVITY_MODS` |
| `eve_trader/production/pricing.py` | Buy-vs-build pricing |
| `eve_trader/production/invention.py` | Invention math |
| `eve_trader/production/constants.py` | Structure/rig-tier enums |
| `eve_trader/refining/engine.py`, `pricing.py`, `reprocessing.py`, `optimizer.py`, `paste_parser.py` | Ore/mineral/reprocessing math |
| `eve_trader/doctrine/engine.py`, `parser.py`, `validation.py` | Fitting parsing/validation logic |
| `eve_trader/*/models.py` | Plain dataclasses — portable as-is where they don't reference `storage`/`TRADING_CONFIG` directly |
| `TradingConfig`/`ProductionConfig` **field definitions** (`config.py`, `production/config.py`) | The behavioral fields (region ids, thresholds, economics) — not the `ConfigProxy`/contextvars machinery around them; this repo's `config.py` already carries the local equivalent of the OAuth-related subset |

## Never sync (intentionally divergent, or not applicable)

- `eve_trader/storage.py`, `eve_trader/tenant_scope.py` — Postgres/RLS is
  the whole reason this repo exists separately, not as a fork.
- `eve_trader/access_gate.py`, `eve_trader/admin.py` — multi-tenant/
  cross-tenant concepts with no local-single-user equivalent.
- `eve_trader/api/*`, `eve-trader`'s `frontend/*` — this repo's target UI is
  a native GUI, not a web frontend/backend split.
- `eve_trader/scheduler.py` — per-tenant background-job iteration; a local
  "scheduler" here (if ever needed) would be a single-user timer, not worth
  sharing code with this.
- `eve_trader/backup.py` — shells out to `docker exec pg_dump`; meaningless
  without Postgres.
- `eve_trader/sqlite_migration.py` — a one-time Postgres *cutover* tool;
  conceptually the opposite direction from this repo's SQLite-native
  storage.
- Any tenant's or local user's own data/settings — this file tracks code,
  never data.

## Maintenance

Update this table (both copies — see the mirrored `SYNC.md` in eve-trader)
whenever:
- A "candidate" module actually gets ported here → move its row to
  "Already ported".
- A genuinely new, storage/tenant-agnostic business-logic module is added
  in eve-trader → add it to "Candidates".
- A module you'd expect to share turns out to be too entangled with
  storage/config to port cleanly → note it under "Never sync" with why, so
  nobody re-attempts the same porting effort later.
