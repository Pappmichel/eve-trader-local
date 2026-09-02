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
| `eve_trader_local/esi_client.py` | `eve_trader/esi_client.py` | The whole generic ESI HTTP layer: `OrderStats`/`_summarize_orders`/`_percentile`, the retry-with-backoff core (`_get_response`/`_post_response`, `_retry_after_seconds`), ESI's `X-Esi-Error-Limit-*` budget tracking, `_get_all_pages`' page-1-then-concurrent-rest pagination, the class-level price/cost-index and short-TTL order-book caches, and every endpoint wrapper (character/corp orders, assets, wallet, industry jobs, blueprints, contracts, skills, structures, name/system resolution, market history, adjusted prices, cost indices) | Ported 2026-09-02, near-verbatim. `structure_order_stats_bulk_or_goonmetrics` followed 2026-09-02 with the `goonmetrics_client.py` port (see the row below) — it was held back only because it's the one method depending on that client. Adapted: the parent's `storage.with_current_tenant` wrapper around every `ThreadPoolExecutor.submit` is dropped (it exists purely because a worker thread doesn't inherit the tenant contextvar; there is no ambient tenant here), and `ESIError` subclasses this repo's `ActionError` (the parent keeps them separate only to dodge a `config.py`↔`actions.py` circular import — see `errors.py`). `resolve_effective_volume`/`_bulk` came over with two new SQLite-side helpers they need (`storage.get_type_category`, plus a `type_packaged_volume` cache table and its getter/setter) — that table is an ESI-only per-type constant, so it deliberately survives an SDE refresh. |
| `eve_trader_local/goonmetrics_client.py` | `eve_trader/goonmetrics_client.py` | The current-price fetch shape (`appraise.gnf.lt/market/{slug}/prices.json`, its `CurrentPrice` parsing and retry-with-backoff) and the caching *pattern*: module-level cache dicts keyed by market, with a per-market lock rather than one shared lock, so a multi-second Jita fetch never blocks a concurrent home-market fetch that shares no cache entry with it. Plus `ESIClient.structure_order_stats_bulk_or_goonmetrics` (in `esi_client.py`, where the parent keeps it too) — the failsafe that falls back to a Goonmetrics snapshot when no seller is logged in or the real structure order book fails, returning `(stats, used_fallback)` with volumes zeroed. Plus the region price-history half: `price_history`/`price_history_chunked`/`HistoryPoint`/`_parse_history_xml` (`goonmetrics.apps.gnf.lt/api/price_history/`), its silent per-type ESI history fallback for when that no-SLA API is down — where `movement` passes ESI's `volume` (a unit count) straight through, never multiplied by average price, the parent's own fix for silently mixing unit counts and ISK values in one field — and the chunked fetch's per-chunk isolation so one bad chunk can't lose a whole multi-minute search. | Ported 2026-09-02 (current prices), completed 2026-09-02 with the price-history half. Both endpoints are now here, and the module docstring spells out which question each answers — a region history average ("was this historically worth importing", asked against `reference_region_id`) is not interchangeable with a live current quote. Added here: `TradingConfig.goonmetrics_appraise_base` and `goonmetrics_history_base` (the parent hardcodes the former as a module constant `APPRAISE_BASE`; this repo already has an `esi_base` config precedent, so both are fields) plus `chunk_size` (with a `_FIELD_RANGES` entry). |
| `eve_trader_local/candidate_discovery.py`, `eve_trader_local/models.py` | `eve_trader/candidate_discovery.py`, `eve_trader/models.py` | The whole discovery pass: `is_wanted_market_path`'s exclusion-prefix-only filtering (no keyword allowlist, no per-item m3 cap — an item is only ever dropped for failing on real profitability/volume later), `guess_category`'s real-SDE-category classification (with the string/volume heuristic kept strictly as the live-ESI-walk fallback, and the `IMPLANT_CATEGORY_ID`/`BOOSTER_GROUP_ID` split that keeps drugs from being labelled implants), `_market_group_path`, the SDE-cache-first/live-ESI-walk-fallback `build_candidate_universe`, and `build_focused_candidate_universe` (a deliberate pass-through). Plus the `Candidate` dataclass. | Ported 2026-09-02, near-verbatim — including the parent's issue #73 (capital-sized modules need their *packaged* volume, not the SDE flight volume) and issue #96 (resolve it in one bulk concurrent pass, never per-type inside the loop) context, which are real bugs, not stylistic notes. Added here to support it: `TradingConfig.excluded_path_prefixes`, `storage.load_sde_market_groups`/`load_sde_types_with_market_group`/`load_sde_category_names`/`load_sde_type_groups` (part of the SDE read-helper set the `sde.py` row deliberately deferred), and the `candidate_universe`/`focused_candidates` tables with `save_candidate_universe`/`load_candidate_universe`/`get_candidate_universe_built_at` — persistence is the *caller's* job in the parent too (`actions.do_build_universe`), so the module itself stays free of storage writes. `load_candidate_universe` returns `Candidate` objects where the parent's `read_table` returns a pandas DataFrame (no pandas here, and every caller wants the dataclass). Only `Candidate` came from the parent's `models.py`; its other dataclasses arrive with `shortlist.py`/`history_backtest.py`/`trade_reconciliation.py`. |
| `eve_trader_local/sde.py` | `eve_trader/production/sde.py` | Fuzzwork CSV fetch/parse: the file list, `_fetch_csv`'s retry-with-backoff, `_dump_etag`'s ETag freshness check, every row-shaping rule (the `_RELEVANT_ACTIVITIES` filter, the slot-defining dogma effect IDs, `metaGroupID`/`portionSize` handling) | Ported 2026-09-02, near-verbatim. **Data layer only.** Deliberately left out: the parent's `production/constants.py` import (the four activity IDs are inlined), `ProductionConfig.fuzzwork_csv_base` (a module constant here — no Production config exists yet), and most of the parent's `storage.py` SDE *read* helpers (`load_sde_*`, `get_type_category`, `get_blueprint_*`, `find_invention_recipe_candidates_*`, `get_type_slot`, `get_type_materials`, …) — those exist to serve Production/Doctrine/Refining business logic that isn't ported here yet; port each one when the feature that needs it arrives. Only `sde_row_counts`, `get_sde_type` and `search_sde_types` came over, to make `refresh-sde`/`sde-status` verifiable by a human. The parent's `lru_cache` memoisation on those reads was dropped with them (nothing here calls them in a hot loop, and a cache would need invalidation on every refresh). |

## Candidates — port from eve-trader when a real local feature needs it

None of these exist in this repo yet. When one gets ported over, add it to the table above and keep it in sync
going forward. Each entry names *what part* of the parent file is
shareable — usually the computation, not the surrounding
storage/`TRADING_CONFIG` wiring:

| File in eve-trader | Shareable logic |
|---|---|
| `eve_trader/shortlist.py` | `average_market_daily_volume`, Profit/Day computation (issue #100's fix) |
| `eve_trader/history_backtest.py` | Historical candidate-scoring logic |
| `eve_trader/station_trading/candidate_discovery.py` | Station-trading discovery heuristics (the Jita→structure import `candidate_discovery.py` is ported, see above) |
| `eve_trader/trade_reconciliation.py` | Realized-sales matching |
| `eve_trader/own_orders.py` | Order-management logic |
| `eve_trader/production/engine.py` | `classify_activity`, `potential_daily_profit`/`daily_movement`, `ACTIVITY_MODS` |
| `eve_trader/production/pricing.py` | Buy-vs-build pricing |
| `eve_trader/production/invention.py` | Invention math |
| `eve_trader/production/constants.py` | Structure/rig-tier enums |
| `eve_trader/refining/engine.py`, `pricing.py`, `reprocessing.py`, `optimizer.py`, `paste_parser.py` | Ore/mineral/reprocessing math |
| `eve_trader/doctrine/engine.py`, `parser.py`, `validation.py` | Fitting parsing/validation logic |
| `eve_trader/models.py` (the rest), `eve_trader/*/models.py` | Plain dataclasses — portable as-is where they don't reference `storage`/`TRADING_CONFIG` directly. `Candidate` is ported; `ShortlistItem`/`ShortlistRow`/`NewCandidateResult`/`RealizedTrade`/… belong to modules still listed here, so each arrives with its own |
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
