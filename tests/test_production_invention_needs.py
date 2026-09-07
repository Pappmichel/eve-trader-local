"""Tests for the computed invention-needs preview (InventionNeedRow) on
plan_production (stock targets) and plan_special_order (one-off orders).

Same synthetic-SDE/no-network pattern as test_production_invention.py, whose
Tech II chain (T1 blueprint invents T2 blueprint, which manufactures a T2
module) this reuses almost verbatim.
"""
from __future__ import annotations

import pytest

from eve_trader_local import storage
from eve_trader_local.goonmetrics_client import CurrentPrice
from eve_trader_local.production import actions, engine
from eve_trader_local.production.config import ProductionConfig
from eve_trader_local.production.constants import DECRYPTORS

T1_BLUEPRINT = 1002
T2_BLUEPRINT = 1001
T2_MODULE = 2048
T1_BLUEPRINT_B = 1004
T2_BLUEPRINT_B = 1003
T2_MODULE_B = 2050
DATACORE_A = 20410
DATACORE_B = 20424
TRITANIUM = 34
MORPHITE = 11399

BASE_PROBABILITY = 0.34
BASE_RUNS = 10


def _cfg(**overrides) -> ProductionConfig:
    cfg = ProductionConfig(jita_buy_broker_fee=0.0, haul_cost_per_m3=0.0,
                           facility_tax_rate=0.0, market_fees=0.0,
                           component_overbuild=0.0, min_margin=0.0,
                           encryption_skill_level=4, datacore_skill_1_level=4,
                           datacore_skill_2_level=4)
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


def _seed():
    storage.replace_sde_data(
        types=[
            (TRITANIUM, 18, "Tritanium", 0.01, 1, 100, 0, 1, 1),
            (MORPHITE, 18, "Morphite", 0.01, 1, 100, 0, 1, 1),
            (DATACORE_A, 333, "Datacore - Mechanical Engineering", 0.1, 1, 100, 0, None, 1),
            (DATACORE_B, 333, "Datacore - Molecular Engineering", 0.1, 1, 100, 0, None, 1),
            (T2_MODULE, 60, "Damage Control II", 5.0, 1, 200, 5, 2, 1),
            (T2_MODULE_B, 60, "Capacitor Power Relay II", 5.0, 1, 200, 5, 2, 1),
            (T1_BLUEPRINT, 9, "Damage Control I Blueprint", 0.01, 1, None, 0, None, 1),
            (T2_BLUEPRINT, 9, "Damage Control II Blueprint", 0.01, 1, None, 0, None, 1),
            (T1_BLUEPRINT_B, 9, "Capacitor Power Relay I Blueprint", 0.01, 1, None, 0, None, 1),
            (T2_BLUEPRINT_B, 9, "Capacitor Power Relay II Blueprint", 0.01, 1, None, 0, None, 1),
        ] + [(d.type_id, 314, name, 0.1, 1, 100, 0, None, 1)
             for name, d in DECRYPTORS.items() if d.type_id],
        groups=[(18, 4, "Mineral"), (60, 7, "Damage Control"), (9, 9, "Blueprint"),
                (333, 4, "Datacores"), (314, 4, "Decryptors")],
        market_groups=[(100, None, "Manufacture & Research"), (200, None, "Ship Equipment")],
        blueprint_time=[(T1_BLUEPRINT, 8, 3600.0), (T1_BLUEPRINT_B, 8, 3600.0)],
        blueprint_materials=[
            (T1_BLUEPRINT, 8, DATACORE_A, 2),
            (T1_BLUEPRINT, 8, DATACORE_B, 2),
            (T2_BLUEPRINT, 1, TRITANIUM, 1000),
            (T2_BLUEPRINT, 1, MORPHITE, 1),
            (T1_BLUEPRINT_B, 8, DATACORE_A, 2),
            (T1_BLUEPRINT_B, 8, DATACORE_B, 2),
            (T2_BLUEPRINT_B, 1, TRITANIUM, 1000),
            (T2_BLUEPRINT_B, 1, MORPHITE, 1),
        ],
        blueprint_products=[
            (T2_BLUEPRINT, 1, T2_MODULE, 1),
            (T1_BLUEPRINT, 8, T2_BLUEPRINT, BASE_RUNS),
            (T2_BLUEPRINT_B, 1, T2_MODULE_B, 1),
            (T1_BLUEPRINT_B, 8, T2_BLUEPRINT_B, BASE_RUNS),
        ],
        invention_probability=[
            (T1_BLUEPRINT, T2_BLUEPRINT, BASE_PROBABILITY),
            (T1_BLUEPRINT_B, T2_BLUEPRINT_B, BASE_PROBABILITY),
        ],
        categories=[(4, "Material"), (7, "Module"), (9, "Blueprint")],
    )


DECRYPTOR_PRICES = {
    "Accelerant": 500_000.0, "Attainment": 400_000.0, "Augmentation": 1_000_000.0,
    "Parity": 600_000.0, "Process": 300_000.0, "Symmetry": 450_000.0,
    "Optimized Attainment": 900_000.0, "Optimized Augmentation": 1_200_000.0,
}

JITA = {
    DATACORE_A: CurrentPrice(type_id=DATACORE_A, updated="", buy=0.0, sell=1000.0),
    DATACORE_B: CurrentPrice(type_id=DATACORE_B, updated="", buy=0.0, sell=2000.0),
    TRITANIUM: CurrentPrice(type_id=TRITANIUM, updated="", buy=0.0, sell=5.0),
    MORPHITE: CurrentPrice(type_id=MORPHITE, updated="", buy=0.0, sell=10000.0),
}
JITA.update({
    d.type_id: CurrentPrice(type_id=d.type_id, updated="", buy=0.0, sell=price)
    for name, price in DECRYPTOR_PRICES.items()
    for d in [DECRYPTORS[name]] if d.type_id
})


@pytest.fixture
def invention_sde(db, monkeypatch):
    _seed()
    monkeypatch.setattr(engine.pricing, "home_prices", lambda type_ids, cfg=None, client=None: {})
    monkeypatch.setattr(engine.pricing, "jita_prices", lambda type_ids, client=None, trading_cfg=None: JITA)

    class _FakeESIClient:
        def __init__(self, *a, **kw):
            pass

        def get_adjusted_prices(self):
            return {}

    import eve_trader_local.esi_client as esi_client_module
    monkeypatch.setattr(esi_client_module, "ESIClient", _FakeESIClient)
    return db


def test_invention_list_has_one_row_per_tech_ii_stock_target(invention_sde):
    storage.upsert_stock_target(T2_MODULE, "Damage Control II", 100.0)

    plan = engine.plan_production(_cfg())

    assert len(plan["invention_list"]) == 1
    row = plan["invention_list"][0]
    assert row.type_id == T2_MODULE
    assert row.t1_blueprint_type_id == T1_BLUEPRINT
    assert row.probability > 0
    assert row.output_runs > 0
    # 100 units missing / product_qty 1 = 100 manufacturing runs needed.
    assert row.runs_needed == 100
    assert row.recommended_invention_runs > 0
    assert row.t2_bpc_owned == 0
    assert row.stockpile_pct == pytest.approx(0.0)


def test_invention_list_nets_off_owned_bpc_runs(invention_sde):
    storage.upsert_stock_target(T2_MODULE, "Damage Control II", 100.0)
    # A single owned T2 BPC copy with 50 runs remaining, sitting anywhere.
    storage.replace_assets("character_assets", [
        (1, T2_BLUEPRINT, 60003760, "Hangar", 1, 1, "Test Character"),
    ])
    storage.replace_blueprints("character_blueprints", [
        (1, T2_BLUEPRINT, 60003760, "Hangar", -2, 0, 0, 50),
    ])

    plan = engine.plan_production(_cfg())

    row = plan["invention_list"][0]
    assert row.t2_bpc_owned == 50
    assert row.stockpile_pct == pytest.approx(50.0)  # 50 owned / 100 target runs


def test_invention_list_is_empty_when_stock_target_is_fully_covered(invention_sde):
    storage.upsert_stock_target(T2_MODULE, "Damage Control II", 10.0)
    storage.replace_assets("character_assets", [
        (1, T2_MODULE, 60003760, "Hangar", 10, 0, "Test Character"),
    ])

    plan = engine.plan_production(_cfg())

    row = plan["invention_list"][0]
    assert row.runs_needed == 0
    assert row.bpcs_needed == 0
    assert row.recommended_invention_runs == 0


def test_invention_list_is_sorted_by_recommended_runs_desc(invention_sde):
    storage.upsert_stock_target(T2_MODULE, "Damage Control II", 100.0)
    plan = engine.plan_production(_cfg())
    runs = [row.recommended_invention_runs for row in plan["invention_list"]]
    assert runs == sorted(runs, reverse=True)


# ------------------------------------------ special-order invention preview


def test_special_order_tech_ii_has_invention_needs(invention_sde):
    order_id = actions.do_create_special_order(
        [{"type_id": T2_MODULE, "quantity": 100.0}])["order_id"]

    plan = actions.do_compute_special_order(order_id, cfg=_cfg())

    assert len(plan["invention_list"]) == 1
    row = plan["invention_list"][0]
    assert row.type_id == T2_MODULE
    assert row.t1_blueprint_type_id == T1_BLUEPRINT
    assert row.t1_blueprint_name == "Damage Control I Blueprint"
    assert row.runs_needed == 100
    assert row.bpcs_needed > 0
    assert row.recommended_invention_runs > 0
    assert row.decryptor in DECRYPTORS


def test_special_order_invention_uses_automatic_best_without_override(invention_sde):
    assert storage.load_selected_decryptors() == {}
    order_id = actions.do_create_special_order(
        [{"type_id": T2_MODULE, "quantity": 100.0}])["order_id"]

    plan = actions.do_compute_special_order(order_id, cfg=_cfg())
    row = plan["invention_list"][0]
    assert row.decryptor in DECRYPTORS

    _, _, _, chosen = engine._tech_ii_mods(
        T2_MODULE, T2_BLUEPRINT, 1, _cfg(), {}, JITA, {}, {})
    assert chosen is not None
    assert row.decryptor == chosen.decryptor


def test_special_order_invention_honors_decryptor_override(invention_sde):
    storage.upsert_selected_decryptor(T2_MODULE, "Process")
    order_id = actions.do_create_special_order(
        [{"type_id": T2_MODULE, "quantity": 100.0}])["order_id"]

    plan = actions.do_compute_special_order(order_id, cfg=_cfg())
    row = plan["invention_list"][0]
    assert row.decryptor == "Process"
    assert row.probability > 0
    assert row.output_runs > 0


def test_special_order_invention_honors_none_as_real_decryptor_choice(invention_sde):
    storage.upsert_selected_decryptor(T2_MODULE, "None")
    order_id = actions.do_create_special_order(
        [{"type_id": T2_MODULE, "quantity": 100.0}])["order_id"]

    plan = actions.do_compute_special_order(order_id, cfg=_cfg())
    row = plan["invention_list"][0]
    assert row.decryptor == "None"
    assert row.output_runs == BASE_RUNS


def test_special_order_invention_none_differs_from_automatic_best(invention_sde):
    auto_id = actions.do_create_special_order(
        [{"type_id": T2_MODULE, "quantity": 100.0}])["order_id"]
    auto_row = actions.do_compute_special_order(auto_id, cfg=_cfg())["invention_list"][0]

    storage.upsert_selected_decryptor(T2_MODULE, "None")
    none_id = actions.do_create_special_order(
        [{"type_id": T2_MODULE, "quantity": 100.0}])["order_id"]
    none_row = actions.do_compute_special_order(none_id, cfg=_cfg())["invention_list"][0]

    assert none_row.decryptor == "None"
    if auto_row.decryptor != "None":
        assert auto_row.probability != none_row.probability or auto_row.output_runs != none_row.output_runs
        assert auto_row.recommended_invention_runs != none_row.recommended_invention_runs


def test_special_order_invention_multiple_items(invention_sde):
    order_id = actions.do_create_special_order([
        {"type_id": T2_MODULE, "quantity": 100.0},
        {"type_id": T2_MODULE_B, "quantity": 10.0},
    ])["order_id"]

    plan = actions.do_compute_special_order(order_id, cfg=_cfg())
    by_type = {row.type_id: row for row in plan["invention_list"]}
    assert set(by_type) == {T2_MODULE, T2_MODULE_B}
    assert by_type[T2_MODULE].runs_needed == 100
    assert by_type[T2_MODULE_B].runs_needed == 10
    assert by_type[T2_MODULE].t1_blueprint_type_id == T1_BLUEPRINT
    assert by_type[T2_MODULE_B].t1_blueprint_type_id == T1_BLUEPRINT_B
    runs = [row.recommended_invention_runs for row in plan["invention_list"]]
    assert runs == sorted(runs, reverse=True)


def test_combined_special_order_invention_does_not_duplicate_products(invention_sde):
    """Two line items of the same T2 product (what a combined-order preview
    would pass after pooling, or duplicate rows before pooling) collapse to
    one invention row covering the summed quantity."""
    plan = engine.plan_special_order(
        [(T2_MODULE, "Damage Control II", 40.0),
         (T2_MODULE, "Damage Control II", 60.0)],
        _cfg(), net_against_stock=False,
    )
    assert len(plan["invention_list"]) == 1
    row = plan["invention_list"][0]
    assert row.type_id == T2_MODULE
    assert row.runs_needed == 100
    build_by_type = {r.type_id: r.job_runs for r in plan["build_list"]}
    assert build_by_type[T2_MODULE] == 100


def test_combined_orders_share_one_invention_preview(invention_sde):
    first = actions.do_create_special_order(
        [{"type_id": T2_MODULE, "quantity": 40.0}])["order_id"]
    second = actions.do_create_special_order(
        [{"type_id": T2_MODULE, "quantity": 60.0}])["order_id"]
    isolated_a = actions.do_compute_special_order(first, cfg=_cfg())
    isolated_b = actions.do_compute_special_order(second, cfg=_cfg())
    combined = actions.do_compute_combined_special_orders(
        [first, second], net_against_stock=False, cfg=_cfg())

    assert len(combined["invention_list"]) == 1
    assert combined["invention_list"][0].runs_needed == 100
    assert isolated_a["invention_list"][0].runs_needed == 40
    assert isolated_b["invention_list"][0].runs_needed == 60
    assert (isolated_a["invention_list"][0].recommended_invention_runs
            + isolated_b["invention_list"][0].recommended_invention_runs
            >= combined["invention_list"][0].recommended_invention_runs)


def test_special_order_invention_does_not_net_top_level_hangar_stock(invention_sde):
    storage.replace_assets("character_assets", [
        (1, T2_MODULE, 60003760, "Hangar", 50, 0, "Test Character"),
    ])
    order_id = actions.do_create_special_order(
        [{"type_id": T2_MODULE, "quantity": 100.0}], net_against_stock=True)["order_id"]

    plan = actions.do_compute_special_order(order_id, cfg=_cfg())
    row = plan["invention_list"][0]
    assert row.runs_needed == 100
