"""Phase G.1 measurement helpers. Test-only; does not import engine."""
from __future__ import annotations

import time
import tracemalloc

from eve_trader_local.production import actions
from eve_trader_local.production.config import ProductionConfig


def fingerprint(plan: dict) -> dict:
    return {
        "line": frozenset((r.type_id, r.quantity) for r in plan["line_items"]),
        "build": frozenset((r.type_id, r.job_runs) for r in plan["build_list"]),
        "buy": frozenset((r.type_id, r.quantity) for r in plan["buy_list"]),
        "inv": frozenset((r.type_id, r.runs_needed) for r in plan["invention_list"]),
    }


def timed(fn):
    """Return (result, wall_ms, peak_kib)."""
    tracemalloc.start()
    try:
        t0 = time.perf_counter()
        result = fn()
        wall_ms = (time.perf_counter() - t0) * 1000.0
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return result, wall_ms, peak / 1024.0


def create_n(n: int, type_id: int, quantity: float, note_prefix: str = "g1") -> list[str]:
    ids = []
    for i in range(n):
        ids.append(actions.do_create_special_order(
            [{"type_id": type_id, "quantity": quantity}],
            note=f"{note_prefix}-{i}",
        )["order_id"])
    return ids


def measure_scale(n: int, type_id: int, quantity: float, cfg: ProductionConfig) -> dict:
    created, create_ms, create_kib = timed(
        lambda: create_n(n, type_id, quantity))
    _listed, list_ms, list_kib = timed(actions.do_list_special_orders)
    _one, one_ms, one_kib = timed(
        lambda: actions.do_compute_special_order(created[0], cfg=cfg))
    combined, comb_ms, comb_kib = timed(
        lambda: actions.do_compute_combined_special_orders(
            created, net_against_stock=False, cfg=cfg))
    _audit, audit_ms, audit_kib = timed(actions.do_audit_special_orders)
    return {
        "n": n,
        "order_ids": created,
        "create_ms": create_ms,
        "list_ms": list_ms,
        "one_compute_ms": one_ms,
        "combined_ms": comb_ms,
        "audit_ms": audit_ms,
        "peak_kib": max(create_kib, list_kib, one_kib, comb_kib, audit_kib),
        "combined_plan": combined,
    }
