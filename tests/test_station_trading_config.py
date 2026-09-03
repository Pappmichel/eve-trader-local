from __future__ import annotations

import pytest
import yaml

from eve_trader_local import storage
from eve_trader_local.config import validate_config_overrides
from eve_trader_local.errors import ConfigError
from eve_trader_local.station_trading import config as station_trading_config
from eve_trader_local.station_trading.config import (
    StationTradingConfig,
    load_station_trading_config,
    save_config_overrides,
)


# ------------------------------------------------- generic checks still apply
@pytest.mark.parametrize("overrides", [
    {"station_id": "60003760"},
    {"broker_fee_rate": True},
    {"min_daily_volume": "1000"},
    {"enforce_shortlist_cap": "yes"},
])
def test_wrong_type_raises(overrides):
    with pytest.raises(ConfigError):
        validate_config_overrides(StationTradingConfig(), overrides)


@pytest.mark.parametrize("overrides", [
    {"station_id": 0},
    {"broker_fee_rate": -0.1},
    {"sales_tax_rate": 1.5},
    {"min_spread_threshold": -0.01},
    {"min_daily_volume": -1},
    {"max_active_shortlist_items": 0},
])
def test_out_of_range_raises(overrides):
    with pytest.raises(ConfigError):
        validate_config_overrides(StationTradingConfig(), overrides)


def test_defaults_are_themselves_valid():
    validate_config_overrides(StationTradingConfig(), vars(StationTradingConfig()))


# -------------------------------------------------------------- load / save
def test_load_layers_yaml_then_stored_overrides(db, tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.safe_dump({"min_spread_threshold": 0.1, "station_id": 60003760}),
                        encoding="utf-8")
    monkeypatch.setenv("EVE_TRADER_LOCAL_CONFIG", str(cfg_file))
    storage.save_settings("station_trading", {"min_spread_threshold": 0.2})

    cfg = load_station_trading_config()
    assert cfg.station_id == 60003760            # from config.yaml
    assert cfg.min_spread_threshold == 0.2        # stored override wins
    assert cfg.sales_tax_rate == 0.075            # untouched default


def test_load_rejects_bad_type_in_yaml(db, tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.safe_dump({"broker_fee_rate": "five percent"}), encoding="utf-8")
    monkeypatch.setenv("EVE_TRADER_LOCAL_CONFIG", str(cfg_file))
    with pytest.raises(ConfigError):
        load_station_trading_config()


def test_station_trading_overrides_use_their_own_scope(db, tmp_path, monkeypatch):
    monkeypatch.setenv("EVE_TRADER_LOCAL_CONFIG", str(tmp_path / "missing.yaml"))
    cfg = StationTradingConfig()
    save_config_overrides({"min_daily_volume": 5000}, cfg)
    assert cfg.min_daily_volume == 5000
    assert storage.load_settings("station_trading") == {"min_daily_volume": 5000}
    assert storage.load_settings("trading") == {}
    assert storage.load_settings("production") == {}


def test_save_persists_nothing_when_invalid(db):
    cfg = StationTradingConfig()
    with pytest.raises(ConfigError):
        save_config_overrides({"broker_fee_rate": 5}, cfg)
    assert storage.load_settings("station_trading") == {}
    assert cfg.broker_fee_rate == 0.05


def test_reload_mutates_the_module_level_config_in_place(db, tmp_path, monkeypatch):
    monkeypatch.setenv("EVE_TRADER_LOCAL_CONFIG", str(tmp_path / "missing.yaml"))
    held = station_trading_config.STATION_TRADING_CONFIG
    storage.save_settings("station_trading", {"min_daily_volume": 42})
    station_trading_config.reload()
    assert held is station_trading_config.STATION_TRADING_CONFIG
    assert held.min_daily_volume == 42
    # Restore: STATION_TRADING_CONFIG is module-level state that outlives the
    # throwaway database this test wrote to.
    held.__dict__.update(StationTradingConfig().__dict__)
