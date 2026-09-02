from __future__ import annotations

import pytest
import yaml

from eve_trader_local import config, storage
from eve_trader_local.config import (
    OAuthConfig,
    TradingConfig,
    apply_config_overrides,
    validate_config_overrides,
)
from eve_trader_local.errors import ActionError, ConfigError


# ---------------------------------------------------------------- type checks
@pytest.mark.parametrize("overrides", [
    {"structure_id": "not a number"},
    {"import_cost_per_m3": "900"},
    {"lookback_days": 30.5},
    {"lookback_days": True},          # bool is an int subclass, but always a typo here
    {"esi_base": 42},
    {"structure_market_slug": 7},
])
def test_wrong_type_raises(overrides):
    with pytest.raises(ConfigError):
        validate_config_overrides(TradingConfig(), overrides)


@pytest.mark.parametrize("overrides", [
    {"structure_id": 60003760},
    {"structure_id": None},           # Optional field
    {"import_cost_per_m3": 900},      # int accepted for a float field
    {"import_cost_per_m3": 900.5},
    {"esi_base": "https://example.test"},
    {"structure_market_slug": "C-J"},
])
def test_valid_values_accepted(overrides):
    validate_config_overrides(TradingConfig(), overrides)


def test_unknown_keys_are_ignored_not_rejected():
    validate_config_overrides(TradingConfig(), {"totally_made_up": object()})


# --------------------------------------------------------------- range checks
@pytest.mark.parametrize("overrides", [
    {"structure_id": 0},
    {"jita_region_id": -1},
    {"import_cost_per_m3": -1.0},
    {"structure_sell_haircut": 1.5},
    {"jita_buy_broker_fee": -0.01},
    {"lookback_days": -1},
])
def test_out_of_range_raises(overrides):
    with pytest.raises(ConfigError):
        validate_config_overrides(TradingConfig(), overrides)


def test_unranged_field_accepts_anything_numeric():
    validate_config_overrides(TradingConfig(), {"buyer_character_name": "x"})


def test_config_error_is_an_action_error():
    """Callers only ever need to catch ActionError at the boundary."""
    assert issubclass(ConfigError, ActionError)


# ----------------------------------------------------- validate-before-apply
def test_nothing_is_applied_when_one_value_is_bad():
    cfg = TradingConfig()
    bad = {"lookback_days": 14, "structure_id": "nope"}
    with pytest.raises(ConfigError):
        validate_config_overrides(cfg, bad)
    assert cfg.lookback_days == 30  # untouched - validation ran before any apply


def test_apply_sets_only_known_fields():
    cfg = TradingConfig()
    apply_config_overrides(cfg, {"lookback_days": 14, "nope": 1})
    assert cfg.lookback_days == 14
    assert not hasattr(cfg, "nope")


# -------------------------------------------------------------- load / save
def test_load_layers_yaml_then_stored_overrides(db, tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.safe_dump({"lookback_days": 7, "structure_id": 123}), encoding="utf-8")
    monkeypatch.setenv("EVE_TRADER_LOCAL_CONFIG", str(cfg_file))
    storage.save_settings("trading", {"lookback_days": 90})

    cfg = config.load_trading_config()
    assert cfg.structure_id == 123    # from config.yaml
    assert cfg.lookback_days == 90    # stored override wins
    assert cfg.jita_region_id == 10000002  # untouched default


def test_load_rejects_bad_yaml_value(db, tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.safe_dump({"structure_id": "not a number"}), encoding="utf-8")
    monkeypatch.setenv("EVE_TRADER_LOCAL_CONFIG", str(cfg_file))
    with pytest.raises(ConfigError):
        config.load_trading_config()


def test_save_overrides_persists_and_applies(db):
    cfg = TradingConfig()
    config.save_config_overrides({"lookback_days": 5}, cfg)
    assert cfg.lookback_days == 5
    assert storage.load_settings("trading") == {"lookback_days": 5}


def test_save_overrides_persists_nothing_when_invalid(db):
    cfg = TradingConfig()
    with pytest.raises(ConfigError):
        config.save_config_overrides({"lookback_days": -3}, cfg)
    assert storage.load_settings("trading") == {}
    assert cfg.lookback_days == 30


def test_reload_mutates_the_module_level_config_in_place(db, tmp_path, monkeypatch):
    monkeypatch.setenv("EVE_TRADER_LOCAL_CONFIG", str(tmp_path / "missing.yaml"))
    held = config.TRADING_CONFIG
    storage.save_settings("trading", {"lookback_days": 11})
    config.reload()
    assert held is config.TRADING_CONFIG
    assert held.lookback_days == 11


# ------------------------------------------------------------------- oauth
def test_redirect_uri_built_from_host_port_path(monkeypatch):
    monkeypatch.delenv("EVE_SSO_REDIRECT_URI", raising=False)
    cfg = OAuthConfig(callback_host="localhost", callback_port=8000,
                      callback_path="/callback", redirect_uri_override="")
    assert cfg.redirect_uri == "http://localhost:8000/callback"


def test_redirect_uri_override_wins():
    cfg = OAuthConfig(redirect_uri_override="http://127.0.0.1:9/x")
    assert cfg.redirect_uri == "http://127.0.0.1:9/x"


def test_bad_port_env_var_raises_config_error(monkeypatch):
    monkeypatch.setenv("EVE_SSO_CALLBACK_PORT", "eighty-eighty")
    with pytest.raises(ConfigError):
        OAuthConfig()
