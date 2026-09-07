# Phase G — Scale, performance & reliability

The Production Special-Order **core stays frozen**. `PRODUCTION_SEMANTICS.md`
(SF-1..SF-8 and the missing-price policy) is binding. Phase F confirmed the
E.2–E.5 extensions do not interfere. Phase G asks whether the same system
stays correct **under load, under repetition, and after failed writes**.

Measure first. Do not optimize. Do not edit `engine.plan_special_order`,
`_expand_all`, `_invention_need_row`, `_buy_or_build_decision`, or
`_job_cost_rate`. Fixes, if any, stay at extension points (CLI/GUI,
transactional storage wrappers, audit).

---

## G.1 — Performance & scale baseline

### Goal

Record reproducible timings and memory for the published matrix. Identify
bottlenecks. **Do not change code to make a number smaller.**

### Matrix

| Axis | Points |
|---|---|
| Order count | 1, 10, 50, 100 |
| Line items | few (1) vs many (20 on one order) |
| Pooling | same `type_id` across many orders vs distinct finished types |
| Preview | combined of N orders; 3-level widget BOM vs 4-level chain |
| Production | T2 invention combined; hangar net on vs off |

### What is measured

Wall time (`time.perf_counter`) and peak traced Python allocations
(`tracemalloc`) for:

- `do_create_special_order` × N
- `do_list_special_orders`
- `do_compute_special_order` (one order)
- `do_compute_combined_special_orders` (all N)
- `do_audit_special_orders`

Semantic checks at the same scale (not timing assertions):

- Combined of N copies of type T qty Q is one planner run with qty N×Q (SF-2, SF-3)
- Top-level hangar is not netted (SF-1)
- Combined does not persist (SF-4)
- Repeat compute fingerprints match (SF-4)

### Non-goals

Indexes, caching, batching `do_list_special_orders`, rewriting the planner.

### Deliverables

- This file's **Measured baseline** section (filled from one harness run)
- `tests/test_phase_g1_scale_baseline.py` (`pytest.mark.release`)
- Bottleneck notes: observation only

---

## G.2 — Reliability & repeatability

### Goal

The loop Edit → Auto-Recompute → Reload → Compute → Combined is
deterministic no matter how often it runs.

### Protocol

Repeat the loop **20 times** on the same SQLite file:

1. `preview_refresh.set_item_and_preview` (quantity `10 + i`)
2. Re-read `do_get_special_order` (reload)
3. `do_compute_special_order`
4. `do_compute_combined_special_orders` with a second order

Expect:

- Wrapper plan fingerprint == post-reload Compute fingerprint every cycle
- Combined fingerprint is a function of current stored items + per-call
  `net_against_stock`, not of cycle index
- Events: one `item_set` per successful Set, never a Compute event
- Hangar COMPONENT stock is not consumed across repeats (SF-4 / no
  cumulative netting)
- CLI `--recompute` and the wrapper produce the same plan for the same qty

### Non-goals

Persisting previews; changing event schema.

### Deliverables

`tests/test_phase_g2_reliability.py` (`pytest.mark.release`).

---

## G.3 — Data integrity & failure recovery

### Goal

No half-written special orders. Failed mutations leave the previous
consistent snapshot. Audit after errors is complete.

### Cases

1. Invalid create payload (unknown type, qty ≤ 0, empty list) — zero new
   headers, zero new events for a would-be order.
2. Create with one good type and one unknown type — still all-or-nothing
   (already E.2).
3. Set Item qty ≤ 0 / unknown type / unknown order — no `item_set` event,
   items unchanged.
4. Remove last remaining item — refused, no `item_removed`.
5. Mid-write failure: inject an exception after the header insert inside
   `create_special_order_with_items` — rollback, no leftover header, no
   orphan items (SQLite `connect()` rollback).
6. Reload + audit after each failure — `ok` for the surviving healthy
   orders; injected incomplete rows still reported (empty / orphan /
   nonpositive) without repair.

### Non-goals

Retry queues, WAL recovery tooling, changing `SpecialOrder`.

### Deliverables

`tests/test_phase_g3_failure_recovery.py` (`pytest.mark.release`).
Integrity notes in this file after the suite.

---

## Measured baseline (G.1)

Recorded 2026-09-07 on the Cloud Agent VM (Python 3.12, tmp SQLite, synthetic
widget SDE, `QT_QPA_PLATFORM=offscreen`). **Not an SLO.** Re-run
`pytest tests/test_phase_g1_scale_baseline.py -s` to refresh.

Pooled orders: N headers, each `Finished Widget A × 2`. Combined is one
planner run over qty `2N`.

| Scenario | N | create_ms | list_ms | one_compute_ms | combined_ms | audit_ms | peak_kib |
|---|---|---|---|---|---|---|---|
| pooled A×2 | 1 | 4.67 | 1.25 | 15.89 | 9.07 | 2.28 | 7.8 |
| pooled A×2 | 10 | 85.01 | 6.33 | 8.86 | 18.52 | 7.07 | 8.9 |
| pooled A×2 | 50 | 279.35 | 26.99 | 8.81 | 60.24 | 28.39 | 27.2 |
| pooled A×2 | 100 | 727.75 | 54.25 | 8.78 | 112.81 | 55.21 | 46.0 |

Other points (same machine):

| Scenario | wall_ms | Notes |
|---|---|---|
| One order, 20 distinct finished types, compute | 115.41 | All share COMPONENT |
| Combined 20 distinct one-item orders | 135.70 | One planner call, 20 line items |
| Combined 4-level BOM (A+B) | 27.55 | SUB in build list |
| Combined T2 10 orders × qty 10 | 35.66 | One invention row, runs_needed=100 |

`one_compute_ms` at N=1 is a cold first planner call (~16ms); later single-order
computes sit ~9ms. Combined of 100 pooled orders is slower than one compute
(~113ms vs ~9ms) even though the planner input is one type: Combined loads
every header/item row first.

### Bottleneck analysis (G.1)

Ranked by measured cost at N=100 pooled, **observation only**:

1. **Create × N** (~728ms) — N separate SQLite transactions (header + items +
   event). Linear in N. Expected for the current API; not a planner issue.
2. **Combined load** (~113ms) — `_pooled_special_order_items` does N existence
   checks + N item lists, then one `plan_special_order`. Planner work for a
   pooled type is close to a single compute (~9ms). Extra time is storage
   round-trips around the frozen engine.
3. **List / audit** (~54–55ms) — `do_list_special_orders` calls
   `list_special_order_items` once per header (item_count). Audit walks the
   same tables. Linear in N.
4. **Planner** — flat for pooled same-type; grows when Combined has many
   distinct products (20 types ~136ms vs 100 pooled copies ~113ms, different
   N). Deep BOM and T2 combined stay tens of milliseconds on this SDE.

No leak signal in the traced windows (peak 46 KiB at N=100; tracemalloc only
sees allocations inside each timed call).

**No optimization in this phase.** A future change would need a new
measurement against this table. Candidates, if product later asks: batch
item_count for list; pool Combined loads in one query. Neither is done here.

---

## Integrity notes (G.3)

Pending G.3 suite.

---

## Release

```
pytest -m release
```

then the full suite. Timing rows in this file are documentation, not
gates. Semantic-under-load and reliability/recovery tests are gates.
