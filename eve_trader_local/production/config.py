"""Configuration for the Production tool, loaded exactly like TradingConfig
(see ../config.py's module docstring for the layering: dataclass defaults ->
config.yaml -> overrides stored in SQLite, validated before applied). The
stored-override scope key is "production", matching the parent repo.

Only the fields the ported Production code actually reads live here - the
parent's ProductionConfig also carries build-candidate thresholds, logistics
locations and per-category cost-index overrides, none of which have a consumer
in this repo yet (see SYNC.md).

Layering: the shared config.py must never import this module. It stays free of
any Production concept, which is why the enum-style structure/rig checks below
are layered on top of validate_config_overrides here rather than folded into
it - they need production/constants.py, and reaching into that from the shared
module would invert the dependency.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml

from .. import storage
from ..config import apply_config_overrides, validate_config_overrides
from ..errors import ConfigError
from ..paths import config_path
from .constants import RIG_TIERS, STRUCTURE_TYPES


@dataclass
class ProductionConfig:
    # -- Economics (pricing.py) --
    # Buy-side broker fee, applied to whichever market a material is sourced
    # from: the same character pays the same rate wherever they buy.
    jita_buy_broker_fee: float = 0.0147
    # ISK/m3 to move goods from Jita to the home structure. Only Jita-sourced
    # material is charged this - anything already at home needs no hauling.
    haul_cost_per_m3: float = 900.0
    # Sell-side costs subtracted from a sale before the build margin is
    # computed (engine.margin_home/margin_jita): SCC surcharge 0.5% +
    # broker's fee 1.5% + sales tax 3.37%, confirmed against the in-game
    # sell-order breakdown in the parent repo.
    market_fees: float = 0.0537
    # Job-fee facility tax, *additive* on top of the system cost index rather
    # than folded into it - EVE's real formula is
    # `EIV * (cost_index * structure_bonus + facility_tax + SCC_surcharge)`
    # (see engine._job_cost_rate). 0.25% is the fixed NPC-station rate; an
    # Upwell structure's owner sets their own, so override this when building
    # somewhere with a non-default facility tax.
    facility_tax_rate: float = 0.0025

    # -- Where the jobs actually run, for the live system cost index (see
    # engine._job_cost_rate): reactions and the rig-covered component groups
    # share one system, everything else uses the other. Unset is a fully
    # supported state - pricing.system_cost_indices_for returns {} and the job
    # cost falls back to the flat ACTIVITY_MODS rate rather than erroring. The
    # parent additionally carries *_system_name fields that a web-only
    # Settings action resolves into these ids; here the id is set directly.
    component_system_id: Optional[int] = None
    manufacturing_system_id: Optional[int] = None

    # Manual escape hatch, highest priority of all (see engine._job_cost_rate):
    # a value here replaces the looked-up index entirely, so it works even with
    # no system configured at all. Three fields, not two, because the
    # "component" profile feeds two genuinely different rates (Reaction jobs
    # vs. Tech I/II component-group jobs) - the "manufacturing" profile's own
    # reaction rate is never read by any code path, so an override for it
    # would be dead weight. None means "use the computed value".
    reaction_cost_index_override: Optional[float] = None
    component_cost_index_override: Optional[float] = None
    manufacturing_cost_index_override: Optional[float] = None

    # -- Home market/structure --
    # No sane default across installs; both are fully supported as unset -
    # pricing.home_prices() returns {} rather than requesting a "None" market.
    home_market: Optional[str] = None       # appraise.gnf.lt market slug (case-sensitive)
    home_location_id: Optional[int] = None  # structure ID whose live order book is read first

    # -- Invention station (engine.invention_logistics/t1_bpc_invention_needs) --
    # Where datacores/decryptors/T1 BPCs are checked against what's actually
    # needed. The parent additionally carries a whole Logistik tab of
    # per-category structure assignments (distribution_source_location_id,
    # job_category_locations) for its multi-structure logistics_status/
    # distribution_recommendations - genuinely out of scope for a single-user
    # install (see engine.py's module docstring and SYNC.md); this one field
    # is different; it's just "where do you invent", the same single-location
    # shape home_location_id already has.
    invention_location_id: Optional[int] = None

    # -- Where you build, split by build profile (see constants.py's
    # STRUCTURE_TYPES/RIG_TIERS and structure_rig_multiplier): reactions
    # typically run in a rigged Refinery, rig-covered component groups in an
    # Engineering Complex with the component ME rigs, everything else in a
    # separate Engineering Complex.
    reaction_structure_type: str = "Citadel (no bonuses)"
    reaction_rig_tier: str = "No Rig"
    component_structure_type: str = "Citadel (no bonuses)"
    component_rig_tier: str = "No Rig"
    manufacturing_structure_type: str = "Citadel (no bonuses)"
    manufacturing_rig_tier: str = "No Rig"

    # -- Invention skills (invention.skill_multiplier) --
    # EVE's real invention formula reads three separate trained skills off the
    # inventing character: the encryption method skill, plus the two
    # datacore/science skills the blueprint in question needs. Levels only ever
    # run 0-5 (range-checked in the shared config.py). 4 is a deliberately
    # ordinary "trained but not perfect" default, not an optimistic one.
    encryption_skill_level: int = 4
    datacore_skill_1_level: int = 4
    datacore_skill_2_level: int = 4

    # -- Build-candidate discovery (engine.discover_build_candidates) --
    # A build clearing this margin still isn't necessarily worth surfacing -
    # see min_daily_profit below, which is the real ranking gate. 15% matches
    # the parent's own stock-target planner default (min_margin there): the
    # same "is this worth building at all" threshold, just applied here to
    # everything, not only configured stock targets.
    min_margin: float = 0.15
    # Gates discover_build_candidates on potential_daily_profit (margin x
    # build_cost x real market-wide daily movement, never just margin alone -
    # see that function's own docstring) rather than admitting every
    # margin-qualifying item regardless of how rarely it actually trades. 0.0
    # (the default) means "no floor" - every margin-qualifying candidate is
    # kept.
    min_daily_profit: float = 0.0

    # -- Stock-aware planner (engine.plan_production/_expand_all) --
    # Extra buffer kept for build-chain components below a stock target, sized
    # off each component's stock-oblivious whole-tree run count (_base_runs) so
    # intermediate materials keep a cushion instead of being planned down to
    # exactly zero. 0.7 matches the parent's own default.
    component_overbuild: float = 0.7


_STRUCTURE_TYPE_FIELDS = ("reaction_structure_type", "component_structure_type",
                          "manufacturing_structure_type")
_RIG_TIER_FIELDS = ("reaction_rig_tier", "component_rig_tier", "manufacturing_rig_tier")


def validate_production_overrides(overrides: dict[str, Any]) -> None:
    """The structure/rig fields are only meaningful if they name one of the
    specific options constants.structure_rig_multiplier can actually price. A
    hand-edited config.yaml bypasses any UI dropdown, so a typo would
    otherwise pass the plain string-type check and only fail much later with a
    bare KeyError deep inside the build-cost math."""
    for key in _STRUCTURE_TYPE_FIELDS:
        if key in overrides and overrides[key] not in STRUCTURE_TYPES:
            raise ConfigError(f"{key}: '{overrides[key]}' is not a known structure type. "
                              f"Options: {', '.join(STRUCTURE_TYPES)}")
    for key in _RIG_TIER_FIELDS:
        if key in overrides and overrides[key] not in RIG_TIERS:
            raise ConfigError(f"{key}: '{overrides[key]}' is not a known rig tier. "
                              f"Options: {', '.join(RIG_TIERS)}")


def _validate(cfg: ProductionConfig, overrides: dict[str, Any]) -> None:
    validate_config_overrides(cfg, overrides)
    validate_production_overrides(overrides)


def load_production_config(path: Optional[Path] = None) -> ProductionConfig:
    """Defaults + config.yaml + stored overrides, each layer validated before
    it is applied. Not cached, same reasoning as load_trading_config."""
    path = path or config_path()
    storage.init_db()
    cfg = ProductionConfig()
    if path.exists():
        overrides = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        _validate(cfg, overrides)  # fail fast, naming the bad field
        apply_config_overrides(cfg, overrides)
    stored = storage.load_settings("production")
    if stored:
        _validate(cfg, stored)
        apply_config_overrides(cfg, stored)
    return cfg


def save_config_overrides(updates: dict[str, Any], cfg: Optional[ProductionConfig] = None) -> None:
    """Validates, then persists, then applies - in that order, so a rejected
    value changes nothing at all."""
    cfg = cfg if cfg is not None else PRODUCTION_CONFIG
    _validate(cfg, updates)
    storage.save_settings("production", updates)
    apply_config_overrides(cfg, updates)


def reload() -> ProductionConfig:
    """Refreshes the module-level PRODUCTION_CONFIG *in place*, so callers
    holding a reference to it see the new values."""
    fresh = load_production_config()
    PRODUCTION_CONFIG.__dict__.update(fresh.__dict__)
    return PRODUCTION_CONFIG


# Bare defaults at import time, deliberately - importing this module must not
# touch the filesystem or the database (same contract as config.TRADING_CONFIG).
PRODUCTION_CONFIG = ProductionConfig()
