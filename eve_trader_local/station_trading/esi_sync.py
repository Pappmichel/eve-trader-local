"""Character registration for the Station Trading tool's own "trader" role -
see production/esi_sync.py's PRODUCER_ROLE_PREFIX/list_producer_characters
for the precedent this mirrors.

Unlike Production, there's no sync_esi()-style bulk pull here: nothing this
tool computes is worth caching ahead of time (own orders and skill levels
are both read live, on demand, by actions.py - see undercut.py and this
module's own STATION_TRADING_SCOPES for why: an undercut check is only ever
meaningful against the current live order book, and a skill level changes
rarely enough that a live per-request pull costs nothing worth caching).
"""
from __future__ import annotations

from typing import Optional

from ..auth import TokenManager
from ..config import OAUTH_CONFIG, OAuthConfig

STATION_TRADING_ROLE_PREFIX = "trader"

STATION_TRADING_SCOPES = [
    "esi-markets.read_character_orders.v1",
    "esi-skills.read_skills.v1",
]


def list_trader_characters(tm: Optional[TokenManager] = None) -> list[tuple[str, int, str]]:
    """[(role_key, character_id, character_name)] for every registered trader
    character, e.g. [("trader:2112625428", 2112625428, "Some Character")] -
    uses the stored records without refreshing anything, same reasoning as
    production/esi_sync.py's list_producer_characters: merely listing who is
    registered must not be able to fail on one dead refresh token."""
    tm = tm or TokenManager(OAUTH_CONFIG)
    return [(r.role, r.character_id, r.character_name)
            for r in tm.list_records(STATION_TRADING_ROLE_PREFIX)]
