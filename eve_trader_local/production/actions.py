"""Production's orchestration layer - the Production-side counterpart to the
top-level actions.py, kept separate for the same reason the parent repo keeps
them apart: nothing here is useful to Trading and vice versa.

Same rule as everywhere else: no real logic lives here. Each do_* function
calls one module and returns a plain dict, so the CLI and a future GUI drive
identical code paths.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .. import storage
from ..auth import TokenManager
from ..config import OAUTH_CONFIG, OAuthConfig
from ..errors import ActionError
from . import engine, esi_sync
from .config import PRODUCTION_CONFIG, ProductionConfig
from .models import BuildCandidate

SYNC_SCOPE = "production"


def do_list_producer_characters(oauth_cfg: OAuthConfig = OAUTH_CONFIG) -> list[tuple[str, int, str]]:
    return esi_sync.list_producer_characters(TokenManager(oauth_cfg))


def do_remove_producer_character(role_key: str, oauth_cfg: OAuthConfig = OAUTH_CONFIG) -> dict:
    """Only drops the stored token. The last-synced assets/blueprints/jobs stay
    exactly as they are until the next do_sync_esi, which is when that
    character's rows actually disappear - removing a character is not itself a
    statement about what anyone owns."""
    TokenManager(oauth_cfg).remove_token(role_key)
    return {"removed": role_key}


def do_sync_esi(oauth_cfg: OAuthConfig = OAUTH_CONFIG) -> dict:
    """Refreshes every producer character's (and their corps') assets,
    blueprints and industry jobs from ESI.

    This is what puts real owned-BPO ME/TE behind engine._owned_bpo_mods, so it
    covers every case that can change a Tech I build cost: a newly registered
    character's first sync, a BPO finishing research, or a blueprint changing
    hands. Registering or removing a character changes no stored blueprint data
    by itself - only this does."""
    result = esi_sync.sync_esi(oauth_cfg)
    storage.set_esi_sync_time(SYNC_SCOPE, datetime.now(timezone.utc).isoformat())
    return result


def do_get_esi_sync_time() -> dict:
    return {"synced_at": storage.get_esi_sync_time(SYNC_SCOPE)}


def do_discover_build_candidates(top_n: int = 200, cfg: ProductionConfig = PRODUCTION_CONFIG) -> dict:
    """Scans every manufacturable, market-listed SDE item for ones where
    building clearly beats buying right now - see engine.
    discover_build_candidates. Needs the SDE cache populated (refresh-sde)
    to find anything at all."""
    if not storage.sde_row_counts().get("sde_types"):
        raise ActionError("SDE cache is empty. Run: eve-trader-local refresh-sde")
    candidates = engine.discover_build_candidates(cfg, top_n=top_n)
    return {"rows": [BuildCandidate(**c) for c in candidates]}
