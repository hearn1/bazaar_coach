"""Unit tests for the transport-agnostic asset_refresh helper (issue #175).

These exercise the runners and state machine directly (no live threads) so the
behavior is deterministic: each kind triggers the right backend function,
failures are recorded rather than raised, unknown/duplicate kinds are rejected,
and the status payload shape is stable.
"""

import pytest

import asset_refresh
import card_cache
import refresh_builds
from refresh_builds import HeroRefreshResult


@pytest.fixture(autouse=True)
def reset_state():
    """Reset module-level refresh state around each test."""
    asset_refresh._state = {
        kind: {"running": False, "last_result": None} for kind in asset_refresh.KINDS
    }
    yield
    asset_refresh._state = {
        kind: {"running": False, "last_result": None} for kind in asset_refresh.KINDS
    }


# ── runners ────────────────────────────────────────────────────────────────────

def test_run_builds_updated(monkeypatch):
    results = [
        HeroRefreshResult("Karnok", "karnok_builds.json", "updated", "new"),
        HeroRefreshResult("Mak", "mak_builds.json", "unchanged", "same"),
    ]
    monkeypatch.setattr(refresh_builds, "refresh_builds", lambda: results)
    payload = asset_refresh._run_builds()
    assert payload["status"] == "updated"
    assert payload["ok"] is True
    assert payload["updated"] == 1
    assert "1 catalog" in payload["message"]


def test_run_builds_skipped_reports_failure(monkeypatch):
    results = [HeroRefreshResult("Karnok", "karnok_builds.json", "skipped", "bad json")]
    monkeypatch.setattr(refresh_builds, "refresh_builds", lambda: results)
    payload = asset_refresh._run_builds()
    assert payload["status"] == "failed"
    assert payload["ok"] is False
    assert payload["skipped"] == 1


def test_run_content_updated(monkeypatch):
    summary = {
        "status": "ok",
        "endpoints_fetched": ["items", "skills"],
        "endpoint_diff": {"added": [], "changed": ["items"], "unchanged": ["skills"]},
        "card_diff": {"added_count": 3, "removed_count": 0, "changed_count": 1},
        "warnings": [],
        "cards": [{"x": 1}],  # should be stripped from the status payload
    }
    monkeypatch.setattr(card_cache, "refresh_cache", lambda **kw: summary)
    payload = asset_refresh._run_content()
    assert payload["status"] == "updated"
    assert payload["ok"] is True
    assert "cards" not in payload
    assert "3 added" in payload["message"]


def test_run_content_unchanged(monkeypatch):
    summary = {
        "status": "ok",
        "endpoints_fetched": ["items"],
        "endpoint_diff": {"added": [], "changed": [], "unchanged": ["items"]},
        "card_diff": {"added_count": 0, "removed_count": 0, "changed_count": 0},
        "warnings": [],
    }
    monkeypatch.setattr(card_cache, "refresh_cache", lambda **kw: summary)
    payload = asset_refresh._run_content()
    assert payload["status"] == "unchanged"
    assert payload["ok"] is True


def test_run_content_no_endpoints_is_skipped(monkeypatch):
    summary = {
        "status": "warn",
        "endpoints_fetched": [],
        "endpoint_diff": {},
        "card_diff": {},
        "warnings": ["No static endpoints were refreshed; previous cache remains active."],
    }
    monkeypatch.setattr(card_cache, "refresh_cache", lambda **kw: summary)
    payload = asset_refresh._run_content()
    assert payload["status"] == "skipped"
    assert payload["ok"] is False


# ── worker: failures recorded, not raised ───────────────────────────────────────

def test_worker_records_failure(monkeypatch):
    def boom():
        raise RuntimeError("network down")

    monkeypatch.setattr(refresh_builds, "refresh_builds", boom)
    asset_refresh._worker("builds", "manual")  # must not raise
    entry = asset_refresh._state["builds"]
    assert entry["running"] is False
    result = entry["last_result"]
    assert result["status"] == "failed"
    assert result["ok"] is False
    assert "network down" in result["error"]
    assert result["trigger"] == "manual"
    assert result["checked_at"]


def test_worker_clears_running_on_success(monkeypatch):
    monkeypatch.setattr(refresh_builds, "refresh_builds", lambda: [])
    asset_refresh._state["builds"]["running"] = True
    asset_refresh._worker("builds", "manual")
    assert asset_refresh._state["builds"]["running"] is False
    assert asset_refresh._state["builds"]["last_result"]["status"] == "unchanged"


# ── start_refresh: unknown kind + concurrency rejection ──────────────────────────

def test_start_refresh_unknown_kind():
    assert asset_refresh.start_refresh("images") is False
    assert asset_refresh.start_refresh("bogus") is False


def test_start_refresh_rejects_when_running():
    asset_refresh._state["builds"]["running"] = True
    # Already running -> rejected without spawning a thread.
    assert asset_refresh.start_refresh("builds") is False


def test_start_refresh_starts_and_completes(monkeypatch):
    monkeypatch.setattr(refresh_builds, "refresh_builds", lambda: [])
    assert asset_refresh.start_refresh("builds") is True
    # Daemon thread should finish quickly; join via the worker name.
    import threading
    for t in threading.enumerate():
        if t.name.startswith("asset-refresh-builds"):
            t.join(timeout=5)
    assert asset_refresh._state["builds"]["running"] is False
    assert asset_refresh._state["builds"]["last_result"]["status"] == "unchanged"


# ── status payload shape ─────────────────────────────────────────────────────────

def test_status_all_kinds_shape():
    payload = asset_refresh.status()
    assert set(payload["kinds"].keys()) == set(asset_refresh.KINDS)
    for entry in payload["kinds"].values():
        assert "running" in entry
        assert "last_result" in entry


def test_status_single_kind_shape():
    payload = asset_refresh.status("content")
    assert payload["running"] is False
    assert payload["last_result"] is None


def test_status_unknown_kind_is_empty():
    payload = asset_refresh.status("nope")
    assert payload == {"running": False, "last_result": None}
