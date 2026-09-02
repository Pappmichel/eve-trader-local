"""Minimal CLI - just enough to smoke-test the foundation end to end.

    eve-trader-local init-db
    eve-trader-local auth --role buyer
    eve-trader-local whoami

This is not the intended long-term interface (a native GUI is - see README);
it exists so storage + config + auth can be proven to work together before
any real feature is built on top of them.
"""
from __future__ import annotations

import argparse
import sys
import time

from . import config, storage
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="eve-trader-local", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="create the SQLite database/tables (idempotent)").set_defaults(func=cmd_init_db)

    p_auth = sub.add_parser("auth", help="run the interactive EVE SSO login for a role")
    p_auth.add_argument("--role", required=True, help="role prefix, e.g. buyer / seller")
    p_auth.set_defaults(func=cmd_auth)

    sub.add_parser("whoami", help="list authorized characters").set_defaults(func=cmd_whoami)
    sub.add_parser("config", help="show the resolved configuration").set_defaults(func=cmd_config)
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
