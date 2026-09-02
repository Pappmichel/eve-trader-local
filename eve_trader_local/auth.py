"""EVE SSO OAuth2 (authorization code + PKCE) handling.

Ported essentially as-is from the parent eve-trader repo, because this part
of its design was already correct for a local app: the login flow binds a
throwaway http.server on loopback just long enough to catch the SSO
redirect, then shuts it down. No persistent server, no hosted callback
endpoint - which is exactly why a server-less variant can keep the whole
flow unchanged. The only real adaptation is the storage layer: token records
go to the local SQLite `tokens` table with no tenant scoping.

Tokens are stored per character as "<role>:<character_id>" (which character
logs in isn't known until *after* the redirect), so authorizing twice under
the same role registers a second character rather than overwriting the
first.

Usage:
    tm = TokenManager()
    tm.login("buyer")            # opens a browser once
    tm.get_token(record.role)    # reuses/refreshes silently afterwards
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import secrets
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import asdict, dataclass
from typing import Optional

import requests

from . import storage
from .config import OAUTH_CONFIG, OAuthConfig
from .errors import ActionError

LOGIN_TIMEOUT_SECONDS = 300


@dataclass
class TokenRecord:
    role: str                  # e.g. "buyer:123456789"
    character_id: int
    character_name: str
    access_token: str
    refresh_token: str
    expires_at: float           # unix timestamp
    scopes: str

    def is_expired(self, skew_seconds: int = 60) -> bool:
        return time.time() >= (self.expires_at - skew_seconds)


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Captures the ?code=...&state=... redirect from EVE SSO."""

    result: dict = {}

    def do_GET(self):  # noqa: N802 (http.server API)
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _CallbackHandler.result = {
            "code": qs.get("code", [None])[0],
            "state": qs.get("state", [None])[0],
            "error": qs.get("error_description", [None])[0],
        }
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        msg = ("Login successful, you can close this window."
               if _CallbackHandler.result["code"]
               else f"Login failed: {_CallbackHandler.result['error']}")
        self.wfile.write(f"<html><body><h2>{msg}</h2></body></html>".encode("utf-8"))

    def log_message(self, format, *args):  # noqa: A002 - silence default stderr logging
        pass


def make_pkce_pair() -> tuple[str, str]:
    """(verifier, challenge). Both are base64url *without* padding - RFC 7636
    requires the padding stripped, and EVE SSO rejects the exchange if it
    isn't."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode("ascii")
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    return verifier, challenge


class TokenManager:
    def __init__(self, cfg: OAuthConfig = OAUTH_CONFIG):
        self.cfg = cfg
        self._tokens: dict[str, TokenRecord] = {}
        self._loaded = False

    # ---------------------------------------------------------------- storage
    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self._load()

    def _load(self) -> None:
        self._tokens = {role: TokenRecord(**rec) for role, rec in storage.load_all_tokens().items()}
        self._loaded = True

    def _save_record(self, role: str) -> None:
        storage.save_token(role, asdict(self._tokens[role]))

    # -------------------------------------------------------------- SSO flow
    def login(self, role_prefix: str, scopes: Optional[list[str]] = None) -> TokenRecord:
        """Full browser login. Stores the result under
        f"{role_prefix}:{character_id}" - the character id is only known
        after the token comes back, so the final key can't be decided
        up front."""
        scopes = scopes or list(self.cfg.scopes)
        token_json = self._authorize_browser_flow(role_prefix, scopes)
        character_id, character_name = self._verify(token_json["access_token"])
        role = f"{role_prefix}:{character_id}"
        record = self._to_record(role, token_json, " ".join(scopes),
                                 character_id=character_id, character_name=character_name)
        self._ensure_loaded()
        self._tokens[role] = record
        self._save_record(role)
        return record

    def _authorize_browser_flow(self, label: str, scopes: list[str]) -> dict:
        if not self.cfg.client_id:
            raise ActionError(
                "EVE_SSO_CLIENT_ID is not set. Register an application at "
                "https://developers.eveonline.com and put its client id in a "
                ".env file (see .env.example)."
            )
        state = secrets.token_urlsafe(16)
        verifier, challenge = make_pkce_pair()

        params = {
            "response_type": "code",
            "redirect_uri": self.cfg.redirect_uri,
            "client_id": self.cfg.client_id,
            "scope": " ".join(scopes),
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        url = f"{self.cfg.authorize_url}?{urllib.parse.urlencode(params)}"

        _CallbackHandler.result = {}
        try:
            server = http.server.HTTPServer(
                (self.cfg.callback_host, self.cfg.callback_port), _CallbackHandler
            )
        except OSError as e:
            raise ActionError(
                f"Can't start the login callback server on {self.cfg.callback_host}:"
                f"{self.cfg.callback_port} ({e}) - something else is already using "
                "that port. Stop it, or point EVE_SSO_CALLBACK_PORT at a free port "
                "and register the matching callback URL at developers.eveonline.com."
            ) from e
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()

        print(f"[{label}] Opening browser for EVE SSO login: {url}")
        webbrowser.open(url)
        thread.join(timeout=LOGIN_TIMEOUT_SECONDS)
        server.server_close()

        result = _CallbackHandler.result
        if not result.get("code"):
            raise ActionError(f"SSO login failed or timed out: {result.get('error')}")
        if result.get("state") != state:
            raise ActionError("SSO state mismatch - possible CSRF, aborting.")
        return self._exchange_code(result["code"], verifier)

    def _exchange_code(self, code: str, verifier: str) -> dict:
        resp = requests.post(
            self.cfg.token_url,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": self.cfg.client_id,
                "code_verifier": verifier,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def _refresh(self, record: TokenRecord) -> TokenRecord:
        resp = requests.post(
            self.cfg.token_url,
            data={
                "grant_type": "refresh_token",
                "refresh_token": record.refresh_token,
                "client_id": self.cfg.client_id,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        if resp.status_code >= 400:
            raise ActionError(
                f"Refreshing the token for '{record.role}' failed ({resp.status_code}). "
                f"The refresh token may have been revoked - re-run "
                f"`eve-trader-local auth --role {record.role.split(':')[0]}`."
            )
        new_record = self._to_record(record.role, resp.json(), record.scopes,
                                     character_id=record.character_id,
                                     character_name=record.character_name)
        self._tokens[record.role] = new_record
        self._save_record(record.role)
        return new_record

    def _to_record(self, role: str, token_json: dict, scopes: str,
                   character_id: Optional[int] = None,
                   character_name: Optional[str] = None) -> TokenRecord:
        access_token = token_json["access_token"]
        if character_id is None or character_name is None:
            character_id, character_name = self._verify(access_token)
        return TokenRecord(
            role=role,
            character_id=character_id,
            character_name=character_name,
            access_token=access_token,
            refresh_token=token_json.get("refresh_token", ""),
            expires_at=time.time() + token_json.get("expires_in", 1200),
            scopes=scopes,
        )

    def _verify(self, access_token: str) -> tuple[int, str]:
        """Resolves character id/name from an access token via /oauth/verify."""
        resp = requests.get(
            self.cfg.verify_url,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return int(data["CharacterID"]), data["CharacterName"]

    # ------------------------------------------------------------ public API
    def get_token(self, role: str) -> TokenRecord:
        """Refreshes transparently when expired. No lock, unlike the parent
        repo's version - that guarded concurrent requests hitting a shared
        FastAPI thread pool, which a single-user local process doesn't have."""
        self._ensure_loaded()
        record = self._tokens.get(role)
        if record is None:
            raise ActionError(
                f"No stored token for role '{role}'. Run "
                f"`eve-trader-local auth --role {role.split(':')[0]}` first."
            )
        if record.is_expired():
            record = self._refresh(record)
        return record

    def get_record(self, role: str) -> Optional[TokenRecord]:
        """Like get_token but never refreshes - character id/name are plain
        stored fields, so listing who is registered shouldn't be able to fail
        on one dead refresh token and take the whole list down with it."""
        self._ensure_loaded()
        return self._tokens.get(role)

    def has_token(self, role: str) -> bool:
        self._ensure_loaded()
        return role in self._tokens

    def auth_header(self, role: str) -> dict:
        return {"Authorization": f"Bearer {self.get_token(role).access_token}"}

    def list_records(self, prefix: Optional[str] = None) -> list[TokenRecord]:
        """Every stored record, or just those under f"{prefix}:"."""
        self._ensure_loaded()
        records = list(self._tokens.values())
        if prefix is not None:
            records = [r for r in records if r.role.startswith(f"{prefix}:")]
        return sorted(records, key=lambda r: r.role)

    def remove_token(self, role: str) -> None:
        """Unconditional delete - idempotent at the storage layer, so callers
        never need to check existence first."""
        self._tokens.pop(role, None)
        storage.delete_token(role)
