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
from . import esi_sync

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
