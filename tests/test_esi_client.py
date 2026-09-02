"""ESI client tests.

Nothing here touches the network: `requests.Session.get`/`.post` are the only
seams the real code uses, so every test swaps `ESIClient.session` for a fake
that replays a scripted list of responses. `time.sleep` is stubbed out too -
the retry/backoff and error-budget paths are exactly what's under test, and
they'd otherwise cost real seconds.
"""
from __future__ import annotations

import pytest

from eve_trader_local import esi_client
from eve_trader_local.config import TradingConfig
from eve_trader_local.esi_client import ESIClient, ESIError, OrderStats, _percentile, _summarize_orders


class FakeResponse:
    def __init__(self, status_code: int = 200, json_body=None, headers: dict | None = None,
                 text: str = ""):
        self.status_code = status_code
        self._json = json_body
        self.headers = headers or {}
        self.text = text

    def json(self):
        return self._json


class FakeSession:
    """Replays `responses` in order, one per request. A response may instead be
    a callable taking (url, params) so a test can vary its answer per page."""

    def __init__(self, responses: list):
        self.responses = list(responses)
        self.headers: dict = {}
        self.calls: list[tuple[str, str, dict, dict]] = []  # (method, url, params, headers)

    def _next(self, method, url, params, headers):
        self.calls.append((method, url, dict(params or {}), dict(headers or {})))
        if not self.responses:
            raise AssertionError(f"unexpected extra {method} for {url}")
        item = self.responses.pop(0)
        return item(url, params) if callable(item) else item

    def get(self, url, params=None, headers=None, timeout=None):
        return self._next("GET", url, params, headers)

    def post(self, url, json=None, params=None, timeout=None):
        self.last_post_body = json
        return self._next("POST", url, params, {})


class FakeTokens:
    def __init__(self):
        self.roles: list[str] = []

    def auth_header(self, role: str) -> dict:
        self.roles.append(role)
        return {"Authorization": f"Bearer token-for-{role}"}


@pytest.fixture(autouse=True)
def _isolate_class_state(monkeypatch):
    """The caches and the error budget are class-level by design (see
    ESIClient's own comments), so they leak between tests unless reset."""
    ESIClient.clear_price_caches()
    ESIClient.clear_order_book_caches()
    ESIClient._error_limit_remain = 100
    ESIClient._error_limit_reset_at = 0.0
    monkeypatch.setattr(esi_client.time, "sleep", lambda _s: None)
    yield
    ESIClient.clear_price_caches()
    ESIClient.clear_order_book_caches()


def make_client(responses: list) -> tuple[ESIClient, FakeSession, FakeTokens]:
    tokens = FakeTokens()
    client = ESIClient(cfg=TradingConfig(esi_base="https://esi.test/latest"), tokens=tokens)
    session = FakeSession(responses)
    client.session = session
    return client, session, tokens


# --------------------------------------------------------------------------
# pure helpers
# --------------------------------------------------------------------------

def test_percentile_is_nearest_rank_and_clamped():
    values = [1.0, 2.0, 3.0, 4.0]
    assert _percentile(values, 0.0) == 1.0
    assert _percentile(values, 0.5) == 3.0
    assert _percentile(values, 1.0) == 4.0  # index 4 clamps to the last element
    assert _percentile([], 0.05) is None


def test_summarize_orders_splits_sides_and_sums_volume():
    orders = [
        {"price": 100.0, "is_buy_order": False, "volume_remain": 5},
        {"price": 110.0, "is_buy_order": False, "volume_remain": 7},
        {"price": 90.0, "is_buy_order": True, "volume_remain": 3},
        {"price": 80.0, "is_buy_order": True, "volume_remain": 2},
    ]
    stats = _summarize_orders(orders)
    # sells sorted ascending -> 5th percentile is the cheapest ask;
    # buys sorted descending -> the same percentile picks the highest bid.
    assert stats.sell_percentile == 100.0
    assert stats.buy_percentile == 90.0
    assert stats.sell_volume == 12
    assert stats.buy_volume == 5


def test_summarize_orders_empty_book():
    assert _summarize_orders([]) == OrderStats(None, 0.0, None, 0.0)


def test_extract_meta_level():
    info = {"dogma_attributes": [{"attribute_id": 4, "value": 1}, {"attribute_id": 633, "value": 5}]}
    assert esi_client.extract_meta_level(info) == 5
    assert esi_client.extract_meta_level({"dogma_attributes": []}) is None
    assert esi_client.extract_meta_level({}) is None


# --------------------------------------------------------------------------
# retry / backoff
# --------------------------------------------------------------------------

def test_transient_5xx_retries_then_succeeds():
    client, session, _ = make_client([
        FakeResponse(503, text="bad gateway"),
        FakeResponse(200, {"ok": True}),
    ])
    assert client._get("/x/") == {"ok": True}
    assert len(session.calls) == 2


def test_retries_exhausted_raises_esi_error():
    client, session, _ = make_client([FakeResponse(503, text="down")] * 3)
    with pytest.raises(ESIError):
        client._get("/x/", retries=3)
    assert len(session.calls) == 3


def test_non_retryable_status_fails_immediately():
    client, session, _ = make_client([FakeResponse(403, text="forbidden")])
    with pytest.raises(ESIError, match="403"):
        client._get("/x/")
    assert len(session.calls) == 1


def test_rate_limited_uses_retry_after_header(monkeypatch):
    slept: list[float] = []
    client, session, _ = make_client([
        FakeResponse(429, headers={"Retry-After": "7"}),
        FakeResponse(200, {"ok": True}),
    ])
    monkeypatch.setattr(esi_client.time, "sleep", lambda s: slept.append(s))
    assert client._get("/x/") == {"ok": True}
    assert slept == [7.0]


def test_post_retries_then_raises():
    client, session, _ = make_client([FakeResponse(500)] * 3)
    with pytest.raises(ESIError):
        client._post_response("/universe/ids/", ["Jita"])
    assert len(session.calls) == 3


# --------------------------------------------------------------------------
# error budget
# --------------------------------------------------------------------------

def test_error_budget_headers_are_recorded():
    client, _, _ = make_client([
        FakeResponse(200, {}, headers={"X-Esi-Error-Limit-Remain": "1",
                                       "X-Esi-Error-Limit-Reset": "30"}),
    ])
    client._get("/x/")
    assert ESIClient._error_limit_remain == 1
    assert ESIClient._error_limit_reset_at > 0


def test_await_error_budget_waits_out_the_window(monkeypatch):
    """With <=2 errors left and the window still open, the next request must
    sleep for the rest of that window rather than spend the budget."""
    slept: list[float] = []
    monkeypatch.setattr(esi_client.time, "time", lambda: 1000.0)
    monkeypatch.setattr(esi_client.time, "sleep", lambda s: slept.append(s))
    ESIClient._error_limit_remain = 2
    ESIClient._error_limit_reset_at = 1015.0
    ESIClient._await_error_budget()
    assert slept == [15.0]


def test_await_error_budget_does_not_wait_with_budget_left(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(esi_client.time, "time", lambda: 1000.0)
    monkeypatch.setattr(esi_client.time, "sleep", lambda s: slept.append(s))
    ESIClient._error_limit_remain = 50
    ESIClient._error_limit_reset_at = 1015.0
    ESIClient._await_error_budget()
    assert slept == []


def test_await_error_budget_does_not_wait_on_an_expired_window(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(esi_client.time, "time", lambda: 2000.0)
    monkeypatch.setattr(esi_client.time, "sleep", lambda s: slept.append(s))
    ESIClient._error_limit_remain = 0
    ESIClient._error_limit_reset_at = 1015.0
    ESIClient._await_error_budget()
    assert slept == []


# --------------------------------------------------------------------------
# pagination
# --------------------------------------------------------------------------

def test_pagination_stops_at_one_page():
    client, session, _ = make_client([FakeResponse(200, [{"id": 1}])])  # no X-Pages header
    assert client._get_all_pages("/things/") == [{"id": 1}]
    assert len(session.calls) == 1


def test_pagination_fetches_every_page_in_order():
    def page(url, params):
        n = params["page"]
        return FakeResponse(200, [{"page": n}], headers={"X-Pages": "3"})

    client, session, _ = make_client([page, page, page])
    assert client._get_all_pages("/things/") == [{"page": 1}, {"page": 2}, {"page": 3}]
    assert len(session.calls) == 3


def test_pagination_short_circuits_on_an_empty_first_page():
    client, session, _ = make_client([FakeResponse(200, [], headers={"X-Pages": "5"})])
    assert client._get_all_pages("/things/") == []
    assert len(session.calls) == 1


# --------------------------------------------------------------------------
# endpoints
# --------------------------------------------------------------------------

def test_character_assets_sends_the_auth_header():
    client, session, tokens = make_client([FakeResponse(200, [{"item_id": 1}])])
    assert client.character_assets(42, auth_role="buyer") == [{"item_id": 1}]
    method, url, params, headers = session.calls[0]
    assert url == "https://esi.test/latest/characters/42/assets/"
    assert headers["Authorization"] == "Bearer token-for-buyer"
    assert params["page"] == 1
    assert tokens.roles == ["buyer"]


def test_region_order_stats_summarizes_and_caches():
    orders = [
        {"price": 50.0, "is_buy_order": False, "volume_remain": 4},
        {"price": 40.0, "is_buy_order": True, "volume_remain": 6},
    ]
    client, session, _ = make_client([FakeResponse(200, orders)])
    stats = client.region_order_stats(10000002, 34)
    assert stats.sell_percentile == 50.0
    assert stats.buy_percentile == 40.0
    assert stats.sell_volume == 4

    # Second call inside the TTL must not hit the network at all (the fake
    # session would raise on an unexpected extra request).
    assert client.region_order_stats(10000002, 34) == stats
    assert len(session.calls) == 1


def test_structure_order_stats_bulk_downloads_the_book_once():
    book = [
        {"type_id": 34, "price": 10.0, "is_buy_order": False, "volume_remain": 1},
        {"type_id": 35, "price": 20.0, "is_buy_order": False, "volume_remain": 2},
    ]
    client, session, _ = make_client([FakeResponse(200, book)])
    stats = client.structure_order_stats_bulk(1234, [34, 35, 36], auth_role="seller")
    assert stats[34].sell_percentile == 10.0
    assert stats[35].sell_volume == 2
    assert stats[36] == OrderStats(None, 0.0, None, 0.0)  # not on the book at all
    assert len(session.calls) == 1


def test_adjusted_prices_are_cached_class_wide():
    rows = [{"type_id": 34, "adjusted_price": 5.0}, {"type_id": 35}]  # no adjusted_price -> dropped
    client, session, _ = make_client([FakeResponse(200, rows)])
    assert client.get_adjusted_prices([34, 35]) == {34: 5.0, 35: 0}
    # A *different* instance must hit the same class-level cache.
    other, other_session, _ = make_client([])
    assert other.get_adjusted_prices()[34] == 5.0
    assert other_session.calls == []


def test_system_cost_indices_missing_system_raises():
    client, _, _ = make_client([FakeResponse(200, [
        {"solar_system_id": 30000142, "cost_indices": [{"activity": "manufacturing", "cost_index": 0.05}]},
    ])])
    assert client.get_system_cost_indices(30000142) == {"manufacturing": 0.05}
    with pytest.raises(ESIError, match="No cost indices"):
        client.get_system_cost_indices(30001234)


def test_resolve_names_chunks_and_maps():
    client, session, _ = make_client([
        FakeResponse(200, [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]),
    ])
    assert client.resolve_names([1, 2, 1]) == {1: "Alice", 2: "Bob"}
    assert session.last_post_body == [1, 2]  # duplicates collapsed before sending
    assert client.resolve_names([]) == {}


def test_resolve_system_id_matches_case_insensitively():
    client, _, _ = make_client([FakeResponse(200, {"systems": [{"id": 30000142, "name": "Jita"}]})])
    assert client.resolve_system_id(" jita ") == 30000142


# --------------------------------------------------------------------------
# effective volume (SDE fallback + packaged-volume cache)
# --------------------------------------------------------------------------

def test_effective_volume_passes_through_non_ship_categories(db, monkeypatch):
    monkeypatch.setattr(esi_client.storage, "get_type_category", lambda _t: 4)  # Material
    assert esi_client.resolve_effective_volume(34, 0.01) == 0.01


def test_effective_volume_fetches_and_caches_packaged_volume(db, monkeypatch):
    calls: list[int] = []

    class StubClient:
        def get_packaged_volume(self, type_id):
            calls.append(type_id)
            return 1000.0

    monkeypatch.setattr(esi_client, "ESIClient", lambda *a, **k: StubClient())
    assert esi_client.resolve_effective_volume(3829, 4000.0, category_id=7) == 1000.0
    # Cached in SQLite now - a second call must not re-fetch.
    assert esi_client.resolve_effective_volume(3829, 4000.0, category_id=7) == 1000.0
    assert calls == [3829]


def test_effective_volume_falls_back_without_caching_a_failure(db, monkeypatch):
    class FailingClient:
        def get_packaged_volume(self, type_id):
            raise ESIError("ESI is down")

    monkeypatch.setattr(esi_client, "ESIClient", lambda *a, **k: FailingClient())
    from eve_trader_local import storage
    assert esi_client.resolve_effective_volume(3829, 4000.0, category_id=6) == 4000.0
    assert storage.get_cached_packaged_volume(3829) is None


def test_effective_volume_bulk(db, monkeypatch):
    class StubClient:
        def get_packaged_volume(self, type_id):
            return 100.0

    monkeypatch.setattr(esi_client, "ESIClient", lambda *a, **k: StubClient())
    out = esi_client.resolve_effective_volume_bulk([
        (34, 0.01, 4),      # not a ship/module - untouched
        (3829, 4000.0, 6),  # ship - packaged
        (99, None, 6),      # no SDE volume at all
    ])
    assert out == {34: 0.01, 3829: 100.0, 99: None}
