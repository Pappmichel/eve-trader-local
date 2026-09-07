"""Read-only integrity scan for special_orders / special_order_items.

Phase E.2. Does not repair rows, does not call the planner, and does not
change SpecialOrder. Callers (do_audit_special_orders) only report.
"""
from __future__ import annotations

from .. import storage


def audit() -> list[dict]:
    """Every integrity issue currently in the local database.

    Each issue is ``{"kind": str, "order_id": str, "detail": str}``.
    ``kind`` is one of ``orphan_item``, ``empty_order``, ``nonpositive_quantity``.
    """
    issues: list[dict] = []
    headers = {row[0] for row in storage.list_special_orders()}
    for order_id, _note, _net, _status, _created in storage.list_special_orders():
        items = storage.list_special_order_items(order_id)
        if not items:
            issues.append({
                "kind": "empty_order",
                "order_id": order_id,
                "detail": "header has no line items",
            })
        for type_id, type_name, quantity in items:
            if quantity <= 0:
                issues.append({
                    "kind": "nonpositive_quantity",
                    "order_id": order_id,
                    "detail": f"{type_name} ({type_id}) quantity={quantity}",
                })
    for order_id, type_id, type_name, quantity in storage.list_all_special_order_item_rows():
        if order_id not in headers:
            issues.append({
                "kind": "orphan_item",
                "order_id": order_id,
                "detail": f"{type_name} ({type_id}) quantity={quantity}",
            })
    return issues
