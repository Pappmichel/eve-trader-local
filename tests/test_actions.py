"""The orchestration layer, exercised against stubs - no network at all.

Everything that would touch ESI/Goonmetrics/EVE SSO is replaced at the module
boundary (actions.ESIClient / actions.GoonmetricsClient / actions.TokenManager),
so what these tests actually check is the wiring: which module gets called with
what, what gets persisted, which failures become ActionError, and that
do_pipeline's steps are isolated from each other. The computation itself is
covered by each module's own test file.
"""
from __future__ import annotations

import datetime as dt

import pytest

from eve_trader_local import actions, candidate_discovery, history_backtest, own_orders, storage
from eve_trader_local.config import TradingConfig
from eve_trader_local.errors import ActionError
from eve_trader_local.esi_client import ESIError, OrderStats
from eve_trader_local.goonmetrics_client import HistoryPoint
from eve_trader_local.models import (Candidate, NewCandidateResult, RealizedTrade, ShortlistItem,
                                     ShortlistRow)

STRUCTURE = 1234567890123
JITA_REGION = 10000002
REF_REGION = 10000009
TRIT = 34
PYERITE = 35


@pytest.fixture
def cfg() -> TradingConfig:
    return TradingConfig(jita_region_id=JITA_REGION, reference_region_id=REF_REGION,
                         structure_id=STRUCTURE, jita_buy_broker_fee=0.0,
                         structure_sell_haircut=1.0, import_cost_per_m3=0.0,
                         min_margin_threshold=0.05, min_profit_threshold=0.0)


# ------------------------------------------------------------------- stubs
class StubRecord:
    def __init__(self, role, character_id, character_name):
        self.role = role
        self.character_id = character_id
        self.character_name = character_name


class StubTokenManager:
    def __init__(self, records=()):
        self.records = list(records)
        self.removed = []

    def list_records(self, prefix=None):
        if prefix is None:
            return list(self.records)
        return [r for r in self.records if r.role.startswith(f"{prefix}:")]

    def get_record(self, role):
        return next((r for r in self.records if r.role == role), None)

    def remove_token(self, role):
        self.removed.append(role)


class StubClient:
    """Stands in for ESIClient - only the methods actions.py actually calls."""

    def __init__(self, *, orders=None, assets=None, book=None, structure_stats=None,
                 jita_stats=None, balance=0.0, meta_levels=None, structure_error=None):
        self.orders = orders or {}
        self.assets = assets or {}
        self.book = book or []
        self.structure_stats = structure_stats or {}
        self.jita_stats = jita_stats or {}
        self.balance = balance
        self.meta_levels = meta_levels or {}
        self.structure_error = structure_error

    def character_orders(self, character_id, auth_role):
        return list(self.orders.get(character_id, []))

    def character_assets(self, character_id, auth_role):
        return list(self.assets.get(character_id, []))

    def structure_orders_raw(self, structure_id, auth_role):
        return list(self.book)

    def character_wallet_balance(self, character_id, auth_role):
        return self.balance

    def get_meta_level(self, type_id):
        return self.meta_levels.get(type_id)

    def structure_order_stats_bulk(self, structure_id, type_ids, auth_role):
        if self.structure_error:
            raise self.structure_error
        return {tid: self.structure_stats.get(tid, stats(None)) for tid in type_ids}

    def structure_order_stats_bulk_or_goonmetrics(self, structure_id, type_ids, auth_role,
                                                  goonmetrics_market_slug):
        if self.structure_error:
            raise self.structure_error
        return {tid: self.structure_stats.get(tid, stats(None)) for tid in type_ids}, False

    def region_order_stats_bulk(self, region_id, type_ids):
        return {tid: self.jita_stats.get(tid, stats(None)) for tid in type_ids}


class StubGoonmetrics:
    def __init__(self, history=()):
        self.history = list(history)

    def price_history_chunked(self, region_id, type_ids, *args, **kwargs):
        return [p for p in self.history if p.type_id in set(type_ids)]


def stats(sell_percentile, sell_volume=100.0) -> OrderStats:
    return OrderStats(sell_percentile=sell_percentile, sell_volume=sell_volume,
                      buy_percentile=None, buy_volume=0.0)


def install(monkeypatch, *, client=None, tokens=None, goonmetrics=None):
    """Swaps the three network-facing constructors actions.py uses."""
    if client is not None:
        monkeypatch.setattr(actions, "ESIClient", lambda *a, **k: client)
    if tokens is not None:
        monkeypatch.setattr(actions, "TokenManager", lambda *a, **k: tokens)
    if goonmetrics is not None:
        monkeypatch.setattr(actions, "GoonmetricsClient", lambda *a, **k: goonmetrics)


def sell_order(order_id, type_id, price, volume_remain=10, location_id=STRUCTURE):
    return {"order_id": order_id, "type_id": type_id, "price": price,
            "volume_remain": volume_remain, "location_id": location_id, "is_buy_order": False}


def row(item_id=TRIT, *, decision="Skip", active=True, profit=None, margin=None,
        sell_volume=None) -> ShortlistRow:
    return ShortlistRow(item=f"Item {item_id}", category="Material", landed_cost=100.0,
                        net_sell=110.0, sell_volume=sell_volume, own_orders_remaining=0.0,
                        profit_per_unit=profit, margin=margin, profit_per_m3=None,
                        decision=decision, active=active, item_id=item_id, volume_m3=1.0,
                        jita_sell=100.0, import_cost=0.0)


def candidate(type_id=TRIT, item="Tritanium") -> Candidate:
    return Candidate(item=item, type_id=type_id, volume_m3=1.0, category="Material",
                     market_group_path="Manufacture & Research")


def scored(type_id=TRIT, item="Tritanium", add=True) -> NewCandidateResult:
    return NewCandidateResult(item=item, category="Material", type_id=type_id, volume_m3=1.0,
                              paired_days=20, profitable_days=15, hit_rate=0.75,
                              latest_margin=0.2, best_margin=0.4, avg_profit_m3=50.0,
                              avg_sell_movement=1000.0, score=12.0,
                              recommendation="Consider import" if add else "Skip", add=add)


# -------------------------------------------------------------- characters
def test_role_characters_are_listed_per_prefix(monkeypatch, db):
    tm = StubTokenManager([StubRecord("buyer:1", 1, "Buyer One"),
                           StubRecord("seller:2", 2, "Seller Two")])
    install(monkeypatch, tokens=tm)
    assert actions.do_list_buyer_characters() == [("buyer:1", 1, "Buyer One")]
    assert actions.do_list_seller_characters() == [("seller:2", 2, "Seller Two")]


def test_remove_trading_character_delegates_to_the_token_manager(monkeypatch, db):
    tm = StubTokenManager([StubRecord("buyer:1", 1, "Buyer One")])
    install(monkeypatch, tokens=tm)
    assert actions.do_remove_trading_character("buyer:1") == {"removed": "buyer:1"}
    assert tm.removed == ["buyer:1"]


# ------------------------------------------------------------------ wallet
def test_cache_wallet_balances_writes_the_live_value(monkeypatch, db, cfg):
    install(monkeypatch, tokens=StubTokenManager([StubRecord("buyer:1", 1, "Buyer One")]),
            client=StubClient(balance=1234.5))
    result = actions._cache_wallet_balances(cfg)
    assert result == {"cached": 1, "characters": 1}
    assert actions.do_wallet_balance("buyer:1")["balance"] == 1234.5


def test_wallet_balance_for_an_unknown_character_is_an_action_error(monkeypatch, db, cfg):
    install(monkeypatch, tokens=StubTokenManager(), client=StubClient())
    with pytest.raises(ActionError, match="not logged in"):
        actions.do_wallet_balance("buyer:9")


def test_wallet_balance_needs_a_prior_sync(monkeypatch, db, cfg):
    install(monkeypatch, tokens=StubTokenManager([StubRecord("buyer:1", 1, "Buyer One")]),
            client=StubClient())
    with pytest.raises(ActionError, match="Update Data"):
        actions.do_wallet_balance("buyer:1")


def test_cache_wallet_transactions_labels_and_writes_the_live_value(monkeypatch, db, cfg):
    install(monkeypatch, tokens=StubTokenManager([StubRecord("buyer:1", 1, "Buyer One")]),
            client=StubClient())
    storage.replace_sde_data(types=[(TRIT, 18, "Tritanium", 0.01, 1, 1857, 0, None, 1)],
                             groups=[], market_groups=[], blueprint_time=[],
                             blueprint_materials=[], blueprint_products=[])
    monkeypatch.setattr(actions, "fetch_recent_transactions", lambda *a, **k: [
        {"transaction_id": 1, "date": "2026-08-01T00:00:00Z", "type_id": TRIT, "is_buy": True,
         "quantity": 10, "unit_price": 5.0, "location_id": 60003760},
        {"transaction_id": 2, "date": "2026-08-05T00:00:00Z", "type_id": PYERITE, "is_buy": False,
         "quantity": 2, "unit_price": 7.0, "location_id": 60003760},
    ])
    result = actions._cache_wallet_transactions(cfg)
    assert result == {"cached": 1, "characters": 1}
    rows = actions.do_wallet_transactions("buyer:1", cfg=cfg)
    assert [r["transaction_id"] for r in rows] == [2, 1]
    assert rows[1]["item"] == "Tritanium"
    assert rows[0]["item"] == str(PYERITE)     # not in the SDE cache - id, never a guess
    assert rows[1]["total"] == pytest.approx(50.0)


def test_wallet_transactions_needs_a_prior_sync(monkeypatch, db, cfg):
    install(monkeypatch, tokens=StubTokenManager([StubRecord("buyer:1", 1, "Buyer One")]),
            client=StubClient())
    with pytest.raises(ActionError, match="Update Data"):
        actions.do_wallet_transactions("buyer:1", cfg=cfg)


# ---------------------------------------------------------------- settings
def test_update_settings_persists_and_applies(db):
    cfg = TradingConfig()
    actions.do_update_settings({"import_cost_per_m3": 1500.0}, cfg)
    assert cfg.import_cost_per_m3 == 1500.0
    assert storage.load_settings("trading")["import_cost_per_m3"] == 1500.0


def test_update_settings_rejects_a_bad_value_without_persisting(db):
    cfg = TradingConfig()
    # ConfigError already subclasses ActionError here (errors.py), so no
    # translation layer sits in between - the CLI catches this as-is.
    with pytest.raises(ActionError):
        actions.do_update_settings({"import_cost_per_m3": -5.0}, cfg)
    assert storage.load_settings("trading") == {}
    assert cfg.import_cost_per_m3 == TradingConfig().import_cost_per_m3


# ------------------------------------------------------- candidate universe
def test_build_universe_persists_what_discovery_returns(monkeypatch, db, cfg):
    install(monkeypatch, client=StubClient())
    monkeypatch.setattr(candidate_discovery, "build_candidate_universe",
                        lambda *a, **k: [candidate(), candidate(PYERITE, "Pyerite")])
    assert actions.do_build_universe(cfg) == {"count": 2}
    assert len(storage.load_candidate_universe("candidate_universe")) == 2


def test_build_focused_needs_a_universe_first(db, cfg):
    with pytest.raises(ActionError, match="build-universe"):
        actions.do_build_focused(cfg)


def test_build_focused_writes_the_focused_table(db, cfg):
    storage.save_candidate_universe([candidate()], "2026-09-01T00:00:00", table="candidate_universe")
    assert actions.do_build_focused(cfg) == {"count": 1}
    assert [c.type_id for c in storage.load_candidate_universe("focused_candidates")] == [TRIT]


# ------------------------------------------------------- candidate search
def test_find_new_candidates_needs_focused_candidates(db, cfg):
    with pytest.raises(ActionError, match="Focused candidates is empty"):
        actions.do_find_new_candidates(cfg=cfg)


def test_find_new_candidates_persists_results_history_and_cursor(monkeypatch, db, cfg):
    storage.save_candidate_universe([candidate()], "2026-09-01T00:00:00", table="focused_candidates")
    install(monkeypatch, goonmetrics=StubGoonmetrics())

    def fake_search(candidates, existing_ids, client, config, offset=0,
                    history_sink=None, results_sink=None):
        assert offset == 0
        history_sink([HistoryPoint(region_id=REF_REGION, type_id=TRIT, date="2026-08-01",
                                   min_price=1.0, max_price=2.0, avg_price=1.5,
                                   movement=500.0, num_orders=4)])
        results_sink([scored()])
        return [scored()], 7

    monkeypatch.setattr(history_backtest, "find_new_import_candidates_safe", fake_search)
    assert actions.do_find_new_candidates(safe=True, cfg=cfg) == {"evaluated": 1, "recommended": 1}
    assert storage.get_candidate_search_offset() == 7
    assert [r.type_id for r in storage.latest_new_candidates()] == [TRIT]
    assert len(storage.read_goonmetrics_history_for_types([TRIT])) == 1


def test_find_new_candidates_skips_items_already_on_the_shortlist(monkeypatch, db, cfg):
    storage.save_candidate_universe([candidate()], "2026-09-01T00:00:00", table="focused_candidates")
    storage.upsert_shortlist([ShortlistItem(item="Tritanium", item_id=TRIT, category="Material",
                                            volume_m3=1.0)])
    install(monkeypatch, goonmetrics=StubGoonmetrics())
    seen = {}

    def fake_search(candidates, existing_ids, *a, **k):
        seen["existing"] = existing_ids
        return [], 0

    monkeypatch.setattr(history_backtest, "find_new_import_candidates_safe", fake_search)
    actions.do_find_new_candidates(safe=True, cfg=cfg)
    assert seen["existing"] == {TRIT}


# -------------------------------------------------------------- shortlist
def test_add_to_shortlist_needs_a_search_run(db):
    with pytest.raises(ActionError, match="find-candidates"):
        actions.do_add_to_shortlist()


def test_add_to_shortlist_adds_only_recommended_rows(db):
    storage.save_new_candidates([scored(), scored(PYERITE, "Pyerite", add=False)],
                                "2026-09-01T00:00:00")
    assert actions.do_add_to_shortlist() == {"added": 1}
    assert [i.item_id for i in storage.load_shortlist()] == [TRIT]


def test_add_to_shortlist_is_a_no_op_when_nothing_was_recommended(db):
    storage.save_new_candidates([scored(add=False)], "2026-09-01T00:00:00")
    assert actions.do_add_to_shortlist() == {"added": 0}
    assert storage.load_shortlist() == []


def test_refresh_shortlist_needs_a_shortlist(monkeypatch, db, cfg):
    install(monkeypatch, tokens=StubTokenManager(), client=StubClient(),
            goonmetrics=StubGoonmetrics())
    with pytest.raises(ActionError, match="Shortlist is empty"):
        actions.do_refresh_shortlist(cfg)


def test_refresh_shortlist_prices_every_item_and_saves_a_snapshot(monkeypatch, db, cfg):
    """_cache_shortlist_market_prices (Market Prices scope) does the live
    fetch; do_refresh_shortlist (pure local, see esi_update.py's own
    docstring) then recomputes from that cache alone."""
    storage.upsert_shortlist([ShortlistItem(item="Tritanium", item_id=TRIT, category="Material",
                                            volume_m3=1.0, meta_level=0)])
    client = StubClient(structure_stats={TRIT: stats(200.0)}, jita_stats={TRIT: stats(100.0)})
    install(monkeypatch, tokens=StubTokenManager([StubRecord("seller:2", 2, "Seller")]),
            client=client,
            goonmetrics=StubGoonmetrics([HistoryPoint(region_id=REF_REGION, type_id=TRIT,
                                                      date="2026-08-01", min_price=1.0,
                                                      max_price=2.0, avg_price=1.5,
                                                      movement=1000.0, num_orders=4)]))
    actions._cache_shortlist_market_prices(cfg)
    result = actions.do_refresh_shortlist(cfg)
    assert result["summary"]["import_candidates"] == 1
    assert result["top_imports"][0]["max_profit_per_day"] == pytest.approx(100_000.0)
    saved = storage.latest_shortlist_snapshot()
    assert [(r.item_id, r.decision) for r in saved] == [(TRIT, "Import")]


def test_refresh_shortlist_turns_a_structure_failure_into_a_clear_error(monkeypatch, db, cfg):
    storage.upsert_shortlist([ShortlistItem(item="Tritanium", item_id=TRIT, category="Material",
                                            volume_m3=1.0, meta_level=0)])
    install(monkeypatch, tokens=StubTokenManager([StubRecord("seller:2", 2, "Seller")]),
            client=StubClient(structure_error=ESIError("403 Forbidden")),
            goonmetrics=StubGoonmetrics())
    with pytest.raises(ActionError, match="docking access"):
        actions._cache_shortlist_market_prices(cfg)


def test_refresh_shortlist_survives_a_history_outage(monkeypatch, db, cfg):
    """A Goonmetrics outage must degrade Profit/Day to "no data", not abort
    the price cache step."""
    storage.upsert_shortlist([ShortlistItem(item="Tritanium", item_id=TRIT, category="Material",
                                            volume_m3=1.0, meta_level=0)])

    class BrokenGoonmetrics:
        def price_history_chunked(self, *a, **k):
            raise RuntimeError("goonmetrics is down")

    install(monkeypatch, tokens=StubTokenManager([StubRecord("seller:2", 2, "Seller")]),
            client=StubClient(structure_stats={TRIT: stats(200.0)}, jita_stats={TRIT: stats(100.0)}),
            goonmetrics=BrokenGoonmetrics())
    actions._cache_shortlist_market_prices(cfg)
    result = actions.do_refresh_shortlist(cfg)
    assert result["top_imports"] == []
    assert storage.latest_shortlist_snapshot()[0].avg_daily_volume is None


def test_meta_levels_are_backfilled_and_persisted(monkeypatch, db, cfg):
    storage.upsert_shortlist([ShortlistItem(item="Tritanium", item_id=TRIT, category="Material",
                                            volume_m3=1.0, meta_level=None)])
    install(monkeypatch, tokens=StubTokenManager([StubRecord("seller:2", 2, "Seller")]),
            client=StubClient(structure_stats={TRIT: stats(200.0)},
                              jita_stats={TRIT: stats(100.0)}, meta_levels={TRIT: 5}),
            goonmetrics=StubGoonmetrics())
    result = actions._cache_shortlist_market_prices(cfg)
    assert result["meta_level_backfill"]["fetched"] == 1
    assert storage.load_shortlist()[0].meta_level == 5


# ------------------------------------------------------ live one-shot checks
# do_check_seller_unlisted_stock/do_check_undercut are cache-only reads now
# (see esi_update.py's own docstring) - the private _fetch_*/_cache_* below
# are the real live fetch, exercised the same way the old do_* tests did.
def test_fetch_seller_unlisted_stock_needs_a_seller(monkeypatch, db, cfg):
    install(monkeypatch, tokens=StubTokenManager(), client=StubClient())
    with pytest.raises(ActionError, match="No seller character"):
        actions._fetch_seller_unlisted_stock(cfg)


def test_fetch_seller_unlisted_stock_is_enriched_with_name_and_margin(monkeypatch, db, cfg):
    storage.upsert_shortlist([ShortlistItem(item="Tritanium", item_id=TRIT, category="Material",
                                            volume_m3=1.0)])
    storage.replace_sde_data(types=[(TRIT, 18, "Tritanium", 2.0, 1, 1857, 0, None, 1)],
                             groups=[], market_groups=[], blueprint_time=[],
                             blueprint_materials=[], blueprint_products=[])
    client = StubClient(assets={2: [{"type_id": TRIT, "quantity": 40, "location_id": STRUCTURE,
                                     "location_flag": "Hangar"}]},
                        structure_stats={TRIT: stats(200.0, sell_volume=12.0)},
                        jita_stats={TRIT: stats(100.0)})
    install(monkeypatch, tokens=StubTokenManager([StubRecord("seller:2", 2, "Seller")]), client=client)
    rows = actions._fetch_seller_unlisted_stock(cfg)["rows"]
    assert len(rows) == 1
    assert rows[0].item == "Tritanium"
    assert rows[0].unlisted_quantity == 40
    assert rows[0].sell_volume == 12.0
    assert rows[0].margin == pytest.approx(1.0)      # net sell 200 vs landed cost 100


def test_fetch_seller_unlisted_stock_asks_for_a_re_login_when_the_assets_scope_is_missing(monkeypatch, db, cfg):
    storage.upsert_shortlist([ShortlistItem(item="Tritanium", item_id=TRIT, category="Material",
                                            volume_m3=1.0)])
    install(monkeypatch, tokens=StubTokenManager([StubRecord("seller:2", 2, "Seller")]),
            client=StubClient())
    monkeypatch.setattr(own_orders, "fetch_seller_stock_without_order_pooled",
                        lambda *a, **k: (_ for _ in ()).throw(ESIError("403 Forbidden")))
    with pytest.raises(ActionError, match="esi-assets.read_assets.v1"):
        actions._fetch_seller_unlisted_stock(cfg)


def test_unlisted_stock_needs_a_prior_sync(db):
    with pytest.raises(ActionError, match="Update Data"):
        actions.do_check_seller_unlisted_stock()


def test_fetch_undercut_resolves_item_names(monkeypatch, db, cfg):
    storage.replace_sde_data(types=[(TRIT, 18, "Tritanium", 0.01, 1, 1857, 0, None, 1)],
                             groups=[], market_groups=[], blueprint_time=[],
                             blueprint_materials=[], blueprint_products=[])
    client = StubClient(orders={2: [sell_order(10, TRIT, 100.0)]},
                        book=[sell_order(10, TRIT, 100.0), sell_order(99, TRIT, 90.0)])
    install(monkeypatch, tokens=StubTokenManager([StubRecord("seller:2", 2, "Seller")]), client=client)
    rows = actions._fetch_undercut(cfg)["rows"]
    assert [(r.item, r.my_price, r.competitor_price) for r in rows] == [("Tritanium", 100.0, 90.0)]


def test_fetch_undercut_needs_a_seller(monkeypatch, db, cfg):
    install(monkeypatch, tokens=StubTokenManager(), client=StubClient())
    with pytest.raises(ActionError, match="No seller character"):
        actions._fetch_undercut(cfg)


def test_check_undercut_needs_a_prior_sync(db):
    with pytest.raises(ActionError, match="Update Data"):
        actions.do_check_undercut()


def test_cache_unlisted_stock_and_undercut_isolates_failures_and_caches_rows(monkeypatch, db, cfg):
    # TRIT: unlisted stock (asset sitting at the structure, no sell order at
    # all). PYERITE: undercut (my own order beaten by a cheaper competitor) -
    # two different items, since "has an open order" and "has no open order
    # at all" are mutually exclusive for the same type_id.
    storage.upsert_shortlist([ShortlistItem(item="Tritanium", item_id=TRIT, category="Material",
                                            volume_m3=1.0)])
    storage.replace_sde_data(types=[(TRIT, 18, "Tritanium", 2.0, 1, 1857, 0, None, 1),
                                    (PYERITE, 18, "Pyerite", 2.0, 1, 1857, 0, None, 1)],
                             groups=[], market_groups=[], blueprint_time=[],
                             blueprint_materials=[], blueprint_products=[])
    client = StubClient(assets={2: [{"type_id": TRIT, "quantity": 40, "location_id": STRUCTURE,
                                     "location_flag": "Hangar"}]},
                        orders={2: [sell_order(10, PYERITE, 100.0)]},
                        book=[sell_order(10, PYERITE, 100.0), sell_order(99, PYERITE, 90.0)],
                        structure_stats={TRIT: stats(200.0, sell_volume=12.0)},
                        jita_stats={TRIT: stats(100.0)})
    install(monkeypatch, tokens=StubTokenManager([StubRecord("seller:2", 2, "Seller")]), client=client)
    result = actions._cache_unlisted_stock_and_undercut(cfg)
    assert result["unlisted_stock"] == {"count": 1}
    assert result["undercut"] == {"count": 1}
    assert actions.do_check_seller_unlisted_stock()["rows"][0].item == "Tritanium"
    assert actions.do_check_undercut()["rows"][0].item == "Pyerite"


# --------------------------------------------------------- realized trades
def test_cache_reconcile_trades_needs_both_roles(monkeypatch, db, cfg):
    install(monkeypatch, tokens=StubTokenManager([StubRecord("buyer:1", 1, "Buyer")]),
            client=StubClient())
    with pytest.raises(ActionError, match="buyer and one seller"):
        actions._cache_reconcile_trades(cfg)


def test_cache_reconcile_trades_persists_the_matched_pairs(monkeypatch, db, cfg):
    install(monkeypatch, tokens=StubTokenManager([StubRecord("buyer:1", 1, "Buyer"),
                                                  StubRecord("seller:2", 2, "Seller")]),
            client=StubClient())
    trade = RealizedTrade(type_id=TRIT, item="Tritanium", buy_date="2026-08-01",
                          buy_qty=10, buy_unit_price=5.0, sell_date="2026-08-03",
                          sell_qty=10, sell_unit_price=8.0, matched_qty=10,
                          realized_profit=30.0, margin=0.6)
    monkeypatch.setattr(actions, "reconcile_realized_trades", lambda *a, **k: [trade])
    result = actions._cache_reconcile_trades(cfg)
    assert result["matched_trades"] == 1
    assert result["total_realized_profit"] == pytest.approx(30.0)
    assert [t.type_id for t in storage.latest_realized_trades()] == [TRIT]
    # do_reconcile_trades is the cache-read counterpart - same result, no network.
    assert actions.do_reconcile_trades()["matched_trades"] == 1


def test_reconcile_trades_needs_a_prior_sync(db):
    with pytest.raises(ActionError, match="Update Data"):
        actions.do_reconcile_trades()


# ------------------------------------------------------ pruning / reactivation
def test_skip_grace_period_only_deactivates_streaks_old_enough():
    now = dt.datetime(2026, 9, 1)
    rows = [row(1, decision="Skip"), row(2, decision="No market data"),
            row(3, decision="Import"), row(4, decision="Skip")]
    skip_since = {1: "2026-07-01T00:00:00", 2: "2026-08-30T00:00:00", 3: "2026-01-01T00:00:00"}
    # 1: streak long enough. 2: too recent. 3: profitable this run, so not
    # eligible regardless of its stored streak. 4: no streak recorded yet.
    assert actions._items_past_skip_grace_period(rows, skip_since, 30, now) == [(1, "Item 1")]


def test_cap_ranks_by_daily_profit_and_sorts_unknowns_last():
    rows = [row(1, profit=10.0, sell_volume=10.0), row(2, profit=1.0, sell_volume=1.0),
            row(3, profit=None, sell_volume=None)]
    assert actions._items_beyond_rank(rows, 1) == [(2, "Item 2"), (3, "Item 3")]


def test_reactivation_needs_volume_profit_and_margin(cfg):
    rows = [row(1, active=False, profit=5.0, margin=0.2, sell_volume=10.0),
            row(2, active=False, profit=5.0, margin=0.01, sell_volume=10.0),   # margin too thin
            row(3, active=False, profit=5.0, margin=0.2, sell_volume=0.0),     # nothing listed
            row(4, active=True, profit=5.0, margin=0.2, sell_volume=10.0)]     # already active
    assert actions._items_to_reactivate(rows, cfg) == [(1, "Item 1")]


def test_refresh_and_prune_deactivates_and_reflects_it_in_the_snapshot(monkeypatch, db, cfg):
    items = [ShortlistItem(item="Item 1", item_id=1, category="Material", volume_m3=1.0)]
    rows = [row(1, decision="Skip")]
    monkeypatch.setattr(actions, "do_add_to_shortlist", lambda: {"added": 0})
    monkeypatch.setattr(actions, "_compute_shortlist_rows", lambda *a, **k: (items, rows))
    storage.upsert_shortlist(items)
    storage.start_shortlist_skip_streak([1], "2026-01-01T00:00:00")

    result = actions.do_refresh_and_prune_candidates(cfg=cfg)
    assert result["deactivated_items"] == ["Item 1"]
    assert storage.load_shortlist()[0].active is False
    # The saved snapshot has to show this run's own prune, not lag a cycle.
    assert storage.latest_shortlist_snapshot()[0].decision == "Inactive"
    # Streak tracking ends once it has led to deactivation.
    assert storage.get_shortlist_skip_since() == {}


def test_refresh_and_prune_starts_and_clears_streaks(monkeypatch, db, cfg):
    items = [ShortlistItem(item="Item 1", item_id=1, category="Material", volume_m3=1.0),
             ShortlistItem(item="Item 2", item_id=2, category="Material", volume_m3=1.0)]
    rows = [row(1, decision="Skip"), row(2, decision="Import")]
    monkeypatch.setattr(actions, "do_add_to_shortlist", lambda: {"added": 0})
    monkeypatch.setattr(actions, "_compute_shortlist_rows", lambda *a, **k: (items, rows))
    storage.upsert_shortlist(items)
    storage.start_shortlist_skip_streak([2], "2026-01-01T00:00:00")

    actions.do_refresh_and_prune_candidates(cfg=cfg)
    skip_since = storage.get_shortlist_skip_since()
    assert 1 in skip_since             # newly Skip-like this run
    assert 2 not in skip_since         # profitable again, streak broken
    assert storage.load_shortlist()[0].active is True     # grace period not up yet


def test_skip_deactivation_days_counts_down(db, cfg):
    since = (dt.datetime.utcnow() - dt.timedelta(days=10)).isoformat(timespec="seconds")
    storage.start_shortlist_skip_streak([1], since)
    cfg.skip_grace_period_days = 30
    assert actions.shortlist_skip_deactivation_days(cfg) == {1: 20}


# ---------------------------------------------------------------- pipeline
def test_pipeline_isolates_a_failing_step(monkeypatch, db, cfg):
    def boom(**kwargs):
        raise ActionError("shortlist is empty")

    monkeypatch.setattr(actions, "do_refresh_and_prune_candidates", boom)
    monkeypatch.setattr(actions, "_cache_reconcile_trades", lambda *a, **k: {"matched_trades": 3})
    monkeypatch.setattr(actions, "_cache_wallet_balances", lambda *a, **k: {"cached": 0})
    monkeypatch.setattr(actions, "_cache_wallet_transactions", lambda *a, **k: {"cached": 0})
    monkeypatch.setattr(actions, "_cache_unlisted_stock_and_undercut", lambda *a, **k: {})
    results = actions.do_pipeline(cfg=cfg)
    assert results["refresh_and_prune_candidates"] == {"error": "shortlist is empty"}
    # The later step still ran - that is the whole point of the isolation.
    assert results["reconcile_trades"] == {"matched_trades": 3}


def test_pipeline_isolates_an_esi_failure_too(monkeypatch, db, cfg):
    def boom(*a, **k):
        raise ESIError("503 Service Unavailable")

    monkeypatch.setattr(actions, "do_refresh_and_prune_candidates", lambda **k: {"ok": True})
    monkeypatch.setattr(actions, "_cache_reconcile_trades", boom)
    monkeypatch.setattr(actions, "_cache_wallet_balances", lambda *a, **k: {"cached": 0})
    monkeypatch.setattr(actions, "_cache_wallet_transactions", lambda *a, **k: {"cached": 0})
    monkeypatch.setattr(actions, "_cache_unlisted_stock_and_undercut", lambda *a, **k: {})
    results = actions.do_pipeline(cfg=cfg)
    assert results["refresh_and_prune_candidates"] == {"ok": True}
    assert "503" in results["reconcile_trades"]["error"]


def test_pipeline_skips_the_universe_rebuild_unless_asked(monkeypatch, db, cfg):
    calls = []
    monkeypatch.setattr(actions, "do_build_universe", lambda *a, **k: calls.append("universe") or {"count": 1})
    monkeypatch.setattr(actions, "do_build_focused", lambda *a, **k: calls.append("focused") or {"count": 1})
    monkeypatch.setattr(actions, "do_refresh_and_prune_candidates", lambda **k: {"ok": True})
    monkeypatch.setattr(actions, "_cache_reconcile_trades", lambda *a, **k: {"matched_trades": 0})
    monkeypatch.setattr(actions, "_cache_wallet_balances", lambda *a, **k: {"cached": 0})
    monkeypatch.setattr(actions, "_cache_wallet_transactions", lambda *a, **k: {"cached": 0})
    monkeypatch.setattr(actions, "_cache_unlisted_stock_and_undercut", lambda *a, **k: {})

    actions.do_pipeline(cfg=cfg)
    assert calls == []
    actions.do_pipeline(rebuild_universe=True, cfg=cfg)
    assert calls == ["universe", "focused"]
