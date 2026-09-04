"""Updater tests.

Nothing here touches the network or runs a real git command: every `git`
invocation goes through `updater._git`, and that is the single seam the fake
below replaces. The point of these tests is the refusal logic - each
preflight failure is a case where a real run would otherwise `git reset
--hard` someone's working tree.
"""
from __future__ import annotations

import hashlib
import subprocess

import pytest
import requests

from eve_trader_local import updater
from eve_trader_local.errors import ActionError

OLD_SHA = "1111111111111111111111111111111111111111"
NEW_SHA = "2222222222222222222222222222222222222222"


class FakeGit:
    """Replaces updater._git. `responses` maps the git subcommand (first arg)
    to (returncode, stdout); `calls` records everything attempted so a test
    can assert that a destructive command was never reached."""

    def __init__(self, **responses):
        defaults = {
            "rev-parse": (0, OLD_SHA),
            "remote": (0, f"https://github.com/{updater.GITHUB_REPO}"),
            "status": (0, ""),
            "fetch": (0, ""),
            "reset": (0, ""),
            "check-ignore": (0, ""),
        }
        defaults.update(responses)
        self.responses = defaults
        self.calls: list[list[str]] = []

    def __call__(self, args, *, check=True):
        args = list(args)
        self.calls.append(args)
        key = args[0]
        if key == "rev-parse" and "--show-toplevel" in args:
            code, out = self.responses.get("show-toplevel", (0, str(updater.repo_root())))
        elif key == "rev-parse" and "--abbrev-ref" in args:
            code, out = self.responses.get("branch", (0, "main"))
        elif key == "rev-parse" and any(a.startswith("origin/") for a in args):
            code, out = self.responses.get("remote-head", (0, NEW_SHA))
        else:
            code, out = self.responses[key]
        if check and code != 0:
            raise ActionError(f"git {' '.join(args)} failed")
        return subprocess.CompletedProcess(args, code, out + "\n", "")

    def ran(self, subcommand: str) -> bool:
        return any(c[0] == subcommand for c in self.calls)


@pytest.fixture
def fake_git(monkeypatch):
    def install(**responses):
        git = FakeGit(**responses)
        monkeypatch.setattr(updater, "_git", git)
        return git

    return install


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Keeps the update lock file (and the data-safety check) off the real
    ~/.eve-trader-local."""
    monkeypatch.setenv("EVE_TRADER_LOCAL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("EVE_TRADER_LOCAL_CONFIG", raising=False)


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def patch_get(monkeypatch, result):
    def fake_get(url, **kwargs):
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(updater.requests, "get", fake_get)


# --------------------------------------------------------------------------
# version check
# --------------------------------------------------------------------------

def test_check_reports_update_available(fake_git, monkeypatch):
    fake_git()
    patch_get(monkeypatch, FakeResponse(200, {"sha": NEW_SHA}))
    status = updater.check_for_update()
    assert status.update_available
    assert status.installed_sha == OLD_SHA and status.latest_sha == NEW_SHA
    assert "Update available" in status.summary()


def test_check_reports_up_to_date(fake_git, monkeypatch):
    fake_git()
    patch_get(monkeypatch, FakeResponse(200, {"sha": OLD_SHA}))
    status = updater.check_for_update()
    assert not status.update_available
    assert "Up to date" in status.summary()


def test_check_survives_network_failure(fake_git, monkeypatch):
    fake_git()
    patch_get(monkeypatch, requests.ConnectionError("no route to host"))
    status = updater.check_for_update()
    assert not status.update_available
    assert status.installed_sha == OLD_SHA
    assert "could not reach GitHub" in status.error


def test_check_survives_rate_limit(fake_git, monkeypatch):
    fake_git()
    patch_get(monkeypatch, FakeResponse(403))
    status = updater.check_for_update()
    assert "rate-limited" in status.error
    assert not status.update_available


def test_check_survives_garbage_response(fake_git, monkeypatch):
    fake_git()
    patch_get(monkeypatch, FakeResponse(200, {"not_a_sha": True}))
    assert "unexpected response" in updater.check_for_update().error


def test_check_on_non_repo_is_not_fatal(fake_git, monkeypatch):
    fake_git(**{"show-toplevel": (128, "")})
    patch_get(monkeypatch, FakeResponse(200, {"sha": NEW_SHA}))
    status = updater.check_for_update()
    assert status.installed_sha is None
    assert "not a git checkout" in status.error


# --------------------------------------------------------------------------
# preflight refusals - each of these must stop before `git reset`
# --------------------------------------------------------------------------

def test_refuses_when_not_a_git_repo(fake_git):
    git = fake_git(**{"show-toplevel": (128, "")})
    with pytest.raises(ActionError, match="not a git checkout"):
        updater.apply_update()
    assert not git.ran("reset")


def test_refuses_when_repo_root_is_a_parent_repo(fake_git):
    """A zip unpacked inside some other repository: git works, but it is not
    *this* app's repository."""
    git = fake_git(**{"show-toplevel": (0, "/somewhere/else")})
    with pytest.raises(ActionError, match="not a git checkout"):
        updater.apply_update()
    assert not git.ran("reset")


def test_refuses_without_origin_remote(fake_git):
    git = fake_git(remote=(128, ""))
    with pytest.raises(ActionError, match="no 'origin' remote"):
        updater.apply_update()
    assert not git.ran("reset")


def test_refuses_when_origin_is_an_unrelated_repo(fake_git):
    git = fake_git(remote=(0, "https://github.com/someone/something-else"))
    with pytest.raises(ActionError, match="not the eve-trader-local"):
        updater.apply_update()
    assert not git.ran("reset")


def test_refuses_on_wrong_branch(fake_git):
    git = fake_git(branch=(0, "feature/x"))
    with pytest.raises(ActionError, match="not 'main'"):
        updater.apply_update()
    assert not git.ran("reset")


def test_refuses_on_detached_head(fake_git):
    git = fake_git(branch=(0, "HEAD"))
    with pytest.raises(ActionError, match="detached HEAD"):
        updater.apply_update()
    assert not git.ran("reset")


def test_refuses_on_dirty_working_tree(fake_git):
    git = fake_git(status=(0, " M eve_trader_local/cli.py\n?? scratch.py"))
    with pytest.raises(ActionError, match="uncommitted changes"):
        updater.apply_update()
    assert not git.ran("reset")


def test_refuses_when_data_lives_untracked_inside_the_checkout(fake_git, monkeypatch):
    monkeypatch.setenv("EVE_TRADER_LOCAL_DATA_DIR", str(updater.repo_root() / "_test_data"))
    git = fake_git(**{"check-ignore": (1, "")})
    with pytest.raises(ActionError, match="not ignored by git"):
        updater.apply_update()
    assert not git.ran("reset")


def test_allows_data_inside_the_checkout_when_gitignored(fake_git, monkeypatch):
    monkeypatch.setenv("EVE_TRADER_LOCAL_DATA_DIR", str(updater.repo_root() / "_test_data"))
    fake_git()  # check-ignore returns 0 = ignored
    assert updater.preflight() == OLD_SHA


def test_real_gitignore_covers_a_data_dir_inside_the_checkout(tmp_path, monkeypatch):
    """Not mocked on purpose: asserts this repo's actual .gitignore really
    does cover a data directory pointed inside the checkout, which is what
    _assert_user_data_safe relies on for that layout."""
    if not updater.is_git_checkout():
        pytest.skip("not running from a git checkout")
    monkeypatch.setenv("EVE_TRADER_LOCAL_DATA_DIR", str(updater.repo_root() / "data"))
    updater._assert_user_data_safe()


# --------------------------------------------------------------------------
# applying
# --------------------------------------------------------------------------

def test_apply_resets_and_reinstalls(fake_git, monkeypatch):
    git = fake_git()
    installed = []
    monkeypatch.setattr(updater, "_reinstall_dependencies", lambda: installed.append(True))

    result = updater.apply_update()
    assert result.previous_sha == OLD_SHA and result.new_sha == NEW_SHA
    assert result.dependencies_reinstalled and installed
    assert ["reset", "--hard", NEW_SHA] in git.calls
    assert git.calls.index(["fetch", "origin", "main"]) < git.calls.index(["reset", "--hard", NEW_SHA])


def test_apply_is_a_noop_when_remote_matches(fake_git, monkeypatch):
    git = fake_git(**{"remote-head": (0, OLD_SHA)})
    monkeypatch.setattr(updater, "_reinstall_dependencies", lambda: pytest.fail("should not reinstall"))
    result = updater.apply_update()
    assert result.previous_sha == result.new_sha
    assert not result.dependencies_reinstalled
    assert not git.ran("reset")


def test_apply_reports_actionable_error_when_pip_fails(fake_git, monkeypatch):
    fake_git()
    monkeypatch.setattr(
        updater.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "ERROR: could not build wheel"),
    )
    with pytest.raises(ActionError) as exc:
        updater.apply_update()
    message = str(exc.value)
    assert "code was updated" in message and "pip install -e ." in message


def test_second_concurrent_update_is_refused(fake_git, monkeypatch):
    fake_git()
    with updater._UpdateLock():
        with pytest.raises(ActionError, match="another update is already running"):
            updater.apply_update()


def test_lock_is_released_after_a_failed_update(fake_git):
    fake_git(branch=(0, "feature/x"))
    with pytest.raises(ActionError):
        updater.apply_update()
    assert not (updater.data_dir() / "update.lock").exists()


# --------------------------------------------------------------------------
# Stage 2: binary release version check
# --------------------------------------------------------------------------

NEW_TAG = "v1.2.3"


def _release_payload(tag=NEW_TAG, exe_url="https://example.invalid/exe", checksum_url="https://example.invalid/sha256"):
    return {
        "tag_name": tag,
        "assets": [
            {"name": "eve-trader-local.exe", "browser_download_url": exe_url},
            {"name": "eve-trader-local.exe.sha256", "browser_download_url": checksum_url},
        ],
    }


def test_latest_release_parses_assets(monkeypatch):
    patch_get(monkeypatch, FakeResponse(200, _release_payload()))
    release = updater.latest_release()
    assert release.tag == NEW_TAG
    assert release.exe_url == "https://example.invalid/exe"
    assert release.checksum_url == "https://example.invalid/sha256"


def test_latest_release_missing_assets_is_an_error(monkeypatch):
    patch_get(monkeypatch, FakeResponse(200, {"tag_name": NEW_TAG, "assets": []}))
    with pytest.raises(ActionError, match="missing the expected"):
        updater.latest_release()


def test_check_for_binary_update_reports_available(monkeypatch):
    monkeypatch.setattr(updater, "is_frozen", lambda: True)
    monkeypatch.setattr(updater._version, "VERSION", "v1.0.0")
    patch_get(monkeypatch, FakeResponse(200, _release_payload()))
    status = updater.check_for_binary_update()
    assert status.installed_version == "v1.0.0"
    assert status.update_available
    assert "Update available" in status.summary()


def test_check_for_binary_update_up_to_date(monkeypatch):
    monkeypatch.setattr(updater, "is_frozen", lambda: True)
    monkeypatch.setattr(updater._version, "VERSION", NEW_TAG)
    patch_get(monkeypatch, FakeResponse(200, _release_payload()))
    status = updater.check_for_binary_update()
    assert not status.update_available
    assert "Up to date" in status.summary()


def test_check_for_binary_update_not_frozen_has_no_installed_version(monkeypatch):
    monkeypatch.setattr(updater, "is_frozen", lambda: False)
    patch_get(monkeypatch, FakeResponse(200, _release_payload()))
    status = updater.check_for_binary_update()
    assert status.installed_version is None
    assert not status.update_available


def test_check_for_binary_update_survives_network_failure(monkeypatch):
    monkeypatch.setattr(updater, "is_frozen", lambda: True)
    monkeypatch.setattr(updater._version, "VERSION", "v1.0.0")
    patch_get(monkeypatch, requests.ConnectionError("no route to host"))
    status = updater.check_for_binary_update()
    assert status.installed_version == "v1.0.0"
    assert "could not reach GitHub" in status.error


# --------------------------------------------------------------------------
# Stage 2: download + apply - each of these must stop before Popen (the
# helper script that would actually replace the running .exe) unless the
# checksum genuinely verifies, same "refuse before anything destructive"
# shape the Stage-1 preflight tests above assert.
# --------------------------------------------------------------------------

EXE_BYTES = b"fake-exe-bytes"
EXE_SHA256 = hashlib.sha256(EXE_BYTES).hexdigest()


class FakeStreamResponse:
    """Replaces requests.get(..., stream=True)'s return value - just enough
    of a Response to support `_download_to`'s `with ... as resp:` /
    `iter_content` usage."""

    def __init__(self, status_code, content: bytes = b""):
        self.status_code = status_code
        self._content = content

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def iter_content(self, chunk_size):
        yield self._content


def patch_downloads(monkeypatch, by_url: dict):
    def fake_get(url, **kwargs):
        assert kwargs.get("stream") is True
        return by_url[url]

    monkeypatch.setattr(updater.requests, "get", fake_get)


@pytest.fixture
def frozen_windows(monkeypatch):
    """A frozen, Windows, portable-.exe environment - the only one
    `download_and_apply_binary_update` will proceed under. Replaces
    `subprocess.Popen` with a recorder rather than actually launching
    cmd.exe."""
    monkeypatch.setattr(updater, "is_frozen", lambda: True)
    monkeypatch.setattr(updater.os, "name", "nt")
    monkeypatch.setattr(updater.sys, "executable", "C:\\portable\\eve-trader-local.exe")
    calls: list[tuple] = []
    monkeypatch.setattr(updater.subprocess, "Popen", lambda *a, **k: calls.append((a, k)))
    return calls


def _fake_release():
    return updater.ReleaseInfo(
        tag=NEW_TAG, exe_url="https://example.invalid/exe", checksum_url="https://example.invalid/sha256"
    )


def test_download_and_apply_verifies_checksum_and_launches_helper(frozen_windows, monkeypatch):
    release = _fake_release()
    patch_downloads(monkeypatch, {
        release.exe_url: FakeStreamResponse(200, EXE_BYTES),
        release.checksum_url: FakeStreamResponse(200, f"{EXE_SHA256}  eve-trader-local.exe".encode()),
    })

    updater.download_and_apply_binary_update(release)

    update_dir = updater.data_dir() / updater._UPDATE_SUBDIR
    new_exe = update_dir / updater._NEW_EXE_NAME
    assert new_exe.read_bytes() == EXE_BYTES
    assert len(frozen_windows) == 1
    args, _ = frozen_windows[0]
    assert str(update_dir / updater._HELPER_SCRIPT_NAME) in args[0]


def test_download_and_apply_refuses_checksum_mismatch(frozen_windows, monkeypatch):
    release = _fake_release()
    wrong_hash = "0" * 64
    patch_downloads(monkeypatch, {
        release.exe_url: FakeStreamResponse(200, EXE_BYTES),
        release.checksum_url: FakeStreamResponse(200, f"{wrong_hash}  eve-trader-local.exe".encode()),
    })

    with pytest.raises(ActionError, match="checksum verification"):
        updater.download_and_apply_binary_update(release)

    assert not frozen_windows  # Popen (the relaunch helper) was never reached
    update_dir = updater.data_dir() / updater._UPDATE_SUBDIR
    assert not (update_dir / updater._NEW_EXE_NAME).exists()


def test_download_and_apply_refuses_on_non_windows(monkeypatch):
    monkeypatch.setattr(updater, "is_frozen", lambda: True)
    monkeypatch.setattr(updater.os, "name", "posix")
    with pytest.raises(ActionError, match="only supported on the Windows build"):
        updater.download_and_apply_binary_update(_fake_release())


def test_download_and_apply_refuses_when_not_frozen(monkeypatch):
    monkeypatch.setattr(updater, "is_frozen", lambda: False)
    monkeypatch.setattr(updater.os, "name", "nt")
    with pytest.raises(ActionError, match="packaged .exe"):
        updater.download_and_apply_binary_update(_fake_release())
