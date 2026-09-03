"""Configuration for the Ore & Minerals tool, loaded exactly like
ProductionConfig/DoctrineConfig (see ../config.py's module docstring for the
layering: dataclass defaults -> config.yaml -> overrides stored in SQLite,
validated before applied). Stored-override scope key is "refining", matching
the parent repo.

Ported in two passes: the yield-math fields (structure/rig/security/implant/
skills) first, then `refining_tax_rate` alongside `pricing.py`/`quote.py`
(the Ore-Shortlist and Reprocessing-tab quote calculations - see SYNC.md).

Layering: the shared config.py must never import this module, same reasoning
as production/config.py's own layering note - the enum-style structure/rig/
implant checks below need refining/constants.py, so they're layered on top
of validate_config_overrides here rather than folded into it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from .. import storage
from ..config import apply_config_overrides, validate_config_overrides
from ..errors import ConfigError
from ..paths import config_path
from .constants import REPROCESSING_IMPLANT_BONUS, RIG_YIELD_BONUS_POINTS, STRUCTURE_YIELD_MODIFIER


@dataclass
class RefiningConfig:
    # -- Ore/ice path: structure/rig/security/implant --
    # Settings-page dropdowns, not pulled from ESI (confirmed with the user
    # during the parent's own planning: "structure type, rig, skill, and
    # implant should be configurable as settings via a dropdown. don't pull
    # skills via ESI") - independent of TradingConfig.structure_id/
    # ProductionConfig's own structure_type fields, since the refining
    # structure may be a different one (a dedicated Refinery) than either
    # tool's own.
    structure_type: str = "Citadel (no bonuses)"
    rig_tier: str = "No Rig"
    # Representative system security for the refining structure - -1.0 (deep
    # null-sec/wormhole) .. 1.0 (highsec), same raw scale as the SDE's own
    # solar_system security field. Defaults to 0.0 (mid null-sec) rather than
    # None: unlike Production's own system_id fields (resolved from ESI via a
    # named system), this is a plain manual number, and every "not set yet"
    # value should look like a real system, not a special-cased blank.
    security_status: float = 0.0
    implant: str = "None"

    # -- Ore/ice path: skills (manual, not ESI-synced - see structure_type above) --
    reprocessing_skill_level: int = 0
    reprocessing_efficiency_skill_level: int = 0
    # {ore/ice family name: skill level} - one skill per ore/ice *family*
    # (e.g. "Veldspar" covers Veldspar/Concentrated Veldspar/Dense Veldspar),
    # not per exact type_id. A family missing from this dict is treated as
    # level 5 (maxed), not an error and not unskilled - see reprocessing.py's
    # ore_ice_yield docstring for why (these are cheap skills almost every
    # active player has trained to 5; defaulting to 0 would understate every
    # not-yet-configured family's profit).
    ore_family_skill_levels: dict[str, int] = field(default_factory=dict)

    # -- Scrapmetal path (independent of everything above - see constants.py's
    # module docstring for why structure/rig/security/implant/the two skills
    # above don't apply here) --
    scrapmetal_processing_skill_level: int = 0

    # -- Economics --
    # Deducted from mineral value on both the Ore Shortlist (pricing.py) and
    # Reprocessing-tab (quote.py) quotes - a separate field from Production's
    # facility_tax_rate/market_fees, since this tool's own refining structure
    # may not be the same one Production builds in.
    refining_tax_rate: float = 0.0


def validate_refining_overrides(overrides: dict[str, Any]) -> None:
    """Beyond the generic type checks (validate_config_overrides),
    structure_type/rig_tier/implant are only meaningful if they're one of the
    specific named options the yield math actually knows how to price
    (constants.py's STRUCTURE_YIELD_MODIFIER/RIG_YIELD_BONUS_POINTS/
    REPROCESSING_IMPLANT_BONUS - the same dicts a Settings page's dropdowns
    would be built from) - mirrors production/config.py's
    validate_production_overrides for the same reasoning (a config.yaml
    hand-edit bypasses the dropdown, so a typo'd name would otherwise pass the
    plain string-type check and only fail later with a bare KeyError deep in
    the yield math)."""
    if "structure_type" in overrides and overrides["structure_type"] not in STRUCTURE_YIELD_MODIFIER:
        raise ConfigError(f"structure_type: {overrides['structure_type']!r} is not a known structure type. "
                           f"Options: {', '.join(STRUCTURE_YIELD_MODIFIER)}")
    if "rig_tier" in overrides and overrides["rig_tier"] not in RIG_YIELD_BONUS_POINTS:
        raise ConfigError(f"rig_tier: {overrides['rig_tier']!r} is not a known rig tier. "
                           f"Options: {', '.join(RIG_YIELD_BONUS_POINTS)}")
    if "implant" in overrides and overrides["implant"] not in REPROCESSING_IMPLANT_BONUS:
        raise ConfigError(f"implant: {overrides['implant']!r} is not a known implant. "
                           f"Options: {', '.join(REPROCESSING_IMPLANT_BONUS)}")


def _validate(cfg: RefiningConfig, overrides: dict[str, Any]) -> None:
    validate_config_overrides(cfg, overrides)
    validate_refining_overrides(overrides)


def load_refining_config(path: Optional[Path] = None) -> RefiningConfig:
    """Defaults + config.yaml + stored overrides, each layer validated before
    it is applied. Not cached, same reasoning as load_trading_config/
    load_production_config."""
    path = path or config_path()
    storage.init_db()
    cfg = RefiningConfig()
    if path.exists():
        overrides = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        _validate(cfg, overrides)  # fail fast, naming the bad field
        apply_config_overrides(cfg, overrides)
    stored = storage.load_settings("refining")
    if stored:
        _validate(cfg, stored)
        apply_config_overrides(cfg, stored)
    return cfg


def save_config_overrides(updates: dict[str, Any], cfg: Optional[RefiningConfig] = None) -> None:
    """Validates, then persists, then applies - in that order, so a rejected
    value changes nothing at all."""
    cfg = cfg if cfg is not None else REFINING_CONFIG
    _validate(cfg, updates)
    storage.save_settings("refining", updates)
    apply_config_overrides(cfg, updates)


def reload() -> RefiningConfig:
    """Refreshes the module-level REFINING_CONFIG *in place*, so callers
    holding a reference to it see the new values."""
    fresh = load_refining_config()
    REFINING_CONFIG.__dict__.update(fresh.__dict__)
    return REFINING_CONFIG


# Bare defaults at import time, deliberately - importing this module must not
# touch the filesystem or the database (same contract as config.TRADING_CONFIG).
REFINING_CONFIG = RefiningConfig()
