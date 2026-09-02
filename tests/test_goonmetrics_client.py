"""Goonmetrics current-price client, plus the one ESIClient method that
falls back to it.

Nothing here touches the network - `requests.Session.get` is the only seam
the real code uses, so every test swaps `GoonmetricsClient.session` for the
same FakeSession shape the ESI/SDE tests use. The module-level price cache is
reset between tests (autouse fixture below): it deliberately outlives any
client instance, so without that reset one test's fetch would satisfy the
next test's cache check.
"""
from __future__ import annotations

import threading
import time

import pytest
import requests

from eve_trader_local import goonmetrics_client
from eve_trader_local.config import TradingConfig
from eve_trader_local.esi_client import ESIClient, ESIError, OrderStats
from eve_trader_local.goonmetrics_client import CurrentPrice, GoonmetricsClient


class FakeResponse:
    def __init__(self, status_code: int = 200, json_body=None):
        self.status_code = status_code
        self._json = json_body

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeSession:
    """Replays `responses` in order, one per request. A response may instead
    be a callable taking (url) so a test can vary its answer per call."""

    def __init__(self, responses: list):
        self.responses = list(responses)
        self.headers: dict = {}
        self.calls: list[str] = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(url)
        if not self.responses:
            raise AssertionError(f"unexpected extra GET for {url}")
        item = self.responses.pop(0)
        return item(url) if callable(item) else item


def _row(type_id: int, buy: float, sell: float, updated: str = "2026-09-02T00:00:00"):
    return {"typeID": type_id,
            "prices": {"updated": updated, "buy": {"max": buy}, "sell": {"min": sell}}}


@pytest.fixture(autouse=True)
def _clear_cache():
    goonmetrics_client.clear_prices_cache()
    yield
    goonmetrics_client.clear_prices_cache()


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(goonmetrics_client.time, "sleep", lambda s: None)


def _client(responses, cfg: TradingConfig | None = None) -> GoonmetricsClient:
    client = GoonmetricsClient(cfg or TradingConfig())
    client.session = FakeSession(responses)
    return client


# ----------------------------------------------------------- current_prices
def test_parses_and_sorts_by_type_id():
    client = _client([FakeResponse(json_body=[_row(999, 1.0, 2.0), _row(34, 4.5, 5.5)])])

    prices = client.current_prices("jita")

    assert prices == [CurrentPrice(type_id=34, updated="2026-09-02T00:00:00", buy=4.5, sell=5.5),
                      CurrentPrice(type_id=999, updated="2026-09-02T00:00:00", buy=1.0, sell=2.0)]
    assert client.session.calls == ["https://appraise.gnf.lt/market/jita/prices.json"]


def test_uses_configured_appraise_base():
    cfg = TradingConfig()
    cfg.goonmetrics_appraise_base = "https://example.test"
    client = _client([FakeResponse(json_body=[])], cfg)

    client.current_prices("my-structure")

    assert client.session.calls == ["https://example.test/market/my-structure/prices.json"]


def test_second_call_within_ttl_is_served_from_the_cache():
    client = _client([FakeResponse(json_body=[_row(34, 4.5, 5.5)])])

    first = client.current_prices("jita")
    second = client.current_prices("jita")  # FakeSession would raise on a second GET

    assert first == second
    assert len(client.session.calls) == 1


def test_a_fresh_client_instance_still_hits_the_module_level_cache():
    """The cache deliberately lives at module scope, not on the instance -
    callers construct a fresh client per call site."""
    _client([FakeResponse(json_body=[_row(34, 4.5, 5.5)])]).current_prices("jita")

    prices = _client([]).current_prices("jita")  # no responses left: a real fetch would raise

    assert prices == [CurrentPrice(type_id=34, updated="2026-09-02T00:00:00", buy=4.5, sell=5.5)]


def test_cached_list_is_a_copy_callers_cannot_mutate():
    client = _client([FakeResponse(json_body=[_row(34, 4.5, 5.5)])])

    client.current_prices("jita").clear()

    assert len(client.current_prices("jita")) == 1


def test_expired_cache_entry_is_refetched(monkeypatch):
    client = _client([FakeResponse(json_body=[_row(34, 4.5, 5.5)]),
                      FakeResponse(json_body=[_row(34, 9.0, 9.5)])])
    client.current_prices("jita")
    later = time.time() + goonmetrics_client._PRICES_CACHE_TTL + 1
    monkeypatch.setattr(goonmetrics_client.time, "time", lambda: later)

    assert client.current_prices("jita")[0].buy == 9.0


def test_markets_have_independent_cache_entries():
    client = _client([FakeResponse(json_body=[_row(34, 4.5, 5.5)]),
                      FakeResponse(json_body=[_row(34, 6.5, 7.5)])])

    jita = client.current_prices("jita")
    home = client.current_prices("my-structure")

    assert (jita[0].buy, home[0].buy) == (4.5, 6.5)
    assert len(client.session.calls) == 2
    assert set(goonmetrics_client._prices_cache) == {"jita", "my-structure"}


def test_each_market_gets_its_own_lock_so_they_never_serialize():
    """One shared lock would make an in-flight multi-second Jita fetch block
    an unrelated home-market fetch that shares no cache entry with it."""
    jita_lock = goonmetrics_client._lock_for_market("jita")
    home_lock = goonmetrics_client._lock_for_market("my-structure")

    assert jita_lock is not home_lock
    assert goonmetrics_client._lock_for_market("jita") is jita_lock


def test_a_held_lock_does_not_block_another_market(monkeypatch):
    goonmetrics_client._lock_for_market("jita").acquire()
    try:
        client = _client([FakeResponse(json_body=[_row(34, 6.5, 7.5)])])
        done = threading.Event()

        def _fetch():
            client.current_prices("my-structure")
            done.set()

        threading.Thread(target=_fetch, daemon=True).start()
        assert done.wait(timeout=5), "an unrelated market's fetch blocked on the jita lock"
    finally:
        goonmetrics_client._lock_for_market("jita").release()


def test_retries_before_giving_up():
    client = _client([FakeResponse(status_code=503),
                      FakeResponse(status_code=503),
                      FakeResponse(json_body=[_row(34, 4.5, 5.5)])])

    assert client.current_prices("jita")[0].buy == 4.5
    assert len(client.session.calls) == 3


def test_raises_the_last_error_after_three_failed_attempts():
    client = _client([FakeResponse(status_code=503)] * 3)

    with pytest.raises(requests.RequestException):
        client.current_prices("jita")
    assert len(client.session.calls) == 3


def test_a_failed_fetch_does_not_populate_the_cache():
    client = _client([FakeResponse(status_code=503)] * 3)
    with pytest.raises(requests.RequestException):
        client.current_prices("jita")

    assert "jita" not in goonmetrics_client._prices_cache


# ---------------------------------- structure_order_stats_bulk_or_goonmetrics
def test_uses_real_order_book_when_a_seller_is_logged_in(monkeypatch):
    real_stats = {34: OrderStats(sell_percentile=5.5, sell_volume=100.0,
                                 buy_percentile=5.0, buy_volume=50.0)}
    monkeypatch.setattr(ESIClient, "structure_order_stats_bulk",
                        lambda self, structure_id, type_ids, auth_role: real_stats)

    def _boom(*args, **kwargs):
        raise AssertionError("must not call Goonmetrics when the real order book succeeds")
    monkeypatch.setattr(GoonmetricsClient, "current_prices", _boom)

    stats, used_fallback = ESIClient().structure_order_stats_bulk_or_goonmetrics(
        1000, [34], auth_role="seller", goonmetrics_market_slug="my-structure")

    assert stats == real_stats
    assert used_fallback is False


def test_falls_back_when_no_seller_is_logged_in(monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("must not call the real order book with auth_role=None")
    monkeypatch.setattr(ESIClient, "structure_order_stats_bulk", _boom)
    monkeypatch.setattr(GoonmetricsClient, "current_prices",
                        lambda self, market: [CurrentPrice(type_id=34, updated="now", buy=4.5, sell=5.5)])

    stats, used_fallback = ESIClient().structure_order_stats_bulk_or_goonmetrics(
        1000, [34], auth_role=None, goonmetrics_market_slug="my-structure")

    assert used_fallback is True
    assert stats == {34: OrderStats(sell_percentile=5.5, sell_volume=0.0,
                                    buy_percentile=4.5, buy_volume=0.0)}


def test_falls_back_when_the_real_esi_call_fails(monkeypatch):
    monkeypatch.setattr(ESIClient, "structure_order_stats_bulk",
                        lambda self, structure_id, type_ids, auth_role: (_ for _ in ()).throw(ESIError("403")))
    monkeypatch.setattr(GoonmetricsClient, "current_prices",
                        lambda self, market: [CurrentPrice(type_id=34, updated="now", buy=4.5, sell=5.5)])

    stats, used_fallback = ESIClient().structure_order_stats_bulk_or_goonmetrics(
        1000, [34], auth_role="seller", goonmetrics_market_slug="my-structure")

    assert used_fallback is True
    assert stats[34].sell_percentile == 5.5


def test_filters_the_fallback_response_to_the_requested_type_ids(monkeypatch):
    monkeypatch.setattr(GoonmetricsClient, "current_prices", lambda self, market: [
        CurrentPrice(type_id=34, updated="now", buy=4.5, sell=5.5),
        CurrentPrice(type_id=999, updated="now", buy=1.0, sell=2.0),
    ])

    stats, _ = ESIClient().structure_order_stats_bulk_or_goonmetrics(
        1000, [34], auth_role=None, goonmetrics_market_slug="my-structure")

    assert set(stats) == {34}


def test_a_zero_price_becomes_none_not_zero(monkeypatch):
    """A missing side comes back as 0.0 from the API; callers treat a price of
    None as "unknown", which is what an absent quote actually means."""
    monkeypatch.setattr(GoonmetricsClient, "current_prices",
                        lambda self, market: [CurrentPrice(type_id=34, updated="now", buy=0.0, sell=5.5)])

    stats, _ = ESIClient().structure_order_stats_bulk_or_goonmetrics(
        1000, [34], auth_role=None, goonmetrics_market_slug="my-structure")

    assert stats[34].buy_percentile is None


def test_raises_when_no_seller_and_no_fallback_market_configured():
    with pytest.raises(ESIError, match="No seller/producer character logged in"):
        ESIClient().structure_order_stats_bulk_or_goonmetrics(
            1000, [34], auth_role=None, goonmetrics_market_slug=None)


def test_raises_the_original_esi_error_when_no_fallback_market_configured(monkeypatch):
    monkeypatch.setattr(ESIClient, "structure_order_stats_bulk",
                        lambda self, structure_id, type_ids, auth_role: (_ for _ in ()).throw(ESIError("403 boom")))

    with pytest.raises(ESIError, match="403 boom"):
        ESIClient().structure_order_stats_bulk_or_goonmetrics(
            1000, [34], auth_role="seller", goonmetrics_market_slug=None)


def test_raises_when_both_sources_fail(monkeypatch):
    monkeypatch.setattr(ESIClient, "structure_order_stats_bulk",
                        lambda self, structure_id, type_ids, auth_role: (_ for _ in ()).throw(ESIError("403 boom")))
    monkeypatch.setattr(GoonmetricsClient, "current_prices",
                        lambda self, market: (_ for _ in ()).throw(requests.ConnectionError("offline")))

    with pytest.raises(ESIError, match="403 boom"):
        ESIClient().structure_order_stats_bulk_or_goonmetrics(
            1000, [34], auth_role="seller", goonmetrics_market_slug="my-structure")
