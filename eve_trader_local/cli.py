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
    eve-trader-local sync-esi
    eve-trader-local discover-build-candidates
    eve-trader-local set-stock-target <item> <quantity> [--jita] / remove-stock-target <item> / list-stock-targets
    eve-trader-local plan-production / plan-asset-optimized
    eve-trader-local market-status / stock-value
    eve-trader-local discover-ship-margins
    eve-trader-local invention-logistics / t1-bpc-invention-needs
    eve-trader-local set-manual-stock <item> <count> / remove-manual-stock <item> / list-manual-stock
    eve-trader-local create-special-order <item:qty> [<item:qty> ...] [--note] [--net-against-stock]
    eve-trader-local list-special-orders / remove-special-order <order_id>
    eve-trader-local compute-special-order <order_id>
    eve-trader-local pipeline [--rebuild-universe]
    eve-trader-local parse-fitting <path>
    eve-trader-local auth --role doctrine
    eve-trader-local create-doctrine <name> [--description] / list-doctrines
    eve-trader-local add-fitting <doctrine_id> <path> [--name] [--contract-target] [--stockpile-target]
    eve-trader-local list-fittings [doctrine_id]
    eve-trader-local sync-doctrine / validate-contracts
    eve-trader-local doctrine-status [doctrine_id] / stockpile-status [doctrine_id]
    eve-trader-local shopping-list [doctrine_id]
    eve-trader-local list-contracts [--status] / contract-history [doctrine_id]
    eve-trader-local add-ore-to-shortlist / refresh-ore-shortlist / list-ore-shortlist
    eve-trader-local quote-reprocessing <paste-file>
    eve-trader-local set-mineral-requirement <item> <qty> / list-mineral-requirements
    eve-trader-local solve-shopping-list
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
from .doctrine import actions as doctrine_actions
from .doctrine import config as doctrine_config
from .refining import actions as refining_actions
from .refining import config as refining_config
from .doctrine import esi_sync as doctrine_esi_sync
from .errors import ActionError
from .paths import config_path, db_path
from .production import actions as production_actions
from .production import config as production_config
from .production import esi_sync


def cmd_init_db(args: argparse.Namespace) -> None:
    storage.init_db()
    print(f"Database ready: {db_path()}")


def cmd_auth(args: argparse.Namespace) -> None:
    # A producer/doctrine character is authorized for a different set of
    # scopes than a buyer/seller (assets, blueprints, industry jobs/contracts
    # - see production/esi_sync.py and doctrine/esi_sync.py), and EVE SSO
    # grants exactly what the login asks for, so the role has to choose the
    # scope list here rather than after the fact.
    if args.role == esi_sync.PRODUCER_ROLE_PREFIX:
        scopes = esi_sync.PRODUCTION_SCOPES
    elif args.role == doctrine_esi_sync.DOCTRINE_ROLE_PREFIX:
        scopes = doctrine_esi_sync.DOCTRINE_SCOPES
    else:
        scopes = None
    record = TokenManager().login(args.role, scopes)
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
    print(f"config.yaml: {config_path()}{'' if config_path().exists() else ' (not present, using defaults)'}")
    print("\n[trading]")
    for key, value in vars(config.reload()).items():
        print(f"  {key} = {value!r}")
    print("\n[production]")
    for key, value in vars(production_config.reload()).items():
        print(f"  {key} = {value!r}")
    print("\n[doctrine]")
    for key, value in vars(doctrine_config.reload()).items():
        print(f"  {key} = {value!r}")
    print("\n[refining]")
    for key, value in vars(refining_config.reload()).items():
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


def cmd_sync_esi(args: argparse.Namespace) -> None:
    print("Syncing producer assets, blueprints and industry jobs from ESI...")
    result = production_actions.do_sync_esi()
    for owner, summary in list(result["characters"].items()) + list(result["corporations"].items()):
        if isinstance(summary, dict):
            extra = summary.get("corp")
            print(f"  {owner:<32} {summary['assets']:>7,} assets  "
                  f"{summary['blueprints']:>6,} blueprints  {summary['industry_jobs']:>5,} jobs"
                  + (f"  [{extra}]" if extra else ""))
        else:
            print(f"  {owner:<32} {summary}")


def cmd_discover_build_candidates(args: argparse.Namespace) -> None:
    print("Scanning the SDE for build-vs-buy opportunities (this can take a while)...")
    rows = production_actions.do_discover_build_candidates(top_n=args.top_n)["rows"]
    if not rows:
        print("No build candidates cleared the configured margin/profit thresholds.")
        return
    print(f"{len(rows)} candidate(s), top by potential daily profit (whole-market turnover, "
          "not one builder's):")
    for r in rows[:10]:
        print(f"  {r.type_name:<40} {r.activity:<10} margin {r.margin * 100:6.1f}%   "
              f"{r.potential_daily_profit:>15,.0f} ISK/day")


def cmd_set_stock_target(args: argparse.Namespace) -> None:
    result = production_actions.do_add_stock_target(args.item, args.quantity, jita_target=args.jita)
    where = "Jita" if result["jita_target"] else "home"
    print(f"Stock target set: {result['type_name']} -> {result['quantity']:,.0f} units (sells at {where}).")


def cmd_remove_stock_target(args: argparse.Namespace) -> None:
    result = production_actions.do_remove_stock_target(args.item)
    print(f"Removed stock target: {result['type_name']}")


def cmd_list_stock_targets(args: argparse.Namespace) -> None:
    rows = production_actions.do_list_stock_targets()["rows"]
    if not rows:
        print("No stock targets configured.")
        return
    for type_id, type_name, quantity, jita_target in rows:
        where = "Jita" if jita_target else "home"
        print(f"  {type_name:<40} {quantity:>10,.0f} units   sells at {where}")


def cmd_plan_production(args: argparse.Namespace) -> None:
    print("Planning production against configured stock targets (this can take a while)...")
    plan = production_actions.do_plan_production()
    print("\nInventory:")
    for row in plan["inventory"]:
        print(f"  {row.type_name:<40} target {row.target:>10,.0f}   on hand {row.current_stock:>10,.0f}   "
              f"missing {row.total_missing:>10,.0f}")
    if plan["build_list"]:
        print("\nBuild List:")
        for row in plan["build_list"]:
            margin = f"{row.margin * 100:6.1f}%" if row.margin is not None else "     -"
            print(f"  {row.type_name:<40} {row.job_runs:>6,} runs   "
                  f"cost/unit {row.unit_build_cost or 0:>14,.2f}   margin {margin}")
    if plan["buy_list"]:
        print("\nBuy List:")
        for row in plan["buy_list"]:
            total = f"{row.total_price:>16,.0f}" if row.total_price is not None else "               -"
            print(f"  {row.type_name:<40} {row.quantity:>10,.0f} units   {total} ISK   "
                  f"on hand {row.on_hand_pct:5.1f}%")
    if not plan["build_list"] and not plan["buy_list"]:
        print("\nEvery stock target is already fully covered.")
    if plan["invention_list"]:
        print("\nInvention Needs:")
        for row in plan["invention_list"]:
            print(f"  {row.type_name:<40} attempts {row.recommended_invention_runs:>6,}   "
                  f"BPCs owned {row.t2_bpc_owned:>6,}   stockpile {row.stockpile_pct:6.1f}%")


def cmd_plan_asset_optimized(args: argparse.Namespace) -> None:
    print("Planning readiness against configured stock targets (this can take a while)...")
    plan = production_actions.do_plan_asset_optimized()
    if not plan["jobs"]:
        print("Nothing to build right now.")
        return
    for row in plan["jobs"]:
        margin = f"{row.margin * 100:6.1f}%" if row.margin is not None else "     -"
        coverage = f"{row.stock_coverage * 100:5.1f}%" if row.stock_coverage is not None else "    -"
        print(f"  {row.type_name:<40} {row.job_runs:>6,} runs   ready now {row.runs_ready_now:>6,}   "
              f"margin {margin}   stock coverage {coverage}")


def cmd_market_status(args: argparse.Namespace) -> None:
    rows = production_actions.do_market_status()["rows"]
    if not rows:
        print("No stock targets with a shortfall to report.")
        return
    for row in rows:
        where = "Jita" if row.jita_target else "home"
        print(f"  {row.type_name:<40} target {row.target:>10,.0f}   on hand {row.current_stock:>10,.0f}   "
              f"missing {row.missing:>10,.0f}   sells at {where}")


def cmd_stock_value(args: argparse.Namespace) -> None:
    result = production_actions.do_stock_value()
    print(f"Total stock value: {result['total_value']:,.0f} ISK "
          f"({result['priced_items']} priced, {result['unpriced_items']} unpriced)")


def cmd_discover_ship_margins(args: argparse.Namespace) -> None:
    print("Scanning ships for build cost/margin (this can take a while)...")
    rows = production_actions.do_get_ship_margins()["rows"]
    if not rows:
        print("No manufacturable ships found - refresh SDE first?")
        return
    for r in rows[:20]:
        home_price = f"{r.home_price:,.0f}" if r.home_price is not None else "-"
        margin = f"{r.margin_home * 100:6.1f}%" if r.margin_home is not None else "     -"
        print(f"  {r.type_name:<40} {r.activity:<10} home {home_price:>16}   margin {margin}")


def cmd_invention_logistics(args: argparse.Namespace) -> None:
    rows = production_actions.do_invention_logistics()["rows"]
    if not rows:
        print("Nothing needed at the configured invention location right now.")
        return
    for row in rows:
        print(f"  {row.type_name:<40} needed {row.needed:>8,.0f}   available {row.available:>8,.0f}   "
              f"missing {row.missing:>8,.0f}")


def cmd_t1_bpc_invention_needs(args: argparse.Namespace) -> None:
    rows = production_actions.do_t1_bpc_invention_needs()["rows"]
    if not rows:
        print("Nothing needed at the configured invention location right now.")
        return
    for row in rows:
        bpo = "BPO on site" if row.bpo_present else ""
        print(f"  {row.name:<40} needed {row.needed:>6,}   available {row.available:>6,}   "
              f"missing {row.missing:>6,}   {row.stockpile_pct:5.1f}%  {bpo}")


def cmd_set_manual_stock(args: argparse.Namespace) -> None:
    result = production_actions.do_set_manual_stock(args.item, args.count)
    print(f"Manual stock set: {result['type_name']} -> {result['count']:,.0f} units.")


def cmd_remove_manual_stock(args: argparse.Namespace) -> None:
    result = production_actions.do_remove_manual_stock(args.item)
    print(f"Removed manual stock: {result['type_name']}")


def cmd_list_manual_stock(args: argparse.Namespace) -> None:
    rows = production_actions.do_list_manual_stock()["rows"]
    if not rows:
        print("No manual stock overrides configured.")
        return
    for type_id, type_name, count in rows:
        print(f"  {type_name:<40} {count:>10,.0f} units")


def _resolve_type_for_cli(type_id_or_name: str) -> int:
    """NAME_OR_TYPE_ID -> type_id, same lookup shape as production/actions.py's
    own module-private _resolve_type (duplicated rather than imported - that
    helper is intentionally module-private, same "duplicate this small
    resolver rather than cross-import a private name" precedent
    refining/actions.py's own copy already sets)."""
    stripped = type_id_or_name.strip()
    if stripped.isdigit():
        type_id = int(stripped)
        if storage.get_sde_type(type_id) is None:
            raise ActionError(f"Unknown type_id {type_id} - refresh SDE first?")
        return type_id
    matches = storage.search_sde_types(stripped, limit=2)
    exact = [m for m in matches if m[1].lower() == stripped.lower()]
    if not exact:
        if not matches:
            raise ActionError(f"No type found for '{stripped}'. Refresh SDE first?")
        raise ActionError(f"No exact match for '{stripped}'. Did you mean: {matches[0][1]}?")
    return exact[0][0]


def cmd_create_special_order(args: argparse.Namespace) -> None:
    items = []
    for spec in args.item:
        if ":" not in spec:
            raise ActionError(f"Invalid item '{spec}' - expected NAME_OR_TYPE_ID:QUANTITY")
        name_or_id, qty_str = spec.rsplit(":", 1)
        try:
            quantity = float(qty_str)
        except ValueError:
            raise ActionError(f"Invalid quantity in '{spec}'")
        items.append({"type_id": _resolve_type_for_cli(name_or_id), "quantity": quantity})
    result = production_actions.do_create_special_order(
        items, note=args.note, net_against_stock=args.net_against_stock)
    print(f"Created special order {result['order_id']} with {len(items)} item(s).")


def cmd_list_special_orders(args: argparse.Namespace) -> None:
    rows = production_actions.do_list_special_orders()
    if not rows:
        print("No special orders.")
        return
    for row in rows:
        net = "nets against stock" if row.net_against_stock else "from scratch"
        note = f" - {row.note}" if row.note else ""
        print(f"  {row.order_id}  [{row.status}]  {row.item_count} item(s)  ({net}){note}")


def cmd_remove_special_order(args: argparse.Namespace) -> None:
    result = production_actions.do_remove_special_order(args.order_id)
    print(f"Removed special order {result['removed']}.")


def cmd_update_special_order(args: argparse.Namespace) -> None:
    result = production_actions.do_update_special_order(args.order_id, status=args.status, note=args.note)
    print(f"Special order {result['order'].order_id} updated - status: {result['order'].status}.")


def cmd_compute_special_order(args: argparse.Namespace) -> None:
    plan = production_actions.do_compute_special_order(args.order_id)
    print("Line Items:")
    for row in plan["line_items"]:
        print(f"  {row.type_name:<40} {row.quantity:>10,.0f} units")
    if plan["build_list"]:
        print("\nBuild List:")
        for row in plan["build_list"]:
            margin = f"{row.margin * 100:6.1f}%" if row.margin is not None else "     -"
            print(f"  {row.type_name:<40} {row.job_runs:>6,} runs   "
                  f"cost/unit {row.unit_build_cost or 0:>14,.2f}   margin {margin}")
    if plan["buy_list"]:
        print("\nBuy List:")
        for row in plan["buy_list"]:
            total = f"{row.total_price:>16,.0f}" if row.total_price is not None else "               -"
            print(f"  {row.type_name:<40} {row.quantity:>10,.0f} units   {total} ISK   "
                  f"on hand {row.on_hand_pct:5.1f}%")
    if not plan["build_list"] and not plan["buy_list"]:
        print("\nEvery line item is already fully covered.")
    if plan["stock_overlap_warning"]:
        print("\nStock overlap warning (shared with configured stock targets):")
        for row in plan["stock_overlap_warning"]:
            print(f"  {row.type_name:<40} {row.current_stock:>10,.0f} units on hand")


def cmd_parse_fitting(args: argparse.Namespace) -> None:
    # A standalone "prove the SDE-backed parser works" preview - do_parse_
    # fitting never persists (see its own docstring).
    with open(args.path, encoding="utf-8") as f:
        raw_eft = f.read()
    result = doctrine_actions.do_parse_fitting(raw_eft)

    print(f"Hull: {result['hull_name']} (type_id {result['hull_type_id']})")
    print(f"Fit name: {result['fit_name']}")
    if result["items"]:
        print("\nItems:")
        for item in result["items"]:
            offline = "  /offline" if item["is_offline"] else ""
            print(f"  line {item['line_no']:<4} {item['slot_section']:<18} type_id {item['type_id']:<10} "
                  f"qty {item['quantity']:g}{offline}")
    if result["issues"]:
        print("\nIssues:")
        for issue in result["issues"]:
            print(f"  line {issue['line_no']:<4} {issue['issue_kind']:<18} {issue['message']}")


def cmd_create_doctrine(args: argparse.Namespace) -> None:
    result = doctrine_actions.do_create_doctrine(args.name, args.description)
    print(f"Created doctrine '{result['name']}' ({result['doctrine_id']}).")


def cmd_list_doctrines(args: argparse.Namespace) -> None:
    rows = doctrine_actions.do_list_doctrines()["rows"]
    if not rows:
        print("No doctrines yet. Run: eve-trader-local create-doctrine <name>")
        return
    for r in rows:
        state = "active" if r["active"] else "inactive"
        print(f"  {r['doctrine_id']}  {r['name']:<30} {state}")


def cmd_add_fitting(args: argparse.Namespace) -> None:
    with open(args.path, encoding="utf-8") as f:
        raw_eft = f.read()
    result = doctrine_actions.do_add_fitting(
        args.doctrine_id, raw_eft, name=args.name, contract_target=args.contract_target,
        stockpile_target=args.stockpile_target,
    )
    fitting = result["fitting"]
    print(f"Added fitting '{fitting['name']}' ({fitting['fitting_id']}) - hull type_id {fitting['hull_type_id']}.")
    if result["issues"]:
        print(f"{len(result['issues'])} parse issue(s):")
        for issue in result["issues"]:
            print(f"  line {issue['line_no']:<4} {issue['issue_kind']:<18} {issue['message']}")


def cmd_list_fittings(args: argparse.Namespace) -> None:
    rows = doctrine_actions.do_list_fittings(args.doctrine_id)["rows"]
    if not rows:
        print("No fittings yet. Run: eve-trader-local add-fitting <doctrine_id> <path>")
        return
    for r in rows:
        state = "active" if r["active"] else "inactive"
        print(f"  {r['fitting_id']}  {r['name']:<30} contract target {r['contract_target']:>3}  "
              f"stockpile target {r['stockpile_target']:>5}  {state}")


def cmd_sync_doctrine(args: argparse.Namespace) -> None:
    print("Syncing Doctrine contracts and assets from ESI...")
    result = doctrine_actions.do_sync_doctrine()
    contracts = result.get("contracts", {})
    if "error" in contracts:
        print(f"  contracts: failed - {contracts['error']}")
    else:
        print(f"  contracts synced: {contracts['contracts_synced']:,}  "
              f"(not a relevant hull: {contracts['contracts_no_relevant_hull']:,})")
    assets = result.get("assets", {})
    if "error" in assets:
        print(f"  assets: failed - {assets['error']}")
    else:
        for owner, summary in list(assets["characters"].items()) + list(assets["corporations"].items()):
            if isinstance(summary, dict):
                print(f"  {owner:<32} {summary['assets']:>7,} assets")
            else:
                print(f"  {owner:<32} {summary}")


def cmd_validate_contracts(args: argparse.Namespace) -> None:
    result = doctrine_actions.do_validate_contracts()
    print(f"Revalidated {result['revalidated']:,} contract(s).")


def cmd_doctrine_status(args: argparse.Namespace) -> None:
    for d in doctrine_actions.do_get_doctrine_status(args.doctrine_id)["doctrines"]:
        print(f"{d['doctrine_name']}  overall={d['overall']}  "
              f"contracts={d['contract_rollup']}  stockpile={d['stockpile_rollup']}")
        for f in d["fittings"]:
            print(f"  {f['fitting_name']:<30} contracts {f['valid_contracts']}/{f['contract_target']} "
                  f"({f['contract_status']})   stockpile shortfall "
                  f"{f['worst_stockpile_shortfall_pct'] * 100:5.1f}% ({f['stockpile_status']})")


def cmd_stockpile_status(args: argparse.Namespace) -> None:
    result = doctrine_actions.do_get_stockpile_status(args.doctrine_id)
    if not result["assets_available"]:
        print("No Doctrine asset sync has ever run - stockpile figures are unavailable. "
              "Run: eve-trader-local sync-doctrine")
    rows = result["aggregated_rows"]
    if not rows:
        print("Nothing short of target.")
        return
    print("Aggregated shortfalls (across every doctrine/fitting that needs the item):")
    for r in rows:
        print(f"  {r['type_name']:<40} required {r['required_total']:>8,.0f}   available {r['available']:>8,.0f}   "
              f"short {r['shortfall']:>8,.0f}   severity {r['severity'] or '-'}")


def _fmt_isk(v: Optional[float]) -> str:
    return f"{v:>12,.0f}" if v is not None else f"{'-':>12}"


def cmd_shopping_list(args: argparse.Namespace) -> None:
    rows = doctrine_actions.do_get_shopping_list(args.doctrine_id)["rows"]
    if not rows:
        print("Nothing short of target.")
        return
    print(f"{'Item':<40} {'Short':>8} {'Build':>12} {'C-J':>12} {'Jita':>12}   Cheapest   Total cost")
    for r in rows:
        total = f"{r['total_cost']:,.0f}" if r["total_cost"] is not None else "no price available"
        print(f"{r['type_name']:<40} {r['shortfall']:>8,.0f} {_fmt_isk(r['build_cost'])} "
              f"{_fmt_isk(r['cj_price'])} {_fmt_isk(r['jita_landed_price'])}   "
              f"{r['recommended_source'] or '-':<8}   {total}")


def cmd_list_contracts(args: argparse.Namespace) -> None:
    rows = doctrine_actions.do_list_contracts(status=args.status)["rows"]
    if not rows:
        print("No synced contracts.")
        return
    for r in rows:
        hull = r["hull_name"] or "-"
        print(f"  {r['contract_id']:<12} {hull:<30} {r['validation_status']:<10} {r['status']:<12} "
              f"{r['price'] or 0:>14,.0f} ISK")


def cmd_contract_history(args: argparse.Namespace) -> None:
    rows = doctrine_actions.do_contract_history(args.doctrine_id)["rows"]
    if not rows:
        print("No finished contracts recorded yet.")
        return
    for r in rows:
        hull = r["hull_name"] or r["fitting_name"] or "-"
        buyer = r["acceptor_name"] or (str(r["acceptor_id"]) if r["acceptor_id"] else "-")
        completed = r["date_completed"] or "-"
        print(f"  {r['contract_id']:<12} {hull:<30} {buyer:<25} {r['price'] or 0:>14,.0f} ISK   {completed}")


def cmd_add_ore_to_shortlist(args: argparse.Namespace) -> None:
    result = refining_actions.do_add_ore_to_shortlist()
    print(f"Added {result['added']:,} candidate(s); {result['already_tracked']:,} already tracked.")


def cmd_refresh_ore_shortlist(args: argparse.Namespace) -> None:
    print("Refreshing live market data for every Ore Shortlist item...")
    result = refining_actions.do_refresh_ore_shortlist()
    print(f"Evaluated {result['evaluated']:,} item(s), {result['import_candidates']:,} worth importing.")
    if result.get("priced_via_fallback"):
        print("Note: mineral prices came from the Goonmetrics fallback, not the real order book.")


def cmd_list_ore_shortlist(args: argparse.Namespace) -> None:
    rows = refining_actions.do_get_ore_shortlist()["rows"]
    if not rows:
        print("Ore Shortlist is empty - run add-ore-to-shortlist then refresh-ore-shortlist.")
        return
    for r in rows:
        state = "" if r.active else "  [inactive]"
        profit = f"{r.profit_per_unit:>12,.2f}" if r.profit_per_unit is not None else "           -"
        margin = f"{r.margin * 100:6.1f}%" if r.margin is not None else "     -"
        print(f"  {r.item:<32} {r.decision:<16} profit/unit {profit}  margin {margin}{state}")


def cmd_quote_reprocessing(args: argparse.Namespace) -> None:
    with open(args.path, encoding="utf-8") as f:
        paste_text = f.read()
    result = refining_actions.do_quote_reprocessing(paste_text)
    for r in result["rows"]:
        if r.error:
            print(f"  {r.name:<32} error: {r.error}")
            continue
        sell = f"{r.sell_as_is_value:>14,.0f}" if r.sell_as_is_value is not None else "             -"
        refined = f"{r.refined_value:>14,.0f}" if r.refined_value is not None else "             -"
        print(f"  {r.name:<32} x{r.quantity:<6,} sell-as-is {sell}  refined {refined}  -> {r.decision}")
    totals = result["totals"]
    print(f"\n{totals['reprocess_count']:,} item(s) worth reprocessing - "
          f"{totals['total_refined_value']:,.0f} ISK refined vs. "
          f"{totals['total_sell_as_is_value']:,.0f} ISK sold as-is.")
    if result.get("priced_via_fallback"):
        print("Note: prices came from the Goonmetrics fallback, not the real order book.")


def cmd_set_mineral_requirement(args: argparse.Namespace) -> None:
    result = refining_actions.do_set_mineral_requirement(args.item, args.quantity)
    print(f"Requirement set: {result['type_name']} -> {result['required_qty']:,.0f} units.")


def cmd_remove_mineral_requirement(args: argparse.Namespace) -> None:
    result = refining_actions.do_remove_mineral_requirement(args.item)
    print(f"Removed requirement: {result['type_name']}")


def cmd_list_mineral_requirements(args: argparse.Namespace) -> None:
    rows = refining_actions.do_load_mineral_requirements()
    if not rows:
        print("No mineral requirements configured.")
        return
    for r in rows:
        print(f"  {r['name']:<32} {r['required_qty']:>12,.0f} units")


def cmd_solve_shopping_list(args: argparse.Namespace) -> None:
    print("Solving the Mineral Shopping List (this can take a moment)...")
    plan = refining_actions.do_optimize_mineral_shopping_list()
    if plan["ore_purchases"]:
        print("\nOre to buy:")
        for p in plan["ore_purchases"]:
            print(f"  {p.item:<32} {p.portions:>6,} portions ({p.units:>10,.0f} units)  "
                  f"{p.total_cost:>16,.0f} ISK")
    if plan["direct_purchases"]:
        print("\nBuy directly:")
        for p in plan["direct_purchases"]:
            print(f"  {p.name:<32} {p.quantity:>10,.0f} units  {p.total_cost:>16,.0f} ISK  ({p.source})")
    print(f"\nTotal cost: {plan['total_cost']:,.0f} ISK  (ore {plan['ore_cost']:,.0f} + "
          f"direct {plan['direct_cost']:,.0f})")
    if plan.get("savings_vs_all_direct") is not None:
        print(f"Savings vs. buying everything outright: {plan['savings_vs_all_direct']:,.0f} ISK")


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
    p_auth.add_argument("--role", required=True, help="role prefix: buyer / seller / producer / doctrine")
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

    sub.add_parser(
        "sync-esi", help="refresh owned assets/blueprints/industry jobs for every producer character"
    ).set_defaults(func=cmd_sync_esi)

    p_discover = sub.add_parser(
        "discover-build-candidates", help="scan the SDE for build-vs-buy opportunities"
    )
    p_discover.add_argument("--top-n", dest="top_n", type=int, default=200,
                            help="max rows to return, already ranked (default 200)")
    p_discover.set_defaults(func=cmd_discover_build_candidates)

    p_set_target = sub.add_parser(
        "set-stock-target", help="set (or update) how many units of an item to keep in stock"
    )
    p_set_target.add_argument("item", help="type_id or exact item name")
    p_set_target.add_argument("quantity", type=float, help="target quantity to keep in stock")
    p_set_target.add_argument("--jita", action="store_true",
                              help="this target sells at Jita, not home (feeds the build-margin gate)")
    p_set_target.set_defaults(func=cmd_set_stock_target)

    p_remove_target = sub.add_parser("remove-stock-target", help="remove a stock target")
    p_remove_target.add_argument("item", help="type_id or exact item name")
    p_remove_target.set_defaults(func=cmd_remove_stock_target)

    sub.add_parser(
        "list-stock-targets", help="list every configured stock target"
    ).set_defaults(func=cmd_list_stock_targets)

    sub.add_parser(
        "plan-production", help="run the stock-aware buy/build planner against configured stock targets"
    ).set_defaults(func=cmd_plan_production)

    sub.add_parser(
        "plan-asset-optimized",
        help="readiness-focused planner: which jobs are startable right now vs. blocked upstream",
    ).set_defaults(func=cmd_plan_asset_optimized)

    sub.add_parser(
        "market-status", help="cheap target-vs-current-stock read, no live pricing needed"
    ).set_defaults(func=cmd_market_status)

    sub.add_parser(
        "stock-value", help="total ISK value of current stock, priced at home/Jita sell quotes"
    ).set_defaults(func=cmd_stock_value)

    sub.add_parser(
        "discover-ship-margins", help="every manufacturable ship's current price/build cost/margin"
    ).set_defaults(func=cmd_discover_ship_margins)

    sub.add_parser(
        "invention-logistics",
        help="datacores/decryptors/T1 BPCs needed vs. what's at the configured invention location",
    ).set_defaults(func=cmd_invention_logistics)

    sub.add_parser(
        "t1-bpc-invention-needs", help="T1-blueprint(-or-relic)-only slice of invention-logistics"
    ).set_defaults(func=cmd_t1_bpc_invention_needs)

    p_set_manual = sub.add_parser(
        "set-manual-stock", help="record a manually-counted stock override, on top of ESI-synced assets"
    )
    p_set_manual.add_argument("item", help="type_id or exact item name")
    p_set_manual.add_argument("count", type=float, help="physically counted quantity")
    p_set_manual.set_defaults(func=cmd_set_manual_stock)

    p_remove_manual = sub.add_parser("remove-manual-stock", help="remove a manual stock override")
    p_remove_manual.add_argument("item", help="type_id or exact item name")
    p_remove_manual.set_defaults(func=cmd_remove_manual_stock)

    sub.add_parser(
        "list-manual-stock", help="list every configured manual stock override"
    ).set_defaults(func=cmd_list_manual_stock)

    p_create_order = sub.add_parser(
        "create-special-order", help="create a one-off build order for an ad-hoc list of items"
    )
    p_create_order.add_argument("item", nargs="+", help="one or more NAME_OR_TYPE_ID:QUANTITY pairs")
    p_create_order.add_argument("--note", default=None, help="optional free-text note")
    p_create_order.add_argument("--net-against-stock", dest="net_against_stock", action="store_true",
                                help="net against current ESI-synced stock instead of planning from scratch")
    p_create_order.set_defaults(func=cmd_create_special_order)

    sub.add_parser(
        "list-special-orders", help="list every special order"
    ).set_defaults(func=cmd_list_special_orders)

    p_remove_order = sub.add_parser("remove-special-order", help="delete a special order")
    p_remove_order.add_argument("order_id")
    p_remove_order.set_defaults(func=cmd_remove_special_order)

    p_update_order = sub.add_parser("update-special-order", help="update a special order's status/note")
    p_update_order.add_argument("order_id")
    p_update_order.add_argument("--status", choices=("open", "done"), default=None)
    p_update_order.add_argument("--note", default=None)
    p_update_order.set_defaults(func=cmd_update_special_order)

    p_compute_order = sub.add_parser(
        "compute-special-order", help="run the buy/build planner for one special order's current line items"
    )
    p_compute_order.add_argument("order_id")
    p_compute_order.set_defaults(func=cmd_compute_special_order)

    p_pipeline = sub.add_parser("pipeline", help="run the daily workflow (each step isolated)")
    p_pipeline.add_argument("--safe", dest="safe", action="store_true", default=True,
                            help="cap the candidate search at safe_mode_max_ids (default)")
    p_pipeline.add_argument("--full", dest="safe", action="store_false",
                            help="scan the whole focused universe (slow)")
    p_pipeline.add_argument("--rebuild-universe", action="store_true",
                            help="rebuild the candidate universe first (occasional setup step)")
    p_pipeline.set_defaults(func=cmd_pipeline)

    p_parse_fitting = sub.add_parser(
        "parse-fitting", help="parse an EFT-format fitting file against the local SDE cache and print the result"
    )
    p_parse_fitting.add_argument("path", help="path to a text file containing an EFT fitting export")
    p_parse_fitting.set_defaults(func=cmd_parse_fitting)

    p_create_doctrine = sub.add_parser("create-doctrine", help="create a new doctrine")
    p_create_doctrine.add_argument("name")
    p_create_doctrine.add_argument("--description", default=None)
    p_create_doctrine.set_defaults(func=cmd_create_doctrine)

    sub.add_parser("list-doctrines", help="list every doctrine").set_defaults(func=cmd_list_doctrines)

    p_add_fitting = sub.add_parser(
        "add-fitting", help="parse an EFT fitting file and add it to a doctrine"
    )
    p_add_fitting.add_argument("doctrine_id")
    p_add_fitting.add_argument("path", help="path to a text file containing an EFT fitting export")
    p_add_fitting.add_argument("--name", default=None, help="override the fit name parsed from the EFT header")
    p_add_fitting.add_argument("--contract-target", type=int, default=0,
                               help="how many contracts of this fit should be listed at once")
    p_add_fitting.add_argument("--stockpile-target", type=int, default=0,
                               help="how many complete sets of this fit's materials to keep in stock")
    p_add_fitting.set_defaults(func=cmd_add_fitting)

    p_list_fittings = sub.add_parser("list-fittings", help="list fittings, optionally for one doctrine")
    p_list_fittings.add_argument("doctrine_id", nargs="?", default=None)
    p_list_fittings.set_defaults(func=cmd_list_fittings)

    sub.add_parser(
        "sync-doctrine", help="sync Doctrine contracts + assets from ESI and match/validate them"
    ).set_defaults(func=cmd_sync_doctrine)

    sub.add_parser(
        "validate-contracts", help="re-match/re-validate every synced contract against current fittings, no ESI"
    ).set_defaults(func=cmd_validate_contracts)

    p_doctrine_status = sub.add_parser(
        "doctrine-status", help="contract + stockpile ampel status per doctrine/fitting"
    )
    p_doctrine_status.add_argument("doctrine_id", nargs="?", default=None)
    p_doctrine_status.set_defaults(func=cmd_doctrine_status)

    p_stockpile_status = sub.add_parser(
        "stockpile-status", help="aggregated stockpile shortfalls across every doctrine/fitting"
    )
    p_stockpile_status.add_argument("doctrine_id", nargs="?", default=None)
    p_stockpile_status.set_defaults(func=cmd_stockpile_status)

    p_shopping_list = sub.add_parser(
        "shopping-list", help="every stockpile shortfall priced Build vs. Buy-C-J vs. Buy-Jita"
    )
    p_shopping_list.add_argument("doctrine_id", nargs="?", default=None)
    p_shopping_list.set_defaults(func=cmd_shopping_list)

    p_list_contracts = sub.add_parser("list-contracts", help="list every synced Doctrine contract")
    p_list_contracts.add_argument("--status", default=None,
                                  help="filter by validation_status (valid/tolerable/unmatched/invalid)")
    p_list_contracts.set_defaults(func=cmd_list_contracts)

    p_contract_history = sub.add_parser(
        "contract-history", help="permanent log of finished Doctrine contract sales - who bought what and when"
    )
    p_contract_history.add_argument("doctrine_id", nargs="?", default=None)
    p_contract_history.set_defaults(func=cmd_contract_history)

    sub.add_parser(
        "add-ore-to-shortlist", help="add every compressed ore/ice type from the SDE to the Ore Shortlist"
    ).set_defaults(func=cmd_add_ore_to_shortlist)
    sub.add_parser(
        "refresh-ore-shortlist", help="re-price every Ore Shortlist item against live market data"
    ).set_defaults(func=cmd_refresh_ore_shortlist)
    sub.add_parser(
        "list-ore-shortlist", help="show the last Ore Shortlist evaluation run"
    ).set_defaults(func=cmd_list_ore_shortlist)

    p_quote = sub.add_parser(
        "quote-reprocessing", help="quote a pasted inventory list: sell as-is vs. reprocess"
    )
    p_quote.add_argument("path", help="path to a text file with an EVE inventory 'Copy As' paste")
    p_quote.set_defaults(func=cmd_quote_reprocessing)

    p_set_req = sub.add_parser(
        "set-mineral-requirement", help="set (or update) how many units of a mineral the shopping list needs"
    )
    p_set_req.add_argument("item", help="type_id or exact item name")
    p_set_req.add_argument("quantity", type=float, help="required quantity")
    p_set_req.set_defaults(func=cmd_set_mineral_requirement)

    p_remove_req = sub.add_parser("remove-mineral-requirement", help="remove a mineral requirement")
    p_remove_req.add_argument("item", help="type_id or exact item name")
    p_remove_req.set_defaults(func=cmd_remove_mineral_requirement)

    sub.add_parser(
        "list-mineral-requirements", help="list every configured mineral requirement"
    ).set_defaults(func=cmd_list_mineral_requirements)

    sub.add_parser(
        "solve-shopping-list",
        help="solve the cheapest ore-buy-and-refine-vs-buy-direct mix for the saved mineral requirements",
    ).set_defaults(func=cmd_solve_shopping_list)

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
    # Same layered load as TRADING_CONFIG (config.yaml + stored Settings
    # overrides) - previously only done on demand inside `cmd_config`, which
    # meant a saved Production/Doctrine override never actually took effect
    # on the next CLI invocation. Fixed here rather than left as a doctrine-
    # only patch, since the same gap applied to Production too.
    production_config.reload()
    doctrine_config.reload()
    refining_config.reload()
    try:
        args.func(args)
    except ActionError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
