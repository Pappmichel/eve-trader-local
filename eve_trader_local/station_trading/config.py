"""Configuration for the Station Trading tool, loaded exactly like
TradingConfig/ProductionConfig/DoctrineConfig/RefiningConfig (see
../config.py's module docstring for the layering: dataclass defaults ->
config.yaml -> overrides stored in SQLite, validated before applied).
Stored-override scope key is "station_trading".

No enum-style fields here (unlike production/config.py's structure/rig
checks), so this needs nothing on top of the shared validate_config_overrides
- same "no tool-specific validation layer needed" shape doctrine/config.py
already has.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

from .. import storage
from ..config import apply_config_overrides, validate_config_overrides
from ..paths import config_path


@dataclass
class StationTradingConfig:
    # Jita 4 - Moon 4 - Caldari Navy Assembly Plant - the real trade hub
    # station (confirmed against the parent repo's local SDE sde_stations
    # table, not assumed from memory). Region is deliberately not a separate
    # field here - reused directly from TRADING_CONFIG.jita_region_id
    # wherever needed (see production/pricing.py's jita_prices for the same
    # precedent), since it's a Trading-level concept this tool doesn't own.
    station_id: int = 60003760

    # Starting defaults for the base-game NPC-station rates (no standings/
    # skill reduction applied) - not asserted precise, meant to be checked
    # against your own in-game Market window and adjusted (same "manual
    # value, live hint alongside it" pattern as Production's cost-index
    # overrides - see do_get_skill_summary). Itemized separately (unlike
    # Trading's blended structure_sell_haircut) because Station Trading
    # needs broker fee charged per order leg (both buy and sell) and sales
    # tax charged once (sell leg only).
    broker_fee_rate: float = 0.05
    sales_tax_rate: float = 0.075

    # Candidate-discovery gates (candidate_discovery.discover_candidates).
    # min_daily_volume's real-world shape was checked live against Jita's
    # actual market in the parent repo (2026-08-28): items clearing an 8%
    # spread are sharply bimodal - thousands of genuinely dead items
    # (avg_daily_volume in the single-to-low-hundreds range, a wide "spread"
    # on these is meaningless noise from near-zero order-book depth) vs. a
    # small cluster of real, liquid commodities (minerals etc., volume in
    # the tens of millions+) - 1000 sits well inside the gap between those
    # two clusters, not a precisely-derived number, and already brings the
    # real live candidate count down from 10,000+ to ~1,300 (checked live
    # 2026-08-29) - a real, legitimately large opportunity set, not
    # something to additionally cap by default.
    min_spread_threshold: float = 0.08
    min_daily_volume: float = 1000.0

    # Same "off by default, user opts in" shape as TradingConfig's own
    # enforce_shortlist_cap/max_active_shortlist_items - confirmed in the
    # parent repo with the user 2026-08-29 that an always-on hard cap (that
    # tool's initial 200) was wrong: min_daily_volume above is the real
    # noise filter, a cap on top of that should be an explicit choice, not a
    # silent default.
    enforce_shortlist_cap: bool = False
    max_active_shortlist_items: int = 300


def load_station_trading_config(path: Optional[Path] = None) -> StationTradingConfig:
    """Defaults + config.yaml + stored overrides, each layer validated before
    it is applied. Not cached, same reasoning as load_trading_config."""
    path = path or config_path()
    storage.init_db()
    cfg = StationTradingConfig()
    if path.exists():
        overrides = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        validate_config_overrides(cfg, overrides)  # fail fast, naming the bad field
        apply_config_overrides(cfg, overrides)
    stored = storage.load_settings("station_trading")
    if stored:
        validate_config_overrides(cfg, stored)
        apply_config_overrides(cfg, stored)
    return cfg


def save_config_overrides(updates: dict, cfg: Optional[StationTradingConfig] = None) -> None:
    """Validates, then persists, then applies - in that order, so a rejected
    value changes nothing at all."""
    cfg = cfg if cfg is not None else STATION_TRADING_CONFIG
    validate_config_overrides(cfg, updates)
    storage.save_settings("station_trading", updates)
    apply_config_overrides(cfg, updates)


def reload() -> StationTradingConfig:
    """Refreshes the module-level STATION_TRADING_CONFIG *in place*, so
    callers holding a reference to it see the new values."""
    fresh = load_station_trading_config()
    STATION_TRADING_CONFIG.__dict__.update(fresh.__dict__)
    return STATION_TRADING_CONFIG


# Bare defaults at import time, deliberately - importing this module must not
# touch the filesystem or the database (same contract as config.TRADING_CONFIG).
STATION_TRADING_CONFIG = StationTradingConfig()
