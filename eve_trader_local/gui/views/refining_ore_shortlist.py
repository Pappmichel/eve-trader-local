"""Ore & Minerals' Ore Shortlist tab: groups the ore-shortlist CLI commands
into one view, same "one table, buttons that (re)build it" shape as
trading_shortlist.py -

- `add-ore-to-shortlist` (`do_add_ore_to_shortlist`) - adds every SDE-derived
  compressed ore/ice candidate not already tracked. Pure local SDE/storage
  work (no network - see `refining/candidate_discovery.py`'s own docstring),
  but still routed through `run_action` rather than called synchronously:
  it's a real write (a mutation), and every other view in this GUI (Doctrine
  included) treats "write something" and "read what's already saved" as two
  different categories regardless of whether the write happens to touch the
  network - see doctrine_fittings.py's own `_create_doctrine`.
- `refresh-ore-shortlist` (`do_refresh_ore_shortlist`) - a pure local
  recompute now (no network - see esi_update.py's own docstring): re-reads
  whatever the Market Prices scope last cached and re-evaluates every active
  row's profit/decision. The live Jita/C-J fetch itself only runs from App >
  Update Data...'s Market Prices scope.
- `list-ore-shortlist` (`do_get_ore_shortlist`) - the last saved snapshot, a
  pure local read (`storage.latest_ore_shortlist_snapshot`) - loaded
  directly on tab-open and after "Add New Candidates", same as
  trading_shortlist.py's `_load_last_snapshot`.

Deactivate/activate (`do_deactivate_ore_shortlist_items`/
`do_activate_ore_shortlist_items`) aren't exposed here yet - there's no CLI
command for them either (they exist for a future Settings-style bulk-edit
flow the parent's own web frontend has); add them here once that need shows
up rather than guessing at a UI for it now.
"""
from __future__ import annotations

import functools

from PySide6.QtCore import Qt

from ...refining import actions as refining_actions
from .base import TableView
from .production_common import fmt_isk, fmt_pct

_COLUMNS = ["Item", "Family", "Ice?", "Volume/m3", "Landed Cost", "Yield %", "Mineral Value",
            "Refining Tax", "Net Sell", "Profit/Unit", "Margin", "Profit/m3", "Decision", "Active"]
_COLUMN_WIDTHS = [170, 120, None, None, None, None, None, None, None, None, None, None, 90, None]
# storage.load_ore_shortlist/latest_ore_shortlist_snapshot have no ORDER BY
# of their own (same as trading_shortlist.py) - Margin desc is the most
# decision-relevant default for this same "unordered decision table" shape.
_DEFAULT_SORT = (10, Qt.SortOrder.DescendingOrder)


def _row_to_cells(row) -> list:
    return [row.item, row.family, "yes" if row.is_ice else "no", row.volume_m3, fmt_isk(row.landed_cost),
            fmt_pct(row.yield_pct), fmt_isk(row.mineral_value), fmt_isk(row.refining_tax), fmt_isk(row.net_sell),
            fmt_isk(row.profit_per_unit), fmt_pct(row.margin), fmt_isk(row.profit_per_m3), row.decision,
            "yes" if row.active else "no"]


class OreShortlistView(TableView):
    title = "Ore & Minerals - Ore Shortlist"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_toolbar([
            ("Add New Candidates", self._add_candidates, "add"),
            ("Reload", self._reload, "reload"),
        ])
        self._build_table(_COLUMNS, column_widths=_COLUMN_WIDTHS, default_sort=_DEFAULT_SORT)
        self._add_staleness_label("market_prices")
        self._finish_status_row()
        self._load_last_snapshot()

    def _load_last_snapshot(self) -> None:
        try:
            rows = refining_actions.do_get_ore_shortlist()["rows"]
        except Exception as e:  # noqa: BLE001 - best-effort initial fill, Reload recovers from any real problem
            self.show_error(f"Could not load the last saved snapshot: {e!r}")
            return
        self.populate_table([_row_to_cells(r) for r in rows])
        self._refresh_staleness_label()
        if rows:
            self.show_info(f"Showing the last saved snapshot ({len(rows)} items). "
                           "Click Reload to recompute it, or App > Update Data... to refresh prices first.")

    def _reload(self) -> None:
        self.run_action(functools.partial(refining_actions.do_refresh_ore_shortlist), self._on_reloaded,
                        busy_message="Recomputing the ore shortlist from cached prices...")

    def _on_reloaded(self, result: dict) -> None:
        self._load_last_snapshot()
        self.show_info(f"Evaluated {result['evaluated']:,} item(s), "
                       f"{result['import_candidates']:,} worth importing.")

    def _add_candidates(self) -> None:
        self.run_action(functools.partial(refining_actions.do_add_ore_to_shortlist), self._on_added,
                        busy_message="Scanning the SDE for compressed ore/ice types not yet tracked...")

    def _on_added(self, result: dict) -> None:
        self.show_info(f"Added {result['added']:,} new candidate(s); "
                       f"{result['already_tracked']:,} already tracked. "
                       "Use App > Update Data... to price them.")
