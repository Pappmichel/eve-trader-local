# Phase F — System-wide validation

The Production Special-Order **core stays frozen**. `PRODUCTION_SEMANTICS.md`
(SF-1..SF-8 and the missing-price policy) is binding. `PHASE_E_EXPANSION.md`
describes the isolated E.2–E.5 modules this phase exercises **together**.

Phase C asked whether the core can be broken. Phase F asks whether the
Phase E extensions can break **each other** while each still passes its
own suite.

No new semantics. No architecture changes. Fixes, if any, stay at
extension points (CLI/GUI wrappers, event logging around existing writes,
capacity overlay). `engine.plan_special_order`, `_expand_all`,
`_invention_need_row`, `_buy_or_build_decision`, and `_job_cost_rate` are
not edited.

---

## F.1 — Cross-feature integration audit

### Goal

Combinations that never appear in a single E.2–E.5 test file.

### Combinations

1. **Persistence × auto-recompute**
   Create → persist → Set Item → auto-recompute wrapper → reload
   (re-read storage / new view) → Compute.
   Expect: stored items match the Set; wrapper Compute and post-reload
   Compute fingerprint equal; events are `created` then one `item_set`
   (Compute writes no event); no second source of preview truth.

2. **Remove × events × reload**
   Order → Set Item → Remove Item → event history → Delete Order →
   list events / audit.
   Expect: one event per mutation; delete appends `deleted` and does not
   wipe history; `do_get_special_order` fails; audit does not report the
   deleted id as empty/orphan.

3. **GUI × CLI × persistence**
   CLI create → GUI edit / save note → CLI list → Compute.
   Expect: both interfaces read the same header and line items; no
   GUI-only or CLI-only persisted fields.

4. **`net_against_stock` × persistence × combined preview**
   Stored flag true → Combined false → Combined true → reload → Combined
   false.
   Expect: SF-5 per-call combined flag; stored flags unchanged; Combined
   false fingerprints match across the round trip; GUI selected-order net
   checkbox does not drive Combined (Combined uses its own control).

5. **Cost indices × job slots × production**
   Industry job rows present → cost-index override → slot-capacity overlay
   → GUI/CLI capacity view.
   Expect: override writes only the existing config fields; slot totals
   never enter the planner or special-order tables; `jobs.character_slot_overview`
   stays used-only; Compute fingerprints ignore slot totals.

### Non-goals

New event types; persisting combined preview; ESI character-skills; core
planner edits.

### Deliverable tests

`tests/test_phase_f1_cross_feature.py` (`pytest.mark.release`).

---

## F.2 — Realistic end-to-end workflows

### Goal

Full operator paths, not isolated pairwise combos. Persistence across a
simulated restart (new GUI view + CLI re-read of the same SQLite file).

### Operator path (canonical)

CLI create → add items → GUI edit → auto-recompute → save note → mark
done → reload application → Combined preview with other orders → cost-index
override → capacity check → final Compute.

### Scenarios

| Id | Operator situation |
|----|-------------------|
| S1 | Small single order |
| S2 | Several larger orders sharing COMPONENT |
| S3 | Tech II / invention preview |
| S4 | Hangar stock present (top-level never netted; components may net) |
| S5 | Missing market prices (unpriced Buy, BOM not expanded, state unchanged) |
| S6 | Persisted orders after restart |
| S7 | CLI ↔ GUI handoff on one order |

### Non-goals

New workflows in the planner; inventing a second compute path.

### Deliverable tests

`tests/test_phase_f2_end_to_end.py` (`pytest.mark.release`).

User-facing narrative of the same paths lives in this file's
"Operator workflows" section (filled in F.2).

---

## Conflict analysis (F.1)

Recorded after the F.1 suite. Each row is a place two extensions share
state.

| Shared surface | Extensions | Verdict |
|---|---|---|
| `special_orders` / `special_order_items` | E.2 persist, E.3 name-create/note, E.4 wrapper (Set/Remove then Compute) | Wrapper mutates items only through Set/Remove; Compute is read-only. Reload Compute matches wrapper plan. |
| `special_order_events` | E.2 log, E.4 wrapper, E.1 remove | One event per storage write. Compute / Combined / auto-recompute preview add none. Delete keeps history. |
| Combined `net_against_stock` | E.2 stored flag, E.3 GUI net checkbox, SF-5 | Stored flag used only by one-order Compute. Combined takes the call argument. GUI Combine checkbox is independent of the selected-order checkbox. |
| `PRODUCTION_CONFIG` cost-index fields | E.5 override actions, existing `_job_cost_rate` | Override is the documented Settings path. Slot totals do not read or write these fields. |
| `character_job_slot_totals` / industry jobs | E.5 overlay vs `jobs.character_slot_overview` | Overlay joins used counts with optional totals. Used-only overview unchanged. Neither table is read by `plan_special_order`. |
| CLI vs GUI | E.3 ergonomics, E.4 `--recompute` / checkbox | Both call `production.actions` (and the E.4 wrapper). No second item or note store. |

Extension-point corrections found during F.1, if any, are listed next to
the failing test — not as new product rules.

---

## Operator workflows (F.2)

Filled when the F.2 suite lands. Until then this section is the scenario
table above, not a placeholder for unimplemented behavior.

---

## Release

```
pytest -m release
```

then the full suite. Phase C/D tests remain the core freeze; F.1/F.2 do
not copy that attack matrix.
