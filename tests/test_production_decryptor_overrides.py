"""Selected-decryptor overrides: persist a named decryptor for a Tech II/III
*product*, feed it through actions into engine._tech_ii_mods / _unit_cost.

Mirrors the parent repo's selected_decryptors table and
do_set/clear_selected_decryptor, minus tenant_id. "None" is a real
DECRYPTORS key (invent without a decryptor); deleting the row returns to
automatic Best. Reuses the synthetic T2 tree from test_production_engine
so auto vs Process vs Augmentation is the same economic fixture already
asserted there.
"""
from __future__ import annotations

import pytest

from eve_trader_local import storage
from eve_trader_local.errors import ActionError
from eve_trader_local.production import actions as production_actions
from eve_trader_local.production import engine
from eve_trader_local.production.constants import DECRYPTORS

from test_production_engine import (
    ADJUSTED,
    COST_INDICES,
    HOME,
    T2_BP,
    T2_WIDGET,
    _cfg,
    _seed_build_tree,
)


@pytest.fixture
def tree(db):
    _seed_build_tree()
    return db


def _mods(selected: dict[int, str]):
    return engine._tech_ii_mods(T2_WIDGET, T2_BP, 1, _cfg(), HOME, {}, selected, {})


def _build_cost(selected: dict[int, str]) -> float:
    _, build_cost, _buy = engine.unit_cost_detail(
        T2_WIDGET, _cfg(), HOME, {}, {}, selected, {}, COST_INDICES, ADJUSTED)
    assert build_cost is not None
    return build_cost


# -------------------------------------------------------------- storage
def test_selected_decryptors_storage_round_trip(db):
    assert storage.load_selected_decryptors() == {}
    storage.upsert_selected_decryptor(T2_WIDGET, "Process")
    assert storage.load_selected_decryptors() == {T2_WIDGET: "Process"}
    storage.upsert_selected_decryptor(T2_WIDGET, "Accelerant")
    assert storage.load_selected_decryptors() == {T2_WIDGET: "Accelerant"}
    storage.delete_selected_decryptor(T2_WIDGET)
    assert storage.load_selected_decryptors() == {}


def test_list_selected_decryptors_joins_type_name(tree):
    storage.upsert_selected_decryptor(T2_WIDGET, "Process")
    assert storage.list_selected_decryptors() == [
        (T2_WIDGET, "Test T2 Widget", "Process")]


# --------------------------------------------------------------- actions
def test_no_selection_uses_automatic_best_decryptor(tree):
    """Empty table → same Augmentation choice as passing {} into _tech_ii_mods."""
    assert storage.load_selected_decryptors() == {}
    _material_mult, _time_mult, decryptor, chosen = _mods(storage.load_selected_decryptors())
    assert decryptor == "Augmentation"
    assert chosen is not None and chosen.output_runs == 10


def test_do_set_selected_decryptor_persists_and_overrides(tree):
    result = production_actions.do_set_selected_decryptor("Test T2 Widget", "Process")
    assert result == {
        "type_id": T2_WIDGET, "type_name": "Test T2 Widget", "decryptor": "Process"}
    assert production_actions.do_list_selected_decryptors()["rows"] == [
        (T2_WIDGET, "Test T2 Widget", "Process")]

    material_mult, _time_mult, decryptor, _chosen = _mods(storage.load_selected_decryptors())
    assert decryptor == "Process"
    assert material_mult == pytest.approx(1 - 5 / 100)


def test_do_set_selected_decryptor_overwrites_existing(tree):
    production_actions.do_set_selected_decryptor(str(T2_WIDGET), "Process")
    result = production_actions.do_set_selected_decryptor(str(T2_WIDGET), "Accelerant")
    assert result["decryptor"] == "Accelerant"
    assert storage.load_selected_decryptors() == {T2_WIDGET: "Accelerant"}

    _material_mult, _time_mult, decryptor, _chosen = _mods(storage.load_selected_decryptors())
    assert decryptor == "Accelerant"
    assert DECRYPTORS["Accelerant"].me_bonus == 4


def test_do_clear_selected_decryptor_returns_to_automatic(tree):
    production_actions.do_set_selected_decryptor("Test T2 Widget", "Process")
    cleared = production_actions.do_clear_selected_decryptor("Test T2 Widget")
    assert cleared == {
        "type_id": T2_WIDGET, "type_name": "Test T2 Widget", "decryptor": "Best"}
    assert production_actions.do_list_selected_decryptors()["rows"] == []

    _material_mult, _time_mult, decryptor, _chosen = _mods(storage.load_selected_decryptors())
    assert decryptor == "Augmentation"


def test_none_decryptor_is_a_real_choice_not_a_clear(tree):
    result = production_actions.do_set_selected_decryptor("Test T2 Widget", "None")
    assert result["decryptor"] == "None"
    assert storage.load_selected_decryptors() == {T2_WIDGET: "None"}
    _material_mult, _time_mult, decryptor, _chosen = _mods(storage.load_selected_decryptors())
    assert decryptor == "None"


def test_do_set_selected_decryptor_rejects_unknown_decryptor(tree):
    with pytest.raises(ActionError, match="Unknown decryptor"):
        production_actions.do_set_selected_decryptor("Test T2 Widget", "Attenuation")
    assert storage.load_selected_decryptors() == {}


def test_do_set_selected_decryptor_rejects_unknown_item(tree):
    with pytest.raises(ActionError, match="No type found"):
        production_actions.do_set_selected_decryptor("Not A Real Item", "Process")


def test_set_and_clear_invalidate_discover_cache(tree, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        production_actions.engine, "invalidate_discover_cache", lambda: calls.append("x"))
    production_actions.do_set_selected_decryptor("Test T2 Widget", "Process")
    production_actions.do_clear_selected_decryptor("Test T2 Widget")
    assert calls == ["x", "x"]


# ------------------------------------ effect on production / invention math
def test_override_changes_unit_build_cost(tree):
    auto_cost = _build_cost({})
    production_actions.do_set_selected_decryptor("Test T2 Widget", "Process")
    process_cost = _build_cost(storage.load_selected_decryptors())
    # Process ME5 uses fewer minerals than auto Augmentation ME0.
    assert process_cost < auto_cost

    production_actions.do_clear_selected_decryptor("Test T2 Widget")
    assert _build_cost(storage.load_selected_decryptors()) == pytest.approx(auto_cost)


def test_fallback_applies_manual_decryptor_me_without_invention_recipe(tree, monkeypatch):
    monkeypatch.setattr(storage, "find_invention_recipe_candidates_by_product_type_id",
                        lambda blueprint_id: ())
    material_mult, time_mult, decryptor_name, chosen = engine._tech_ii_mods(
        T2_WIDGET, T2_BP, 1, _cfg(), HOME, {}, {T2_WIDGET: "Accelerant"}, {})
    assert round(material_mult, 4) == 0.96
    assert round(time_mult, 4) == 0.86
    assert decryptor_name == "Accelerant"
    assert chosen is None
