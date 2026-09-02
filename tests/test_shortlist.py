"""Shortlist evaluation - no network, no database (except the storage
round-trip tests at the bottom, which use the throwaway `db` fixture).

shortlist.py itself never opens a connection or a socket: order-book stats
and price history are handed in already fetched, and everything else is
arithmetic over them.
"""
from __future__ import annotations

import pytest

from eve_trader_local import shortlist, storage
from eve_trader_local.config import TradingConfig
from eve_trader_local.esi_client import OrderStats
from eve_trader_local.goonmetrics_client import HistoryPoint
from eve_trader_local.models import ShortlistItem, ShortlistRow

REF = 10000009


@pytest.fixture
def cfg() -> TradingConfig:
    """Round numbers so every expected figure below can be worked out by
    hand: no broker fee, no haircut, no import cost - landed cost is exactly
    the Jita ask, net sell exactly the structure ask."""
    return TradingConfig(jita_buy_broker_fee=0.0, structure_sell_haircut=1.0,
                         import_cost_per_m3=0.0, min_profit_threshold=0.0,
                         min_margin_threshold=0.05)


def stats(sell_percentile, sell_volume=100.0) -> OrderStats:
    return OrderStats(sell_percentile=sell_percentile, sell_volume=sell_volume,
                      buy_percentile=None, buy_volume=0.0)


def item(item_id=34, volume_m3=1.0, active=True) -> ShortlistItem:
    return ShortlistItem(item="Tritanium", item_id=item_id, category="Material",
                         volume_m3=volume_m3, active=active)


def history(type_id, movements, region_id=REF) -> list[HistoryPoint]:
    return [HistoryPoint(region_id=region_id, type_id=type_id, date=f"2026-08-{d + 1:02d}",
                         min_price=1.0, max_price=2.0, avg_price=1.5,
                         movement=m, num_orders=3)
            for d, m in enumerate(movements)]


# --------------------------------------------------------------- the formula
def test_landed_cost_net_sell_and_margin(cfg):
    cfg.jita_buy_broker_fee = 0.01
    cfg.import_cost_per_m3 = 10.0
    cfg.structure_sell_haircut = 0.95
    row = shortlist.evaluate_shortlist_item(item(volume_m3=2.0), 0.0,
                                            stats(100.0), stats(200.0), cfg)
    assert row.import_cost == pytest.approx(20.0)          # 2 m3 x 10 ISK
    assert row.landed_cost == pytest.approx(121.0)         # 100 x 1.01 + 20
    assert row.net_sell == pytest.approx(190.0)            # 200 x 0.95
    assert row.profit_per_unit == pytest.approx(69.0)
    assert row.margin == pytest.approx(69.0 / 121.0)
    assert row.profit_per_m3 == pytest.approx(34.5)


def test_missing_item_id_is_unpriceable(cfg):
    row = shortlist.evaluate_shortlist_item(item(item_id=0), 0.0, stats(100.0), stats(200.0), cfg)
    assert row.decision == "Missing ID"
    assert (row.landed_cost, row.net_sell, row.margin) == (None, None, None)


def test_inactive_item_is_still_fully_priced(cfg):
    """Only the decision short-circuits - the numbers stay visible, so a
    deactivated item's economics can be seen to have recovered."""
    row = shortlist.evaluate_shortlist_item(item(active=False), 0.0,
                                            stats(100.0), stats(200.0), cfg)
    assert row.decision == "Inactive"
    assert row.margin == pytest.approx(1.0)


@pytest.mark.parametrize("structure, own, covered, expected", [
    (stats(200.0), 0.0, False, "Import"),
    (stats(200.0), 5.0, False, "Already ordered"),      # already listed by me
    (stats(200.0), 0.0, True, "Already ordered"),       # buyer already covered
    (stats(101.0), 0.0, False, "Skip"),                 # priced, under the margin bar
    (None, 0.0, False, "No market data"),               # never priced at all
])
def test_decision_precedence(cfg, structure, own, covered, expected):
    row = shortlist.evaluate_shortlist_item(item(), own, stats(100.0), structure, cfg,
                                            buyer_already_covered=covered)
    assert row.decision == expected


def test_no_market_data_is_distinct_from_skip(cfg):
    """Two different next actions (seed the market vs. accept it isn't
    viable), so they must not collapse into one label."""
    assert shortlist.NO_MARKET_DATA_DECISION != shortlist.SKIP_DECISION


def test_nothing_listed_is_not_an_import(cfg):
    """sell_volume is order-book depth, and its only legitimate use is this
    gate: with nothing listed at the structure there is no ask to price
    against."""
    row = shortlist.evaluate_shortlist_item(item(), 0.0, stats(100.0),
                                            stats(200.0, sell_volume=0.0), cfg)
    assert row.decision == "Skip"


def test_evaluate_shortlist_maps_each_item_to_its_own_inputs(cfg):
    items = [item(item_id=34), item(item_id=35)]
    rows = shortlist.evaluate_shortlist(
        items, own_orders_by_item={35: 7.0},
        jita_stats_by_item={34: stats(100.0), 35: stats(100.0)},
        structure_stats_by_item={34: stats(200.0), 35: stats(200.0)},
        cfg=cfg, avg_daily_volume_by_item={34: 50.0})
    assert [r.decision for r in rows] == ["Import", "Already ordered"]
    assert [r.avg_daily_volume for r in rows] == [50.0, None]


# ------------------------------------ average_market_daily_volume (issue #100)
def test_average_market_daily_volume_averages_movement_per_type():
    points = history(34, [10.0, 20.0, 30.0]) + history(35, [5.0])
    assert shortlist.average_market_daily_volume(points) == {34: 20.0, 35: 5.0}


def test_average_market_daily_volume_omits_types_without_history():
    """A type Goonmetrics returned nothing for is simply absent - it must
    never be estimated from order-book depth or anything else."""
    volumes = shortlist.average_market_daily_volume(history(34, [10.0]))
    assert 35 not in volumes
    assert shortlist.average_market_daily_volume([]) == {}


def test_row_without_history_has_no_avg_daily_volume(cfg):
    """Even with a deep order book (sell_volume=999), no Goonmetrics history
    means avg_daily_volume stays None rather than falling back to it."""
    row = shortlist.evaluate_shortlist_item(item(), 0.0, stats(100.0),
                                            stats(200.0, sell_volume=999.0), cfg,
                                            avg_daily_volume=None)
    assert row.sell_volume == 999.0
    assert row.avg_daily_volume is None


# ------------------------------------------- Profit / Day (issues #51, #100)
def row(item_id=34, profit=10.0, margin=0.5, avg_daily_volume=None, sell_volume=None):
    return ShortlistRow(item=f"Item {item_id}", category="Material", landed_cost=20.0,
                        net_sell=30.0, sell_volume=sell_volume, own_orders_remaining=0.0,
                        profit_per_unit=profit, margin=margin, profit_per_m3=profit,
                        decision="Import", active=True, item_id=item_id, volume_m3=1.0,
                        jita_sell=20.0, import_cost=0.0,
                        avg_daily_volume=avg_daily_volume)


def test_profit_per_day_uses_region_history_volume_not_order_book_depth():
    """The exact shape of the parent's GitHub issue #51: one item with a
    huge parked sell order (order-book depth 100000) but almost no real
    trading, another with a modest book but genuine turnover. Ranking by
    listed quantity would put the parked one first."""
    parked = row(item_id=34, profit=10.0, sell_volume=100_000.0, avg_daily_volume=1.0)
    liquid = row(item_id=35, profit=10.0, sell_volume=5.0, avg_daily_volume=500.0)
    top = shortlist.top_imports_by_daily_profit([parked, liquid])
    assert [t["item"] for t in top] == ["Item 35", "Item 34"]
    assert top[0]["max_profit_per_day"] == pytest.approx(5000.0)
    assert top[1]["max_profit_per_day"] == pytest.approx(10.0)


def test_profit_per_day_excludes_items_without_history():
    """Issue #100's other half: a candidate with no history is dropped from
    the ranking, not backfilled from sell_volume."""
    top = shortlist.top_imports_by_daily_profit(
        [row(item_id=34, avg_daily_volume=None, sell_volume=100_000.0),
         row(item_id=35, avg_daily_volume=2.0)])
    assert [t["item"] for t in top] == ["Item 35"]


def test_top_imports_honours_top_n():
    rows = [row(item_id=i, profit=float(i), avg_daily_volume=1.0) for i in range(1, 6)]
    assert len(shortlist.top_imports_by_daily_profit(rows, top_n=2)) == 2


def test_profit_per_day_survives_a_full_evaluate_pass(cfg):
    """End to end from raw history points through evaluate_shortlist into
    the ranking, so the volume figure can't quietly become something else in
    between."""
    volumes = shortlist.average_market_daily_volume(history(34, [40.0, 60.0]))
    rows = shortlist.evaluate_shortlist(
        [item(item_id=34)], own_orders_by_item={},
        jita_stats_by_item={34: stats(100.0)},
        structure_stats_by_item={34: stats(200.0, sell_volume=9999.0)},
        cfg=cfg, avg_daily_volume_by_item=volumes)
    top = shortlist.top_imports_by_daily_profit(rows)
    assert top[0]["avg_daily_volume"] == pytest.approx(50.0)
    assert top[0]["max_profit_per_day"] == pytest.approx(100.0 * 50.0)


# ------------------------------------------------------------- summary/audit
def test_summary_counts():
    rows = [row(margin=0.5), row(margin=-0.2), row(margin=0.1)]
    rows[1].decision = "Skip"
    rows[2].decision = "Already ordered"
    summary = shortlist.summary_counts(rows)
    assert summary["import_candidates"] == 1
    assert summary["skipped"] == 1
    assert summary["already_ordered"] == 1
    assert summary["positive_margin"] == 2
    assert summary["avg_margin"] == pytest.approx(0.3)


def test_summary_avg_margin_is_none_without_positive_margins():
    assert shortlist.summary_counts([row(margin=-0.5)])["avg_margin"] is None


def test_audit_shortlist():
    items = [item(item_id=34), item(item_id=34), item(item_id=0),
             item(item_id=36, volume_m3=0.0)]
    assert shortlist.audit_shortlist(items) == {
        "duplicate_type_ids": 1, "missing_type_ids": 1, "invalid_volume": 1}


# ------------------------------------------------------------------- storage
def test_shortlist_round_trip(db):
    storage.upsert_shortlist([item(item_id=34), item(item_id=35, active=False)])
    loaded = {i.item_id: i for i in storage.load_shortlist()}
    assert loaded[34].active is True and loaded[35].active is False
    assert loaded[34].item == "Tritanium"


def test_upsert_shortlist_updates_in_place_and_keeps_meta_level(db):
    storage.upsert_shortlist([item(item_id=34)])
    storage.update_shortlist_meta_levels({34: 5})
    # A later upsert from a source that doesn't know the meta level must not
    # blank out the backfilled one.
    storage.upsert_shortlist([ShortlistItem(item="Tritanium", item_id=34, category="Mineral",
                                            volume_m3=0.01, active=True)])
    loaded = storage.load_shortlist()
    assert len(loaded) == 1
    assert loaded[0].category == "Mineral"
    assert loaded[0].meta_level == 5


def test_deactivate_and_reactivate(db):
    storage.upsert_shortlist([item(item_id=34)])
    storage.deactivate_shortlist_items([34])
    assert storage.load_shortlist()[0].active is False
    storage.activate_shortlist_items([34])
    assert storage.load_shortlist()[0].active is True


def test_empty_id_lists_are_noops(db):
    storage.deactivate_shortlist_items([])
    storage.activate_shortlist_items([])
    storage.update_shortlist_meta_levels({})


def test_snapshot_round_trip_keeps_avg_daily_volume(db):
    storage.save_shortlist_snapshot([row(item_id=34, avg_daily_volume=50.0)], "2026-09-01T00:00:00Z")
    storage.save_shortlist_snapshot([row(item_id=34, avg_daily_volume=75.0),
                                     row(item_id=35, avg_daily_volume=None)],
                                    "2026-09-02T00:00:00Z")
    latest = storage.latest_shortlist_snapshot()
    assert {r.item_id: r.avg_daily_volume for r in latest} == {34: 75.0, 35: None}


def test_latest_snapshot_is_empty_before_any_run(db):
    assert storage.latest_shortlist_snapshot() == []
