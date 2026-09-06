"""Trading's Realized Trades & Transactions tab: `do_reconcile_trades`
(matched buy/sell P&L) and `do_wallet_transactions` (raw wallet transaction
history per character), grouped into one tab since both are "what actually
happened" historical views - as opposed to the Shortlist's forward-looking
decision table.

Both are cache-only reads now (see esi_update.py's own docstring) - the live
ESI wallet/reconciliation fetch only happens inside App > Update Data...'s
Trading scope. "Reconcile Trades"/"Load Transactions" here just (re)display
whatever that last run cached, same shape as before but no network call of
their own.

- Realized Trades: FIFO-matched buy(Jita)/sell(structure) pairs. Loads the
  last saved run on open (`storage.latest_realized_trades`, no network),
  same convention `trading_shortlist.py`'s `_load_last_snapshot` uses.
  Default sort matches `trade_reconciliation.reconcile_realized_trades`'s
  own documented order ("results.sort(key=lambda r: r.sell_date)") - Sell
  Date ascending.
- Wallet Transactions: one character's cached transaction history, for
  whichever registered buyer/seller character is selected. Default sort
  matches `do_wallet_transactions`'s own documented order ("sorted(rows,
  key=lambda r: r['date'], reverse=True)") - Date descending.
"""
from __future__ import annotations

import functools

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QTabWidget

from ... import actions, storage
from .. import icons
from .base import BaseView
from .production_common import build_table, fmt_isk, populate

_REALIZED_COLUMNS = ["Item", "Buy Date", "Buy Qty", "Buy Unit Price", "Sell Date", "Sell Qty",
                     "Sell Unit Price", "Matched Qty", "Realized Profit", "Margin"]
_REALIZED_WIDTHS = [200, 130, None, None, 130, None, None, None, None, None]
# trade_reconciliation.reconcile_realized_trades: "results.sort(key=lambda
# r: r.sell_date)" - oldest sell first.
_REALIZED_SORT = (4, Qt.SortOrder.AscendingOrder)

_TXN_COLUMNS = ["Date", "Item", "Buy/Sell", "Quantity", "Unit Price", "Total", "Location ID"]
_TXN_WIDTHS = [160, 200, 80, None, None, None, None]
# actions.do_wallet_transactions: "sorted(rows, key=lambda r: r['date'],
# reverse=True)" - newest first.
_TXN_SORT = (0, Qt.SortOrder.DescendingOrder)


def _realized_row(t) -> list:
    return [t.item, t.buy_date, f"{t.buy_qty:,.0f}", fmt_isk(t.buy_unit_price), t.sell_date,
            f"{t.sell_qty:,.0f}", fmt_isk(t.sell_unit_price), f"{t.matched_qty:,.0f}",
            fmt_isk(t.realized_profit), _fmt_pct(t.margin)]


def _fmt_pct(value):
    return f"{value * 100:.1f}%" if value is not None else None


def _txn_row(t: dict) -> list:
    return [t["date"], t["item"], "Buy" if t["is_buy"] else "Sell", f"{t['quantity']:,.0f}",
            fmt_isk(t["unit_price"]), fmt_isk(t["total"]), t["location_id"]]


class RealizedTransactionsView(BaseView):
    title = "Trading - Realized Trades && Transactions"

    def __init__(self, parent=None):
        super().__init__(parent)

        realized_toolbar = QHBoxLayout()
        reconcile_btn = QPushButton(icons.icon("show"), "Show Reconciliation")
        reconcile_btn.clicked.connect(self._reconcile)
        realized_toolbar.addWidget(reconcile_btn)
        realized_toolbar.addStretch(1)
        self.root_layout.addLayout(realized_toolbar)

        txn_toolbar = QHBoxLayout()
        txn_toolbar.addWidget(QLabel("Character:"))
        self.character_combo = QComboBox()
        self._reload_characters()
        txn_toolbar.addWidget(self.character_combo)
        load_txn_btn = QPushButton(icons.icon("show"), "Show Transactions")
        load_txn_btn.clicked.connect(self._load_transactions)
        txn_toolbar.addWidget(load_txn_btn)
        txn_toolbar.addStretch(1)
        self.root_layout.addLayout(txn_toolbar)

        self.tabs = QTabWidget()
        self.realized_table = build_table(_REALIZED_COLUMNS, column_widths=_REALIZED_WIDTHS)
        self.txn_table = build_table(_TXN_COLUMNS, column_widths=_TXN_WIDTHS)
        self.tabs.addTab(self.realized_table, "Realized Trades")
        self.tabs.addTab(self.txn_table, "Wallet Transactions")
        self.root_layout.addWidget(self.tabs)

        self._add_staleness_label("wallet")
        self._finish_status_row()
        self._load_last_realized()

    def _reload_characters(self) -> None:
        """Local-only read (`auth.TokenManager.list_records`, no network) -
        safe to call directly rather than through `run_action`, same as
        `characters_dialog.py`'s own listing."""
        self.character_combo.clear()
        characters = actions.do_list_buyer_characters() + actions.do_list_seller_characters()
        for role_key, _character_id, character_name in characters:
            self.character_combo.addItem(f"{character_name} ({role_key})", role_key)

    def _load_last_realized(self) -> None:
        try:
            trades = storage.latest_realized_trades()
        except Exception as e:  # noqa: BLE001 - best-effort initial fill, Reconcile recovers from any real problem
            self.show_error(f"Could not load the last reconciled trades: {e!r}")
            return
        populate(self.realized_table, [_realized_row(t) for t in trades], default_sort=_REALIZED_SORT)
        if trades:
            self.show_info(f"Showing the last reconciliation ({len(trades)} matched trade(s)).")

    def _reconcile(self) -> None:
        self.run_action(functools.partial(actions.do_reconcile_trades), self._on_reconciled,
                        busy_message="Loading the last cached trade reconciliation...")

    def _on_reconciled(self, result: dict) -> None:
        trades = storage.latest_realized_trades()
        populate(self.realized_table, [_realized_row(t) for t in trades], default_sort=_REALIZED_SORT)
        self.show_info(f"Matched {result['matched_trades']:,} trade(s). "
                       f"Total realized profit: {fmt_isk(result.get('total_realized_profit'))}.")

    def _load_transactions(self) -> None:
        role_key = self.character_combo.currentData()
        if not role_key:
            self.show_error("No buyer/seller character is logged in yet "
                            "(eve-trader-local auth --role buyer/seller).")
            return
        self.run_action(functools.partial(actions.do_wallet_transactions, role_key), self._on_transactions,
                        busy_message=f"Loading cached wallet transactions for {role_key}...")

    def _on_transactions(self, rows: list[dict]) -> None:
        populate(self.txn_table, [_txn_row(r) for r in rows], default_sort=_TXN_SORT)
        self.show_info(f"{len(rows):,} transaction(s)." if rows else "No transactions found.")
