"""Tests for engine.invention_logistics/t1_bpc_invention_needs: needed vs.
available at cfg.invention_location_id, built from an already-computed
InventionNeedRow list (no live pricing needed here - just storage reads over
character/corp blueprints and assets).
"""
from __future__ import annotations

import pytest

from eve_trader_local import storage
from eve_trader_local.production import engine
from eve_trader_local.production.config import ProductionConfig
from eve_trader_local.production.constants import ANCIENT_RELIC_CATEGORY_ID, DECRYPTORS
from eve_trader_local.production.models import InventionNeedRow

T1_BLUEPRINT = 1002
T2_BLUEPRINT = 1001
DATACORE_A = 20410
DATACORE_B = 20424
RELIC = 1010
LOCATION = 60003760
OTHER_LOCATION = 60003761

ACCELERANT = DECRYPTORS["Accelerant"]


def _seed():
    storage.replace_sde_data(
        types=[
            (T1_BLUEPRINT, 9, "Damage Control I Blueprint", 0.01, 1, None, 0, None, 1),
            (T2_BLUEPRINT, 9, "Damage Control II Blueprint", 0.01, 1, None, 0, None, 1),
            (DATACORE_A, 333, "Datacore - Mechanical Engineering", 0.1, 1, 100, 0, None, 1),
            (DATACORE_B, 333, "Datacore - Molecular Engineering", 0.1, 1, 100, 0, None, 1),
            (RELIC, 34, "Intact Armor Nanobot", 10.0, 1, 500, 0, None, 1),
            (ACCELERANT.type_id, 314, "Accelerant Decryptor", 0.1, 1, 100, 0, None, 1),
        ],
        groups=[(9, 9, "Blueprint"), (333, 4, "Datacores"), (34, ANCIENT_RELIC_CATEGORY_ID, "Ancient Relics"),
                (314, 4, "Decryptors")],
        market_groups=[(100, None, "Manufacture & Research"), (500, None, "Ancient Relics")],
        blueprint_time=[],
        blueprint_materials=[(T1_BLUEPRINT, 8, DATACORE_A, 2), (T1_BLUEPRINT, 8, DATACORE_B, 2)],
        blueprint_products=[(T1_BLUEPRINT, 8, T2_BLUEPRINT, 10)],
        categories=[(9, "Blueprint"), (4, "Material"), (ANCIENT_RELIC_CATEGORY_ID, "Ancient Relics")],
    )


def _need(t1_blueprint_type_id=T1_BLUEPRINT, decryptor="Accelerant", runs=10) -> InventionNeedRow:
    return InventionNeedRow(
        type_id=2048, type_name="Damage Control II",
        t1_blueprint_type_id=t1_blueprint_type_id, t1_blueprint_name="Damage Control I Blueprint",
        decryptor=decryptor, probability=0.5, output_runs=1,
        runs_needed=10, bpcs_needed=10, recommended_invention_runs=runs,
    )


@pytest.fixture
def logistics_sde(db):
    _seed()
    return db


def test_invention_logistics_returns_empty_without_a_configured_location(logistics_sde):
    assert engine.invention_logistics([_need()], ProductionConfig(invention_location_id=None)) == []


def test_invention_logistics_sums_datacores_decryptor_and_bpc_runs(logistics_sde):
    cfg = ProductionConfig(invention_location_id=LOCATION)
    rows = {r.type_id: r for r in engine.invention_logistics([_need(runs=10)], cfg)}

    # 2 datacores x 10 attempts each.
    assert rows[DATACORE_A].needed == pytest.approx(20.0)
    assert rows[DATACORE_B].needed == pytest.approx(20.0)
    # One decryptor per attempt.
    assert rows[ACCELERANT.type_id].needed == pytest.approx(10.0)
    # One BPC run consumed per attempt, success or fail.
    assert rows[T1_BLUEPRINT].needed == pytest.approx(10.0)


def test_invention_logistics_t1_blueprint_uses_available_blueprint_copies(logistics_sde):
    storage.replace_assets("character_assets", [(1, T1_BLUEPRINT, LOCATION, "Hangar", 1, 1, "Test")])
    storage.replace_blueprints("character_blueprints", [(1, T1_BLUEPRINT, LOCATION, "Hangar", -2, 0, 0, 7)])
    cfg = ProductionConfig(invention_location_id=LOCATION)

    rows = {r.type_id: r for r in engine.invention_logistics([_need(runs=10)], cfg)}

    assert rows[T1_BLUEPRINT].available == pytest.approx(7.0)
    assert rows[T1_BLUEPRINT].missing == pytest.approx(3.0)


def test_invention_logistics_relic_uses_esi_stock_not_blueprint_copies(logistics_sde):
    """A Tech III relic is t1_blueprint_type_id's real value, but it's a
    plain item, never a blueprint copy - available_blueprint_copies would
    always return 0 for it."""
    storage.replace_assets("character_assets", [(1, RELIC, LOCATION, "Hangar", 4, 0, "Test")])
    cfg = ProductionConfig(invention_location_id=LOCATION)

    rows = {r.type_id: r for r in engine.invention_logistics([_need(t1_blueprint_type_id=RELIC, decryptor="None", runs=6)], cfg)}

    assert rows[RELIC].available == pytest.approx(4.0)
    assert rows[RELIC].missing == pytest.approx(2.0)


def test_invention_logistics_ignores_zero_recommended_runs(logistics_sde):
    cfg = ProductionConfig(invention_location_id=LOCATION)
    fully_covered = _need(runs=0)
    assert engine.invention_logistics([fully_covered], cfg) == []


def test_t1_bpc_invention_needs_reports_bpo_presence(logistics_sde):
    storage.replace_assets("character_assets", [
        (1, T1_BLUEPRINT, LOCATION, "Hangar", 1, 0, "Test"),
    ])
    storage.replace_blueprints("character_blueprints", [
        (1, T1_BLUEPRINT, LOCATION, "Hangar", -1, 10, 20, -1),  # an Original, not a copy
    ])
    cfg = ProductionConfig(invention_location_id=LOCATION)

    rows = engine.t1_bpc_invention_needs([_need(runs=10)], cfg)

    assert len(rows) == 1
    row = rows[0]
    assert row.needed == 10
    assert row.available == 0  # no copies, only the Original
    assert row.missing == 10
    assert row.bpo_present is True
    assert row.stockpile_pct == pytest.approx(0.0)


def test_t1_bpc_invention_needs_relic_never_reports_bpo_present(logistics_sde):
    storage.replace_assets("character_assets", [(1, RELIC, LOCATION, "Hangar", 4, 0, "Test")])
    cfg = ProductionConfig(invention_location_id=LOCATION)

    rows = engine.t1_bpc_invention_needs(
        [_need(t1_blueprint_type_id=RELIC, decryptor="None", runs=6)], cfg)

    assert rows[0].bpo_present is False
    assert rows[0].available == 4
