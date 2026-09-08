"""Tests for esi_update.py - the 10-scope-group sync registry behind the
"Update Data" dialog. Each SCOPES entry's `run` callable is swapped out via
monkeypatch (same "replace the real bundle function" pattern the sync-bundle
tests in test_actions.py/test_production_esi_sync.py/etc. already use for
their own inner steps) - this file only tests the registry/orchestration
logic (intervals, due-ness, failure isolation), not any one tool's actual
ESI-calling bundle."""
from __future__ import annotations

import datetime as dt

import pytest

from eve_trader_local import esi_update, storage
from eve_trader_local.errors import ActionError


def _scope(key: str) -> esi_update.UpdateScope:
    return esi_update._SCOPES_BY_KEY[key]


# --------------------------------------------------------------------- SCOPES
def test_scopes_match_the_ten_scope_groups():
    assert {s.key for s in esi_update.SCOPES} == {
        "market_orders", "wallet", "assets", "industry_jobs", "blueprints",
        "contracts", "skills", "market_prices", "cost_indices", "candidate_universe"}


def test_sync_candidate_universe_builds_both_universe_and_focused(db, monkeypatch):
    """The scope a GUI-only user needs to bootstrap Trading's shortlist
    pipeline (see the module docstring's own "Candidate Universe" paragraph)
    - must run the same two steps the CLI's build-universe command does."""
    monkeypatch.setattr(esi_update.actions, "do_build_universe", lambda: {"count": 3})
    monkeypatch.setattr(esi_update.actions, "do_build_focused", lambda: {"count": 2})
    assert esi_update._sync_candidate_universe() == {
        "universe": {"count": 3}, "focused": {"count": 2}}


# ------------------------------------------------------------------ intervals
def test_interval_seconds_defaults_to_the_scope_default(db):
    assert esi_update.interval_seconds("market_orders") == _scope("market_orders").default_interval_seconds


def test_set_interval_seconds_overrides_just_one_scope(db):
    esi_update.set_interval_seconds("market_orders", 60)
    assert esi_update.interval_seconds("market_orders") == 60
    assert esi_update.interval_seconds("assets") == _scope("assets").default_interval_seconds


def test_set_interval_seconds_rejects_an_unknown_scope(db):
    with pytest.raises(ActionError, match="Unknown update scope"):
        esi_update.set_interval_seconds("not-a-real-scope", 60)


def test_set_interval_seconds_rejects_a_non_positive_value(db):
    with pytest.raises(ActionError, match="positive"):
        esi_update.set_interval_seconds("market_orders", 0)
    with pytest.raises(ActionError, match="positive"):
        esi_update.set_interval_seconds("market_orders", -5)


# ------------------------------------------------------------------- due-ness
def test_never_synced_is_always_due(db):
    assert esi_update.last_synced_at("market_orders") is None
    assert esi_update.next_allowed_at("market_orders") is None
    assert esi_update.is_due("market_orders") is True


def test_just_synced_is_not_due_until_the_interval_elapses(db):
    esi_update.set_interval_seconds("market_orders", 300)
    storage.set_esi_sync_time("market_orders", dt.datetime.utcnow().isoformat(timespec="seconds"))
    assert esi_update.is_due("market_orders") is False
    future = dt.datetime.utcnow() + dt.timedelta(seconds=301)
    assert esi_update.is_due("market_orders", now=future) is True


def test_last_synced_at_normalizes_aware_and_naive_timestamps_the_same_way(db):
    """Production's/Doctrine's own sync bundles can stamp an aware
    `datetime.now(timezone.utc)`; others stamp a naive `datetime.utcnow()` -
    both must compare correctly against a plain naive `now`."""
    naive = dt.datetime(2026, 1, 1, 12, 0, 0)
    aware = dt.datetime(2026, 1, 1, 12, 0, 0, tzinfo=dt.timezone.utc)
    storage.set_esi_sync_time("market_orders", naive.isoformat())
    storage.set_esi_sync_time("assets", aware.isoformat())
    assert esi_update.last_synced_at("market_orders") == naive
    assert esi_update.last_synced_at("assets") == naive
    assert esi_update.last_synced_at("market_orders").tzinfo is None
    assert esi_update.last_synced_at("assets").tzinfo is None


def test_all_status_reports_one_row_per_scope_in_order(db):
    statuses = esi_update.all_status()
    assert [s.key for s in statuses] == [s.key for s in esi_update.SCOPES]
    assert all(s.due for s in statuses)  # nothing has ever synced in this throwaway DB


# --------------------------------------------------------------- run_selected
def test_run_selected_calls_each_scope_and_returns_its_result(db, monkeypatch):
    monkeypatch.setattr(_scope("market_orders"), "run", lambda: {"ok": "market_orders"})
    monkeypatch.setattr(_scope("assets"), "run", lambda: {"ok": "assets"})
    results = esi_update.run_selected(["market_orders", "assets"])
    assert results["market_orders"] == {"ok": "market_orders"}
    assert results["assets"] == {"ok": "assets"}


def test_run_selected_isolates_an_action_error(db, monkeypatch):
    def boom():
        raise ActionError("no seller logged in")

    monkeypatch.setattr(_scope("market_orders"), "run", boom)
    monkeypatch.setattr(_scope("assets"), "run", lambda: {"ok": True})
    results = esi_update.run_selected(["market_orders", "assets"])
    assert results["market_orders"] == {"error": "no seller logged in"}
    assert results["assets"] == {"ok": True}


def test_run_selected_isolates_an_unexpected_exception(db, monkeypatch):
    def boom():
        raise RuntimeError("bug")

    monkeypatch.setattr(_scope("market_orders"), "run", boom)
    monkeypatch.setattr(_scope("assets"), "run", lambda: {"ok": True})
    results = esi_update.run_selected(["market_orders", "assets"])
    assert results["market_orders"] == {"error": "bug"}
    assert results["assets"] == {"ok": True}


def test_run_selected_rejects_an_unknown_scope(db):
    results = esi_update.run_selected(["not-a-real-scope"])
    assert "error" in results["not-a-real-scope"]


def test_run_selected_dedupes_assets_jobs_blueprints_within_one_run(db, monkeypatch):
    """assets/industry_jobs/blueprints all back onto the same expensive
    Production ESI pass (see _cached_sync_assets_jobs_blueprints's own
    comment) - selecting all three together must trigger it once, not once
    per scope, and a later run_selected() call must not reuse a stale
    result left over from an earlier one."""
    calls = []

    def fake_sync(oauth_cfg=None):
        calls.append(1)
        return {"ok": True}

    monkeypatch.setattr(esi_update.production_actions, "_sync_assets_jobs_blueprints", fake_sync)
    # _sync_assets also touches trading/doctrine - stub those out so this run
    # only exercises the dedup behavior, not unrelated real ESI calls.
    monkeypatch.setattr(esi_update.actions, "_cache_buyer_already_covered", lambda: {})
    monkeypatch.setattr(esi_update.doctrine_esi_sync, "sync_assets", lambda: {})

    esi_update.run_selected(["assets", "industry_jobs", "blueprints"], force=True)
    assert len(calls) == 1

    esi_update.run_selected(["industry_jobs"], force=True)
    assert len(calls) == 2


def test_run_selected_skips_a_scope_that_is_not_due_yet(db, monkeypatch):
    storage.set_esi_sync_time("market_orders", dt.datetime.utcnow().isoformat(timespec="seconds"))
    called = []
    monkeypatch.setattr(_scope("market_orders"), "run", lambda: called.append(True) or {"ok": True})
    results = esi_update.run_selected(["market_orders"])
    assert called == []
    assert results["market_orders"] == {"skipped": "not due yet"}


def test_run_selected_force_ignores_the_interval(db, monkeypatch):
    storage.set_esi_sync_time("market_orders", dt.datetime.utcnow().isoformat(timespec="seconds"))
    monkeypatch.setattr(_scope("market_orders"), "run", lambda: {"ok": True})
    results = esi_update.run_selected(["market_orders"], force=True)
    assert results["market_orders"] == {"ok": True}


# ------------------------------------------------------------------- describe
def test_describe_before_any_sync(db):
    assert "Never synced" in esi_update.describe("market_orders")


def test_describe_after_a_sync_names_the_next_update_time(db):
    esi_update.set_interval_seconds("market_orders", 300)
    storage.set_esi_sync_time("market_orders", dt.datetime.utcnow().isoformat(timespec="seconds"))
    assert "Data as of" in esi_update.describe("market_orders")
    assert "updates again in" in esi_update.describe("market_orders")


# -------------------------------------------------------------- describe_many
def test_describe_many_with_one_key_matches_describe(db):
    assert esi_update.describe_many(["market_orders"]) == esi_update.describe("market_orders")


def test_describe_many_reports_never_synced_scopes_first(db):
    esi_update.set_interval_seconds("market_orders", 300)
    storage.set_esi_sync_time("market_orders", dt.datetime.utcnow().isoformat(timespec="seconds"))
    # "assets" has never synced - that must dominate even though
    # "market_orders" just did.
    message = esi_update.describe_many(["market_orders", "assets"])
    assert "Never synced" in message
    assert "Assets" in message


def test_describe_many_reports_the_least_fresh_of_several_synced_scopes(db):
    esi_update.set_interval_seconds("market_orders", 300)
    esi_update.set_interval_seconds("assets", 1800)
    older = (dt.datetime.utcnow() - dt.timedelta(minutes=20)).isoformat(timespec="seconds")
    newer = dt.datetime.utcnow().isoformat(timespec="seconds")
    storage.set_esi_sync_time("market_orders", older)
    storage.set_esi_sync_time("assets", newer)
    message = esi_update.describe_many(["market_orders", "assets"])
    assert "Market Orders" in message
