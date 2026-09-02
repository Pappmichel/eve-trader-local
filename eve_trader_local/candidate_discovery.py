"""Market-group-based candidate discovery.

Builds the universe of tradeable items from the market-group tree. Only a
small, explicit set of top-level categories is excluded
(TradingConfig.excluded_path_prefixes - confirmed one-by-one with the user in
the parent repo); there is no keyword allowlist/denylist and no per-item
volume(m3) cap. Every other item is a candidate and lives or dies on actual
profitability + trading volume, checked later by the historical backtest
(not ported here yet - see SYNC.md).

Run order:
  1. build_candidate_universe()
  2. build_focused_candidate_universe(universe)   # pass-through, see its docstring
  3. the historical candidate scoring pass
"""
from __future__ import annotations

import logging
from typing import Iterable, Optional

from . import storage
from .config import TRADING_CONFIG, TradingConfig
from .esi_client import ESIClient, extract_meta_level, resolve_effective_volume_bulk
from .models import Candidate

log = logging.getLogger(__name__)

# SDE category_id=7 ("Module") covers both regular modules and rigs.
MODULE_CATEGORY_ID = 7

# SDE category_id=20 ("Implant") covers both real cyberimplants and Boosters/
# Drugs - CCP doesn't split them into separate categories, so category alone
# labels both as "Implant", which the user confirmed is wrong. group_id=303 is
# what actually distinguishes them; it's the real "Booster" group (verified
# against live SDE data in the parent repo - an earlier guess of 746 turned
# out to be a skill-hardwiring implant group, not boosters at all).
IMPLANT_CATEGORY_ID = 20
BOOSTER_GROUP_ID = 303


def is_wanted_market_path(path: str, cfg: TradingConfig = TRADING_CONFIG) -> bool:
    """True unless `path` starts with one of the handful of categorically-
    excluded top-level groups (cfg.excluded_path_prefixes - ships/blueprints/
    apparel/personalization/pilot's services/structures, confirmed one-by-one
    with the user as structurally not fitting the import-arbitrage model).
    There is deliberately no "wanted keywords" allowlist: no item should be
    sorted out just for being in an unlisted category. Profitability and real
    trading volume are the only gates now."""
    s = path.lower()
    for prefix in cfg.excluded_path_prefixes:
        if s.startswith(prefix):
            return False
    return True


def guess_category(path: str, item_name: str, volume_m3: float, category_id: Optional[int] = None,
                   category_names: Optional[dict[int, str]] = None,
                   group_id: Optional[int] = None) -> str:
    """Display category for the shortlist's category column - never used for
    any margin/filtering math. Prefers the *real* SDE category name (e.g.
    "Implant", "Charge", "Drone", "Skill", "Material") via `category_names`
    (storage.load_sde_category_names(), from Fuzzwork's invCategories.csv).
    This used to only distinguish "Module/Rig" from a catch-all "Material"
    bucket, which mislabeled e.g. implants (category_id 20) as Material -
    confirmed wrong by the user.

    Falls back to the old string-matching heuristic ("module"/"rig" in the
    name/path, or a volume >=5m3 guess) only on the rare live-ESI-walk path
    (_build_candidate_universe_from_esi), where fetching each type's real
    category would mean an extra ESI call per type.

    `group_id` (optional - only the SDE-backed path has it) splits Boosters/
    Drugs out of the "Implant" category they'd otherwise share with real
    cyberimplants - see IMPLANT_CATEGORY_ID's own comment."""
    if category_id is not None:
        if category_id == IMPLANT_CATEGORY_ID and group_id == BOOSTER_GROUP_ID:
            return "Drugs"
        if category_names and category_id in category_names:
            return category_names[category_id]
        return "Module/Rig" if category_id == MODULE_CATEGORY_ID else "Material"
    s = f"{path} {item_name}".lower()
    if "module" in s or "rig" in s or volume_m3 >= 5:
        return "Module/Rig"
    return "Material"


def _market_group_path(group_id: int, names: dict[int, str], parents: dict[int, int]) -> str:
    parts: list[str] = []
    cur = group_id
    guard = 0
    while cur > 0 and guard < 20:
        parts.insert(0, names.get(cur, "?"))
        cur = parents.get(cur, 0)
        guard += 1
    return " > ".join(parts)


def build_candidate_universe(client: ESIClient | None = None,
                             cfg: TradingConfig = TRADING_CONFIG,
                             progress: bool = True) -> list[Candidate]:
    """Market groups and type name/volume/metaLevel are static SDE data (they
    only change between CCP patches) - if the local Fuzzwork SDE cache
    (sde.refresh_sde(), tables sde_market_groups/sde_types) is populated,
    build the universe from that instead of walking ESI's market-group tree
    live (~2000+ separate ESI calls: list_market_group_ids() plus one
    get_market_group() per group, then one get_type_info() per candidate
    type - why that path is noticeably slower). Falls back to the live-ESI
    walk only when the SDE cache is empty (a fresh install that hasn't run
    `refresh-sde` yet)."""
    market_groups = storage.load_sde_market_groups()
    sde_types = storage.load_sde_types_with_market_group()
    if market_groups and sde_types:
        category_names = storage.load_sde_category_names()
        type_groups = storage.load_sde_type_groups()
        return _build_candidate_universe_from_sde(market_groups, sde_types, cfg,
                                                  category_names, type_groups)
    return _build_candidate_universe_from_esi(client, cfg, progress)


def _build_candidate_universe_from_sde(market_groups: list[tuple[int, int, str]],
                                       sde_types: list[tuple[int, str, float, int, Optional[int], Optional[int]]],
                                       cfg: TradingConfig,
                                       category_names: Optional[dict[int, str]] = None,
                                       type_groups: Optional[dict[int, int]] = None) -> list[Candidate]:
    names = {gid: name for gid, _parent, name in market_groups}
    parents = {gid: (parent or 0) for gid, parent, _name in market_groups}

    wanted_paths: dict[int, str] = {
        gid: path for gid in names
        if is_wanted_market_path(path := _market_group_path(gid, names, parents), cfg)
    }

    rows = []
    for type_id, type_name, volume, market_group_id, meta_level, category_id in sde_types:
        path = wanted_paths.get(market_group_id)
        if path is None or not type_name or not volume or volume <= 0:
            continue
        rows.append((type_id, type_name, volume, meta_level, category_id, path))

    # Capital-sized modules (category_id=MODULE_CATEGORY_ID) have a much
    # smaller *packaged* volume than the raw SDE `volume` above - a real bug
    # in the parent repo (its GitHub issue #73), the same quirk already fixed
    # once for Production's haul cost. Ships have the identical quirk but are
    # already excluded by is_wanted_market_path (excluded_path_prefixes), so
    # only Module-category types actually need the ESI lookup. Resolved in one
    # bulk, concurrent pass rather than per-type_id inside this loop (parent
    # issue #96): on a machine whose type_packaged_volume cache hasn't been
    # backfilled, one-at-a-time meant hundreds of sequential live ESI calls in
    # a single run.
    effective_volumes = resolve_effective_volume_bulk(
        [(type_id, volume, category_id) for type_id, _type_name, volume, _meta, category_id, _path in rows]
    )

    candidates: list[Candidate] = []
    for type_id, type_name, volume, meta_level, category_id, path in rows:
        effective_volume = effective_volumes.get(type_id, volume)
        candidates.append(Candidate(
            item=type_name, type_id=type_id, volume_m3=effective_volume,
            category=guess_category(path, type_name, volume, category_id, category_names,
                                    (type_groups or {}).get(type_id)),
            market_group_path=path, meta_level=meta_level,
        ))
    return candidates


def _build_candidate_universe_from_esi(client: ESIClient | None, cfg: TradingConfig,
                                       progress: bool) -> list[Candidate]:
    client = client or ESIClient(cfg)

    group_ids = client.list_market_group_ids()
    names: dict[int, str] = {}
    parents: dict[int, int] = {}
    group_types: dict[int, list[int]] = {}

    if progress:
        log.info("Loading %d EVE market groups...", len(group_ids))
    for gid in group_ids:
        info = client.get_market_group(gid)
        names[gid] = info.get("name", "")
        parents[gid] = info.get("parent_group_id", 0) or 0
        group_types[gid] = info.get("types", []) or []

    type_paths: dict[int, str] = {}
    for gid in names:
        path = _market_group_path(gid, names, parents)
        if is_wanted_market_path(path, cfg):
            for type_id in group_types.get(gid, []):
                type_paths.setdefault(type_id, path)

    if progress:
        log.info("Loading type names/volumes for %d candidate types...", len(type_paths))
    candidates: list[Candidate] = []
    for i, (type_id, path) in enumerate(type_paths.items()):
        info = client.get_type_info(type_id)
        item_name = info.get("name", "")
        volume = info.get("packaged_volume", info.get("volume", 0)) or 0
        if item_name and volume > 0:
            candidates.append(Candidate(
                item=item_name, type_id=type_id, volume_m3=volume,
                category=guess_category(path, item_name, volume),
                market_group_path=path,
                meta_level=extract_meta_level(info),
            ))
        if progress and i and i % 200 == 0:
            log.info("  ... %d/%d types processed", i, len(type_paths))
    return candidates


def build_focused_candidate_universe(universe: Iterable[Candidate],
                                     cfg: TradingConfig = TRADING_CONFIG) -> list[Candidate]:
    """Used to filter the raw universe down by market-group keyword and
    per-item m3 size - confirmed with the user that no item should be sorted
    out by category or physical size before it's even price-checked, only by
    actual profitability and real trading volume (both applied later, during
    historical scoring). Now a pass-through, kept only so the existing
    "load market groups -> filter candidates -> find new candidates" workflow
    step numbering doesn't need to change - `cfg` is accepted but unused."""
    return list(universe)
