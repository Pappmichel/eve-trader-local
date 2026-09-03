"""ESI sync for the Doctrine tool.

The parent eve-trader repo splits this into two independent flows under two
separate character-auth groups ("doctrine" for contracts, "doctrine-assets"
for Stockpile's own asset cache) - reasoned there as letting a multi-tenant
operator's Doctrine-only tenant use Stockpile standalone without ever
touching Production, and as not asking a contracts-only character for
asset-read scopes it doesn't need.

Neither reason carries over to a single-user local install: there is only
ever one person here, and EVE SSO shows every requested scope on the same
consent screen regardless of how many role prefixes they're split across, so
combining them costs nothing and means one `auth --role doctrine` login
covers the whole tool. So this repo deliberately merges both into one
DOCTRINE_ROLE_PREFIX/DOCTRINE_SCOPES pair and one sync_doctrine() entry
point - a real simplification, not an oversight, following the same
"single-user reality removes the need for a distinction that only existed
for multi-tenant reasons" pattern this repo's CLAUDE.md/SYNC.md already
document elsewhere (e.g. Production's dropped storage.with_current_tenant
wrapping).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Optional

from .. import storage
from ..auth import TokenManager
from ..config import OAUTH_CONFIG, OAuthConfig
from ..errors import ActionError
from ..esi_client import ESIClient, ESIError
from . import engine
from .config import DOCTRINE_CONFIG, DoctrineConfig
from .constants import CONTRACT_TYPE_ITEM_EXCHANGE, FINISHED_CONTRACT_STATUSES, SYNCABLE_CONTRACT_STATUSES
from .models import ContractItemRow

DOCTRINE_ROLE_PREFIX = "doctrine"

# Union of the parent's two separate scope groups - see this module's own
# docstring for why they're combined here. EVE SSO rejects the whole login
# with "invalid_scope" if the dev-portal app doesn't have one of these
# enabled, so don't add any without enabling it there too.
DOCTRINE_SCOPES = [
    "esi-contracts.read_character_contracts.v1",
    "esi-contracts.read_corporation_contracts.v1",
    "esi-assets.read_assets.v1",
    "esi-assets.read_corporation_assets.v1",
]


def list_doctrine_characters(tm: Optional[TokenManager] = None) -> list[tuple[str, int, str]]:
    """[(role_key, character_id, character_name)] for every registered
    doctrine character. Uses stored records without refreshing anything -
    same reasoning as production/esi_sync.py's list_producer_characters."""
    tm = tm or TokenManager(OAUTH_CONFIG)
    return [(r.role, r.character_id, r.character_name) for r in tm.list_records(DOCTRINE_ROLE_PREFIX)]


def _passes_prefilter(contract: dict, structure_id: Optional[int]) -> bool:
    """Applied before any items fetch, never after (an items fetch is 1 ESI
    call per contract)."""
    return (
        contract.get("type") == CONTRACT_TYPE_ITEM_EXCHANGE
        and contract.get("status") in SYNCABLE_CONTRACT_STATUSES
        and structure_id is not None
        and contract.get("start_location_id") == structure_id
    )


def _passes_history_filter(contract: dict, structure_id: Optional[int]) -> bool:
    """Same shape as _passes_prefilter above, for FINISHED_CONTRACT_STATUSES
    instead of SYNCABLE_CONTRACT_STATUSES (GitHub issue #19) - a finished
    contract is about to drop out of the *active* snapshot (SYNCABLE_CONTRACT_
    STATUSES doesn't include "finished"), so this is checked separately, over
    the same already-fetched raw contract list, to catch it for doctrine_
    contract_history before that happens."""
    return (
        contract.get("type") == CONTRACT_TYPE_ITEM_EXCHANGE
        and contract.get("status") in FINISHED_CONTRACT_STATUSES
        and structure_id is not None
        and contract.get("start_location_id") == structure_id
    )


def _issued_by_own_identity(contract: dict, character_id: int, corporation_id: Optional[int]) -> bool:
    """ESI's character-contracts endpoint returns every contract the
    character is issuer, acceptor, OR ASSIGNEE of - a public/third-party
    contract merely *assigned* to this character or their corp (never
    actually created by them) would otherwise leak into the Doctrine match
    pool. Only contracts this character (or their own corp) actually issued
    are relevant."""
    return contract.get("issuer_id") == character_id or (
        corporation_id is not None and contract.get("issuer_corporation_id") == corporation_id
    )


def _fetch_character_contracts(client: ESIClient, role: str, character_id: int) -> dict:
    """One character's own contracts + (best-effort) corporation_id - errors
    are returned, not raised, so one bad token/scope only drops that
    character."""
    result: dict = {"role": role, "contracts": [], "corporation_id": None, "error": None}
    try:
        result["contracts"] = client.character_contracts(character_id, auth_role=role)
    except ESIError as e:
        result["error"] = str(e)
        return result
    try:
        result["corporation_id"] = client.character_public_info(character_id)["corporation_id"]
    except ESIError:
        pass  # corp contracts just won't be attempted for this character
    return result


def sync_contracts(cfg: DoctrineConfig = DOCTRINE_CONFIG, client: Optional[ESIClient] = None,
                   tm: Optional[TokenManager] = None) -> dict:
    """Pulls character + corporation item_exchange contracts for every
    registered doctrine character, pre-filters to this app's own structure
    before ever fetching a contract's items, matches each surviving contract
    against the active Fitting pool, and writes one full snapshot.

    Raises ActionError only if no doctrine character is registered at all, or
    the structure isn't configured - every per-character/per-contract failure
    degrades into the returned report instead."""
    structure_id = cfg.effective_structure_id
    if structure_id is None:
        raise ActionError(
            "No structure configured for Doctrine contract sync. Set doctrine_structure_id "
            "(or Trading's own structure_id, which this falls back to)."
        )

    tm = tm or TokenManager(OAUTH_CONFIG)
    characters = list_doctrine_characters(tm)
    if not characters:
        raise ActionError("No doctrine character logged in yet. Run: eve-trader-local auth --role doctrine")

    client = client or ESIClient(tokens=tm)
    per_character: dict = {}
    # (raw_contract, source_role, for_corporation, corporation_id)
    all_contracts: list[tuple[dict, str, bool, Optional[int]]] = []
    seen_contract_ids: set[int] = set()
    corp_contracts_done: dict[int, list[dict]] = {}
    corp_error_by_id: dict[int, str] = {}
    # (raw_contract, source_role) for contracts that just finished (GitHub
    # issue #19) - collected from the same already-fetched raw contract
    # lists as all_contracts above, no extra ESI calls. Its own seen-set, not
    # shared with seen_contract_ids: a contract could in principle show up as
    # "outstanding" via one character's view and "finished" via another's in
    # the same sync (a small race on ESI's own side), and both sets
    # independently dedupe against themselves only.
    all_history_contracts: list[tuple[dict, str]] = []
    seen_history_ids: set[int] = set()

    for role, character_id, _character_name in characters:
        result = _fetch_character_contracts(client, role, character_id)
        char_name = next(n for r, _c, n in characters if r == role)
        if result["error"] is not None:
            per_character[char_name] = f"skipped ({result['error']})"
            continue
        char_contracts = [c for c in result["contracts"]
                          if _passes_prefilter(c, structure_id)
                          and _issued_by_own_identity(c, character_id, result["corporation_id"])]
        for c in char_contracts:
            if c["contract_id"] not in seen_contract_ids:
                seen_contract_ids.add(c["contract_id"])
                all_contracts.append((c, role, False, None))
        for c in result["contracts"]:
            if (_passes_history_filter(c, structure_id)
                    and _issued_by_own_identity(c, character_id, result["corporation_id"])
                    and c["contract_id"] not in seen_history_ids):
                seen_history_ids.add(c["contract_id"])
                all_history_contracts.append((c, role))
        per_character[char_name] = {"contracts_seen": len(result["contracts"]),
                                     "contracts_matched_filter": len(char_contracts)}

        corporation_id = result["corporation_id"]
        if corporation_id is None or corporation_id in corp_contracts_done or corporation_id in corp_error_by_id:
            continue
        try:
            corp_raw = client.corporation_contracts(corporation_id, auth_role=role)
        except ESIError as e:
            corp_error_by_id[corporation_id] = str(e)  # a later character in this corp might have access
            continue
        corp_contracts_done[corporation_id] = corp_raw
        corp_filtered = [c for c in corp_raw
                         if _passes_prefilter(c, structure_id)
                         and _issued_by_own_identity(c, character_id, corporation_id)]
        for c in corp_filtered:
            if c["contract_id"] not in seen_contract_ids:
                seen_contract_ids.add(c["contract_id"])
                all_contracts.append((c, role, True, corporation_id))
        for c in corp_raw:
            if (_passes_history_filter(c, structure_id)
                    and _issued_by_own_identity(c, character_id, corporation_id)
                    and c["contract_id"] not in seen_history_ids):
                seen_history_ids.add(c["contract_id"])
                all_history_contracts.append((c, role))

    if not all_contracts and not per_character:
        raise ActionError("No doctrine character's contracts could be fetched - check tokens/scopes.")

    # Only fetch items for contracts that are new or whose status changed
    # since the last snapshot - an already-known, unchanged contract's items
    # are immutable in ESI and just carried forward instead.
    existing_by_id = {row[0]: row for row in storage.load_doctrine_contracts()}
    to_fetch: list[tuple[dict, str, bool, Optional[int]]] = []
    carried_items: dict[int, list[tuple]] = {}
    for raw, role, for_corp, corp_id in all_contracts:
        cid = raw["contract_id"]
        existing = existing_by_id.get(cid)
        if existing is not None and existing[5] == raw.get("status"):
            carried_items[cid] = storage.load_doctrine_contract_items(cid)
        else:
            to_fetch.append((raw, role, for_corp, corp_id))

    def _fetch_items(entry: tuple[dict, str, bool, Optional[int]]) -> tuple[int, Optional[list[dict]], Optional[str]]:
        raw, role, for_corp, corp_id = entry
        cid = raw["contract_id"]
        try:
            if for_corp:
                items = client.corporation_contract_items(corp_id, cid, auth_role=role)
            else:
                character_id = next(c_id for r, c_id, _n in characters if r == role)
                items = client.character_contract_items(character_id, cid, auth_role=role)
            return cid, items, None
        except ESIError as e:
            return cid, None, str(e)

    fetched_items: dict[int, list[dict]] = {}
    fetch_errors: dict[int, str] = {}
    if to_fetch:
        with ThreadPoolExecutor(max_workers=min(8, len(to_fetch))) as pool:
            for cid, items, error in pool.map(_fetch_items, to_fetch):
                if error is not None:
                    fetch_errors[cid] = error
                else:
                    fetched_items[cid] = items or []

    # Contracts whose items fetch failed this run are dropped from this
    # snapshot entirely - never write a contract with no items, that would
    # look like a real "invalid" doctrine violation instead of a transient
    # data gap. They're simply retried next sync.
    usable = [(raw, role, for_corp) for raw, role, for_corp, _corp_id in all_contracts
              if raw["contract_id"] in carried_items or raw["contract_id"] in fetched_items]

    candidates = engine.load_match_candidates()
    contract_rows: list[tuple] = []
    item_rows: list[tuple] = []
    deviation_rows: list[tuple] = []
    synced_at = datetime.now(timezone.utc).isoformat()
    no_hull_match_count = 0

    for raw, role, for_corp in usable:
        cid = raw["contract_id"]
        raw_items = carried_items.get(cid)
        if raw_items is not None:
            items = [ContractItemRow(cid, record_id, type_id, qty, bool(is_incl), bool(is_single))
                     for record_id, type_id, qty, is_incl, is_single in raw_items]
        else:
            items = [ContractItemRow(cid, it["record_id"], it["type_id"], it["quantity"],
                                      it.get("is_included", True), it.get("is_singleton", False))
                     for it in fetched_items[cid]]

        matched_fitting_id, score, deviations, status = engine.match_and_validate_contract(
            cid, raw.get("title"), items, candidates, cfg)

        if status == engine.NO_HULL_MATCH:
            # Not a Doctrine ship sale at all - never persisted.
            no_hull_match_count += 1
            continue

        contract_rows.append((
            cid, role, for_corp, raw.get("issuer_id"), raw.get("start_location_id"), raw.get("status"),
            raw.get("title"), raw.get("price"), raw.get("date_expired"), matched_fitting_id, score,
            status, synced_at,
        ))
        for it in items:
            item_rows.append((cid, it.record_id, it.type_id, it.quantity, it.is_included, it.is_singleton))
        for d in deviations:
            deviation_rows.append((cid, d.type_id, d.kind, d.expected_qty, d.actual_qty, d.severity))

    # GitHub issue #19: record every newly-finished contract into permanent
    # history before replace_doctrine_sync_snapshot below drops it from the
    # active table - reuses whichever fitting this contract was already
    # matched against while it was still outstanding (existing_by_id, loaded
    # above from the *previous* sync's snapshot) rather than re-fetching/
    # re-matching its items, which are immutable now anyway. fitting_name/
    # hull_type_id are denormalized here (captured once, from whatever the
    # fitting looks like right now) rather than resolved live at read time -
    # see doctrine_contract_history's own schema comment for why.
    acceptor_ids = {c.get("acceptor_id") for c, _role in all_history_contracts if c.get("acceptor_id")}
    acceptor_names = client.resolve_names(list(acceptor_ids)) if acceptor_ids else {}
    history_rows: list[tuple] = []
    for raw, role in all_history_contracts:
        cid = raw["contract_id"]
        existing = existing_by_id.get(cid)
        fitting_id = existing[9] if existing is not None else None  # _CONTRACT_COLUMNS' matched_fitting_id
        if fitting_id is None:
            # A contract that never matched any doctrine fitting (someone
            # else's unrelated item sale at the same structure, or a doctrine
            # sale that was already finished the very first time we ever saw
            # it) isn't a doctrine contract at all and shouldn't clutter
            # permanent history.
            continue
        fitting_row = storage.get_fitting(fitting_id)
        if fitting_row is None:
            continue
        fitting_name, hull_type_id = fitting_row[2], fitting_row[4]  # _FITTING_COLUMNS' name/hull_type_id
        acceptor_id = raw.get("acceptor_id")
        history_rows.append((
            cid, role, fitting_id, fitting_name, hull_type_id, raw.get("title"), raw.get("price"),
            acceptor_id, acceptor_names.get(acceptor_id) if acceptor_id is not None else None,
            raw.get("date_issued"), raw.get("date_completed"),
        ))

    storage.replace_doctrine_sync_snapshot(contract_rows, item_rows, deviation_rows)
    storage.upsert_doctrine_contract_history(history_rows)

    return {
        "characters": per_character,
        "contracts_synced": len(contract_rows),
        "contracts_dropped_this_run": len(all_contracts) - len(usable),
        "contracts_no_relevant_hull": no_hull_match_count,
        "corp_errors": {str(k): v for k, v in corp_error_by_id.items()},
        "item_fetch_errors": {str(k): v for k, v in fetch_errors.items()},
    }


# =========================================================== asset sync (Stockpile)
def _asset_rows(assets: list[dict]) -> list[tuple]:
    return [
        (a["item_id"], a["type_id"], a["location_id"], a["location_flag"],
         a["quantity"], int(bool(a.get("is_blueprint_copy"))), a.get("owner_name"))
        for a in assets
    ]


def sync_assets(client: Optional[ESIClient] = None, tm: Optional[TokenManager] = None) -> dict:
    """Doctrine's own asset sync (Stockpile's Ist side) - deliberately its own
    tables (doctrine_character_assets/doctrine_corp_assets), not a read of
    Production's, so Doctrine works standalone even if Production was never
    set up. Same registered-character group as sync_contracts (see this
    module's own docstring)."""
    tm = tm or TokenManager(OAUTH_CONFIG)
    characters = list_doctrine_characters(tm)
    if not characters:
        raise ActionError("No doctrine character logged in yet. Run: eve-trader-local auth --role doctrine")
    client = client or ESIClient(tokens=tm)

    def _fetch(c: tuple[str, int, str]) -> dict:
        role, character_id, character_name = c
        result: dict = {"character_name": character_name, "role": role, "assets": [],
                        "corporation_id": None, "error": None}
        try:
            assets = client.character_assets(character_id, auth_role=role)
        except ESIError as e:
            result["error"] = str(e)
            return result
        for a in assets:
            a["owner_name"] = character_name
        result["assets"] = assets
        try:
            result["corporation_id"] = client.character_public_info(character_id)["corporation_id"]
        except ESIError:
            pass
        return result

    with ThreadPoolExecutor(max_workers=min(8, len(characters))) as pool:
        char_results = list(pool.map(_fetch, characters))

    all_char_assets: list[dict] = []
    all_corp_assets: list[dict] = []
    per_character: dict = {}
    per_corporation: dict = {}
    corp_done: set[int] = set()
    corp_error: dict[int, str] = {}

    for r in char_results:
        character_name = r["character_name"]
        if r["error"] is not None:
            per_character[character_name] = f"skipped ({r['error']})"
            continue
        all_char_assets.extend(r["assets"])
        per_character[character_name] = {"assets": len(r["assets"])}

        corporation_id = r["corporation_id"]
        if corporation_id is None or corporation_id in corp_done or corporation_id in corp_error:
            continue
        try:
            corp_assets = client.corporation_assets(corporation_id, auth_role=r["role"])
        except ESIError as e:
            corp_error[corporation_id] = str(e)  # a later character in this corp might have the Director role
            continue
        try:
            corp_name = client.corporation_public_info(corporation_id).get("name", str(corporation_id))
        except ESIError:
            corp_name = str(corporation_id)
        for a in corp_assets:
            a["owner_name"] = f"{corp_name} (corp)"
        corp_done.add(corporation_id)
        all_corp_assets.extend(corp_assets)
        per_corporation[corp_name] = {"assets": len(corp_assets)}

    storage.replace_assets("doctrine_character_assets", _asset_rows(all_char_assets))
    storage.replace_assets("doctrine_corp_assets", _asset_rows(all_corp_assets))

    return {"characters": per_character, "corporations": per_corporation}


def sync_doctrine(cfg: DoctrineConfig = DOCTRINE_CONFIG) -> dict:
    """Runs both halves (contracts, then assets) against one shared
    ESIClient/TokenManager, and stamps the combined sync time. Each half is
    isolated in its own try/except: a fatal problem in one (e.g. no
    structure configured for contracts) must not discard the other half's
    already-fetched-and-persisted result - sync_contracts/sync_assets each
    write their own tables internally before returning, so a later failure
    here can't undo an earlier success. Raises only if *both* halves fail."""
    tm = TokenManager(OAUTH_CONFIG)
    client = ESIClient(tokens=tm)
    result: dict = {}
    errors: list[str] = []
    try:
        result["contracts"] = sync_contracts(cfg, client=client, tm=tm)
    except ActionError as e:
        result["contracts"] = {"error": str(e)}
        errors.append(str(e))
    try:
        result["assets"] = sync_assets(client=client, tm=tm)
    except ActionError as e:
        result["assets"] = {"error": str(e)}
        errors.append(str(e))
    if len(errors) == 2:
        raise ActionError(f"Doctrine sync failed entirely: {'; '.join(errors)}")
    storage.set_esi_sync_time("doctrine", datetime.now(timezone.utc).isoformat())
    return result
