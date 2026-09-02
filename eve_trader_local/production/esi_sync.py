"""Pulls what a producer character (and their corp) actually owns out of ESI
into the local snapshot tables: assets, blueprints with their real ME/TE, and
industry jobs in progress.

Without this the build-cost engine has to assume things it can now look up.
The concrete one today is Tech I ME/TE: engine._owned_bpo_mods asks
storage.get_owned_bpo_best_me_te for your actual researched BPO and only falls
back to the flat "perfectly researched" baseline when you don't own it.

Multiple characters are supported: each is authorized separately (role
"producer:<character_id>", see auth.TokenManager.login) and sync_esi() pulls
all of them into one combined picture. Merging is safe because item_id/job_id
are globally unique across characters.

Corp-level calls need that character to hold the Director role in-game; if
none of the registered characters in a given corp have it, that corp's data is
skipped rather than failing the sync. If several characters share a corp it is
retried with each in turn until one succeeds, so adding a non-Director alt
first doesn't lock the corp out.

There is no such thing as authorizing a corporation directly in EVE's SSO -
corp-level scopes are always granted through a member character. To track a
corp, register a character who belongs to it and holds Director.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from .. import storage
from ..auth import TokenManager
from ..config import OAUTH_CONFIG, OAuthConfig
from ..errors import ActionError
from ..esi_client import ESIClient, ESIError
from .constants import ACTIVITY_REACTION

PRODUCER_ROLE_PREFIX = "producer"

# Exactly what sync_esi() calls, and nothing more. EVE SSO rejects the whole
# login with "invalid_scope" if the dev-portal app doesn't have one of these
# enabled, so don't add any without enabling it there too.
#
# esi-markets.structure_markets.v1 is here without a caller in this module:
# production/pricing.py reads the home structure's order book through the same
# `producer` role, and a character registered without that scope would have to
# be re-added before live home pricing worked for it.
PRODUCTION_SCOPES = [
    "esi-assets.read_assets.v1",
    "esi-assets.read_corporation_assets.v1",
    "esi-industry.read_character_jobs.v1",
    "esi-industry.read_corporation_jobs.v1",
    "esi-characters.read_blueprints.v1",
    "esi-corporations.read_blueprints.v1",
    "esi-markets.structure_markets.v1",
]


def list_producer_characters(tm: Optional[TokenManager] = None) -> list[tuple[str, int, str]]:
    """[(role_key, character_id, character_name)] for every registered producer
    character.

    Uses the stored records without refreshing anything: character id and name
    are plain stored fields, so merely listing who is registered must not be
    able to fail on one dead refresh token. sync_esi() handles a refresh
    failure per character instead, where it can report that one as skipped."""
    tm = tm or TokenManager(OAUTH_CONFIG)
    return [(r.role, r.character_id, r.character_name)
            for r in tm.list_records(PRODUCER_ROLE_PREFIX)]


def _asset_rows(assets: list[dict]) -> list[tuple]:
    return [
        (a["item_id"], a["type_id"], a["location_id"], a["location_flag"],
         a["quantity"], int(bool(a.get("is_blueprint_copy"))), a.get("owner_name"))
        for a in assets
    ]


_LIVE_REACTION_ACTIVITY_ID = 9  # see _normalize_activity_id


def _normalize_activity_id(activity_id: int) -> int:
    """A real CCP data inconsistency, confirmed in the parent repo: the live
    industry-jobs endpoints report Reaction jobs as activity_id 9, while the
    SDE's static blueprint data files every Reaction recipe under activity_id
    11 (constants.ACTIVITY_REACTION) - live jobs never use 11 at all.

    Left unnormalized, every consumer that looks a live activity_id up against
    SDE-keyed data silently breaks for reactions: the job's product quantity
    can't be resolved, and the reaction never counts against a character's
    reaction slots. Normalizing once here, at ingestion, lets the rest of the
    codebase treat 11 as the one and only Reaction activity_id."""
    return ACTIVITY_REACTION if activity_id == _LIVE_REACTION_ACTIVITY_ID else activity_id


def _industry_job_rows(jobs: list[dict], installer_names: dict[int, str]) -> list[tuple]:
    return [
        (j["job_id"], _normalize_activity_id(j["activity_id"]), j["blueprint_type_id"],
         j.get("product_type_id"), j["runs"], j.get("output_location_id"), j["status"],
         j["end_date"], j.get("start_date"), j.get("installer_id"),
         installer_names.get(j.get("installer_id"), str(j.get("installer_id"))))
        for j in jobs
    ]


def _blueprint_rows(bps: list[dict]) -> list[tuple]:
    return [
        (b["item_id"], b["type_id"], b["location_id"], b["location_flag"], b["quantity"],
         b["material_efficiency"], b["time_efficiency"], b["runs"])
        for b in bps
    ]


def _fetch_character_data(client: ESIClient, role: str, character_id: int,
                          character_name: str) -> dict:
    """Everything about ONE character that doesn't depend on any other
    character: their own assets/jobs/blueprints, plus which corp they belong to
    (a public read - it fetches nothing corp-level itself).

    This is the parallelizable half of sync_esi(). Each character's calls are
    purely network-latency-bound and independent of every other character's,
    which is what makes a thread pool worth it at all; the corp-level half
    stays sequential, see sync_esi's own docstring.

    Sharing one ESIClient (and its one requests.Session) across threads is the
    same thing esi_client._get_all_pages already does internally - the error-
    limit budget is class-level-locked precisely so concurrent callers are
    safe. Token refresh, which is *not* locked in this repo, is deliberately
    done on the main thread before the pool starts (see sync_esi)."""
    result: dict = {
        "character_name": character_name, "role": role, "character_id": character_id,
        "assets": [], "jobs": [], "bps": [],
        "corporation_id": None, "corp_name": None, "summary": {},
    }
    try:
        assets = client.character_assets(character_id, auth_role=role)
        jobs = client.character_industry_jobs(character_id, auth_role=role)
        bps = client.character_blueprints(character_id, auth_role=role)
    except ESIError as e:
        result["summary"] = f"skipped ({e})"
        return result

    for a in assets:
        a["owner_name"] = character_name
    result["assets"], result["jobs"], result["bps"] = assets, jobs, bps
    result["summary"] = {"assets": len(assets), "industry_jobs": len(jobs), "blueprints": len(bps)}

    try:
        result["corporation_id"] = client.character_public_info(character_id)["corporation_id"]
        result["corp_name"] = client.corporation_public_info(
            result["corporation_id"]).get("name", str(result["corporation_id"]))
    except (ESIError, KeyError) as e:
        # Non-fatal: this character's own data is already in hand, only their
        # corp's is unreachable.
        result["summary"]["corp"] = f"skipped (public info fetch failed: {e})"
    return result


def _usable_characters(tm: TokenManager, characters: list[tuple[str, int, str]],
                       summary: dict) -> list[tuple[str, int, str]]:
    """Refreshes each character's token up front, on the calling thread, and
    drops (recording in `summary`) any whose refresh fails.

    Two reasons this is not left to happen lazily inside the worker threads:
    a revoked/expired refresh token should be reported as "this character was
    skipped" rather than surfacing as a mid-fetch failure, and this repo's
    TokenManager deliberately has no refresh lock (single-user, no web thread
    pool - see its own comment), so refreshing must not be the thing the pool
    ends up doing concurrently."""
    usable = []
    for role, character_id, character_name in characters:
        try:
            tm.get_token(role)
        except ActionError as e:
            summary[character_name] = f"skipped (re-authorize? {e})"
            continue
        usable.append((role, character_id, character_name))
    return usable


def sync_esi(oauth_cfg: OAuthConfig = OAUTH_CONFIG) -> dict:
    """Replaces the six ownership snapshot tables with what ESI reports right
    now, for every registered producer character and each of their corps.

    Every ESI call is individually isolated: one character missing a scope, or
    one corp whose characters lack Director, must not abort the whole sync - it
    is reported per character/per corp in the returned dict instead, so a
    partial sync still updates whatever it could actually fetch.

    Two phases. Phase A (_fetch_character_data, run across a thread pool)
    fetches everything that is independent per character. Phase B (below,
    sequential, in the original character order) handles corp-level data, which
    is stateful *across* characters - a given corp is fetched once, and retried
    with the next character sharing that corp if an earlier one lacked the
    Director role. Parallelizing that retry-until-success sequencing would let
    two characters race to claim the same corp. Corp count is small regardless
    of how many characters are registered, so the sequential half is cheap
    either way; wall time scales with character count, which Phase A covers."""
    tm = TokenManager(oauth_cfg)
    characters = list_producer_characters(tm)
    if not characters:
        raise ActionError(
            "No producer character authorized yet. Run: "
            "eve-trader-local auth --role producer"
        )

    per_character: dict = {}
    usable = _usable_characters(tm, characters, per_character)
    if not usable:
        raise ActionError(
            "Every registered producer character's token failed to refresh. "
            "Re-run: eve-trader-local auth --role producer"
        )

    client = ESIClient(tokens=tm)
    with ThreadPoolExecutor(max_workers=min(8, len(usable))) as pool:
        # list(), not as_completed() - Phase B's "first character in the
        # original order claims this corp" behavior depends on the order.
        char_results = list(pool.map(
            lambda c: _fetch_character_data(client, c[0], c[1], c[2]), usable))

    all_char_assets: list[dict] = []
    all_char_jobs: list[dict] = []
    all_char_bps: list[dict] = []
    all_corp_assets: list[dict] = []
    all_corp_jobs: list[dict] = []
    all_corp_bps: list[dict] = []
    per_corporation: dict = {}

    for r in char_results:
        per_character[r["character_name"]] = r["summary"]
        all_char_assets.extend(r["assets"])
        all_char_jobs.extend(r["jobs"])
        all_char_bps.extend(r["bps"])

        corporation_id, corp_name, role = r["corporation_id"], r["corp_name"], r["role"]
        if corporation_id is None:
            continue  # this character's own public-info fetch failed, already recorded
        if isinstance(per_corporation.get(corp_name), dict):
            continue  # already fetched via an earlier character in this corp

        try:
            corp_assets = client.corporation_assets(corporation_id, auth_role=role)
            corp_jobs = client.corporation_industry_jobs(corporation_id, auth_role=role)
            corp_bps = client.corporation_blueprints(corporation_id, auth_role=role)
        except ESIError as e:
            # Don't give up on this corp for good - a later character in the
            # loop may hold the Director role this one doesn't.
            per_corporation[corp_name] = f"skipped for {r['character_name']} (missing Director role? {e})"
            continue

        for a in corp_assets:
            a["owner_name"] = f"{corp_name} (corp)"
        all_corp_assets.extend(corp_assets)
        all_corp_jobs.extend(corp_jobs)
        all_corp_bps.extend(corp_bps)
        per_corporation[corp_name] = {
            "assets": len(corp_assets), "industry_jobs": len(corp_jobs),
            "blueprints": len(corp_bps),
        }

    installer_ids = [i for i in {j.get("installer_id") for j in all_char_jobs + all_corp_jobs} if i]
    installer_names = client.resolve_names(installer_ids) if installer_ids else {}

    # Assets before blueprints, per owner: replace_blueprints resolves a
    # blueprint's nested container against the matching asset table, so that
    # table has to hold this run's rows already.
    storage.replace_assets("character_assets", _asset_rows(all_char_assets))
    storage.replace_blueprints("character_blueprints", _blueprint_rows(all_char_bps))
    storage.replace_industry_jobs("character_industry_jobs",
                                  _industry_job_rows(all_char_jobs, installer_names))
    storage.replace_assets("corp_assets", _asset_rows(all_corp_assets))
    storage.replace_blueprints("corp_blueprints", _blueprint_rows(all_corp_bps))
    storage.replace_industry_jobs("corp_industry_jobs",
                                  _industry_job_rows(all_corp_jobs, installer_names))

    return {"characters": per_character, "corporations": per_corporation}
