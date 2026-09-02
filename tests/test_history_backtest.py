"""Scoring/selection logic only - no network, no database.

history_backtest itself never opens a connection or a socket: history comes
in from a GoonmetricsClient the caller supplies (stubbed here) and everything
else is pure arithmetic over the candidates/history handed in.
"""
from __future__ import annotations

import math

import pytest

from eve_trader_local import history_backtest as hb
from eve_trader_local.config import TradingConfig
from eve_trader_local.goonmetrics_client import HistoryPoint
from eve_trader_local.models import Candidate

JITA = 10000002
REF = 10000009


@pytest.fixture
def cfg() -> TradingConfig:
    """Round numbers so the expected margins below can be worked out by hand:
    no broker fee, no haircut, no import cost - landed == jita avg price."""
    return TradingConfig(jita_region_id=JITA, reference_region_id=REF,
                         jita_buy_broker_fee=0.0, structure_sell_haircut=1.0,
                         import_cost_per_m3=0.0, min_margin_threshold=0.05,
                         min_hit_rate=0.30, min_avg_movement=0.0,
                         safe_mode_max_ids=2, chunk_size=25)


def _cand(type_id: int = 100, volume_m3: float = 1.0) -> Candidate:
    return Candidate(item=f"Item {type_id}", type_id=type_id, volume_m3=volume_m3,
                     category="Module/Rig", market_group_path="modules")


def _points(type_id: int, days: list[tuple[str, float, float, float]]) -> list[HistoryPoint]:
    """days: (date, jita_avg, ref_avg, ref_movement)."""
    out = []
    for date, jita, ref, move in days:
        out.append(HistoryPoint(region_id=JITA, type_id=type_id, date=date, min_price=jita,
                                max_price=jita, avg_price=jita, movement=0.0, num_orders=1))
        out.append(HistoryPoint(region_id=REF, type_id=type_id, date=date, min_price=ref,
                                max_price=ref, avg_price=ref, movement=move, num_orders=1))
    return out


class StubClient:
    """Stands in for GoonmetricsClient: returns whatever HistoryPoints were
    handed to it for the region asked about, and records every call."""

    def __init__(self, points: list[HistoryPoint], fail_for: set[int] | None = None):
        self.points = points
        self.fail_for = fail_for or set()
        self.calls: list[tuple[int, list[int]]] = []

    def price_history_chunked(self, region_id: int, type_ids: list[int]):
        self.calls.append((region_id, list(type_ids)))
        if set(type_ids) & self.fail_for:
            raise RuntimeError("simulated Goonmetrics failure")
        wanted = set(type_ids)
        return [p for p in self.points if p.region_id == region_id and p.type_id in wanted]


# --------------------------------------------------------------- _score_candidate
def test_score_candidate_margin_math(cfg):
    # 3 days, all with ref = 2x jita -> margin 1.0 each, profit 100/unit,
    # volume_m3 1.0 -> profit_m3 100. movement 10 every day.
    points = _points(100, [("2026-01-01", 100.0, 200.0, 10.0),
                           ("2026-01-02", 100.0, 200.0, 10.0),
                           ("2026-01-03", 100.0, 200.0, 10.0)])
    r = hb._score_candidate(_cand(), hb._index_history(points), cfg)

    assert r is not None
    assert (r.paired_days, r.profitable_days) == (3, 3)
    assert r.hit_rate == 1.0
    assert r.latest_margin == pytest.approx(1.0)
    assert r.best_margin == pytest.approx(1.0)
    assert r.avg_profit_m3 == pytest.approx(100.0)
    assert r.avg_sell_movement == pytest.approx(10.0)
    assert r.score == pytest.approx(100.0 * math.log(11.0) * 1.0)
    assert r.add is True
    assert r.recommendation == "Consider import"


def test_score_candidate_costs_are_applied(cfg):
    cfg.jita_buy_broker_fee = 0.10          # landed = 100 * 1.10 + 5 * 2 = 120
    cfg.import_cost_per_m3 = 2.0
    cfg.structure_sell_haircut = 0.90       # net_sell = 200 * 0.9 = 180
    points = _points(100, [("2026-01-01", 100.0, 200.0, 10.0)])
    r = hb._score_candidate(_cand(volume_m3=5.0), hb._index_history(points), cfg)

    assert r.latest_margin == pytest.approx((180.0 - 120.0) / 120.0)   # 0.5
    assert r.avg_profit_m3 == pytest.approx((180.0 - 120.0) / 5.0)     # 12.0


def test_score_candidate_ignores_unpaired_and_returns_none_without_jita_history(cfg):
    paired = _points(100, [("2026-01-01", 100.0, 200.0, 10.0)])
    # A Jita-only day: counted in `dates` but skipped for lack of a ref price.
    paired.append(HistoryPoint(region_id=JITA, type_id=100, date="2026-01-02", min_price=100.0,
                               max_price=100.0, avg_price=100.0, movement=0.0, num_orders=1))
    r = hb._score_candidate(_cand(), hb._index_history(paired), cfg)
    assert r.paired_days == 1
    # The latest date has no ref price at all, so the `add` gate's
    # current-profitability check sees 0.0 and refuses the recommendation
    # even though the one paired day was excellent.
    assert r.latest_margin == 0.0
    assert r.add is False

    assert hb._score_candidate(_cand(999), hb._index_history(paired), cfg) is None


def test_score_candidate_add_gates(cfg):
    # 1 good day out of 4 -> hit_rate 0.25, below min_hit_rate 0.30, even
    # though the *latest* day is profitable.
    points = _points(100, [("2026-01-01", 100.0, 100.0, 10.0),
                           ("2026-01-02", 100.0, 100.0, 10.0),
                           ("2026-01-03", 100.0, 100.0, 10.0),
                           ("2026-01-04", 100.0, 200.0, 10.0)])
    r = hb._score_candidate(_cand(), hb._index_history(points), cfg)
    assert r.hit_rate == pytest.approx(0.25)
    assert r.latest_margin == pytest.approx(1.0)
    assert r.add is False
    assert r.recommendation == "Skip"

    # Latest day unprofitable, everything else fine -> still no.
    points = _points(100, [("2026-01-01", 100.0, 200.0, 10.0),
                           ("2026-01-02", 100.0, 200.0, 10.0),
                           ("2026-01-03", 100.0, 100.0, 10.0)])
    r = hb._score_candidate(_cand(), hb._index_history(points), cfg)
    assert r.hit_rate == pytest.approx(2 / 3)
    assert r.latest_margin == pytest.approx(0.0)
    assert r.add is False

    # Liquidity gate: profitable and consistent, but no movement at all.
    cfg.min_avg_movement = 5.0
    points = _points(100, [("2026-01-01", 100.0, 200.0, 1.0)])
    assert hb._score_candidate(_cand(), hb._index_history(points), cfg).add is False


# ---------------------------------------------------------- select_candidate_window
def test_select_candidate_window_rotates_and_wraps():
    cands = [_cand(i) for i in range(5)]

    window, nxt = hb.select_candidate_window(cands, 2, 0)
    assert [c.type_id for c in window] == [0, 1]
    assert nxt == 2

    window, nxt = hb.select_candidate_window(cands, 2, nxt)
    assert [c.type_id for c in window] == [2, 3]
    assert nxt == 4

    # Wraps around the end rather than returning a short final window.
    window, nxt = hb.select_candidate_window(cands, 2, nxt)
    assert [c.type_id for c in window] == [4, 0]
    assert nxt == 1

    # Every candidate is reachable within ceil(5/2)=3 runs.
    seen = set()
    off = 0
    for _ in range(3):
        window, off = hb.select_candidate_window(cands, 2, off)
        seen.update(c.type_id for c in window)
    assert seen == {0, 1, 2, 3, 4}


def test_select_candidate_window_edge_cases():
    assert hb.select_candidate_window([], 10, 7) == ([], 0)
    cands = [_cand(i) for i in range(3)]
    # Fits in one window: no rotation needed, offset resets.
    window, nxt = hb.select_candidate_window(cands, 5, 2)
    assert [c.type_id for c in window] == [0, 1, 2]
    assert nxt == 0
    # An offset past the end wraps rather than returning nothing.
    window, _ = hb.select_candidate_window([_cand(i) for i in range(4)], 2, 5)
    assert [c.type_id for c in window] == [1, 2]


# ------------------------------------------------------ find_new_import_candidates
def test_find_new_import_candidates_skips_existing_and_duplicates(cfg):
    points = _points(1, [("2026-01-01", 100.0, 200.0, 10.0)]) + \
        _points(2, [("2026-01-01", 100.0, 200.0, 10.0)])
    client = StubClient(points)
    candidates = [_cand(1), _cand(2), _cand(2)]   # 2 listed twice

    results, next_offset = hb.find_new_import_candidates(
        candidates, existing_item_ids={1}, client=client, cfg=cfg)

    assert [r.type_id for r in results] == [2]
    assert next_offset == 0                      # full runs never rotate
    assert client.calls[0][1] == [2]             # 1 was never even fetched


def test_find_new_import_candidates_sorts_by_score_then_latest_margin(cfg):
    # 200 is the more profitable item; 300 barely clears breakeven.
    points = _points(200, [("2026-01-01", 100.0, 300.0, 10.0)]) + \
        _points(300, [("2026-01-01", 100.0, 106.0, 10.0)])
    results, _ = hb.find_new_import_candidates(
        [_cand(300), _cand(200)], set(), client=StubClient(points), cfg=cfg)

    assert [r.type_id for r in results] == [200, 300]
    assert results[0].score > results[1].score


def test_find_new_import_candidates_calls_sinks_per_batch(cfg):
    cfg.safe_mode_max_ids = 1                    # forces one batch per candidate
    points = _points(1, [("2026-01-01", 100.0, 200.0, 10.0)]) + \
        _points(2, [("2026-01-01", 100.0, 200.0, 10.0)])
    history_batches, result_batches = [], []

    results, _ = hb.find_new_import_candidates(
        [_cand(1), _cand(2)], set(), client=StubClient(points), cfg=cfg,
        history_sink=history_batches.append, results_sink=result_batches.append)

    assert len(results) == 2
    assert len(history_batches) == 2 and len(result_batches) == 2
    assert [r.type_id for batch in result_batches for r in batch] == [1, 2]


def test_find_new_import_candidates_one_bad_batch_does_not_lose_the_others(cfg):
    cfg.safe_mode_max_ids = 1
    points = _points(1, [("2026-01-01", 100.0, 200.0, 10.0)]) + \
        _points(2, [("2026-01-01", 100.0, 200.0, 10.0)])
    client = StubClient(points, fail_for={1})

    results, _ = hb.find_new_import_candidates(
        [_cand(1), _cand(2)], set(), client=client, cfg=cfg)

    assert [r.type_id for r in results] == [2]


# ------------------------------------------------- find_new_import_candidates_safe
def test_safe_variant_caps_and_rotates_where_the_full_run_does_not(cfg):
    cfg.safe_mode_max_ids = 2
    all_points = []
    for tid in range(1, 6):
        all_points += _points(tid, [("2026-01-01", 100.0, 200.0, 10.0)])
    candidates = [_cand(i) for i in range(1, 6)]
    client = StubClient(all_points)

    results, next_offset = hb.find_new_import_candidates_safe(
        candidates, set(), client=client, cfg=cfg, offset=0)

    # Only the 2-item window was evaluated, and the cursor moved on.
    assert {r.type_id for r in results} == {1, 2}
    assert next_offset == 2
    assert len(client.calls) == 2                # one Jita + one reference call

    results, next_offset = hb.find_new_import_candidates_safe(
        candidates, set(), client=client, cfg=cfg, offset=next_offset)
    assert {r.type_id for r in results} == {3, 4}
    assert next_offset == 4

    # The unrestricted variant evaluates all five in one call instead.
    results, next_offset = hb.find_new_import_candidates(
        candidates, set(), client=StubClient(all_points), cfg=cfg)
    assert len(results) == 5
    assert next_offset == 0


# --------------------------------------------------------- compute_margin_trends
def _trend_points(type_id: int, jita: float, refs: list[float]) -> list[HistoryPoint]:
    days = [(f"2026-01-{i + 1:02d}", jita, ref, 10.0) for i, ref in enumerate(refs)]
    return _points(type_id, days)


def test_compute_margin_trends_improving_and_eroding(cfg):
    # Baseline margin 0.5 (ref 150 vs jita 100) for 6 days, then the last 3
    # days climb to ref 200 -> margin 1.0.
    points = _trend_points(100, 100.0, [150.0] * 6 + [200.0] * 3)
    out = hb.compute_margin_trends(points, {100: 1.0}, cfg)

    assert out[100]["recent_avg_margin"] == pytest.approx(1.0)
    assert out[100]["baseline_avg_margin"] == pytest.approx((0.5 * 6 + 1.0 * 3) / 9)
    assert out[100]["trend_pct"] > 0

    eroding = _trend_points(100, 100.0, [200.0] * 6 + [150.0] * 3)
    assert hb.compute_margin_trends(eroding, {100: 1.0}, cfg)[100]["trend_pct"] < 0


def test_compute_margin_trends_omits_thin_history_and_near_zero_baselines(cfg):
    assert hb.compute_margin_trends([], {100: 1.0}, cfg) == {}

    # 5 paired days, one short of MIN_TREND_HISTORY_DAYS.
    thin = _trend_points(100, 100.0, [150.0] * (hb.MIN_TREND_HISTORY_DAYS - 1))
    assert hb.compute_margin_trends(thin, {100: 1.0}, cfg) == {}

    # Baseline hovering at ~1% margin: inside the dead zone, so no trend is
    # reported even though there is plenty of history.
    flat = _trend_points(100, 100.0, [101.0] * 8)
    assert hb.compute_margin_trends(flat, {100: 1.0}, cfg) == {}

    # A type not in `volumes` has no landed cost to compute and is skipped.
    ok = _trend_points(200, 100.0, [150.0] * 8)
    assert hb.compute_margin_trends(ok, {100: 1.0}, cfg) == {}
    assert set(hb.compute_margin_trends(ok, {200: 1.0}, cfg)) == {200}


def test_compute_margin_trends_ignores_unpaired_days(cfg):
    points = _trend_points(100, 100.0, [150.0] * 8)
    # Six extra Jita-only days can't make a trend on their own.
    points += [HistoryPoint(region_id=JITA, type_id=300, date=f"2026-02-{i:02d}", min_price=1.0,
                            max_price=1.0, avg_price=1.0, movement=0.0, num_orders=1)
               for i in range(1, 7)]
    out = hb.compute_margin_trends(points, {100: 1.0, 300: 1.0}, cfg)
    assert set(out) == {100}
