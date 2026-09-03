"""Tests for doctrine/parser.py's EFT-format parser.

Two layers, matching what the parent eve-trader repo's own
test_doctrine_parser.py covers: fast unit tests against fake in-memory
resolvers (no SDE/storage involved at all - the parser takes its resolvers
as plain injected callables, see parser.py's own docstring), plus one
integration test proving the same parser works against a real
storage-backed SDE cache (storage.replace_sde_data, storage.
resolve_sde_type_by_name/get_type_slot/list_hull_type_names) - the wiring a
future doctrine/engine.py will do for real.
"""
from __future__ import annotations

import pytest

from eve_trader_local import storage
from eve_trader_local.doctrine.parser import FittingParseError, ResolvedType, parse_bay_items, parse_fitting

# Fake SDE: name (lowercase) -> ResolvedType. Category IDs match
# doctrine/constants.py: 6=Ship, 7=Module (arbitrary "not special" stand-in
# here), 8=Charge, 18=Drone.
_SHIP = 6
_MODULE = 7
_CHARGE = 8
_DRONE = 18

_TYPES = {
    "rifter": ResolvedType(1, 100, _SHIP, None, None, "Rifter"),
    "damage control ii": ResolvedType(2, 200, _MODULE, 2, 5, "Damage Control II"),
    "200mm autocannon ii": ResolvedType(3, 300, _MODULE, 2, 5, "200mm AutoCannon II"),
    "antimatter charge s": ResolvedType(4, 400, _CHARGE, None, None, "Antimatter Charge S"),
    "nanofiber internal structure ii": ResolvedType(5, 500, _MODULE, 2, 5, "Nanofiber Internal Structure II"),
    "small ancillary current router i": ResolvedType(6, 600, _MODULE, None, None, "Small Ancillary Current Router I"),
    "warrior ii": ResolvedType(7, 700, _DRONE, 2, 5, "Warrior II"),
    "tritanium": ResolvedType(8, 800, 4, None, None, "Tritanium"),  # category 4 = Material, no slot
    "liquid ozone": ResolvedType(9, 900, 4, None, None, "Liquid Ozone"),
}
_SLOTS = {2: "low", 3: "high", 5: "low", 6: "rig"}


def _resolve_name(name: str):
    return _TYPES.get(name.strip().lower())


def _resolve_slot(type_id: int):
    return _SLOTS.get(type_id)


def _parse(text, candidates=()):
    return parse_fitting(text, _resolve_name, _resolve_slot, hull_name_candidates=candidates)


# --------------------------------------------------------------- hard failures
def test_missing_header_is_hard_error():
    with pytest.raises(FittingParseError):
        _parse("just some text\nno header here\n")


def test_header_without_comma_is_hard_error():
    with pytest.raises(FittingParseError):
        _parse("[Rifter]\nDamage Control II\n")


def test_unresolvable_hull_is_hard_error_with_suggestion():
    with pytest.raises(FittingParseError, match="Rifer"):
        _parse("[Rifer, Typo Fit]\n", candidates=["Rifter"])


def test_hull_wrong_category_is_hard_error():
    with pytest.raises(FittingParseError):
        _parse("[Tritanium, Not A Ship]\n")


def test_empty_ship_name_is_hard_error():
    with pytest.raises(FittingParseError):
        _parse("[, Fit]\n")


# --------------------------------------------------------------- basic parsing
def test_hull_only_fitting_is_valid_with_no_items_or_issues():
    result = _parse("[Rifter, Empty Hull]\n")
    assert result.hull_type_id == 1
    assert result.items == []
    assert result.issues == []


def test_leading_blank_lines_before_header_are_tolerated():
    result = _parse("\n\n[Rifter, Padded]\nDamage Control II\n")
    assert result.hull_type_id == 1
    assert len(result.items) == 1


def test_module_classified_by_sde_slot():
    result = _parse("[Rifter, Fit]\nDamage Control II\n")
    assert len(result.items) == 1
    item = result.items[0]
    assert item.type_id == 2
    assert item.slot_section == "low"
    assert item.quantity == 1
    assert item.is_offline is False


def test_offline_suffix_sets_flag_and_still_resolves():
    result = _parse("[Rifter, Fit]\nDamage Control II /offline\n")
    assert result.items[0].is_offline is True
    assert result.items[0].type_id == 2


def test_offline_suffix_case_insensitive():
    result = _parse("[Rifter, Fit]\nDamage Control II /OFFLINE\n")
    assert result.items[0].is_offline is True


def test_empty_marker_produces_no_item_and_no_issue():
    result = _parse("[Rifter, Fit]\n[Empty Low slot]\nDamage Control II\n")
    assert len(result.items) == 1
    assert result.issues == []


# --------------------------------------------------------------- comma / charges
def test_module_charge_comma_line_produces_two_items():
    result = _parse("[Rifter, Fit]\n200mm AutoCannon II, Antimatter Charge S\n")
    assert len(result.items) == 2
    module, charge = result.items
    assert module.type_id == 3 and module.slot_section == "high"
    assert charge.type_id == 4 and charge.slot_section == "charge"
    assert result.issues == []


def test_bare_charge_line_without_module_gets_charge_section_and_issue():
    result = _parse("[Rifter, Fit]\nAntimatter Charge S\n")
    assert len(result.items) == 1
    assert result.items[0].slot_section == "charge"
    assert result.items[0].type_id == 4
    assert len(result.issues) == 1
    assert result.issues[0].issue_kind == "unknown_section"


def test_ambiguous_comma_split_produces_ambiguous_split_issue_no_item():
    # Both "Damage Control II" and "Nanofiber Internal Structure II" resolve,
    # but neither combination pairs a module with a real charge - genuinely
    # ambiguous (Phase 3 A.5 case 3), not "resolves nowhere".
    result = _parse("[Rifter, Fit]\nDamage Control II, Nanofiber Internal Structure II\n")
    assert result.items == []
    assert len(result.issues) == 1
    assert result.issues[0].issue_kind == "ambiguous_split"


# --------------------------------------------------------------- quantity / drones / cargo
def test_quantity_suffix_on_unfittable_item_is_cargo():
    result = _parse("[Rifter, Fit]\nTritanium x100\n")
    assert len(result.items) == 1
    item = result.items[0]
    assert item.type_id == 8
    assert item.slot_section == "cargo"
    assert item.quantity == 100
    assert result.issues == []


def test_drone_category_with_quantity_is_drone_section():
    result = _parse("[Rifter, Fit]\nWarrior II x5\n")
    assert len(result.items) == 1
    assert result.items[0].slot_section == "drone"
    assert result.items[0].quantity == 5


def test_quantity_suffix_on_fittable_module_flags_issue():
    result = _parse("[Rifter, Fit]\nDamage Control II x2\n")
    assert len(result.items) == 1
    assert result.items[0].slot_section == "cargo"
    assert len(result.issues) == 1
    assert result.issues[0].issue_kind == "unknown_section"


# --------------------------------------------------------------- malformed / unresolved input
def test_unresolvable_item_name_produces_issue_no_item():
    result = _parse("[Rifter, Fit]\nSome Made Up Module\n")
    assert result.items == []
    assert len(result.issues) == 1
    assert result.issues[0].issue_kind == "unresolved_name"


def test_unresolvable_item_name_with_close_match_suggests_it():
    result = _parse("[Rifter, Fit]\nDamage Control I\n", candidates=["Damage Control II"])
    assert result.issues[0].issue_kind == "unresolved_name"
    assert "Damage Control II" in result.issues[0].message


def test_stray_bracket_line_is_malformed():
    result = _parse("[Rifter, Fit]\n[Not An Empty Marker]\n")
    assert result.items == []
    assert len(result.issues) == 1
    assert result.issues[0].issue_kind == "malformed"


def test_line_that_becomes_empty_after_suffix_removal_is_malformed():
    result = _parse("[Rifter, Fit]\n /offline\n")
    assert result.items == []
    assert len(result.issues) == 1
    assert result.issues[0].issue_kind == "malformed"


# --------------------------------------------------------------- position-vs-SDE gegenprobe
def test_marker_position_mismatch_flags_unknown_section_issue():
    # has_markers only turns on (and the position-vs-SDE gegenprobe only
    # fires at all) once the export encodes position via at least one
    # [Empty ... slot] marker (A.4). Block 0 (the marker's own block) is
    # expected "low"; block 1 is expected "med" per _EFT_SECTION_ORDER, but
    # the 200mm AutoCannon II's real SDE slot is "high" - a genuine mismatch.
    text = "[Rifter, Fit]\n[Empty Low slot]\n\n200mm AutoCannon II\n"
    result = _parse(text)
    assert result.items[0].slot_section == "high"
    assert any(i.issue_kind == "unknown_section" and "Expected section 'med'" in i.message
               for i in result.issues)


# --------------------------------------------------------------- CRLF / whitespace normalization
def test_crlf_and_tabs_are_normalized_without_shifting_line_numbers():
    raw = "[Rifter,\tFit]\r\nDamage\t Control  II\r\n"
    result = _parse(raw)
    assert result.fit_name == "Fit"
    assert len(result.items) == 1
    assert result.items[0].line_no == 2


# --------------------------------------------------------------- fuel bay / ship maintenance bay (issue #18)
def test_parse_bay_items_resolves_plain_and_quantity_lines():
    items, issues = parse_bay_items("Liquid Ozone x500\nTritanium\n", _resolve_name, "fuelbay")
    assert len(items) == 2
    assert items[0].type_id == 9 and items[0].slot_section == "fuelbay" and items[0].quantity == 500
    assert items[1].type_id == 8 and items[1].quantity == 1
    assert issues == []


def test_parse_bay_items_unresolvable_line_produces_issue():
    items, issues = parse_bay_items("Not A Real Item\n", _resolve_name, "shipmaintenancebay")
    assert items == []
    assert len(issues) == 1
    assert issues[0].issue_kind == "unresolved_name"


def test_parse_bay_items_line_no_start_continues_numbering():
    items, _ = parse_bay_items("Tritanium\n", _resolve_name, "fuelbay", line_no_start=42)
    assert items[0].line_no == 42


# --------------------------------------------------------------- real, storage-backed SDE cache
def _seed_sde():
    storage.replace_sde_data(
        types=[
            (1, 100, "Rifter", 27.0, 1, None, None, None, 1),
            (2, 200, "Damage Control II", 5.0, 1, 500, 5, 2, 1),
            (3, 300, "200mm AutoCannon II", 5.0, 1, 500, 5, 2, 1),
            (4, 400, "Antimatter Charge S", 0.01, 1, 500, None, None, 1),
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
        type_slots=[(2, "low"), (3, "high")],
    )


def _storage_resolve_name(name: str):
    row = storage.resolve_sde_type_by_name(name)
    if row is None:
        return None
    type_id, group_id, category_id, meta_group_id, meta_level, type_name = row
    return ResolvedType(type_id=type_id, group_id=group_id, category_id=category_id,
                         meta_group_id=meta_group_id, meta_level=meta_level, type_name=type_name)


def test_parse_fitting_against_real_sde_cache(db):
    _seed_sde()
    raw = (
        "[Rifter, C-J Doctrine Fit]\n"
        "Damage Control II\n"
        "200mm AutoCannon II, Antimatter Charge S\n"
    )
    result = parse_fitting(raw, _storage_resolve_name, storage.get_type_slot,
                            hull_name_candidates=storage.list_hull_type_names())
    assert result.hull_type_id == 1
    assert result.hull_name == "Rifter"
    assert result.fit_name == "C-J Doctrine Fit"
    assert [(i.type_id, i.slot_section) for i in result.items] == [
        (2, "low"), (3, "high"), (4, "charge"),
    ]
    assert result.issues == []


def test_parse_fitting_unresolvable_hull_against_real_sde_cache(db):
    _seed_sde()
    with pytest.raises(FittingParseError, match="Rifter"):
        parse_fitting("[Rifte, Typo]\n", _storage_resolve_name, storage.get_type_slot,
                       hull_name_candidates=storage.list_hull_type_names())


def test_resolve_sde_type_by_name_is_exact_and_case_insensitive(db):
    _seed_sde()
    assert storage.resolve_sde_type_by_name("RIFTER") == (1, 100, 6, None, None, "Rifter")
    assert storage.resolve_sde_type_by_name("Rift") is None  # no substring/fuzzy match


def test_list_hull_type_names_only_returns_ship_and_structure_categories(db):
    _seed_sde()
    assert storage.list_hull_type_names() == ["Rifter"]


def test_get_type_slot_returns_none_for_unfittable_type(db):
    _seed_sde()
    assert storage.get_type_slot(4) is None
    assert storage.get_type_slot(2) == "low"
