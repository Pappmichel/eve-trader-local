"""Build-time defaults for values that aren't secrets in the usual sense
(this app's EVE SSO client id is a public OAuth client - PKCE, no client
secret - so it's safe to bake into a distributed binary, unlike
`EVE_TRADER_LOCAL_GITHUB_TOKEN`, which stays environment/.env-only) but that
a source checkout still shouldn't ship with hardcoded, since a dev building
from source should register their own EVE SSO application.

`None` here, as committed - a source checkout still needs its own `.env`
(see `.env.example`). `.github/workflows/build-windows.yml` overwrites this
file from a repository secret right before building the packaged `.exe`,
same mechanism/timing as `_version.py`'s own stamping, so a downloaded
release works without the user creating a `.env` at all. That overwrite
only ever happens inside CI's own throwaway checkout; it is never committed
back here.
"""
from __future__ import annotations

from typing import Optional

EVE_SSO_CLIENT_ID: Optional[str] = None
