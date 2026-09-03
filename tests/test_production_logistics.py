"""Tests for engine.logistics_status/distribution_recommendations - the
multi-structure Logistik-tab helpers (GitHub issue #4 in the parent repo),
ported from the parent's tests/test_production_engine.py. See storage.py's
job_category_locations table comment and engine.py's own docstrings for the
"warehouse first, then surplus, no double-booking" rules these tests pin
down."""
from __future__ import annotations

from eve_trader_local import storage
from eve_trader_local.production import engine
from eve_trader_local.production.config import ProductionConfig


def _build_job(type_id=1, category="Advanced Components", job_runs=1, decryptor=None):
    from eve_trader_local.production.models import BuildJobEntry
    return BuildJobEntry(type_id=type_id, type_name=f"Item{type_id}", blueprint_type_id=type_id + 100,
                          activity="Manufacturing", quantity=job_runs, job_runs=job_runs,
                          unit_build_cost=None, decryptor=decryptor, job_category=category, margin=None)


def _stub_material_demand(monkeypatch, material_id=2, base_qty=10.0):
    monkeypatch.setattr(engine, "classify_activity", lambda type_id: ("Tech I", (type_id + 100, 1, 1.0)))
    monkeypatch.setattr(engine, "_direct_material_mult", lambda *a, **k: 1.0)
    monkeypatch.setattr(storage, "get_blueprint_materials", lambda blueprint_id, activity_id: [(material_id, base_qty)])
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (type_id, 1, f"Item{type_id}", 1.0, 1, 1, 0, None))


# ---------------------------------------------------------------- logistics_status
def test_logistics_status_nets_needed_against_available_at_assigned_location(monkeypatch):
    monkeypatch.setattr(storage, "load_category_locations", lambda: {"Advanced Components": 1001})
    _stub_material_demand(monkeypatch)
    monkeypatch.setattr(storage, "esi_stock_at_location", lambda type_id, location_id: 3.0)

    rows = engine.logistics_status([_build_job()])

    assert len(rows) == 1
    row = rows[0]
    assert row.needed == 10.0
    assert row.available == 3.0
    assert row.missing == 7.0
    assert row.location_id == 1001


def test_logistics_status_skips_categories_with_no_assigned_location(monkeypatch):
    monkeypatch.setattr(storage, "load_category_locations", lambda: {})
    _stub_material_demand(monkeypatch)

    rows = engine.logistics_status([_build_job()])

    assert rows == []


def test_logistics_status_pull_from_hint_picks_richest_surplus_when_no_warehouse(monkeypatch):
    cfg = ProductionConfig(distribution_source_location_id=None, home_location_id=None)
    monkeypatch.setattr(storage, "load_category_locations", lambda: {
        "Advanced Components": 1001, "Capital Components": 1002, "Equipment": 1003,
    })
    _stub_material_demand(monkeypatch)

    def fake_stock(type_id, location_id):
        return {1001: 3.0, 1002: 20.0, 1003: 5.0}[location_id]
    monkeypatch.setattr(storage, "esi_stock_at_location", fake_stock)

    rows = engine.logistics_status([_build_job()], cfg)

    row = rows[0]
    assert row.missing == 7.0
    assert row.pull_from_location_id == 1002  # 20 > 5, richest surplus (neither has its own demand here)
    assert row.pull_from_available == 20.0


def test_logistics_status_prefers_warehouse_over_surplus(monkeypatch):
    cfg = ProductionConfig(distribution_source_location_id=None, home_location_id=9000)
    monkeypatch.setattr(storage, "load_category_locations", lambda: {
        "Advanced Components": 1001, "Capital Components": 1002,
    })
    _stub_material_demand(monkeypatch)

    def fake_stock(type_id, location_id):
        return {1001: 3.0, 1002: 20.0, 9000: 6.0}[location_id]
    monkeypatch.setattr(storage, "esi_stock_at_location", fake_stock)

    rows = engine.logistics_status([_build_job()], cfg)

    row = rows[0]
    assert row.missing == 7.0
    # Warehouse (9000) only has 6, less than Capital Components' surplus of 20,
    # but it's still preferred - GitHub issue #4's explicit ask.
    assert row.pull_from_location_id == 9000
    assert row.pull_from_available == 6.0


def test_logistics_status_falls_back_to_surplus_when_warehouse_empty(monkeypatch):
    cfg = ProductionConfig(distribution_source_location_id=None, home_location_id=9000)
    monkeypatch.setattr(storage, "load_category_locations", lambda: {
        "Advanced Components": 1001, "Capital Components": 1002,
    })
    _stub_material_demand(monkeypatch)

    def fake_stock(type_id, location_id):
        return {1001: 3.0, 1002: 20.0, 9000: 0.0}[location_id]
    monkeypatch.setattr(storage, "esi_stock_at_location", fake_stock)

    rows = engine.logistics_status([_build_job()], cfg)

    row = rows[0]
    assert row.pull_from_location_id == 1002
    assert row.pull_from_available == 20.0


def test_logistics_status_pull_from_hint_nets_other_locations_own_demand(monkeypatch):
    # Capital Components has more raw stock (20) than Equipment (5), but
    # Capital Components' own demand (18) eats almost all of it - Equipment's
    # smaller surplus (5, no demand of its own here) should win instead.
    cfg = ProductionConfig(distribution_source_location_id=None, home_location_id=None)
    monkeypatch.setattr(storage, "load_category_locations", lambda: {
        "Advanced Components": 1001, "Capital Components": 1002, "Equipment": 1003,
    })
    monkeypatch.setattr(engine, "classify_activity", lambda type_id: ("Tech I", (type_id + 100, 1, 1.0)))
    monkeypatch.setattr(engine, "_direct_material_mult", lambda *a, **k: 1.0)
    monkeypatch.setattr(storage, "get_blueprint_materials", lambda blueprint_id, activity_id: [(2, 10.0)])
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (type_id, 1, f"Item{type_id}", 1.0, 1, 1, 0, None))

    def fake_stock(type_id, location_id):
        return {1001: 3.0, 1002: 20.0, 1003: 5.0}[location_id]
    monkeypatch.setattr(storage, "esi_stock_at_location", fake_stock)

    rows = engine.logistics_status(
        [_build_job(category="Advanced Components"), _build_job(category="Capital Components", job_runs=1.8)],
        cfg)

    row = next(r for r in rows if r.category == "Advanced Components")
    assert row.pull_from_location_id == 1003
    assert row.pull_from_available == 5.0


def test_logistics_status_no_pull_from_hint_when_nothing_missing(monkeypatch):
    cfg = ProductionConfig(distribution_source_location_id=None, home_location_id=None)
    monkeypatch.setattr(storage, "load_category_locations", lambda: {
        "Advanced Components": 1001, "Capital Components": 1002,
    })
    _stub_material_demand(monkeypatch)
    monkeypatch.setattr(storage, "esi_stock_at_location", lambda type_id, location_id: 100.0)  # plenty everywhere

    rows = engine.logistics_status([_build_job()], cfg)

    assert rows[0].missing == 0.0
    assert rows[0].pull_from_location_id is None


# ------------------------------------------------------- distribution_recommendations
def test_distribution_recommendations_moves_from_source_to_shortest_category(monkeypatch):
    cfg = ProductionConfig(distribution_source_location_id=2000)
    monkeypatch.setattr(storage, "load_category_locations", lambda: {"Advanced Components": 1001})
    _stub_material_demand(monkeypatch)

    def fake_stock(type_id, location_id):
        return {1001: 3.0, 2000: 50.0}[location_id]
    monkeypatch.setattr(storage, "esi_stock_at_location", fake_stock)

    rows = engine.distribution_recommendations([_build_job()], cfg)

    assert len(rows) == 1
    row = rows[0]
    assert row.type_id == 2
    assert row.from_location_id == 2000
    assert row.to_category == "Advanced Components"
    assert row.to_location_id == 1001
    assert row.quantity == 7.0  # 10 needed - 3 on hand


def test_distribution_recommendations_falls_back_to_home_location(monkeypatch):
    cfg = ProductionConfig(distribution_source_location_id=None, home_location_id=3000)
    monkeypatch.setattr(storage, "load_category_locations", lambda: {"Advanced Components": 1001})
    _stub_material_demand(monkeypatch)
    monkeypatch.setattr(storage, "esi_stock_at_location", lambda type_id, location_id: {1001: 0.0, 3000: 50.0}[location_id])

    rows = engine.distribution_recommendations([_build_job()], cfg)

    assert rows[0].from_location_id == 3000


def test_distribution_recommendations_empty_without_any_source_configured(monkeypatch):
    cfg = ProductionConfig(distribution_source_location_id=None, home_location_id=None)
    monkeypatch.setattr(storage, "load_category_locations", lambda: {"Advanced Components": 1001})

    assert engine.distribution_recommendations([_build_job()], cfg) == []


def test_distribution_recommendations_covers_largest_shortfall_first_when_source_limited(monkeypatch):
    cfg = ProductionConfig(distribution_source_location_id=2000)
    monkeypatch.setattr(storage, "load_category_locations", lambda: {
        "Advanced Components": 1001, "Capital Components": 1002,
    })
    monkeypatch.setattr(engine, "classify_activity", lambda type_id: ("Tech I", (type_id + 100, 1, 1.0)))
    monkeypatch.setattr(engine, "_direct_material_mult", lambda *a, **k: 1.0)
    monkeypatch.setattr(storage, "get_blueprint_materials", lambda blueprint_id, activity_id: [(2, 10.0)])
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (type_id, 1, f"Item{type_id}", 1.0, 1, 1, 0, None))

    def fake_stock(type_id, location_id):
        # Advanced Components short by 8, Capital Components short by 3 - only 5 available at source, not enough for both
        return {1001: 2.0, 1002: 7.0, 2000: 5.0}[location_id]
    monkeypatch.setattr(storage, "esi_stock_at_location", fake_stock)

    rows = engine.distribution_recommendations(
        [_build_job(category="Advanced Components"), _build_job(category="Capital Components")], cfg)

    assert len(rows) == 1  # only enough source stock for the larger shortfall
    assert rows[0].to_category == "Advanced Components"
    assert rows[0].quantity == 5.0


def test_distribution_recommendations_falls_back_to_surplus_when_warehouse_exhausted(monkeypatch):
    # Advanced Components needs 8, Capital Components needs 3, warehouse only has 5
    # (covers Advanced Components' larger shortfall in full) - the leftover 3 for
    # Capital Components should come from Equipment's surplus (stock beyond its
    # own demand), not go unfulfilled the way it used to before GitHub issue #4's
    # "also try surplus locations" follow-up.
    cfg = ProductionConfig(distribution_source_location_id=2000)
    monkeypatch.setattr(storage, "load_category_locations", lambda: {
        "Advanced Components": 1001, "Capital Components": 1002, "Equipment": 1003,
    })
    monkeypatch.setattr(engine, "classify_activity", lambda type_id: ("Tech I", (type_id + 100, 1, 1.0)))
    monkeypatch.setattr(engine, "_direct_material_mult", lambda *a, **k: 1.0)
    monkeypatch.setattr(storage, "get_blueprint_materials", lambda blueprint_id, activity_id: [(2, 8.0)])
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (type_id, 1, f"Item{type_id}", 1.0, 1, 1, 0, None))

    def fake_stock(type_id, location_id):
        return {1001: 0.0, 1002: 5.0, 1003: 20.0, 2000: 8.0}[location_id]
    monkeypatch.setattr(storage, "esi_stock_at_location", fake_stock)

    rows = engine.distribution_recommendations(
        [_build_job(category="Advanced Components"), _build_job(category="Capital Components")], cfg)

    warehouse_row = next(r for r in rows if r.from_location_id == 2000)
    assert warehouse_row.to_category == "Advanced Components"
    assert warehouse_row.quantity == 8.0
    surplus_row = next(r for r in rows if r.from_location_id == 1003)
    assert surplus_row.to_category == "Capital Components"
    assert surplus_row.quantity == 3.0


def test_distribution_recommendations_never_double_books_a_surplus_location(monkeypatch):
    # Two categories (Advanced Components, Capital Components) are both short
    # 10 of the same material with no warehouse configured. The only surplus
    # location (Equipment) has just 12 spare - not enough for both shortfalls
    # in full. Recomputing "surplus" independently per category (the bug an
    # earlier attempt at this shipped with) would recommend pulling up to 10
    # from Equipment for *each* category, 20 total, more than the 12 actually
    # sitting there. The combined recommended quantity out of Equipment must
    # never exceed its real surplus.
    cfg = ProductionConfig(distribution_source_location_id=None, home_location_id=None)
    monkeypatch.setattr(storage, "load_category_locations", lambda: {
        "Advanced Components": 1001, "Capital Components": 1002, "Equipment": 1003,
    })
    monkeypatch.setattr(engine, "classify_activity", lambda type_id: ("Tech I", (type_id + 100, 1, 1.0)))
    monkeypatch.setattr(engine, "_direct_material_mult", lambda *a, **k: 1.0)
    monkeypatch.setattr(storage, "get_blueprint_materials", lambda blueprint_id, activity_id: [(2, 10.0)])
    monkeypatch.setattr(storage, "get_sde_type", lambda type_id: (type_id, 1, f"Item{type_id}", 1.0, 1, 1, 0, None))

    def fake_stock(type_id, location_id):
        return {1001: 0.0, 1002: 0.0, 1003: 12.0}[location_id]
    monkeypatch.setattr(storage, "esi_stock_at_location", fake_stock)

    rows = engine.distribution_recommendations(
        [_build_job(category="Advanced Components"), _build_job(category="Capital Components")], cfg)

    from_equipment = [r for r in rows if r.from_location_id == 1003]
    assert sum(r.quantity for r in from_equipment) <= 12.0
