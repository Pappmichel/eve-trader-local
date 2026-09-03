# Packaging

Builds `eve-trader-local` into a single portable Windows executable via
[PyInstaller](https://pyinstaller.org/). No installer yet - see
`ROADMAP.md`'s "Native GUI"/packaging notes for why a portable `.exe` comes
first and an actual installer (Inno Setup or similar, Start Menu entry +
uninstaller) is a deliberate later step, not this one.

## CI (the normal path)

`.github/workflows/build-windows.yml` builds on every push of a `vX.Y.Z`
tag, smoke-tests the result on a real Windows runner, uploads it as a build
artifact, and (for a real tag push, not a manual `workflow_dispatch` run)
attaches it to a GitHub Release. This is also the artifact a future
Stage-2 self-updater (see `ROADMAP.md`) will eventually fetch from
`/releases/latest` - this workflow produces that artifact; it doesn't build
the updater itself.

## Building locally (Windows only - PyInstaller builds for whatever OS runs it)

```powershell
pip install -e ".[gui,build]"
pyinstaller packaging\eve-trader-local.spec --distpath packaging\dist --workpath packaging\build
```

Produces `packaging\dist\eve-trader-local.exe`. Run it directly - no
install step, no admin rights needed. It reads/writes its data under
`%USERPROFILE%\.eve-trader-local\` (`paths.py`'s own default), same
location a source checkout uses, so a portable build and a source checkout
share the same data if run as the same Windows user.

## Two real bugs this setup already caught (both fixed, documented so they don't recur)

1. **`ModuleNotFoundError: No module named 'eve_trader_local'` at runtime,
   despite a successful build.** PyInstaller's analysis doesn't follow an
   editable install's `.pth`/egg-link indirection on its own - it needs the
   real source directory on `pathex` to find the package to bundle at all.
   Fixed in the `.spec` file itself (`pathex=[repo_root]`, derived from
   `SPECPATH`) - see that file's own top comment for the full explanation.
   A build "succeeding" is not proof the frozen binary actually runs; only
   running it is.
2. **Silent failure risk from `console=False`.** A windowed app has nowhere
   to print a startup traceback if something does go wrong on a real
   Windows machine. If a future build fails mysteriously with no visible
   error, temporarily set `console=True` in the `.spec` file to see the
   real exception before assuming the packaging config itself is broken.

## Cross-platform note

This spec also builds cleanly into a working Linux binary when run on
Linux (confirmed during development) - PyInstaller always targets whatever
OS it runs on, there is no cross-compilation. The CI workflow targets
Windows specifically because that's this project's primary desktop
audience (see the parent `eve-trader` repo's own Windows-only dev
environment note in its CLAUDE.md); a macOS/Linux build would be the same
`.spec` file run on a `macos-latest`/`ubuntu-latest` runner if that's ever
wanted, not a different one.
