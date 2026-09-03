"""Tests for doctrine/engine.py - the orchestration wiring parser.py +
validation.py against real storage-backed SDE/fitting/contract data.
"""
from __future__ import annotations

from eve_trader_local import storage
from eve_trader_local.doctrine import actions as doctrine_actions
from eve_trader_local.doctrine import engine
from eve_trader_local.doctrine.models import ContractItemRow

RIFTER = 1
DAMAGE_CONTROL = 2
AUTOCANNON = 3
AMMO = 4
STRUCTURE_ID = 60003760


def _seed_sde():
    storage.replace_sde_data(
        types=[
            (RIFTER, 100, "Rifter", 27.0, 1, None, None, None, 1),
            (DAMAGE_CONTROL, 200, "Damage Control II", 5.0, 1, 500, 5, 2, 1),
            (AUTOCANNON, 300, "200mm AutoCannon II", 5.0, 1, 500, 5, 2, 1),
            (AMMO, 400, "Antimatter Charge S", 0.01, 1, 500, None, None, 1),
        ],
        groups=[
            (100, 6, "Frigate"),
            (200, 7, "Module"),
            (300, 7, "Module"),
            (400, 8, "Charge"),
        ],
        market_groups=[],
        blueprint_time=[],
        blueprint_materials=[],
        blueprint_products=[],
        categories=[(6, "Ship"), (7, "Module"), (8, "Charge")],
        type_slots=[(DAMAGE_CONTROL, "low"), (AUTOCANNON, "high")],
    )


_RAW_EFT = (
    "[Rifter, C-J Doctrine Fit]\n"
    "Damage Control II\n"
    "200mm AutoCannon II, Antimatter Charge S\n"
)


def test_parse_fitting_text_wires_real_sde(db):
    _seed_sde()
    parsed = engine.parse_fitting_text(_RAW_EFT)
    assert parsed.hull_type_id == RIFTER
    assert parsed.hull_name == "Rifter"
    assert parsed.fit_name == "C-J Doctrine Fit"
    assert [(i.type_id, i.slot_section) for i in parsed.items] == [
        (DAMAGE_CONTROL, "low"), (AUTOCANNON, "high"), (AMMO, "charge"),
    ]


def test_parse_bay_items_text_wires_real_sde(db):
    _seed_sde()
    items, issues = engine.parse_bay_items_text("Antimatter Charge S x50\n", "fuelbay")
    assert issues == []
    assert items[0].type_id == AMMO and items[0].slot_section == "fuelbay" and items[0].quantity == 50


def _add_fitting(stockpile_target=0, contract_target=0):
    _seed_sde()
    result = doctrine_actions.do_create_doctrine("Rifter Fleet")
    doctrine_id = result["doctrine_id"]
    fitting_result = doctrine_actions.do_add_fitting(
        doctrine_id, _RAW_EFT, contract_target=contract_target, stockpile_target=stockpile_target)
    return doctrine_id, fitting_result["fitting"]["fitting_id"]


def test_load_match_candidates_builds_soll_from_stored_fitting(db):
    _doctrine_id, fitting_id = _add_fitting()
    candidates = engine.load_match_candidates()
    assert len(candidates) == 1
    c = candidates[0]
    assert c.fitting.fitting_id == fitting_id
    assert c.exact_soll == {DAMAGE_CONTROL: 1.0, AUTOCANNON: 1.0}
    assert c.consume_soll == {AMMO: 1.0}


def test_match_and_validate_contract_exact_match_is_valid(db):
    _doctrine_id, fitting_id = _add_fitting()
    candidates = engine.load_match_candidates()
    contract_items = [
        ContractItemRow(1, 1, RIFTER, 1, True, True),
        ContractItemRow(1, 2, DAMAGE_CONTROL, 1, True, False),
        ContractItemRow(1, 3, AUTOCANNON, 1, True, False),
        ContractItemRow(1, 4, AMMO, 1, True, False),
    ]
    matched_fitting_id, score, deviations, status = engine.match_and_validate_contract(
        1, "Rifter Fleet Fit", contract_items, candidates)
    assert matched_fitting_id == fitting_id
    assert status == "valid"
    assert deviations == []
    assert score > 0


def test_match_and_validate_contract_no_hull_present(db):
    _doctrine_id, _fitting_id = _add_fitting()
    candidates = engine.load_match_candidates()
    contract_items = [ContractItemRow(2, 1, DAMAGE_CONTROL, 1, True, False)]
    matched_fitting_id, _score, deviations, status = engine.match_and_validate_contract(
        2, None, contract_items, candidates)
    assert matched_fitting_id is None
    assert status == engine.NO_HULL_MATCH
    assert deviations == []


def test_match_and_validate_contract_missing_ammo_is_invalid(db):
    _doctrine_id, fitting_id = _add_fitting()
    candidates = engine.load_match_candidates()
    # Both exact-slot modules present (clears the match threshold on their
    # own), ammo (a "consume" position) entirely missing.
    contract_items = [
        ContractItemRow(3, 1, RIFTER, 1, True, True),
        ContractItemRow(3, 2, DAMAGE_CONTROL, 1, True, False),
        ContractItemRow(3, 3, AUTOCANNON, 1, True, False),
    ]
    matched_fitting_id, _score, deviations, status = engine.match_and_validate_contract(
        3, None, contract_items, candidates)
    assert matched_fitting_id == fitting_id
    assert status == "invalid"
    kinds = {(d.type_id, d.kind) for d in deviations}
    assert (AMMO, "missing") in kinds


def test_stockpile_rows_for_doctrine_no_assets_synced_yet(db):
    _add_fitting(stockpile_target=1)
    rows, assets_available = engine.stockpile_rows_for_doctrine()
    assert rows == []
    assert assets_available is False


def test_stockpile_rows_for_doctrine_computes_shortfall_against_synced_assets(db):
    doctrine_id, _fitting_id = _add_fitting(stockpile_target=2)
    # Simulate a completed Doctrine asset sync: own 1 of the 2 needed
    # Damage Control IIs, none of the autocannons/ammo, at the configured
    # structure.
    storage.replace_assets("doctrine_character_assets", [
        (900001, DAMAGE_CONTROL, STRUCTURE_ID, "Hangar", 1, 0, "Alt"),
    ])
    from eve_trader_local.config import TRADING_CONFIG
    TRADING_CONFIG.structure_id = STRUCTURE_ID
    try:
        rows, assets_available = engine.stockpile_rows_for_doctrine(doctrine_id)
    finally:
        TRADING_CONFIG.structure_id = None
    assert assets_available is True
    by_type = {r.type_id: r for r in rows}
    # 2 sets need 2 Damage Control IIs; 1 is available -> shortfall 1.
    assert by_type[DAMAGE_CONTROL].required_total == 2
    assert by_type[DAMAGE_CONTROL].available == 1
    assert by_type[DAMAGE_CONTROL].shortfall == 1
    # Autocannon entirely missing -> full shortfall, no assets at all.
    assert by_type[AUTOCANNON].available == 0
    assert by_type[AUTOCANNON].shortfall == 2


def test_aggregate_stockpile_rows_sums_across_fittings(db):
    from eve_trader_local.doctrine.models import StockpileRow
    rows = [
        StockpileRow("f1", "Fit A", "d1", "Doctrine", DAMAGE_CONTROL, "Damage Control II", "low",
                     required_total=2, available=1, shortfall=1, severity="critical"),
        StockpileRow("f2", "Fit B", "d1", "Doctrine", DAMAGE_CONTROL, "Damage Control II", "low",
                     required_total=3, available=1, shortfall=2, severity="tolerable"),
    ]
    aggregated = engine.aggregate_stockpile_rows(rows)
    assert len(aggregated) == 1
    row = aggregated[0]
    assert row.required_total == 5
    assert row.available == 2
    assert row.shortfall == 3
    assert row.severity == "critical"  # worst of the two
    assert row.fitting_count == 2


def test_fitting_status_and_doctrine_status_roll_up(db):
    doctrine_id, fitting_id = _add_fitting(contract_target=1)
    # No contracts synced yet, and stockpile has never been synced -> both
    # ampels should be gray (unknown), not red.
    fitting, _items = engine.load_fitting_with_items(fitting_id)
    status = engine.fitting_status(fitting)
    assert status.contract_status == "gray"
    assert status.stockpile_status == "gray"

    doctrine_row = storage.get_doctrine(doctrine_id)
    d_status = engine.doctrine_status(doctrine_row)
    assert d_status.doctrine_id == doctrine_id
    assert len(d_status.fittings) == 1
    assert d_status.overall == "gray"
