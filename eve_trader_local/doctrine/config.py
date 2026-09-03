"""Configuration for the Doctrine tool, loaded exactly like TradingConfig/
ProductionConfig (see ../config.py's module docstring for the layering:
dataclass defaults -> config.yaml -> overrides stored in SQLite, validated
before applied). Stored-override scope key is "doctrine".

No enum-style fields here (unlike production/config.py's structure/rig
checks), so this needs nothing on top of the shared validate_config_overrides
- no doctrine-specific validation layer exists, or is needed, yet.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

from .. import storage
from ..config import TRADING_CONFIG, apply_config_overrides, validate_config_overrides
from ..paths import config_path


@dataclass
class DoctrineConfig:
    # No sane default across installs - falls back to Trading's own
    # structure_id (the C-J structure) at read time if unset, since almost
    # every local install running Doctrine also runs Trading against the
    # same structure. See effective_structure_id below.
    doctrine_structure_id: Optional[int] = None
    # Where stockpile Ist is counted from (storage.esi_stock_at_location) -
    # falls back to effective_structure_id if unset, same reasoning.
    stockpile_location_id: Optional[int] = None
    # Consumables (drones/cargo/charges) only need to clear this fraction of
    # their Soll to count as "tolerable" rather than "critical" - exact-class
    # positions (hull/modules/rigs/subsystems) never get this leniency.
    # Per-fitting override: Fitting.cargo_tolerance_pct (None = use this
    # default).
    cargo_tolerance_pct: float = 0.9
    # Off by default: a contract item the fitting doesn't call for at all
    # normally counts as "info" (a free bonus, not a defect). Turning this on
    # treats it as "tolerable" instead, for an operator who wants to enforce
    # clean, exact contracts.
    strict_extras: bool = False

    @property
    def effective_structure_id(self) -> Optional[int]:
        return self.doctrine_structure_id or TRADING_CONFIG.structure_id

    @property
    def effective_stockpile_location_id(self) -> Optional[int]:
        return self.stockpile_location_id or self.effective_structure_id


def load_doctrine_config(path: Optional[Path] = None) -> DoctrineConfig:
    """Defaults + config.yaml + stored overrides, each layer validated before
    it is applied. Not cached, same reasoning as load_trading_config."""
    path = path or config_path()
    storage.init_db()
    cfg = DoctrineConfig()
    if path.exists():
        overrides = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        validate_config_overrides(cfg, overrides)  # fail fast, naming the bad field
        apply_config_overrides(cfg, overrides)
    stored = storage.load_settings("doctrine")
    if stored:
        validate_config_overrides(cfg, stored)
        apply_config_overrides(cfg, stored)
    return cfg


def save_config_overrides(updates: dict, cfg: Optional[DoctrineConfig] = None) -> None:
    """Validates, then persists, then applies - in that order, so a rejected
    value changes nothing at all."""
    cfg = cfg if cfg is not None else DOCTRINE_CONFIG
    validate_config_overrides(cfg, updates)
    storage.save_settings("doctrine", updates)
    apply_config_overrides(cfg, updates)


def reload() -> DoctrineConfig:
    """Refreshes the module-level DOCTRINE_CONFIG *in place*, so callers
    holding a reference to it see the new values."""
    fresh = load_doctrine_config()
    DOCTRINE_CONFIG.__dict__.update(fresh.__dict__)
    return DOCTRINE_CONFIG


# Bare defaults at import time, deliberately - importing this module must not
# touch the filesystem or the database (same contract as config.TRADING_CONFIG).
DOCTRINE_CONFIG = DoctrineConfig()
