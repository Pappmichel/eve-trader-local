"""Tests for engine.plan_production - the stock-aware planner - and the
_build_margin dispatcher it restores. Same synthetic-SDE pattern as
test_production_engine.py/test_production_discovery.py: no network, home/jita
prices and the ESI adjusted-price/cost-index lookups monkeypatched.

BOM used below:
    FINISHED_A --(2x)--> COMPONENT --(10x)--> MINERAL (Input, buy only)
    FINISHED_B --(3x)-->/
Both FINISHED_A and FINISHED_B are configured stock targets, so COMPONENT's
demand from both must pool into one build_runs entry rather than two.
"""
from __future__ import annotations

import pytest

from eve_trader_local import storage
from eve_trader_local.goonmetrics_client import CurrentPrice
from eve_trader_local.production import engine
from eve_trader_local.production.config import ProductionConfig

MINERAL = 34            # Input - no blueprint at all
COMPONENT = 91201
FINISHED_A = 91202
FINISHED_B = 91203
COMPONENT_BP, FINISHED_A_BP, FINISHED_B_BP = 92201, 92202, 92203


def _seed():
    storage.replace_sde_data(
        types=[
            (MINERAL, 18, "Tritanium", 0.01, 1, 100, 0, 1, 1),
            (COMPONENT, 18, "Widget Component", 1.0, 1, 200, 0, 1, 1),
            (FINISHED_A, 18, "Finished Widget A", 1.0, 1, 200, 0, 1, 1),
            (FINISHED_B, 18, "Finished Widget B", 1.0, 1, 200, 0, 1, 1),
            (COMPONENT_BP, 9, "Widget Component Blueprint", 0.01, 1, None, 0, None, 1),
            (FINISHED_A_BP, 9, "Finished Widget A Blueprint", 0.01, 1, None, 0, None, 1),
            (FINISHED_B_BP, 9, "Finished Widget B Blueprint", 0.01, 1, None, 0, None, 1),
        ],
        groups=[(18, 4, "Mineral"), (9, 9, "Blueprint")],
        market_groups=[(100, None, "Manufacture & Research"), (200, None, "Ship Equipment")],
        blueprint_time=[],
        blueprint_materials=[
            (COMPONENT_BP, 1, MINERAL, 10),
            (FINISHED_A_BP, 1, COMPONENT, 2),
            (FINISHED_B_BP, 1, COMPONENT, 3),
        ],
        blueprint_products=[
            (COMPONENT_BP, 1, COMPONENT, 1),
            (FINISHED_A_BP, 1, FINISHED_A, 1),
            (FINISHED_B_BP, 1, FINISHED_B, 1),
        ],
        categories=[(4, "Material")],
    )


def _cfg(**overrides) -> ProductionConfig:
    cfg = ProductionConfig(jita_buy_broker_fee=0.0, haul_cost_per_m3=0.0,
                           facility_tax_rate=0.0, market_fees=0.0,
                           component_overbuild=0.0, min_margin=0.0)
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


# Only MINERAL has a home quote - COMPONENT/FINISHED_A/FINISHED_B are never
# listed at home, so buy_price() returns None for them and
# _buy_or_build_decision picks "Build" for anything with a real recipe (a
# None buy price can never beat a real build cost).
HOME = {MINERAL: CurrentPrice(type_id=MINERAL, updated="", buy=4.5, sell=5.0)}


@pytest.fixture
def planner_sde(db, monkeypatch):
    _seed()
    monkeypatch.setattr(engine.pricing, "home_prices", lambda type_ids, cfg=None, client=None: HOME)
    monkeypatch.setattr(engine.pricing, "jita_prices", lambda type_ids, client=None, trading_cfg=None: {})

    class _FakeESIClient:
        def __init__(self, *args, **kwargs):
            pass

        def get_adjusted_prices(self):
            return {}

    import eve_trader_local.esi_client as esi_client_module
    monkeypatch.setattr(esi_client_module, "ESIClient", _FakeESIClient)
    return db


def test_current_stock_nets_owned_assets_and_incoming_jobs(planner_sde):
    # 3 units physically owned, plus 2 more incoming from an active job
    # (runs=2 x product_qty=1) - 5 units of "as good as in stock" total.
    storage.replace_assets("character_assets", [
        (1, FINISHED_A, 60003760, "Hangar", 3, 0, "Test Character"),
    ])
    storage.replace_industry_jobs("character_industry_jobs", [
        (1, 1, FINISHED_A_BP, FINISHED_A, 2, 60003760, "active", "", "", 1, "Test Character"),
    ])
    storage.upsert_stock_target(FINISHED_A, "Finished Widget A", 10.0)
    storage.upsert_stock_target(FINISHED_B, "Finished Widget B", 5.0)

    plan = engine.plan_production(_cfg())

    inv_a = next(r for r in plan["inventory"] if r.type_id == FINISHED_A)
    assert inv_a.current_stock == pytest.approx(5.0)
    assert inv_a.total_missing == pytest.approx(5.0)  # 10 target - 5 on hand


def test_shared_component_demand_pools_across_stock_targets(planner_sde):
    """FINISHED_A (target 10) and FINISHED_B (target 5) both consume
    COMPONENT - its build_runs entry must reflect their combined demand, not
    two separate (and double-counted) entries."""
    storage.upsert_stock_target(FINISHED_A, "Finished Widget A", 10.0)
    storage.upsert_stock_target(FINISHED_B, "Finished Widget B", 5.0)

    plan = engine.plan_production(_cfg())

    build_by_type = {row.type_id: row for row in plan["build_list"]}
    assert build_by_type[FINISHED_A].job_runs == 10
    assert build_by_type[FINISHED_B].job_runs == 5
    # COMPONENT: ceil(2*0.9*10)=18 for A + ceil(3*0.9*5)=14 for B = 32 units -> 32 runs (product_qty=1).
    assert build_by_type[COMPONENT].job_runs == 32

    # MINERAL is never buildable - the pooled COMPONENT runs' own material
    # demand nets into the Buy List instead of a build entry.
    buy_by_type = {row.type_id: row for row in plan["buy_list"]}
    assert MINERAL in buy_by_type
    assert COMPONENT not in buy_by_type
    assert buy_by_type[MINERAL].quantity == pytest.approx(max(32, 10 * 0.9 * 32))


def test_do_plan_production_raises_without_stock_targets(planner_sde):
    from eve_trader_local.production.actions import do_plan_production
    from eve_trader_local.errors import ActionError

    with pytest.raises(ActionError):
        do_plan_production()


def test_build_margin_dispatches_home_vs_jita(planner_sde):
    cfg = _cfg(market_fees=0.0)
    home = {FINISHED_A: CurrentPrice(type_id=FINISHED_A, updated="", buy=90.0, sell=100.0)}
    jita = {FINISHED_A: CurrentPrice(type_id=FINISHED_A, updated="", buy=190.0, sell=200.0)}

    home_margin = engine._build_margin(FINISHED_A, 50.0, False, home, jita, cfg)
    jita_margin = engine._build_margin(FINISHED_A, 50.0, True, home, jita, cfg)

    assert home_margin == pytest.approx(engine.margin_home(FINISHED_A, 50.0, home, cfg))
    assert jita_margin == pytest.approx(engine.margin_jita(FINISHED_A, 50.0, jita, cfg))
    assert home_margin != jita_margin
