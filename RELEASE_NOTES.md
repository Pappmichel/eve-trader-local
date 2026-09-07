# Release notes — Production Special-Order 0.4.0rc1

Scope of this candidate: the **local** Production Special-Order path in
`eve-trader-local` (CLI + PySide6 GUI → `production/actions.py` →
`engine.plan_special_order`). Other tools (Trading, Doctrine, Refining,
Station Trading) are unchanged by this certification.

Binding rules: `PRODUCTION_SEMANTICS.md` (SF-1..SF-8 and the missing-price
policy). Certification record: `PHASE_H_RELEASE.md`.

## What landed since the semantic freeze (Phases E–G)

Isolated around the frozen planner; no new SpecialOrder fields.

**E.2 Persistence / order management**
- Header + items in one SQLite transaction; duplicate `type_id`s on create
  are summed.
- List filter `open` / `done`; stored `net_against_stock` update (Combined
  still uses a per-call flag).
- Append-only `special_order_events` (survive order delete).
- Read-only audit (`order_integrity.py`).

**E.3 UX**
- Create by `type_id` or name (`type_id_or_name` / `name`).
- GUI status filter, note save, per-order net checkbox.
- Compute/Combined status line shows buy-list ISK total (display only).

**E.4 Auto-recompute**
- Wrapper `preview_refresh.set_item_and_preview` /
  `remove_item_and_preview` (does not import `engine`).
- CLI `--recompute`; GUI checkbox **Auto-recompute after edit**.
- Set/Remove themselves still do not plan (SF-6).

**E.5 Production extras**
- Manual job-slot totals (`character_job_slot_totals`); used-only
  `jobs.character_slot_overview` unchanged.
- Cost-index overrides via existing `ProductionConfig` fields;
  `_job_cost_rate` not edited.

**F System-wide validation**
- Cross-feature combinations (persist × wrapper, events × delete, CLI ×
  GUI, Combined vs stored net flag, slots × cost index) pass.
- Operator paths (restart, hangar, missing prices, T2, shared COMPONENT)
  pass.
- Finding: `create-special-order` prints the **stored pooled** item count,
  not the raw CLI payload length.

**G Scale / reliability**
- Measured baseline for 1/10/50/100 pooled orders, 20 distinct types,
  4-level BOM, T2 combined (`PHASE_G_SCALE.md`). No optimization applied.
- 20 Edit/Recompute/Reload/Compute/Combined cycles stay deterministic;
  hangar is not consumed across repeats.
- Failed writes roll back; audit remains accurate.

## Test status (H.1)

- `pytest -m release`: 178 passed
- Full suite: 1195 passed (59 existing `datetime.utcnow` warnings)

H.2 re-measurement of G.1: list/compute/combined/audit within ~2% at N=100;
create × N varies more; bottleneck ranking unchanged.

## Not in this candidate

See `KNOWN_LIMITATIONS.md`. Combined preview is still not saved. Job-slot
totals are still manual (no ESI character-skills). Missing market prices
still yield an unpriced Buy without BOM expansion.
