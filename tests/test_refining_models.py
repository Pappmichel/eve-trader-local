"""Sanity tests for refining/models.py's plain dataclasses - same level of
test other pure-dataclass ports in this repo get (see
tests/test_doctrine_models.py). Worth having here specifically because
OreOption's yield_per_portion is a mutable-default dict field - the classic
dataclass footgun field(default_factory=...) guards against."""
from __future__ import annotations

from eve_trader_local.refining.models import (
    MineralOption,
    OreCandidate,
    OreOption,
    OreShortlistItem,
)


def test_ore_shortlist_item_defaults_to_active():
    item = OreShortlistItem(item_id=1230, item="Veldspar", family="Veldspar", is_ice=False)
    assert item.active is True


def test_ore_candidate_fields():
    c = OreCandidate(type_id=1230, item="Veldspar", family="Veldspar", is_ice=False, volume_m3=0.1)
    assert c.is_ice is False
    assert c.volume_m3 == 0.1


def test_ore_option_yield_per_portion_defaults_empty_and_not_shared():
    a = OreOption(type_id=1, item="A", family="A", is_ice=False, volume_m3=0.1,
                  portion_size=100, landed_cost_per_unit=1.0)
    b = OreOption(type_id=2, item="B", family="B", is_ice=False, volume_m3=0.1,
                  portion_size=100, landed_cost_per_unit=1.0)
    assert a.yield_per_portion == {}
    a.yield_per_portion[34] = 415
    assert b.yield_per_portion == {}  # no shared mutable default


def test_ore_option_landed_cost_per_portion():
    o = OreOption(type_id=1, item="A", family="A", is_ice=False, volume_m3=0.1,
                  portion_size=100, landed_cost_per_unit=2.5)
    assert o.landed_cost_per_portion == 250.0


def test_mineral_option_defaults_to_no_source():
    m = MineralOption(type_id=34, name="Tritanium", landed_cost_per_unit=None)
    assert m.source is None
