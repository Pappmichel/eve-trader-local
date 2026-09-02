from __future__ import annotations

import pytest
import yaml

from eve_trader_local import storage
from eve_trader_local.config import validate_config_overrides
from eve_trader_local.errors import ConfigError
from eve_trader_local.production import config as production_config
from eve_trader_local.production.config import (
    ProductionConfig,
    load_production_config,
    save_config_overrides,
    validate_production_overrides,
)


# ------------------------------------------------- generic checks still apply
@pytest.mark.parametrize("overrides", [
    {"haul_cost_per_m3": "900"},
    {"home_location_id": 60003760.5},
    {"home_market": 7},
    {"jita_buy_broker_fee": True},
])
def test_wrong_type_raises(overrides):
    with pytest.raises(ConfigError):
        validate_config_overrides(ProductionConfig(), overrides)


@pytest.mark.parametrize("overrides", [
    {"haul_cost_per_m3": -1},
    {"home_location_id": 0},
    {"jita_buy_broker_fee": 1.5},
])
def test_out_of_range_raises(overrides):
    with pytest.raises(ConfigError):
        validate_config_overrides(ProductionConfig(), overrides)


def test_optional_fields_accept_none():
    validate_config_overrides(ProductionConfig(), {"home_market": None, "home_location_id": None})


# ----------------------------------------------------- enum-style extra layer
def test_unknown_structure_type_rejected():
    with pytest.raises(ConfigError):
        validate_production_overrides({"manufacturing_structure_type": "Raitary"})


def test_unknown_rig_tier_rejected():
    with pytest.raises(ConfigError):
        validate_production_overrides({"reaction_rig_tier": "T3-Rig"})


def test_known_structure_and_rig_accepted():
    validate_production_overrides({
        "manufacturing_structure_type": "Raitaru (M Engineering Complex)",
        "component_rig_tier": "T2-Rig",
    })


def test_generic_validator_alone_would_let_a_typo_through():
    """Why the extra layer exists: a bad structure name is a perfectly valid
    string, so only the enum check catches it."""
    validate_config_overrides(ProductionConfig(), {"manufacturing_structure_type": "Raitary"})


# -------------------------------------------------------------- load / save
def test_load_layers_yaml_then_stored_overrides(db, tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.safe_dump({"haul_cost_per_m3": 500, "home_market": "C-J"}),
                        encoding="utf-8")
    monkeypatch.setenv("EVE_TRADER_LOCAL_CONFIG", str(cfg_file))
    storage.save_settings("production", {"haul_cost_per_m3": 750})

    cfg = load_production_config()
    assert cfg.home_market == "C-J"          # from config.yaml
    assert cfg.haul_cost_per_m3 == 750       # stored override wins
    assert cfg.jita_buy_broker_fee == 0.0147  # untouched default


def test_load_rejects_bad_enum_value_in_yaml(db, tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.safe_dump({"reaction_structure_type": "Athanor"}), encoding="utf-8")
    monkeypatch.setenv("EVE_TRADER_LOCAL_CONFIG", str(cfg_file))
    with pytest.raises(ConfigError):
        load_production_config()


def test_production_overrides_use_their_own_scope(db, tmp_path, monkeypatch):
    monkeypatch.setenv("EVE_TRADER_LOCAL_CONFIG", str(tmp_path / "missing.yaml"))
    cfg = ProductionConfig()
    save_config_overrides({"haul_cost_per_m3": 1200}, cfg)
    assert cfg.haul_cost_per_m3 == 1200
    assert storage.load_settings("production") == {"haul_cost_per_m3": 1200}
    assert storage.load_settings("trading") == {}


def test_save_persists_nothing_when_invalid(db):
    cfg = ProductionConfig()
    with pytest.raises(ConfigError):
        save_config_overrides({"component_rig_tier": "T9-Rig"}, cfg)
    assert storage.load_settings("production") == {}
    assert cfg.component_rig_tier == "No Rig"


def test_reload_mutates_the_module_level_config_in_place(db, tmp_path, monkeypatch):
    monkeypatch.setenv("EVE_TRADER_LOCAL_CONFIG", str(tmp_path / "missing.yaml"))
    held = production_config.PRODUCTION_CONFIG
    storage.save_settings("production", {"haul_cost_per_m3": 42})
    production_config.reload()
    assert held is production_config.PRODUCTION_CONFIG
    assert held.haul_cost_per_m3 == 42
    # Restore: PRODUCTION_CONFIG is module-level state that outlives the
    # throwaway database this test wrote to.
    held.__dict__.update(ProductionConfig().__dict__)
