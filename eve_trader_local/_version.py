"""The tagged release version this build was built from.

`None` in a source checkout (this file, as committed) - there is no
release tag to speak of, and `updater.py`'s Stage-1 git-based flow answers
"what version is this" from `git rev-parse HEAD` instead (see that module).

`.github/workflows/build-windows.yml` overwrites this file with the actual
`vX.Y.Z` tag right before building the packaged `.exe`, so the frozen build
carries its own version baked in - there is no git checkout inside a
PyInstaller bundle to ask instead. That overwrite only ever happens inside
CI's own throwaway checkout; it is never committed back here.
"""
from __future__ import annotations

from typing import Optional

VERSION: Optional[str] = None
