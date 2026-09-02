"""FIFO reconciliation of buys against sells - no network, no real ESI.

Every transaction is synthetic and dated relative to "now" so it lands inside
the lookback window regardless of when the suite runs. The `db` fixture is
needed even by the pure matching tests: the buyer-side location filter reads
the SDE station list out of SQLite (see trade_reconciliation's module
docstring on why this module touches storage at all).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from eve_trader_local import storage, trade_reconciliation as tr
from eve_trader_local.config import TradingConfig
from eve_trader_local.models import RealizedTrade

JITA_REGION = 10000002
JITA_STATION = 60003760
OTHER_REGION_STATION = 60011866
STRUCTURE = 1234567890123
OTHER_STRUCTURE = 9999999999999
TRITANIUM = 34


def days_ago(days: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def cfg() -> TradingConfig:
    """Round numbers so every expected figure below is workable by hand: no
    broker fee, no haircut, no freight - landed cost is exactly the buy price,
    net sell exactly the sell price."""
    return TradingConfig(jita_region_id=JITA_REGION, structure_id=STRUCTURE,
                         jita_buy_broker_fee=0.0, structure_sell_haircut=1.0,
                         import_cost_per_m3=0.0, lookback_days=30)


@pytest.fixture
def sde(db):
    """One station in the buy region and one outside it, so the location
    filter has something real to accept and reject."""
    with storage.connect() as conn:
        conn.executemany("INSERT INTO sde_solar_systems VALUES (?,?,?,?)",
                         [(30000142, "Jita", 0.9, JITA_REGION),
                          (30002187, "Amarr", 1.0, 10000043)])
        conn.executemany("INSERT INTO sde_stations VALUES (?,?,?)",
                         [(JITA_STATION, 30000142, "Jita IV - Moon 4"),
                          (OTHER_REGION_STATION, 30002187, "Amarr VIII")])


def buy(qty, price, ago, transaction_id=1, location_id=JITA_STATION, type_id=TRITANIUM):
    return {"transaction_id": transaction_id, "type_id": type_id, "is_buy": True,
            "quantity": qty, "unit_price": price, "date": days_ago(ago),
            "location_id": location_id, "journal_ref_id": 1000 + transaction_id}


def sell(qty, price, ago, transaction_id=1, location_id=STRUCTURE, type_id=TRITANIUM):
    return {"transaction_id": transaction_id, "type_id": type_id, "is_buy": False,
            "quantity": qty, "unit_price": price, "date": days_ago(ago),
            "location_id": location_id, "journal_ref_id": 2000 + transaction_id}


class FakeClient:
    """Serves one canned page of transactions per character, plus optional
    journal entries. `pages` maps character_id -> list of pages, consumed in
    order, so a test can exercise the from_id cursor loop."""

    def __init__(self, pages: dict[int, list[list[dict]]], journal: dict[int, list[dict]] = None,
                 types: dict[int, dict] = None, journal_raises: bool = False):
        self.pages = {cid: list(p) for cid, p in pages.items()}
        self.journal = journal or {}
        self.types = types or {}
        self.journal_raises = journal_raises
        self.from_ids: list = []
        self.type_lookups: list[int] = []

    def character_wallet_transactions(self, character_id, auth_role, from_id=None):
        self.from_ids.append(from_id)
        remaining = self.pages.get(character_id, [])
        return remaining.pop(0) if remaining else []

    def character_wallet_journal(self, character_id, auth_role):
        if self.journal_raises:
            raise RuntimeError("no wallet journal scope")
        return self.journal.get(character_id, [])

    def get_type_info(self, type_id):
        self.type_lookups.append(type_id)
        if type_id not in self.types:
            raise RuntimeError("unknown type")
        return self.types[type_id]


def reconcile(client, cfg, names=None, volumes=None):
    return tr.reconcile_realized_trades([(1, "buyer")], [(2, "seller")], client,
                                        names if names is not None else {TRITANIUM: "Tritanium"},
                                        volumes if volumes is not None else {TRITANIUM: 0.01},
                                        cfg)


# ------------------------------------------------------------ the match
def test_clean_buy_then_sell(sde, cfg):
    client = FakeClient({1: [[buy(10, 100.0, ago=5)]], 2: [[sell(10, 150.0, ago=2)]]})
    trades = reconcile(client, cfg)

    assert len(trades) == 1
    t = trades[0]
    assert (t.type_id, t.item, t.matched_qty) == (TRITANIUM, "Tritanium", 10)
    assert t.realized_profit == pytest.approx(500.0)
    assert t.margin == pytest.approx(0.5)
    assert t.buy_qty == 10 and t.sell_qty == 10


def test_costs_are_applied_to_the_right_side(sde, cfg):
    cfg.jita_buy_broker_fee = 0.1
    cfg.structure_sell_haircut = 0.9
    cfg.import_cost_per_m3 = 100.0
    client = FakeClient({1: [[buy(1, 100.0, ago=5)]], 2: [[sell(1, 200.0, ago=2)]]})

    t = reconcile(client, cfg, volumes={TRITANIUM: 0.5})[0]

    landed = 100.0 * 1.1 + 0.5 * 100.0        # broker fee on the buy + per-m3 freight
    assert t.realized_profit == pytest.approx(200.0 * 0.9 - landed)
    assert t.margin == pytest.approx((200.0 * 0.9 - landed) / landed)


def test_one_sell_consumes_several_buys_oldest_first(sde, cfg):
    client = FakeClient({
        1: [[buy(4, 100.0, ago=9, transaction_id=1), buy(6, 200.0, ago=6, transaction_id=2)]],
        2: [[sell(10, 300.0, ago=2)]],
    })
    trades = reconcile(client, cfg)

    assert [t.matched_qty for t in trades] == [4, 6]
    assert [t.buy_unit_price for t in trades] == [100.0, 200.0]   # FIFO: cheapest/oldest first
    assert sum(t.realized_profit for t in trades) == pytest.approx(4 * 200.0 + 6 * 100.0)


def test_one_buy_covers_several_sells_and_keeps_its_remainder(sde, cfg):
    client = FakeClient({
        1: [[buy(10, 100.0, ago=9)]],
        2: [[sell(3, 150.0, ago=5, transaction_id=1), sell(2, 160.0, ago=3, transaction_id=2)]],
    })
    trades = reconcile(client, cfg)

    assert [t.matched_qty for t in trades] == [3, 2]
    assert all(t.buy_unit_price == 100.0 for t in trades)
    # The buy still has 5 units left over; nothing fabricates a third row for them.
    assert sum(t.matched_qty for t in trades) == 5


def test_sell_without_an_earlier_buy_is_dropped_not_matched_forward(sde, cfg):
    """A buy dated *after* a sell can't be that sale's cost basis. The sell is
    dropped (no row at all), and the buy stays available for a later sell."""
    client = FakeClient({
        1: [[buy(5, 100.0, ago=4)]],
        2: [[sell(5, 150.0, ago=8, transaction_id=1), sell(5, 150.0, ago=1, transaction_id=2)]],
    })
    trades = reconcile(client, cfg)

    assert len(trades) == 1
    assert trades[0].sell_date > trades[0].buy_date
    assert trades[0].matched_qty == 5


def test_sell_with_no_buy_at_all_produces_nothing(sde, cfg):
    client = FakeClient({1: [[]], 2: [[sell(5, 150.0, ago=2)]]})
    assert reconcile(client, cfg) == []


def test_partial_cover_matches_what_it_can(sde, cfg):
    client = FakeClient({1: [[buy(3, 100.0, ago=6)]], 2: [[sell(10, 150.0, ago=2)]]})
    trades = reconcile(client, cfg)

    assert len(trades) == 1
    assert trades[0].matched_qty == 3
    assert trades[0].sell_qty == 10          # the whole transaction is still recorded


def test_types_are_matched_independently(sde, cfg):
    other = 35
    client = FakeClient({
        1: [[buy(5, 100.0, ago=6), buy(5, 10.0, ago=6, transaction_id=2, type_id=other)]],
        2: [[sell(5, 150.0, ago=2), sell(5, 20.0, ago=2, transaction_id=2, type_id=other)]],
    })
    trades = reconcile(client, cfg, names={TRITANIUM: "Tritanium", other: "Pyerite"},
                       volumes={TRITANIUM: 0.01, other: 0.01})

    assert {t.type_id: t.realized_profit for t in trades} == pytest.approx(
        {TRITANIUM: 250.0, other: 50.0})


def test_results_are_sorted_by_sell_date(sde, cfg):
    client = FakeClient({
        1: [[buy(1, 100.0, ago=20, transaction_id=1), buy(1, 100.0, ago=19, transaction_id=2)]],
        2: [[sell(1, 150.0, ago=2, transaction_id=1), sell(1, 150.0, ago=10, transaction_id=2)]],
    })
    trades = reconcile(client, cfg)
    assert [t.sell_date for t in trades] == sorted(t.sell_date for t in trades)


# ------------------------------------------------------- location filters
def test_buys_outside_the_buy_region_are_ignored(sde, cfg):
    client = FakeClient({
        1: [[buy(5, 100.0, ago=6, location_id=OTHER_REGION_STATION)]],
        2: [[sell(5, 150.0, ago=2)]],
    })
    assert reconcile(client, cfg) == []


def test_sells_at_another_structure_are_ignored(sde, cfg):
    client = FakeClient({
        1: [[buy(5, 100.0, ago=6)]],
        2: [[sell(5, 150.0, ago=2, location_id=OTHER_STRUCTURE)]],
    })
    assert reconcile(client, cfg) == []


def test_a_sell_on_the_buy_side_is_not_a_buy(sde, cfg):
    """is_buy, not just location, decides which side a transaction belongs to."""
    stray = sell(5, 100.0, ago=6, location_id=JITA_STATION)
    client = FakeClient({1: [[stray]], 2: [[sell(5, 150.0, ago=2)]]})
    assert reconcile(client, cfg) == []


# --------------------------------------------------------- the sell price
def test_journal_amount_replaces_the_modeled_tax(sde, cfg):
    cfg.structure_sell_haircut = 0.9463
    s = sell(10, 200.0, ago=2)
    # 1800 ISK credited for 10 units = 180/unit, already net of real sales tax.
    client = FakeClient({1: [[buy(10, 100.0, ago=6)]], 2: [[s]]},
                        journal={2: [{"id": s["journal_ref_id"], "amount": 1800.0,
                                      "ref_type": "market_transaction", "date": s["date"]}]})

    t = reconcile(client, cfg)[0]

    net_sell = 180.0 * (0.9463 + tr._ASSUMED_TAX_RATE_IN_DEFAULT_HAIRCUT)
    assert t.realized_profit == pytest.approx((net_sell - 100.0) * 10)


def test_unrelated_journal_entries_are_ignored(sde, cfg):
    s = sell(10, 200.0, ago=2)
    client = FakeClient({1: [[buy(10, 100.0, ago=6)]], 2: [[s]]},
                        journal={2: [{"id": s["journal_ref_id"], "amount": 1.0,
                                      "ref_type": "bounty_prizes", "date": s["date"]}]})

    t = reconcile(client, cfg)[0]
    assert t.realized_profit == pytest.approx(1000.0)   # modeled formula, haircut 1.0


def test_a_broken_wallet_journal_falls_back_to_the_modeled_formula(sde, cfg):
    client = FakeClient({1: [[buy(10, 100.0, ago=6)]], 2: [[sell(10, 150.0, ago=2)]]},
                        journal_raises=True)
    assert reconcile(client, cfg)[0].realized_profit == pytest.approx(500.0)


def test_journal_entries_outside_the_window_are_dropped(sde):
    client = FakeClient({}, journal={2: [
        {"id": 1, "amount": 10.0, "ref_type": "market_transaction", "date": days_ago(2)},
        {"id": 2, "amount": 20.0, "ref_type": "market_transaction", "date": days_ago(90)},
    ]})
    assert tr.fetch_recent_journal_entries(2, "seller", client, lookback_days=30) == {1: 10.0}


# ------------------------------------------------------------- backfills
def trades_one(trades):
    assert len(trades) == 1
    return trades[0]


def test_unknown_type_is_backfilled_from_esi(sde, cfg):
    cfg.import_cost_per_m3 = 100.0
    unknown = 4247
    client = FakeClient({1: [[buy(2, 100.0, ago=6, transaction_id=1, type_id=unknown),
                              buy(1, 100.0, ago=5, transaction_id=2, type_id=unknown)]],
                         2: [[sell(3, 500.0, ago=2, type_id=unknown)]]},
                        types={unknown: {"name": "Kernite", "volume": 2.0}})

    trades = reconcile(client, cfg, names={}, volumes={})

    assert [t.item for t in trades] == ["Kernite", "Kernite"]
    assert trades[0].realized_profit == pytest.approx((500.0 - (100.0 + 2.0 * 100.0)) * 2)
    assert client.type_lookups == [unknown]          # fetched once, not per matched trade


def test_failed_backfill_degrades_to_zero_freight(sde, cfg):
    cfg.import_cost_per_m3 = 100.0
    unknown = 4247
    client = FakeClient({1: [[buy(1, 100.0, ago=6, type_id=unknown)]],
                         2: [[sell(1, 500.0, ago=2, type_id=unknown)]]})

    t = trades_one(reconcile(client, cfg, names={}, volumes={}))

    assert t.item == str(unknown)
    assert t.realized_profit == pytest.approx(400.0)   # no volume known -> no freight


# ------------------------------------------------------------ pagination
def test_transactions_page_back_until_the_cutoff(sde):
    full_page = [buy(1, 1.0, ago=2, transaction_id=i) for i in range(tr.WALLET_TRANSACTIONS_PAGE_SIZE)]
    second_page = [buy(1, 1.0, ago=90, transaction_id=-1)]
    client = FakeClient({1: [full_page, second_page]})

    txns = tr.fetch_recent_transactions(1, "buyer", client, lookback_days=30)

    assert client.from_ids == [None, 0]        # cursored on the oldest transaction_id
    assert len(txns) == len(full_page)         # the out-of-window page is filtered out


def test_a_short_page_stops_paging(sde):
    client = FakeClient({1: [[buy(1, 1.0, ago=2)], [buy(1, 1.0, ago=3)]]})
    tr.fetch_recent_transactions(1, "buyer", client, lookback_days=30)
    assert client.from_ids == [None]


# -------------------------------------------------------------- summary
def test_summarize_weights_margin_by_cost_basis():
    trades = [
        RealizedTrade(34, "Tritanium", "d1", 100, 10.0, "d2", 100, 20.0, 100, 1000.0, 1.0),
        RealizedTrade(35, "Pyerite", "d1", 1, 1000.0, "d2", 1, 1100.0, 1, 100.0, 0.1),
    ]
    summary = tr.summarize_realized(trades)

    assert summary["total_realized_profit"] == pytest.approx(1100.0)
    assert summary["average_margin"] == pytest.approx(1100.0 / (10.0 * 100 + 1000.0 * 1))
    assert summary["top3_items_by_profit"] == [("Tritanium", 1000.0), ("Pyerite", 100.0)]


def test_summarize_of_nothing_is_zero_not_a_division_error():
    assert tr.summarize_realized([])["average_margin"] == 0.0


# ------------------------------------------- average daily sold (storage)
def test_average_daily_sold_by_type(db, cfg):
    cfg.lookback_days = 10
    storage.save_realized_trades([
        RealizedTrade(34, "Tritanium", "d1", 100, 10.0, "d2", 100, 20.0, 60, 600.0, 1.0),
        RealizedTrade(34, "Tritanium", "d1", 100, 10.0, "d2", 100, 20.0, 40, 400.0, 1.0),
        RealizedTrade(35, "Pyerite", "d1", 5, 10.0, "d2", 5, 20.0, 5, 50.0, 1.0),
    ], run_ts="2026-09-02T00:00:00Z")

    assert tr.average_daily_sold_by_type(cfg) == pytest.approx({34: 10.0, 35: 0.5})


def test_average_daily_sold_only_sees_the_latest_run(db, cfg):
    """save_realized_trades replaces the table wholesale - an earlier run must
    never leak into the figure."""
    cfg.lookback_days = 10
    storage.save_realized_trades(
        [RealizedTrade(34, "Tritanium", "d1", 1, 1.0, "d2", 1, 2.0, 1000, 1.0, 1.0)],
        run_ts="2026-09-01T00:00:00Z")
    storage.save_realized_trades(
        [RealizedTrade(34, "Tritanium", "d1", 1, 1.0, "d2", 1, 2.0, 20, 1.0, 1.0)],
        run_ts="2026-09-02T00:00:00Z")

    assert tr.average_daily_sold_by_type(cfg) == pytest.approx({34: 2.0})


def test_average_daily_sold_is_empty_before_any_run(db, cfg):
    assert tr.average_daily_sold_by_type(cfg) == {}


def test_realized_trades_round_trip(db):
    trade = RealizedTrade(34, "Tritanium", "2026-08-01T00:00:00Z", 100, 10.5,
                          "2026-08-05T00:00:00Z", 50, 22.25, 50, 585.0, 1.11)
    storage.save_realized_trades([trade], run_ts="2026-09-02T00:00:00Z")
    assert storage.latest_realized_trades() == [trade]
