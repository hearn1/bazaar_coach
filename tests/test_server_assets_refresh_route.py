"""Tests for the generic asset-refresh routes (issue #175).

POST /api/assets/refresh?kind=... starts a refresh via the asset_refresh helper;
GET /api/assets/refresh/status reports per-kind state. The legacy builds routes
must keep working (the dashboard depends on them). start_refresh is monkeypatched
so no real network refresh / background thread fires during tests.
"""

import pytest

import asset_refresh
import web.server as server


@pytest.fixture()
def client():
    return server.app.test_client()


@pytest.fixture()
def stub_start(monkeypatch):
    """Record start_refresh calls without spawning real refresh threads."""
    calls = []

    def fake_start(kind, trigger="manual"):
        calls.append((kind, trigger))
        return True

    monkeypatch.setattr(asset_refresh, "start_refresh", fake_start)
    return calls


# ── generic status ──────────────────────────────────────────────────────────────

def test_assets_status_lists_all_kinds(client):
    resp = client.get("/api/assets/refresh/status")
    assert resp.status_code == 200
    body = resp.get_json()
    assert set(body["kinds"].keys()) == set(asset_refresh.KINDS)
    # Builds entry carries catalog notes for dashboard parity.
    assert "catalogs" in body["kinds"]["builds"]


def test_assets_status_single_kind(client):
    resp = client.get("/api/assets/refresh/status?kind=content")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "running" in body and "last_result" in body


def test_assets_status_unknown_kind_400(client):
    resp = client.get("/api/assets/refresh/status?kind=images")
    assert resp.status_code == 400


# ── generic POST ──────────────────────────────────────────────────────────────

def test_assets_refresh_builds_202(client, stub_start):
    resp = client.post("/api/assets/refresh?kind=builds")
    assert resp.status_code == 202
    assert stub_start == [("builds", "manual")]


def test_assets_refresh_content_202(client, stub_start):
    resp = client.post("/api/assets/refresh?kind=content")
    assert resp.status_code == 202
    assert stub_start == [("content", "manual")]


def test_assets_refresh_unknown_kind_400(client, stub_start):
    resp = client.post("/api/assets/refresh?kind=images")
    assert resp.status_code == 400
    assert stub_start == []  # never reached the helper


def test_assets_refresh_already_running_409(client, monkeypatch):
    monkeypatch.setattr(asset_refresh, "start_refresh", lambda kind, trigger="manual": False)
    resp = client.post("/api/assets/refresh?kind=builds")
    assert resp.status_code == 409
    assert "already running" in resp.get_json()["error"]


# ── legacy builds routes: no regression ──────────────────────────────────────────

def test_legacy_builds_refresh_202(client, stub_start):
    resp = client.post("/api/builds/refresh")
    assert resp.status_code == 202
    assert stub_start == [("builds", "manual")]


def test_legacy_builds_status_has_catalogs(client):
    resp = client.get("/api/builds/refresh/status")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "running" in body
    assert "last_result" in body
    assert "catalogs" in body
