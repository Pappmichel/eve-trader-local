# Known limitations — Production Special-Order 0.4.0rc1

Facts about the certified path. Not a roadmap.

## Frozen planner policy

- Top-level special-order quantity is never netted against hangar (SF-1).
- Combined preview is one planner run, not the sum of isolated computes
  (SF-2). It is not written to SQLite.
- Combined `net_against_stock` is the **current call** only. Stored
  per-order flags are used only by one-order Compute (SF-5).
- If no market quotes exist and no `build_cost` can be formed: unpriced
  Buy, BOM not expanded, quantity kept, persistent state unchanged.

## Persistence / local isolation

- Single-user SQLite. No `tenant_id`, no RLS (unlike the parent web app).
- `do_list_special_orders` issues one item-count query per header. At 100
  orders this was ~54 ms on the certification VM (`PHASE_G_SCALE.md`).
  Not optimized.
- Combined of N orders loads each order’s items separately before one
  planner call. At 100 pooled copies Combined was ~113–115 ms; a single
  compute of one order stayed ~9 ms. Not optimized.
- Create of N orders is N transactions (~0.6–0.7 s for 100 on that VM).

## Auto-recompute and GUI

- Auto-recompute is a session checkbox / CLI `--recompute`. It is not
  stored on the order. A new view starts with the checkbox off and empty
  Buy/Build tables until Compute.
- GUI preview tables are not a second source of truth; Compute always
  re-reads SQLite.

## Job slots and cost indices

- Slot totals are operator-entered. ESI character-skills are not requested.
- `jobs.character_slot_overview` remains used-only.
- Cost-index overrides write existing config fields only. They do not
  change Special-Order rows or events.

## Invention on special orders

- Invention needs are a computed preview (`_invention_need_row`), not a
  persisted invention workflow. Logistics for datacores/decryptors on
  special orders stay on `plan_production`.

## Product packaging / version tags

- `pyproject.toml` version is `0.4.0rc1`. Git tag `v0.4.0-rc1` matches
  `.github/workflows/build-windows.yml` (`v*.*.*`) and will start a
  Windows build + GitHub Release. That workflow does not set
  `prerelease: true`, so GitHub `/releases/latest` may point at this RC
  until a later non-RC tag.
- Earlier GitHub tags (`v0.3.2` and below) shipped with `pyproject.toml`
  still at `0.2.0`. This RC is the first package-version bump for the
  Special-Order freeze.
- This candidate does not add an installer, ESI skills pull, or parent
  HTTP/Mantine UI.

## Test warnings

- Full suite reports 59 `datetime.utcnow` deprecation warnings in Trading /
  ESI / refining / station-trading code. They predate this freeze and are
  not Special-Order failures.
