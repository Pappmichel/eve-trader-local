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


def test_manual_stock_adds_to_esi_derived_current_stock(planner_sde):
    """A genuinely separate signal from ESI-synced assets - see storage.py's
    manual_stock table comment."""
    storage.replace_assets("character_assets", [
        (1, FINISHED_A, 60003760, "Hangar", 3, 0, "Test Character"),
    ])
    storage.upsert_manual_stock(FINISHED_A, "Finished Widget A", 7.0)
    storage.upsert_stock_target(FINISHED_A, "Finished Widget A", 10.0)

    plan = engine.plan_production(_cfg())

    inv_a = next(r for r in plan["inventory"] if r.type_id == FINISHED_A)
    assert inv_a.current_stock == pytest.approx(10.0)  # 3 ESI + 7 manual
    assert inv_a.total_missing == pytest.approx(0.0)


def test_market_status_is_a_cheap_target_vs_stock_read(planner_sde):
    storage.upsert_stock_target(FINISHED_A, "Finished Widget A", 10.0, jita_target=True)
    storage.replace_assets("character_assets", [
        (1, FINISHED_A, 60003760, "Hangar", 4, 0, "Test Character"),
    ])

    rows = engine.market_status(_cfg())

    assert len(rows) == 1
    row = rows[0]
    assert row.type_id == FINISHED_A
    assert row.target == 10.0
    assert row.current_stock == pytest.approx(4.0)
    assert row.missing == pytest.approx(6.0)
    assert row.jita_target is True


def test_stock_value_prices_current_stock_at_home_sell(planner_sde):
    storage.upsert_stock_target(MINERAL, "Tritanium", 100.0)
    storage.replace_assets("character_assets", [
        (1, MINERAL, 60003760, "Hangar", 50, 0, "Test Character"),
    ])

    result = engine.stock_value(_cfg())

    assert result["priced_items"] == 1
    assert result["unpriced_items"] == 0
    assert result["total_value"] == pytest.approx(50 * HOME[MINERAL].sell)


def test_stock_value_excludes_unpriced_items_from_total(planner_sde):
    # FINISHED_A has no home/jita quote in this fixture's HOME/JITA maps.
    storage.upsert_stock_target(FINISHED_A, "Finished Widget A", 100.0)
    storage.replace_assets("character_assets", [
        (1, FINISHED_A, 60003760, "Hangar", 5, 0, "Test Character"),
    ])

    result = engine.stock_value(_cfg())

    assert result["priced_items"] == 0
    assert result["unpriced_items"] == 1
    assert result["total_value"] == 0.0


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


def test_plan_asset_optimized_splits_ready_now_from_blocked(planner_sde):
    """FINISHED_A needs 18 units of COMPONENT for its 10 runs (2 x 0.9 ME x
    10, same math test_shared_component_demand_pools_across_stock_targets
    checks for plan_production) - with only 9 physically on hand, exactly
    half of FINISHED_A's runs are startable right now, and the remaining
    COMPONENT shortfall (9 units) becomes its own job, itself entirely
    blocked since no MINERAL is on hand to build it."""
    storage.upsert_stock_target(FINISHED_A, "Finished Widget A", 10.0)
    storage.replace_assets("character_assets", [
        (1, COMPONENT, 60003760, "Hangar", 9, 0, "Test Character"),
    ])

    plan = engine.plan_asset_optimized(_cfg())

    jobs_by_type = {j.type_id: j for j in plan["jobs"]}
    finished_a = jobs_by_type[FINISHED_A]
    assert finished_a.job_runs == 10
    assert finished_a.runs_ready_now == 5
    assert finished_a.stock_coverage == pytest.approx(0.0)  # 0 FINISHED_A units on hand / target 10

    component = jobs_by_type[COMPONENT]
    assert component.job_runs == 9          # 18 needed - 9 on hand
    assert component.runs_ready_now == 0    # no MINERAL on hand to build any of it
    assert component.stock_coverage == pytest.approx(0.5)  # 9 available / 18 buffered demand

    # MINERAL has no blueprint at all - it never becomes a job here (this
    # planner has no buy_list, unlike plan_production).
    assert MINERAL not in jobs_by_type


def test_plan_asset_optimized_stock_on_hand_excludes_incoming_jobs(planner_sde):
    """The readiness split (_stock_on_hand) must not count an in-progress
    industry job's eventual output as startable right now, even though
    _current_stock (job *sizing*) correctly does - see _stock_on_hand's own
    docstring for the real bug this guards against."""
    storage.upsert_stock_target(FINISHED_A, "Finished Widget A", 10.0)
    # 18 units "as good as in stock" via an incoming job, but 0 physically on hand.
    storage.replace_industry_jobs("character_industry_jobs", [
        (1, 1, COMPONENT_BP, COMPONENT, 18, 60003760, "active", "", "", 1, "Test Character"),
    ])

    plan = engine.plan_asset_optimized(_cfg())

    jobs_by_type = {j.type_id: j for j in plan["jobs"]}
    # Sizing nets the full 18 incoming units against the 18 needed, so no
    # further COMPONENT job/shortfall exists at all...
    assert COMPONENT not in jobs_by_type
    # ...but readiness sees 0 units physically on hand, so nothing is ready now.
    assert jobs_by_type[FINISHED_A].runs_ready_now == 0
    assert jobs_by_type[FINISHED_A].job_runs == 10


def test_plan_asset_optimized_respects_min_margin_gate(planner_sde, monkeypatch):
    """Same margin gate as plan_production: an unprofitable stock target
    produces no job at all, matching the confirmed "the two Baulisten must
    never disagree on whether to build something" rule."""
    home = dict(HOME)
    home[FINISHED_A] = CurrentPrice(type_id=FINISHED_A, updated="", buy=1.0, sell=1.0)  # near-zero margin
    monkeypatch.setattr(engine.pricing, "home_prices", lambda type_ids, cfg=None, client=None: home)
    storage.upsert_stock_target(FINISHED_A, "Finished Widget A", 10.0)

    plan = engine.plan_asset_optimized(_cfg(min_margin=0.5))

    assert plan["jobs"] == []


def test_do_plan_asset_optimized_raises_without_stock_targets(planner_sde):
    from eve_trader_local.production.actions import do_plan_asset_optimized
    from eve_trader_local.errors import ActionError

    with pytest.raises(ActionError):
        do_plan_asset_optimized()


def test_build_margin_dispatches_home_vs_jita(planner_sde):
    cfg = _cfg(market_fees=0.0)
    home = {FINISHED_A: CurrentPrice(type_id=FINISHED_A, updated="", buy=90.0, sell=100.0)}
    jita = {FINISHED_A: CurrentPrice(type_id=FINISHED_A, updated="", buy=190.0, sell=200.0)}

    home_margin = engine._build_margin(FINISHED_A, 50.0, False, home, jita, cfg)
    jita_margin = engine._build_margin(FINISHED_A, 50.0, True, home, jita, cfg)

    assert home_margin == pytest.approx(engine.margin_home(FINISHED_A, 50.0, home, cfg))
    assert jita_margin == pytest.approx(engine.margin_jita(FINISHED_A, 50.0, jita, cfg))
    assert home_margin != jita_margin
