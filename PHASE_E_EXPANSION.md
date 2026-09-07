# Phase E — Controlled feature expansion

The Production Special-Order **core is frozen**. `PRODUCTION_SEMANTICS.md`
(SF-1..SF-8 and the missing-price policy) stays binding. This file is the
product spec for isolated expansions **after E.1**. It does not change those
rules.

## Global constraints

- No edits to `engine.plan_special_order`, `_expand_all`, `_invention_need_row`,
  `_buy_or_build_decision`, or `_job_cost_rate` except where E.5 explicitly
  uses an existing config field through `save_config_overrides`.
- No new fields on `SpecialOrder` / `SpecialOrderLineItem`.
- Combined preview stays unsaved (SF-2 / SF-4).
- This repo is single-user SQLite (no `tenant_id`, no RLS). Isolation here
  means: one local database, no second operator namespace, no shared-table
  leakage. Parent-style RLS/multi-tenant is not ported.

---

## E.2 — Persistence / order-management

### Goal

Order lifecycle and data integrity around the existing header/child tables.
Compute, pricing, and preview are not involved.

### Behavior

1. **Single-transaction create.** `do_create_special_order` writes the header
   and every line item in one SQLite transaction. A failed item must not leave
   an empty (or partial) order.
2. **Create-time pooling.** Duplicate `type_id`s in the create payload are
   summed into one row (PK is `(order_id, type_id)`). Last-write-wins on
   create is not allowed.
3. **Lifecycle flag.** `do_update_special_order` may set `net_against_stock`
   (already stored). Combined preview still ignores stored flags (SF-5).
4. **List filter.** `do_list_special_orders(status=None)` — `None` lists all
   (current behavior); `"open"` / `"done"` filters. Unknown status is an error.
5. **Append-only events.** Additive table `special_order_events`
   (`event_id`, `order_id`, `event`, `detail`, `at`). Logged on create,
   header update, item set/remove, and order delete. Delete does **not**
   cascade-wipe events (audit survives the order).
6. **Read-only audit.** Isolated module
   `eve_trader_local/production/order_integrity.py` reports:
   - items whose `order_id` is missing from `special_orders`
   - headers with zero items
   - items with `quantity <= 0`
   Audit never repairs and never calls the planner.

### Non-goals

Persisting a combined preview; changing SpecialOrder fields; tenant_id/RLS.

### CLI

- `list-special-orders [--status open|done]`
- `update-special-order --net-against-stock / --from-scratch`
- `audit-special-orders`
- `list-special-order-events [order_id]`

---

## E.3 — UX improvements

### Goal

Additive API/CLI/GUI ergonomics. No breaking changes.

### Behavior

1. **Create by name.** Each create item may use `type_id` (existing) or
   `type_id_or_name` / `name` (resolved with the same `_resolve_type` Set Item
   already uses). GUI/CLI drop duplicated SDE lookup.
2. **GUI lifecycle.** Selected order: edit note, toggle net-against-stock,
   filter the orders table by status (All / Open / Done).
3. **Preview totals.** After Compute / Combined, show summed buy-list
   `total_price` (skip `None`) in the status line. Display only; the plan dict
   is unchanged.
4. **CLI list** prints `order_id` plus status/item count as today, with the
   E.2 `--status` filter.

### Non-goals

Breaking `do_create_special_order({type_id, quantity})`; auto-recompute (E.4).

---

## E.4 — Auto-recompute / preview UX

### Goal

Optional recompute **after** a successful item mutation, as a wrapper. Core
Set/Remove/Compute stay pure (SF-4, SF-6).

### Behavior

1. Isolated `eve_trader_local/production/preview_refresh.py`:
   - `set_item_and_preview(...)` → `do_set_special_order_item` then
     `do_compute_special_order`
   - `remove_item_and_preview(...)` → `do_remove_special_order_item` then
     `do_compute_special_order`
   The wrapper does not import `engine`. Failures in Set/Remove never compute.
2. CLI: `set-special-order-item` / `remove-special-order-item --recompute`
3. GUI: checkbox **Auto-recompute after edit**. When checked, Set Item and
   Remove Item use the wrapper and populate Buy/Build/Invention like Compute.
   When unchecked, behavior stays E.1 (refresh line items only).
4. Combined preview status text stays explicit that source orders are
   unchanged (already true; keep the wording).

### Non-goals

Calling the planner from `do_set_special_order_item`; persisting the preview.

---

## E.5 — Production features (job slots, cost indices)

### Goal

Isolated extras listed as out-of-core in `PRODUCTION_SEMANTICS.md`. Planner
semantics stay frozen.

### Job-slot totals / free

Local ESI sync still does **not** request character-skills. Totals are
**manual**:

- Additive table `character_job_slot_totals`
  (`character_name`, `manufacturing`, `reaction`, `science`), all `>= 0`.
- Isolated `eve_trader_local/production/job_slot_capacity.py` joins those
  totals with the existing used-slot counts from industry jobs.
- New row type `JobSlotCapacityRow` (not a change to `SpecialOrder`).
  `total_slots` / `free_slots` are `None` when no total is stored.
- When a total exists, emit the category even if used is 0 (so free is
  visible). When no total exists, keep today’s skip-zeros used-only rows.
- `jobs.character_slot_overview` and `plan_production` are unchanged.
- GUI Current Jobs & Slots: extra Total / Free columns (`—` if unset) plus
  Set/Clear totals controls.
- CLI: `set-character-job-slots`, `clear-character-job-slots`,
  `character-slot-capacity`

### Cost-index overrides

Engine already reads `reaction_` / `component_` /
`manufacturing_cost_index_override`. E.5 only **sets** them through the
existing Settings path:

- `do_set_cost_index_override(kind, value)` / `do_clear_cost_index_override(kind)`
  via `save_config_overrides`. `kind` in `reaction|component|manufacturing`.
  Range still 0–1 (existing config validation). `None`/clear removes the
  override.
- GUI Margins & Market Status: override row next to the cached-index table.
- `_job_cost_rate` is not edited.

### Non-goals

ESI skills scope; `plan_asset_optimized` slot splitting; changing job-cost
math.
