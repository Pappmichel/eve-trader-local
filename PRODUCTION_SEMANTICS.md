# Production / Special-Order semantic freeze

Frozen baseline for the Production Special-Order core after Phase C
(adversarial audit) and Phase D (release readiness). Change any rule here
only as an explicit product decision, with tests updated in the same change.

This file states *what must remain true*, not how the current functions are
implemented. Implementation lives in `eve_trader_local/production/actions.py`
and `eve_trader_local/production/engine.py`.

## Data flow

```
CLI / GUI / Special Orders
        ↓
production/actions.py
        ↓
do_set_special_order_item          (upsert only — no plan)
do_remove_special_order_item       (delete one line — no plan)
do_compute_special_order           (one stored order)
do_compute_combined_special_orders (unsaved pooled preview)
        ↓
_plan_special_order_items
        ↓
engine.plan_special_order
        ├── Buy/Build (_expand_all)
        └── Invention (_invention_need_row)
```

CLI and GUI must not call `plan_special_order`, `_expand_all`, or
`_invention_need_row` themselves.

## Semantic freeze

### SF-1 — B1 (top-level hangar)

Top-level items of a special order are **never** netted against hangar
stock. Ordered quantity N always seeds N units of demand.

This holds for a single compute, a combined preview, pooled duplicate
`type_id`s, and hangar stock that is exact, below, or above the order.

Component and material demand *below* the seed may still net when
`net_against_stock` is true. A type that is itself a line item is a
top-level item for that quantity and is not netted.

### SF-2 — Combined preview

`Preview(A+B)` is **one** planner run over pooled line items. It is not
`Preview(A) + Preview(B)`.

Shared products and shared BOM demand are sized once. Isolated previews
that each net the same hangar stock will over-claim that stock relative
to the combined run.

The combined result is not persisted. Source orders are not modified.

### SF-3 — Pooling

Identical `type_id`s are summed. Pooling must not last-write-wins, expand
the same product twice, claim the same hangar unit twice, or emit duplicate
invention rows for one pooled product.

### SF-4 — Preview purity

`do_compute_special_order` and `do_compute_combined_special_orders` do not
mutate stored orders, line items, flags, stock, or decryptor overrides.
Repeating the same preview with the same input yields the same plan.

### SF-5 — `net_against_stock`

For **combined** preview, `net_against_stock` is a per-call argument:

- set by the current caller
- not written back to any order
- not merged from stored per-order flags
- must not mutate those stored flags

A stored per-order flag still exists and is used **only** by
`do_compute_special_order` for that one order.

### SF-6 — Upsert

`do_set_special_order_item`:

- requires an existing order
- unknown item → insert; known item → update quantity
- no duplicate rows for the same `type_id` on one order
- quantity `<= 0` → error
- does not compute a plan; the next Compute / Combined uses the stored items

`do_remove_special_order_item` is the matching delete:

- requires an existing order
- unknown type or an item not on that order → error (no silent no-op)
- refuses to delete the last remaining item (remove the whole order instead)
- does not change other items, order flags, or other orders
- does not compute a plan

### SF-7 — Invention single source of truth

`_invention_need_row` is the only place that constructs an `InventionNeedRow`
for this core. CLI, GUI, and actions must not invent a second formula.

Invention on special orders is a computed preview, not a persisted second
invention workflow. `stockpile_pct` is against the ordered quantity.

### SF-8 — Shared planner path

CLI and GUI reach the engine only through the actions above. Do not add
parallel buy/build or invention math in views or `cli.py`.

## CURRENT POLICY — missing market prices

If no market quotes exist and `_buy_or_build_decision` cannot form a
`build_cost`:

- the ordered item is an **unpriced Buy**
- the BOM is **not** expanded
- quantity is kept
- persistent state is not mutated

Do **not** implement Always Build, automatic BOM expansion without a cost
basis, or a new fallback in this freeze. Changing this is a future product
decision.

## Scope boundaries (not this core)

These are intentionally out of the frozen Special-Order core. Do not
implement them *inside* this path. Isolated Phase E modules (see
`PHASE_E_EXPANSION.md`) may add them **around** the core without changing
SF-1..SF-8:

- persisting a combined preview (still forbidden)
- invention logistics on special orders (stays on `plan_production`)
- auto-recompute after Set Item (E.4 wrapper only)
- job-slot totals/free counts (E.5, manual totals, not ESI skills)
- per-category cost-index overrides (E.5, existing config fields)
- persistent invention workflows

## Release regression

Critical semantics are already covered by existing tests. Do not duplicate
them; run this group:

```
pytest -m release
```

| Area | Primary tests |
|---|---|
| SF-1 B1 | `tests/test_phase_c_adversarial_audit.py` (`test_b1_*`, hangar parametrize); `tests/test_production_special_orders.py` (`test_net_against_stock_does_not_net_top_level_quantity`, `test_combine_net_against_stock_does_not_net_top_level_quantity`); `tests/test_production_invention_needs.py` (`test_special_order_invention_does_not_net_top_level_hangar_stock`) |
| SF-2 Combined = one run | `test_combined_preview_is_a_single_planner_run`, `test_combine_does_not_reschedule_planner_per_order`, `test_single_and_combined_compute_share_plan_special_order` |
| SF-3 Pooling | `test_scenario_a_*`, `test_scenario_b_*`, `test_scenario_c_*`, `test_combine_pools_shared_top_level_items`, `test_combined_special_order_invention_does_not_duplicate_products` |
| Shared components | `test_scenario_b_shared_component_stock_claimed_once`, `test_combined_preview_is_not_the_sum_of_isolated_previews` |
| SF-6 Upsert / Remove | `test_do_set_special_order_item_*`, `test_do_remove_special_order_item_*`, `test_repeated_upsert_does_not_create_silent_duplicates`, `test_set_item_does_not_invoke_the_planner` |
| SF-4 Purity / SF-9 repeats | `test_preview_purity_*`, `test_repeated_compute_and_combined_are_stable`, `test_combine_does_not_persist_or_mutate_source_orders` |
| SF-5 Isolation | `test_net_against_stock_preview_is_isolated_and_repeatable` |
| Determinism | `test_three_order_permutation_is_semantically_identical`, `test_combine_is_deterministic` |
| SF-8 CLI path | `test_cli_*`, `test_cli_calls_the_same_action_and_emits_the_same_plan` |
| SF-8 GUI path | `test_gui_compute_combined_only_calls_the_action`, `test_gui_populate_renders_the_action_plan`, `tests/test_gui_production.py` / `tests/test_gui_production_gaps.py` special-order tests |
| SF-7 Invention | `tests/test_production_invention_needs.py`, `test_inv1_*`, `test_combined_t2_*` |
| Composed acceptance | `tests/test_phase_d_release_acceptance.py` |
| Phase E expansions (isolated) | `tests/test_phase_e2_order_persistence.py`, `tests/test_phase_e3_ux.py`, `tests/test_phase_e4_auto_recompute.py`, `tests/test_phase_e5_production_features.py` |
| Phase F.1 cross-feature | `tests/test_phase_f1_cross_feature.py` (see `PHASE_F_VALIDATION.md`) |
| Phase F.2 operator workflows | `tests/test_phase_f2_end_to_end.py` |
| Phase G.1 scale baseline | `tests/test_phase_g1_scale_baseline.py` (see `PHASE_G_SCALE.md`; timings are documentation, not gates) |
| Phase G.2 reliability | `tests/test_phase_g2_reliability.py` |

End-to-end workflows (create → upsert → compute; combined pooling; stock
modes 1==3; T2 invention aggregation) live in
`tests/test_phase_d_release_acceptance.py` so a release check exercises the
full path without copying the Phase C attack matrix. Phase F.1 does not
re-state that matrix; it checks that E.2–E.5 do not interfere.
