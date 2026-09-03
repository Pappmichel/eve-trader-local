"""Tests for plan_production's invention-needs list (InventionNeedRow) - the
part of the stock-aware planner that says how many invention attempts to
queue for a Tech II stock target, using storage.available_blueprint_copies/
get_blueprint_time (both newly ported alongside this feature).

Same synthetic-SDE/no-network pattern as test_production_invention.py, whose
Tech II chain (T1 blueprint invents T2 blueprint, which manufactures a T2
module) this reuses almost verbatim - just seeded as a stock target here
instead of called through invention.py directly.
"""
from __future__ import annotations

import pytest

from eve_trader_local import storage
from eve_trader_local.goonmetrics_client import CurrentPrice
from eve_trader_local.production import engine
from eve_trader_local.production.config import ProductionConfig
from eve_trader_local.production.constants import DECRYPTORS

T1_BLUEPRINT = 1002
T2_BLUEPRINT = 1001
T2_MODULE = 2048
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
            (T1_BLUEPRINT, 9, "Damage Control I Blueprint", 0.01, 1, None, 0, None, 1),
            (T2_BLUEPRINT, 9, "Damage Control II Blueprint", 0.01, 1, None, 0, None, 1),
        ] + [(d.type_id, 314, name, 0.1, 1, 100, 0, None, 1)
             for name, d in DECRYPTORS.items() if d.type_id],
        groups=[(18, 4, "Mineral"), (60, 7, "Damage Control"), (9, 9, "Blueprint"),
                (333, 4, "Datacores"), (314, 4, "Decryptors")],
        market_groups=[(100, None, "Manufacture & Research"), (200, None, "Ship Equipment")],
        blueprint_time=[(T1_BLUEPRINT, 8, 3600.0)],
        blueprint_materials=[
            (T1_BLUEPRINT, 8, DATACORE_A, 2),
            (T1_BLUEPRINT, 8, DATACORE_B, 2),
            (T2_BLUEPRINT, 1, TRITANIUM, 1000),
            (T2_BLUEPRINT, 1, MORPHITE, 1),
        ],
        blueprint_products=[
            (T2_BLUEPRINT, 1, T2_MODULE, 1),
            (T1_BLUEPRINT, 8, T2_BLUEPRINT, BASE_RUNS),
        ],
        invention_probability=[(T1_BLUEPRINT, T2_BLUEPRINT, BASE_PROBABILITY)],
        categories=[(4, "Material"), (7, "Module"), (9, "Blueprint")],
    )


JITA = {
    DATACORE_A: CurrentPrice(type_id=DATACORE_A, updated="", buy=0.0, sell=1000.0),
    DATACORE_B: CurrentPrice(type_id=DATACORE_B, updated="", buy=0.0, sell=2000.0),
    TRITANIUM: CurrentPrice(type_id=TRITANIUM, updated="", buy=0.0, sell=5.0),
    MORPHITE: CurrentPrice(type_id=MORPHITE, updated="", buy=0.0, sell=10000.0),
}


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
