"""The command-line interface - one command per pipeline step, plus a
`pipeline` command that runs the daily workflow end to end.

    eve-trader-local init-db
    eve-trader-local auth --role buyer / whoami / config
    eve-trader-local refresh-sde / sde-status
    eve-trader-local build-universe
    eve-trader-local find-candidates [--full]
    eve-trader-local add-to-shortlist
    eve-trader-local refresh-shortlist
    eve-trader-local check-unlisted-stock / check-undercut
    eve-trader-local reconcile-trades
    eve-trader-local pipeline [--rebuild-universe]
    eve-trader-local check-update / update

No logic lives here: every command calls one actions.do_* function and prints
its result. This is not the intended long-term interface (a native GUI is -
see README), which is exactly why the orchestration sits in actions.py where
that GUI can call the same functions.
"""
from __future__ import annotations

import argparse
import sys
import time

from . import actions, config, sde, storage, updater
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


def cmd_build_universe(args: argparse.Namespace) -> None:
    print("Building the candidate universe from the SDE cache...")
    universe = actions.do_build_universe()
    focused = actions.do_build_focused()
    print(f"Candidate universe: {universe['count']:,} items")
    print(f"Focused candidates: {focused['count']:,} items")


def cmd_find_candidates(args: argparse.Namespace) -> None:
    mode = "safe (rotating window)" if args.safe else "full scan"
    print(f"Backtesting focused candidates - {mode}. This can take a while.")
    result = actions.do_find_new_candidates(safe=args.safe)
    print(f"Evaluated {result['evaluated']:,} candidates, {result['recommended']:,} recommended.")
    if result["recommended"]:
        print("Run: eve-trader-local add-to-shortlist")


def cmd_add_to_shortlist(args: argparse.Namespace) -> None:
    result = actions.do_add_to_shortlist()
    print(f"Added {result['added']:,} recommended candidates to the shortlist.")


def _print_shortlist_result(result: dict) -> None:
    summary = result["summary"]
    print(f"Import: {summary['import_candidates']:,}   already ordered: {summary['already_ordered']:,}   "
          f"skipped: {summary['skipped']:,}")
    if summary["avg_margin"] is not None:
        print(f"Positive margins: {summary['positive_margin']:,} items, averaging "
              f"{summary['avg_margin'] * 100:.1f}%")
    if result.get("priced_via_fallback"):
        print("Note: structure prices came from the Goonmetrics fallback, not the real order book.")
    top = result.get("top_imports") or []
    if top:
        print("\nTop imports by profit/day (whole-market turnover, not one trader's):")
        for row in top[:5]:
            print(f"  {row['item']:<40} {row['max_profit_per_day']:>15,.0f} ISK/day")


def cmd_refresh_shortlist(args: argparse.Namespace) -> None:
    print("Refreshing live market data for every shortlist item...")
    _print_shortlist_result(actions.do_refresh_shortlist())


def cmd_check_unlisted_stock(args: argparse.Namespace) -> None:
    rows = actions.do_check_seller_unlisted_stock()["rows"]
    if not rows:
        print("Nothing unlisted: every shortlist item at the structure has a sell order.")
        return
    print(f"{len(rows)} item(s) sitting at the structure with no sell order:")
    for r in rows:
        margin = f"{r.margin * 100:6.1f}%" if r.margin is not None else "     -"
        print(f"  {r.item:<40} {r.unlisted_quantity:>10,.0f} units   margin {margin}")


def cmd_check_undercut(args: argparse.Namespace) -> None:
    rows = actions.do_check_undercut()["rows"]
    if not rows:
        print("None of your sell orders are currently undercut.")
        return
    print(f"{len(rows)} order(s) undercut:")
    for r in rows:
        print(f"  {r.item:<40} yours {r.my_price:>14,.2f}  vs {r.competitor_price:>14,.2f}  "
              f"(-{r.difference:,.2f})")


def cmd_reconcile_trades(args: argparse.Namespace) -> None:
    print("Matching Jita buys against structure sells...")
    result = actions.do_reconcile_trades()
    print(f"Matched trades:  {result['matched_trades']:,}")
    print(f"Realized profit: {result['total_realized_profit']:,.0f} ISK")
    print(f"Average margin:  {result['average_margin'] * 100:.1f}% (weighted by cost basis)")
    for item, profit in result["top3_items_by_profit"]:
        print(f"  {item:<40} {profit:>15,.0f} ISK")


def cmd_pipeline(args: argparse.Namespace) -> None:
    results = actions.do_pipeline(safe=args.safe, rebuild_universe=args.rebuild_universe)
    for step, result in results.items():
        label = step.replace("_", " ")
        if "error" in result:
            print(f"{label}: failed - {result['error']}")
            continue
        if step in ("build_universe", "build_focused"):
            print(f"{label}: {result['count']:,} items")
        elif step == "refresh_and_prune_candidates":
            print(f"{label}: {result['new_candidates_evaluated']:,} evaluated, "
                  f"{result['new_candidates_added']:,} added, "
                  f"{result['deactivated_count']:,} deactivated, "
                  f"{result['reactivated_count']:,} reactivated")
            _print_shortlist_result(result)
        elif step == "reconcile_trades":
            print(f"{label}: {result['matched_trades']:,} matched, "
                  f"{result['total_realized_profit']:,.0f} ISK realized")


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
        "build-universe", help="rebuild the candidate universe (and its focused subset) from the SDE"
    ).set_defaults(func=cmd_build_universe)

    p_find = sub.add_parser("find-candidates", help="backtest focused candidates for import potential")
    # Same --safe/--full pair the parent repo's CLI uses: safe mode caps each
    # run at safe_mode_max_ids and rotates through the universe across runs.
    p_find.add_argument("--safe", dest="safe", action="store_true", default=True,
                        help="cap this run at safe_mode_max_ids candidates (default)")
    p_find.add_argument("--full", dest="safe", action="store_false",
                        help="scan the whole focused universe in one run (slow)")
    p_find.set_defaults(func=cmd_find_candidates)

    sub.add_parser(
        "add-to-shortlist", help="add the last search's recommended candidates to the shortlist"
    ).set_defaults(func=cmd_add_to_shortlist)
    sub.add_parser(
        "refresh-shortlist", help="re-price every shortlist item against live market data"
    ).set_defaults(func=cmd_refresh_shortlist)
    sub.add_parser(
        "check-unlisted-stock", help="shortlist stock at the structure with no sell order on it"
    ).set_defaults(func=cmd_check_unlisted_stock)
    sub.add_parser(
        "check-undercut", help="your sell orders currently beaten by a cheaper competing order"
    ).set_defaults(func=cmd_check_undercut)
    sub.add_parser(
        "reconcile-trades", help="match Jita buys against structure sells for realized profit"
    ).set_defaults(func=cmd_reconcile_trades)

    p_pipeline = sub.add_parser("pipeline", help="run the daily workflow (each step isolated)")
    p_pipeline.add_argument("--safe", dest="safe", action="store_true", default=True,
                            help="cap the candidate search at safe_mode_max_ids (default)")
    p_pipeline.add_argument("--full", dest="safe", action="store_false",
                            help="scan the whole focused universe (slow)")
    p_pipeline.add_argument("--rebuild-universe", action="store_true",
                            help="rebuild the candidate universe first (occasional setup step)")
    p_pipeline.set_defaults(func=cmd_pipeline)

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
