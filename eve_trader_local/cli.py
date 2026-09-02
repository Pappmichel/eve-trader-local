"""Minimal CLI - just enough to smoke-test the foundation end to end.

    eve-trader-local init-db
    eve-trader-local auth --role buyer
    eve-trader-local whoami
    eve-trader-local refresh-sde / sde-status
    eve-trader-local check-update / update

This is not the intended long-term interface (a native GUI is - see README);
it exists so storage + config + auth can be proven to work together before
any real feature is built on top of them.
"""
from __future__ import annotations

import argparse
import sys
import time

from . import config, sde, storage, updater
from .auth import TokenManager
from .errors import ActionError
from .paths import config_path, db_path


def cmd_init_db(args: argparse.Namespace) -> None:
    storage.init_db()
    print(f"Database ready: {db_path()}")


def cmd_auth(args: argparse.Namespace) -> None:
    record = TokenManager().login(args.role)
    print(f"Authorized {record.character_name} ({record.character_id}) as '{record.role}'.")


def cmd_whoami(args: argparse.Namespace) -> None:
    records = TokenManager().list_records()
    if not records:
        print("No characters authorized yet. Run: eve-trader-local auth --role buyer")
        return
    print(f"{'ROLE':<28} {'CHARACTER':<24} {'ID':>12}  TOKEN")
    for r in records:
        remaining = r.expires_at - time.time()
        state = "expired" if r.is_expired() else f"valid for {int(remaining // 60)}m"
        print(f"{r.role:<28} {r.character_name:<24} {r.character_id:>12}  {state}")


def cmd_config(args: argparse.Namespace) -> None:
    cfg = config.reload()
    print(f"config.yaml: {config_path()}{'' if config_path().exists() else ' (not present, using defaults)'}")
    for key, value in vars(cfg).items():
        print(f"  {key} = {value!r}")


def cmd_check_update(args: argparse.Namespace) -> None:
    print(updater.check_for_update().summary())


def cmd_update(args: argparse.Namespace) -> None:
    status = updater.check_for_update()
    if status.error:
        # Not fatal on its own: the actual comparison the update relies on is
        # git's, not the API's, so offer to continue rather than stopping here.
        print(status.summary())
    elif not status.update_available:
        print(status.summary())
        return

    installed = updater.preflight()
    print(f"Repository: {updater.repo_root()}")
    print(f"Installed:  {installed}")
    if status.latest_sha:
        print(f"Available:  {status.latest_sha}")
    print(
        "\nThis will run `git reset --hard origin/main` in that directory, "
        "discarding anything not committed there, and reinstall dependencies."
    )
    if input("Continue? [y/N] ").strip().lower() not in ("y", "yes"):
        print("Aborted; nothing was changed.")
        return

    result = updater.apply_update()
    if result.previous_sha == result.new_sha:
        print("Already up to date; nothing was changed.")
        return
    print(f"Updated {result.previous_sha[:8]} -> {result.new_sha[:8]} and reinstalled dependencies.")
    print("Restart eve-trader-local to run the new version.")


def cmd_refresh_sde(args: argparse.Namespace) -> None:
    print("Downloading the Static Data Export from Fuzzwork (this takes a minute)...")
    counts = sde.refresh_sde()
    width = max(len(t) for t in counts)
    for table, count in counts.items():
        print(f"  {table:<{width}}  {count:>9,}")
    print("SDE refreshed.")


def cmd_sde_status(args: argparse.Namespace) -> None:
    status = sde.check_for_newer_sde()
    if status["local_refreshed_at"] is None:
        print("SDE has never been refreshed on this machine. Run: eve-trader-local refresh-sde")
    else:
        print(f"Last refreshed: {status['local_refreshed_at']}")
        counts = storage.sde_row_counts()
        print(f"Cached rows:    {sum(counts.values()):,} across {len(counts)} tables")
    if not status["remote_check_succeeded"]:
        print("Could not reach Fuzzwork to check for a newer dump.")
    elif status["newer_sde_available"]:
        print("A newer SDE dump is available. Run: eve-trader-local refresh-sde")
    elif status["local_refreshed_at"] is not None:
        print("Fuzzwork's current dump matches the cached one.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="eve-trader-local", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="create the SQLite database/tables (idempotent)").set_defaults(func=cmd_init_db)

    p_auth = sub.add_parser("auth", help="run the interactive EVE SSO login for a role")
    p_auth.add_argument("--role", required=True, help="role prefix, e.g. buyer / seller")
    p_auth.set_defaults(func=cmd_auth)

    sub.add_parser("whoami", help="list authorized characters").set_defaults(func=cmd_whoami)
    sub.add_parser("config", help="show the resolved configuration").set_defaults(func=cmd_config)
    sub.add_parser(
        "refresh-sde", help="download the current EVE Static Data Export from Fuzzwork"
    ).set_defaults(func=cmd_refresh_sde)
    sub.add_parser(
        "sde-status", help="show when the SDE was last refreshed and whether a newer dump exists"
    ).set_defaults(func=cmd_sde_status)
    sub.add_parser(
        "check-update", help="check GitHub for a newer commit (read-only)"
    ).set_defaults(func=cmd_check_update)
    sub.add_parser(
        "update", help="update this checkout to origin/main and reinstall dependencies"
    ).set_defaults(func=cmd_update)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # Every command needs the tables to exist, and init_db is idempotent -
    # cheaper than making each command remember to check.
    storage.init_db()
    config.reload()
    try:
        args.func(args)
    except ActionError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
