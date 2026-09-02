"""Unit tests for the parts of the SSO flow that are testable without a
browser. The interactive login itself (browser + loopback redirect) is
deliberately not covered - it can't run headlessly, and mocking it end to
end would only test the mock."""
from __future__ import annotations

import base64
import hashlib
import time
from dataclasses import asdict

import pytest

from eve_trader_local import storage
from eve_trader_local.auth import TokenManager, TokenRecord, make_pkce_pair
from eve_trader_local.config import OAuthConfig
from eve_trader_local.errors import ActionError


def a_record(role: str = "buyer:42", **kw) -> TokenRecord:
    base = dict(role=role, character_id=42, character_name="Some Pilot",
                access_token="at", refresh_token="rt",
                expires_at=time.time() + 3600, scopes="scope-a scope-b")
    base.update(kw)
    return TokenRecord(**base)


# -------------------------------------------------------------------- PKCE
def test_pkce_challenge_is_sha256_of_verifier():
    verifier, challenge = make_pkce_pair()
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    assert challenge == expected


def test_pkce_pair_is_unpadded_and_random():
    verifier, challenge = make_pkce_pair()
    assert "=" not in verifier and "=" not in challenge
    assert 43 <= len(verifier) <= 128  # RFC 7636 length bounds
    assert make_pkce_pair()[0] != verifier


# ------------------------------------------------------------- TokenRecord
def test_is_expired_respects_skew():
    assert a_record(expires_at=time.time() + 3600).is_expired() is False
    assert a_record(expires_at=time.time() - 1).is_expired() is True
    # Inside the skew window a still-technically-valid token counts as expired,
    # so a refresh happens before a call goes out with it.
    assert a_record(expires_at=time.time() + 30).is_expired(skew_seconds=60) is True


# ------------------------------------------------------------ TokenManager
def test_records_round_trip_through_storage(db):
    record = a_record()
    storage.save_token(record.role, asdict(record))
    assert TokenManager().get_record("buyer:42") == record


def test_get_record_returns_none_for_unknown_role(db):
    assert TokenManager().get_record("nope:1") is None


def test_get_token_raises_action_error_when_missing(db):
    with pytest.raises(ActionError, match="No stored token"):
        TokenManager().get_token("buyer:42")


def test_get_token_returns_a_valid_stored_record(db):
    record = a_record()
    storage.save_token(record.role, asdict(record))
    assert TokenManager().get_token("buyer:42").access_token == "at"


def test_list_records_filters_by_prefix(db):
    for r in (a_record("buyer:42"), a_record("buyer:43", character_id=43), a_record("seller:44", character_id=44)):
        storage.save_token(r.role, asdict(r))
    tm = TokenManager()
    assert [r.role for r in tm.list_records("buyer")] == ["buyer:42", "buyer:43"]
    assert len(tm.list_records()) == 3


def test_remove_token_clears_cache_and_storage(db):
    record = a_record()
    storage.save_token(record.role, asdict(record))
    tm = TokenManager()
    assert tm.has_token("buyer:42")
    tm.remove_token("buyer:42")
    assert not tm.has_token("buyer:42")
    assert storage.load_all_tokens() == {}


def test_login_without_client_id_fails_before_opening_a_browser(db):
    """Must raise ActionError from the config check, never get as far as
    binding a port or launching a browser."""
    tm = TokenManager(OAuthConfig(client_id=""))
    with pytest.raises(ActionError, match="EVE_SSO_CLIENT_ID"):
        tm.login("buyer")


def test_to_record_uses_supplied_identity_without_calling_verify(db):
    tm = TokenManager(OAuthConfig(client_id="x"))
    record = tm._to_record("buyer:42", {"access_token": "new", "refresh_token": "r2", "expires_in": 1200},
                           "scope-a", character_id=42, character_name="Some Pilot")
    assert record.access_token == "new"
    assert record.character_name == "Some Pilot"
    assert record.expires_at > time.time()
