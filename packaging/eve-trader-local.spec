# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build spec for the eve-trader-local native GUI.

Build with (from the repo root, in an environment with `pip install -e
".[gui]" pyinstaller` already run):

    pyinstaller packaging/eve-trader-local.spec

Produces a single portable executable (`dist/eve-trader-local[.exe]`) with
no separate installer step - see ROADMAP.md's "Native GUI"/packaging notes
for why a portable binary comes first and an actual installer (Inno Setup
or similar) is deliberately a later step, not this one.

Two things this file exists to get right, both confirmed the hard way by
actually running the frozen binary, not just building it (see
`.github/workflows/build-windows.yml`'s own verification step):

1. `pathex` must include the repo root (one directory up from this spec
   file, via `SPECPATH` - a name PyInstaller injects into every spec file's
   execution namespace, not a normal Python import), or PyInstaller's
   analysis silently fails to find the `eve_trader_local` package at all
   when it's installed editable (`pip install -e .`) rather than as a
   normal site-packages copy - the build succeeds either way, but the
   frozen binary then crashes on startup with `ModuleNotFoundError:
   No module named 'eve_trader_local'`. `-e .` is exactly how this repo's
   own CI/dev setup installs itself, so this isn't a hypothetical.
2. `console=False` (a windowed app, no console popup) is correct for the
   real GUI, but makes any startup crash silent on Windows (no console to
   print the traceback to). If a future frozen build fails mysteriously on
   Windows with no visible error, temporarily flip this to `True` to see
   the real exception, don't assume the packaging itself is broken.
"""

import os

repo_root = os.path.abspath(os.path.join(SPECPATH, ".."))

a = Analysis(
    ["run_gui.py"],
    pathex=[repo_root],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="eve-trader-local",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
