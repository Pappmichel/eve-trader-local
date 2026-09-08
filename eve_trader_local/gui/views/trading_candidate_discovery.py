"""Trading's Candidate Discovery tab: the "find new things to import" setup
workflow. `do_build_universe`/`do_build_focused`/`do_find_new_candidates`
(all live ESI/Goonmetrics calls) have no buttons here - they only run via
App > Update Data..., as its own Candidate Universe and Market Prices scopes
respectively (see esi_update.py's own docstring for why the universe rebuild
gets its own separate, much-longer-interval scope rather than being folded
into the routine Market Prices sync). `do_add_to_shortlist` (a purely local
promote-from-storage step, no network) stays a button here - it's the one
step in this whole pipeline that isn't itself an ESI/Goonmetrics call.

A brand new install has nothing in any of the three tables below until App >
Update Data...'s Candidate Universe scope has run at least once (it's always
due on a fresh install regardless of its own long default interval - see
esi_update.is_due) followed by its Market Prices scope, which is what
actually populates "New Candidates" - `do_add_to_shortlist` (this tab's own
"Add Recommended To Shortlist" button) raises "No candidate search results
yet" until both have, and Trading's own Shortlist tab stays "Shortlist is
empty" until this button has then been clicked at least once.

Three sub-tabs, each a cheap local read (`storage.load_candidate_universe`/
`storage.latest_new_candidates`, no network) loaded on tab-open so the view
shows whatever was last computed immediately, same convention
`trading_shortlist.py`'s `_load_last_snapshot` uses:

- "Candidate Universe" / "Focused Candidates" - the two SDE-derived
  candidate lists `build-universe` produces. Neither has a single canonical
  order (candidate_discovery.py never sorts either one) - left at insertion
  order with sorting enabled for the user's own header clicks, same
  restraint Station Trading's own Trader Skills table uses.
- "New Candidates (Search Results)" - the last `find-candidates` run's
  scored rows, defaulted to Score desc to match `history_backtest.py`'s own
  "highest score/margin first" ranking (`batch_results.sort(key=lambda r:
  (r.score, r.latest_margin), reverse=True)`).
"""
from __future__ import annotations

import functools

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QTabWidget

from ... import actions, storage
from .. import icons
from .base import BaseView
from .production_common import build_table, fmt_pct, populate

_CANDIDATE_COLUMNS = ["Item", "Type ID", "Category", "Volume (m3)", "Market Group Path", "Meta Level"]
_CANDIDATE_WIDTHS = [200, None, 130, None, 260, None]

_NEW_CANDIDATE_COLUMNS = ["Item", "Category", "Paired Days", "Profitable Days", "Hit Rate", "Latest Margin",
                         "Best Margin", "Avg Profit/m3", "Avg Sell Movement", "Score", "Recommendation", "Add"]
_NEW_CANDIDATE_WIDTHS = [200, 130, None, None, None, None, None, None, None, None, 140, None]
# history_backtest.py: "batch_results.sort(key=lambda r: (r.score,
# r.latest_margin), reverse=True)" - highest score first.
_NEW_CANDIDATE_SORT = (9, Qt.SortOrder.DescendingOrder)


def _candidate_row(c) -> list:
    return [c.item, c.type_id, c.category, f"{c.volume_m3:,.4f}", c.market_group_path, c.meta_level]


def _new_candidate_row(r) -> list:
    return [r.item, r.category, r.paired_days, r.profitable_days, fmt_pct(r.hit_rate),
            fmt_pct(r.latest_margin), fmt_pct(r.best_margin), f"{r.avg_profit_m3:,.2f}",
            f"{r.avg_sell_movement:,.1f}", f"{r.score:,.2f}", r.recommendation, "yes" if r.add else "no"]


class CandidateDiscoveryView(BaseView):
    title = "Trading - Candidate Discovery"

    def __init__(self, parent=None):
        super().__init__(parent)

        toolbar = QHBoxLayout()
        reload_btn = QPushButton(icons.icon("reload"), "Reload")
        reload_btn.clicked.connect(self._load_local)
        toolbar.addWidget(reload_btn)
        add_btn = QPushButton(icons.icon("add"), "Add Recommended To Shortlist")
        add_btn.clicked.connect(self._add_to_shortlist)
        toolbar.addWidget(add_btn)
        toolbar.addStretch(1)
        self.root_layout.addLayout(toolbar)

        self.tabs = QTabWidget()
        self.universe_table = build_table(_CANDIDATE_COLUMNS, column_widths=_CANDIDATE_WIDTHS)
        self.focused_table = build_table(_CANDIDATE_COLUMNS, column_widths=_CANDIDATE_WIDTHS)
        self.new_candidates_table = build_table(_NEW_CANDIDATE_COLUMNS, column_widths=_NEW_CANDIDATE_WIDTHS)
        self.tabs.addTab(self.universe_table, "Candidate Universe")
        self.tabs.addTab(self.focused_table, "Focused Candidates")
        self.tabs.addTab(self.new_candidates_table, "New Candidates (Search Results)")
        self.root_layout.addWidget(self.tabs)

        self._add_staleness_label("market_prices")
        self._finish_status_row()
        self._load_local()

    def _load_local(self) -> None:
        """Cheap local reads, no network - see module docstring. Building the
        universe/finding new candidates both happen only via App > Update
        Data... (or the CLI) now, so this is also what a "Reload" click
        does - re-read whatever that last produced."""
        try:
            universe = storage.load_candidate_universe("candidate_universe")
            focused = storage.load_candidate_universe("focused_candidates")
            new_candidates = storage.latest_new_candidates()
        except Exception as e:  # noqa: BLE001 - best-effort initial fill, Reload recovers from any real problem
            self.show_error(f"Could not load saved candidate data: {e!r}")
            return
        populate(self.universe_table, [_candidate_row(c) for c in universe])
        populate(self.focused_table, [_candidate_row(c) for c in focused])
        populate(self.new_candidates_table, [_new_candidate_row(r) for r in new_candidates],
                default_sort=_NEW_CANDIDATE_SORT)
        self._refresh_staleness_label()
        self.show_info(f"Universe: {len(universe):,} | Focused: {len(focused):,} | "
                       f"Last search: {len(new_candidates):,} result(s).")

    def _add_to_shortlist(self) -> None:
        self.run_action(functools.partial(actions.do_add_to_shortlist), self._on_added,
                        busy_message="Adding recommended candidates to the shortlist...")

    def _on_added(self, result: dict) -> None:
        self.show_info(f"Added {result['added']:,} recommended candidates to the shortlist.")
