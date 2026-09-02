"""Stage 1 self-update: keep a source checkout in sync with origin/main.

The app ships as a plain `git clone`, so "update" is literally
`git fetch && git reset --hard origin/main` plus a dependency reinstall
(see ROADMAP.md - stage 2, tagged releases + a binary updater, only becomes
meaningful once this is packaged, and is deliberately not built here).

`git reset --hard` throws away work unconditionally, so the safety checks in
`preflight()` are the actual substance of this module: the update is refused
unless the checkout is *exactly* what a normal install looks like. The
installed version is read live from `git rev-parse HEAD` rather than
recorded in a file of its own - there is no way for that to drift out of
sync with reality, and a separately stored SHA would be wrong the moment
anyone ran git by hand.

User data (the SQLite database, config.yaml) lives outside the checkout by
default (see paths.py) and is never a target of an update; `preflight()`
verifies that rather than trusting it, because EVE_TRADER_LOCAL_DATA_DIR can
point anywhere, including into the working tree.
"""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

import requests

from .errors import ActionError
from .paths import PROJECT_ROOT, config_path, data_dir, db_path

GITHUB_REPO = "pappmichel/eve-trader-local"
COMMITS_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/commits/main"
UPDATE_BRANCH = "main"
HTTP_TIMEOUT_SECONDS = 15
GIT_TIMEOUT_SECONDS = 120
PIP_TIMEOUT_SECONDS = 900

# An origin pointing somewhere else entirely means this checkout is a fork or
# an unrelated repo that happens to sit at the same path; hard-resetting it to
# a stranger's main is exactly the destructive accident this module must not
# cause.
EXPECTED_ORIGIN_FRAGMENT = "eve-trader-local"

_LOCK_FILENAME = "update.lock"


@dataclass
class UpdateStatus:
    """Result of a *read-only* version check. Never a failure by itself:
    a startup check that can't reach GitHub reports `error` and the app
    carries on."""

    installed_sha: Optional[str]
    latest_sha: Optional[str] = None
    error: Optional[str] = None

    @property
    def update_available(self) -> bool:
        return bool(
            self.installed_sha
            and self.latest_sha
            and self.installed_sha != self.latest_sha
        )

    def summary(self) -> str:
        if self.error:
            return f"Update check unavailable: {self.error}"
        if not self.installed_sha or not self.latest_sha:
            return "Update check unavailable."
        if self.update_available:
            return (
                f"Update available: {_short(self.installed_sha)} -> "
                f"{_short(self.latest_sha)} (origin/{UPDATE_BRANCH}).\n"
                "Run: eve-trader-local update"
            )
        return f"Up to date ({_short(self.installed_sha)})."


@dataclass
class UpdateResult:
    previous_sha: str
    new_sha: str
    dependencies_reinstalled: bool


def _short(sha: str) -> str:
    return sha[:8]


# --------------------------------------------------------------------------
# git plumbing
# --------------------------------------------------------------------------

def repo_root() -> Path:
    return PROJECT_ROOT


def _git(args: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(repo_root()),
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as e:
        raise ActionError(
            "git is not installed (or not on PATH), so this checkout cannot "
            "update itself. Install git, or re-download the app."
        ) from e
    except subprocess.TimeoutExpired as e:
        raise ActionError(f"git {' '.join(args)} timed out after {GIT_TIMEOUT_SECONDS}s.") from e
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        message = detail[-1] if detail else f"exit code {proc.returncode}"
        raise ActionError(f"git {' '.join(args)} failed: {message}")
    return proc


def is_git_checkout() -> bool:
    """True only if the *repo root itself* is the top of a git work tree.

    Both halves matter: someone who installed from a downloaded zip has no
    .git at all, and someone who dropped that zip inside another repository
    would otherwise have every git command here silently operate on that
    outer repo.
    """
    proc = _git(["rev-parse", "--show-toplevel"], check=False)
    if proc.returncode != 0:
        return False
    try:
        return Path(proc.stdout.strip()).resolve() == repo_root().resolve()
    except OSError:
        return False


def installed_commit() -> str:
    return _git(["rev-parse", "HEAD"]).stdout.strip()


def current_branch() -> str:
    return _git(["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()


def _origin_url() -> Optional[str]:
    proc = _git(["remote", "get-url", "origin"], check=False)
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def working_tree_changes() -> list[str]:
    """Every uncommitted difference, untracked files included - an update
    that would delete a file the user put here by hand is still data loss."""
    proc = _git(["status", "--porcelain"])
    return [line for line in proc.stdout.splitlines() if line.strip()]


def _is_git_ignored(path: Path) -> bool:
    return _git(["check-ignore", "--quiet", "--", str(path)], check=False).returncode == 0


# --------------------------------------------------------------------------
# version check (read-only, never fatal)
# --------------------------------------------------------------------------

def latest_remote_commit() -> str:
    """HEAD SHA of origin/main from the public GitHub API (no auth needed).

    Raises ActionError on anything that went wrong - callers that must not
    fail (the startup check) go through `check_for_update()` instead.
    """
    try:
        resp = requests.get(
            COMMITS_API_URL,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "eve-trader-local-updater",
            },
            timeout=HTTP_TIMEOUT_SECONDS,
        )
    except requests.RequestException as e:
        raise ActionError(f"could not reach GitHub ({e.__class__.__name__})") from e

    if resp.status_code in (403, 429):
        raise ActionError(
            "GitHub rate-limited the update check (unauthenticated requests are "
            "capped per IP). Try again later."
        )
    if resp.status_code != 200:
        raise ActionError(f"GitHub returned HTTP {resp.status_code} for the update check")

    try:
        sha = resp.json()["sha"]
    except (ValueError, KeyError, TypeError) as e:
        raise ActionError("GitHub returned an unexpected response to the update check") from e
    if not isinstance(sha, str) or not sha:
        raise ActionError("GitHub returned an unexpected response to the update check")
    return sha


def check_for_update() -> UpdateStatus:
    """Read-only check. Deliberately swallows every failure into
    `UpdateStatus.error`: being briefly offline (or rate-limited, or having
    installed this from a zip) must never stop the app from running."""
    try:
        if not is_git_checkout():
            return UpdateStatus(
                installed_sha=None,
                error=(
                    f"{repo_root()} is not a git checkout, so there is no installed "
                    "commit to compare. Update by re-downloading the app, or "
                    "reinstall it with `git clone`."
                ),
            )
        installed = installed_commit()
    except ActionError as e:
        return UpdateStatus(installed_sha=None, error=str(e))

    try:
        latest = latest_remote_commit()
    except ActionError as e:
        return UpdateStatus(installed_sha=installed, error=str(e))
    return UpdateStatus(installed_sha=installed, latest_sha=latest)


# --------------------------------------------------------------------------
# safety checks
# --------------------------------------------------------------------------

def preflight() -> str:
    """Refuse the update unless the checkout is exactly a clean install.

    Returns the installed commit SHA. Raises ActionError - with something the
    user can act on - for every reason `git reset --hard` must not run.
    """
    root = repo_root()
    if not is_git_checkout():
        raise ActionError(
            f"{root} is not a git checkout (no repository found at its root). "
            "This copy was probably installed from a downloaded archive; "
            "self-update needs a `git clone`. Re-clone from "
            f"https://github.com/{GITHUB_REPO} to enable updates."
        )

    origin = _origin_url()
    if origin is None:
        raise ActionError(
            "this checkout has no 'origin' remote, so there is nothing to update "
            f"from. Add one: git remote add origin https://github.com/{GITHUB_REPO}"
        )
    if EXPECTED_ORIGIN_FRAGMENT not in origin:
        raise ActionError(
            f"'origin' points at {origin}, which is not the eve-trader-local "
            "repository. Refusing to hard-reset this checkout to an unrelated "
            "remote."
        )

    branch = current_branch()
    if branch != UPDATE_BRANCH:
        # HEAD detached reports "HEAD"; either way resetting would silently
        # move the user off whatever they were doing.
        where = "a detached HEAD" if branch == "HEAD" else f"branch '{branch}'"
        raise ActionError(
            f"this checkout is on {where}, not '{UPDATE_BRANCH}'. "
            f"Switch with: git checkout {UPDATE_BRANCH}"
        )

    changes = working_tree_changes()
    if changes:
        listed = "\n  ".join(changes[:10])
        more = f"\n  ... and {len(changes) - 10} more" if len(changes) > 10 else ""
        raise ActionError(
            "the working tree has uncommitted changes, which `git reset --hard` "
            f"would destroy:\n  {listed}{more}\n"
            "Commit, stash, or remove them first."
        )

    _assert_user_data_safe()
    return installed_commit()


def _assert_user_data_safe() -> None:
    """The database and config.yaml must be out of reach of the reset.

    They live under ~/.eve-trader-local by default, but
    EVE_TRADER_LOCAL_DATA_DIR / EVE_TRADER_LOCAL_CONFIG can put them inside
    the checkout. That is still safe *if* git ignores them (a reset leaves
    ignored files alone) - but only if it actually does, so ask git rather
    than trusting .gitignore to have kept up.
    """
    root = repo_root().resolve()
    for label, path in (
        ("data directory", data_dir()),
        ("database", db_path()),
        ("config.yaml", config_path()),
    ):
        resolved = Path(path).resolve()
        if not _is_inside(resolved, root):
            continue
        if not _is_git_ignored(resolved):
            rel = os.path.relpath(resolved, root)
            raise ActionError(
                f"your {label} ({resolved}) is inside the checkout and is not "
                "ignored by git, so updating could overwrite or delete it. "
                f"Move it outside the repository (set EVE_TRADER_LOCAL_DATA_DIR), "
                f"or add '{rel}' to .gitignore."
            )


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


# --------------------------------------------------------------------------
# applying the update
# --------------------------------------------------------------------------

class _UpdateLock:
    """Cross-process guard so two `update` runs can't fetch/reset/pip over
    each other. O_EXCL on a lock file is enough here: this is a manually run
    single-user CLI, not a service."""

    def __init__(self) -> None:
        self.path = data_dir() / _LOCK_FILENAME
        self._fd: Optional[int] = None

    def __enter__(self) -> "_UpdateLock":
        try:
            self._fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as e:
            raise ActionError(
                f"another update is already running (lock file {self.path}). "
                "If you are sure it isn't, delete that file and retry."
            ) from e
        os.write(self._fd, str(os.getpid()).encode())
        return self

    def __exit__(self, *exc_info) -> None:
        if self._fd is not None:
            os.close(self._fd)
        try:
            self.path.unlink()
        except OSError:
            pass


def apply_update() -> UpdateResult:
    """Fetch, hard-reset to origin/main, reinstall dependencies.

    Everything that can be checked is checked *before* the reset, so the
    common failure modes (offline, dirty tree, wrong branch) leave the
    checkout untouched. The one step that cannot be undone by re-running is
    the reset itself; if the dependency reinstall fails after it, the code is
    already new and the error says exactly what to run by hand.
    """
    with _UpdateLock():
        previous = preflight()

        _git(["fetch", "origin", UPDATE_BRANCH])
        target = _git(["rev-parse", f"origin/{UPDATE_BRANCH}"]).stdout.strip()
        if not target:
            raise ActionError(f"could not resolve origin/{UPDATE_BRANCH} after fetching.")
        if target == previous:
            return UpdateResult(previous_sha=previous, new_sha=target, dependencies_reinstalled=False)

        # Re-checked after the fetch: fetch itself never touches the working
        # tree, but the user may have edited files while it ran.
        changes = working_tree_changes()
        if changes:
            raise ActionError(
                "the working tree changed while fetching; nothing was updated. "
                "Re-run once it is clean."
            )

        _git(["reset", "--hard", target])

        _reinstall_dependencies()
        return UpdateResult(previous_sha=previous, new_sha=target, dependencies_reinstalled=True)


def _reinstall_dependencies() -> None:
    """`pip install -e .` - matching how pyproject.toml declares the package
    (a setuptools project installed editable, per README's setup step)."""
    cmd = [sys.executable, "-m", "pip", "install", "-e", "."]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(repo_root()),
            capture_output=True,
            text=True,
            timeout=PIP_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as e:
        raise ActionError(_deps_failed_message("the install timed out")) from e
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise ActionError(_deps_failed_message(detail[-1] if detail else "pip exited non-zero"))


def _deps_failed_message(reason: str) -> str:
    return (
        "the code was updated, but reinstalling dependencies failed "
        f"({reason}).\n"
        "Your checkout is now on the new version with possibly outdated "
        "dependencies. Finish the update by running, in "
        f"{repo_root()}:\n"
        f"  {Path(sys.executable).name} -m pip install -e ."
    )
