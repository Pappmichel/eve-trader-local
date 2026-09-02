"""Configuration, loaded from (in increasing priority):

1. the dataclass defaults below
2. config.yaml (see paths.config_path)
3. overrides stored in SQLite (settings table)

Secrets (SSO client id, callback host/port) come from the environment / a
.env file instead, never from config.yaml - they are per-install credentials,
not settings.

Unlike the parent eve-trader repo there is no ConfigProxy and no
contextvars machinery here: that exists purely to give each tenant of a
shared web backend its own config instance per request. A single-user local
app has exactly one config, so a plain module-level instance is correct.
The "validate everything before applying anything" discipline is kept - a
bad value must never land half-applied.
"""
from __future__ import annotations

import copy
import os
import typing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from . import storage
from .errors import ConfigError
from .paths import PROJECT_ROOT, config_path


def load_dotenv(path: Optional[Path] = None) -> None:
    """Minimal KEY=VALUE reader - deliberately not python-dotenv, which would
    be a dependency for ~15 lines. Existing environment variables always win,
    matching python-dotenv's own default (a real exported env var should beat
    a checked-out file)."""
    path = path or PROJECT_ROOT / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


load_dotenv()


def _int_env(name: str, default: int) -> int:
    """A malformed env var must fail as a ConfigError naming the variable,
    not as a bare ValueError from inside a dataclass default_factory at
    import time (which is what a plain int(os.getenv(...)) gives you)."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"{name}={raw!r} is not a valid integer.") from None


# --------------------------------------------------------------- validation
def _check_type(key: str, value: Any, expected: type) -> None:
    """Permissive exactly where YAML's type model is coarser than Python's (a
    YAML sequence is always a list, never a tuple; an int is an acceptable
    float), strict everywhere else. `bool` is never accepted for a numeric
    field even though it is technically an int subclass - a stray true/false
    in a numeric field is always a typo."""
    origin = typing.get_origin(expected)
    if origin is typing.Union:  # covers Optional[X] == Union[X, None]
        if value is None:
            return
        real = [a for a in typing.get_args(expected) if a is not type(None)]
        if real:
            _check_type(key, value, real[0])
        return
    if expected is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(f"{key}: expected a number, got {value!r} ({type(value).__name__})")
    elif expected is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"{key}: expected a whole number, got {value!r} ({type(value).__name__})")
    elif expected is bool:
        if not isinstance(value, bool):
            raise ConfigError(f"{key}: expected true/false, got {value!r} ({type(value).__name__})")
    elif expected is str:
        if not isinstance(value, str):
            raise ConfigError(f"{key}: expected text, got {value!r} ({type(value).__name__})")
    elif expected in (list, tuple):
        if not isinstance(value, (list, tuple)):
            raise ConfigError(f"{key}: expected a list, got {value!r} ({type(value).__name__})")
    # Any other/unresolvable annotation (e.g. Path) is skipped rather than
    # guessed at - not a field config.yaml ever actually sets.


# (min, max) inclusive, either side optional. Deliberately not exhaustive -
# only fields where an out-of-range value is unambiguously a typo and never a
# legitimate choice: EVE IDs are always positive, cost/day figures never
# negative, and genuine rate *fractions* are bounded 0-1.
_FIELD_RANGES: dict[str, tuple[Optional[float], Optional[float]]] = {
    "jita_region_id": (1, None),
    "reference_region_id": (1, None),
    "structure_id": (1, None),
    "buyer_character_id": (1, None),
    "seller_character_id": (1, None),
    "import_cost_per_m3": (0, None),
    "structure_sell_haircut": (0, 1),
    "jita_buy_broker_fee": (0, 1),
    "lookback_days": (0, None),
    "chunk_size": (1, None),
    "safe_mode_max_ids": (1, None),
    # Both margin/profit thresholds are bounded at 0 only, deliberately not
    # at 1: a required margin above 100% is a legitimate, intentional setting
    # in this domain, not a typo an upper bound should reject.
    "min_margin_threshold": (0, None),
    "min_profit_threshold": (0, None),
    "min_hit_rate": (0, 1),
    "skip_grace_period_days": (0, None),
    "max_active_shortlist_items": (1, None),
    "min_avg_movement": (0, None),
}


def _check_range(key: str, value: Any) -> None:
    """No-op for anything not in _FIELD_RANGES, same "don't guess" stance as
    _check_type's fallthrough. None is always allowed through - _check_type
    already accepted it for an Optional field, and a range check doesn't
    re-litigate that."""
    bounds = _FIELD_RANGES.get(key)
    if bounds is None or value is None:
        return
    lo, hi = bounds
    if lo is not None and value < lo:
        raise ConfigError(f"{key}: {value!r} is below the minimum allowed value ({lo})")
    if hi is not None and value > hi:
        raise ConfigError(f"{key}: {value!r} is above the maximum allowed value ({hi})")


def validate_config_overrides(cfg: Any, overrides: dict[str, Any]) -> None:
    """Type- and range-checks every override against its field's declared
    type, raising ConfigError on the *first* mismatch - before anything is
    applied, so a bad value never lands with some fields written and others
    not. Unknown keys are skipped rather than rejected: this is about
    catching a wrong type for a real field, not about policing extra keys
    someone left in their config.yaml."""
    hints = typing.get_type_hints(type(cfg))
    for key, value in overrides.items():
        if not hasattr(cfg, key):
            continue
        expected = hints.get(key)
        if expected is not None:
            _check_type(key, value, expected)
        _check_range(key, value)


def apply_config_overrides(cfg: Any, overrides: dict[str, Any]) -> None:
    """Applies already-validated overrides - never raises on a bad value, it
    assumes validate_config_overrides has run."""
    for key, value in overrides.items():
        if hasattr(cfg, key):
            setattr(cfg, key, value)


# ------------------------------------------------------------------ configs
@dataclass
class TradingConfig:
    # -- Regions / structure --
    # "The Forge" - the main trade hub goods are imported from. Note this is a
    # *region*-wide figure: ESI has no station-level order filter, so a "Jita
    # price" is really a Forge price.
    jita_region_id: int = 10000002
    reference_region_id: int = 10000009      # price-history reference region
    # Destination player structure. No sane default across installs; every
    # structure-comparing code path is a safe no-op while this is unset.
    structure_id: Optional[int] = None
    structure_market_slug: Optional[str] = None

    # -- Economics --
    import_cost_per_m3: float = 900.0
    # 1 - (SCC surcharge 0.5% + broker 1.5% + sales tax 3.37%), confirmed in
    # the parent repo against the in-game sell-order breakdown.
    structure_sell_haircut: float = 0.9463
    jita_buy_broker_fee: float = 0.0147

    # -- Shortlist decision bar --
    # An item has to clear *both* to be marked "Import" (see
    # shortlist._decision); below either one it's "Skip".
    min_profit_threshold: float = 0.0        # Minimum absolute profit per unit
    # -- Shortlist pruning (see actions.do_refresh_and_prune_candidates) --
    # How long an item has to stay continuously "No market data"/"Skip" before
    # it is deactivated. The grace period exists so a single temporary
    # market-data gap can't knock an otherwise-fine item off the shortlist.
    skip_grace_period_days: int = 30
    # Optional hard cap on how many items stay active, applied on top of the
    # skip-streak pruning by ranking on max daily profit. Off by default: the
    # skip streak already removes what's genuinely unprofitable, and a cap
    # additionally removes items that are merely *less* profitable than 300
    # others, which is a preference, not a correctness rule.
    enforce_shortlist_cap: bool = False
    max_active_shortlist_items: int = 300
    # (min_margin_threshold, further down under candidate discovery, is the
    # other half of that bar - the same field serves both the live shortlist
    # decision and the historical backtest's per-day profitability test.)

    # -- Characters --
    # Display only. The real identity behind each role comes from whichever
    # character authorized via `eve-trader-local auth --role <role>`, not
    # from these fields.
    buyer_character_id: Optional[int] = None
    buyer_character_name: Optional[str] = None
    seller_character_id: Optional[int] = None
    seller_character_name: Optional[str] = None

    # -- Realized trade history --
    lookback_days: int = 30

    # -- Candidate discovery --
    chunk_size: int = 25                       # type_ids per Goonmetrics price-history request
    safe_mode_max_ids: int = 500               # Cap on candidates evaluated per "safe" search run
    min_margin_threshold: float = 0.05         # Margin a day must clear to count as a profitable one
    min_hit_rate: float = 0.30                 # Minimum share of profitable days to recommend a candidate
    # Minimum average daily reference-region "movement" (Goonmetrics' daily
    # unit-quantity-traded liquidity figure - literally ESI's own
    # /markets/{region_id}/history/ `volume` field re-served verbatim,
    # confirmed live in the parent repo field-by-field against ESI) an item
    # must clear to be recommended, even when margin and hit-rate are both
    # fine: profitability alone isn't enough, an item also needs *some* real
    # trading activity behind it. Defaults to 0.0, a no-op beyond the floor
    # that already exists implicitly (score = avg_profit_m3 x log(1+avg_move)
    # x hit_rate is already 0, and so already excluded, for exactly-zero
    # movement) - raise it once you've seen real avg_sell_movement values for
    # good candidates and know what "enough" looks like. It's a config field
    # so that's a config change, not a code change.
    min_avg_movement: float = 0.0

    # Market-group top-level path prefixes to hard-exclude from candidate
    # discovery entirely. Confirmed one-by-one in the parent repo with the
    # user: these structurally don't fit the Jita->structure import-arbitrage
    # model. Nothing else is categorically pre-filtered - there is no keyword
    # allowlist/denylist and no per-item m3 cap (both existed once and were
    # removed: an item should only ever be dropped for failing on actual
    # profitability or real trading volume, not for its category or size).
    # "skills" used to be on this list and was deliberately taken off.
    excluded_path_prefixes: tuple = (
        "ships", "blueprints", "apparel",
        "personalization", "pilot's services", "structures",
    )

    # -- API endpoints --
    esi_base: str = "https://esi.evetech.net/latest"
    # gnf.lt's rehosting of the Goonmetrics current-price API - the failsafe
    # price source used when a structure's real order book is unreachable
    # (see goonmetrics_client.py).
    goonmetrics_appraise_base: str = "https://appraise.gnf.lt"
    # The other gnf.lt endpoint: region *price history*, a different question
    # from the current quotes above - "was this item historically worth
    # importing", asked against reference_region_id (see goonmetrics_client.
    # price_history).
    goonmetrics_history_base: str = "https://goonmetrics.apps.gnf.lt/api/price_history/"


@dataclass
class OAuthConfig:
    """EVE SSO (OAuth2 authorization code + PKCE). Secrets come from the
    environment, never config.yaml."""
    client_id: str = field(default_factory=lambda: os.getenv("EVE_SSO_CLIENT_ID", ""))
    callback_host: str = field(default_factory=lambda: os.getenv("EVE_SSO_CALLBACK_HOST", "localhost"))
    callback_port: int = field(default_factory=lambda: _int_env("EVE_SSO_CALLBACK_PORT", 8000))
    callback_path: str = field(default_factory=lambda: os.getenv("EVE_SSO_CALLBACK_PATH", "/callback"))
    redirect_uri_override: str = field(default_factory=lambda: os.getenv("EVE_SSO_REDIRECT_URI", ""))
    authorize_url: str = "https://login.eveonline.com/v2/oauth/authorize"
    token_url: str = "https://login.eveonline.com/v2/oauth/token"
    verify_url: str = "https://login.eveonline.com/oauth/verify"

    @property
    def redirect_uri(self) -> str:
        """Must exactly match the callback URL registered for this app at
        https://developers.eveonline.com/applications. Nothing here is served
        by a long-running process - auth.py binds a throwaway loopback server
        on this host/port only for the seconds the login takes."""
        if self.redirect_uri_override:
            return self.redirect_uri_override
        return f"http://{self.callback_host}:{self.callback_port}{self.callback_path}"

    # Exactly the scopes the code actually calls. EVE SSO rejects the *whole*
    # login with "invalid_scope" if your dev-portal app doesn't have one of
    # these enabled, so don't add any without enabling it there too.
    scopes: tuple = (
        "esi-markets.read_character_orders.v1",
        "esi-markets.structure_markets.v1",
        "esi-wallet.read_character_wallet.v1",
        "esi-assets.read_assets.v1",
    )


def load_trading_config(path: Optional[Path] = None) -> TradingConfig:
    """Defaults + config.yaml + stored overrides, validated at each layer
    before being applied. Not cached: a local app reads this a handful of
    times per process, and a stale read after the user edits their own
    config.yaml would be more surprising than the negligible cost."""
    path = path or config_path()
    # Cheap and idempotent, and it means reading config never fails with
    # "no such table: settings" on a first run that hasn't hit init-db yet.
    storage.init_db()
    cfg = TradingConfig()
    if path.exists():
        overrides = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        validate_config_overrides(cfg, overrides)  # fail fast, naming the bad field
        apply_config_overrides(cfg, overrides)
    stored = storage.load_settings("trading")
    if stored:
        validate_config_overrides(cfg, stored)
        apply_config_overrides(cfg, stored)
    return cfg


def save_config_overrides(updates: dict[str, Any], cfg: Optional[TradingConfig] = None) -> None:
    """Validates, then persists to SQLite, then applies to the live config
    object - in that order, so a rejected value changes nothing at all."""
    cfg = cfg if cfg is not None else TRADING_CONFIG
    validate_config_overrides(cfg, updates)
    storage.save_settings("trading", updates)
    apply_config_overrides(cfg, updates)


def reload() -> TradingConfig:
    """Re-reads config.yaml + stored overrides into the module-level
    TRADING_CONFIG *in place*, so callers holding a reference to it see the
    new values (rebinding the name would leave them on the old object)."""
    fresh = load_trading_config()
    TRADING_CONFIG.__dict__.update(copy.deepcopy(fresh.__dict__))
    return TRADING_CONFIG


# Bare defaults at import time, deliberately - importing this module must not
# touch the filesystem or the database (tests import it before pointing
# EVE_TRADER_LOCAL_DATA_DIR at a tmp dir). cli.main() calls reload() once at
# startup to fill it in from config.yaml + stored overrides.
TRADING_CONFIG = TradingConfig()
OAUTH_CONFIG = OAuthConfig()
