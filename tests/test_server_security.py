"""Tests for the per-session local API token guard (issue #197).

The guard is enabled only when _API_TOKEN is set (i.e. coach.py startup).
Existing tests that don't set _API_TOKEN are unaffected — the guard is a no-op
when the token is None.
"""

import pytest
import asset_refresh
import web.server as server

_TOKEN = "test-token-abc123xyz"


@pytest.fixture()
def with_token(monkeypatch):
    monkeypatch.setattr(server, "_API_TOKEN", _TOKEN)
    monkeypatch.setattr(server, "_SERVER_PORT", 5555)


@pytest.fixture()
def client():
    return server.app.test_client()


# ── Missing / invalid token ───────────────────────────────────────────────────

def test_post_missing_token_returns_403(client, with_token):
    resp = client.post("/api/control/shutdown")
    assert resp.status_code == 403
    body = resp.get_json()
    assert body["ok"] is False
    assert "token" in body["error"].lower()


def test_post_wrong_token_returns_403(client, with_token):
    resp = client.post("/api/control/shutdown",
                       headers={"X-Bazaar-Coach-Token": "wrong-token"})
    assert resp.status_code == 403
    body = resp.get_json()
    assert body["ok"] is False


def test_assets_refresh_missing_token_403(client, with_token):
    resp = client.post("/api/assets/refresh?kind=builds")
    assert resp.status_code == 403


def test_updates_install_missing_token_403(client, with_token):
    resp = client.post("/api/updates/install")
    assert resp.status_code == 403


def test_updates_apply_portable_missing_token_403(client, with_token):
    resp = client.post("/api/updates/apply-portable")
    assert resp.status_code == 403


def test_user_build_put_missing_token_403(client, with_token):
    resp = client.put("/api/builds/user/Karnok",
                      json={"archetype": {}})
    assert resp.status_code == 403


def test_user_build_delete_missing_token_403(client, with_token):
    resp = client.delete("/api/builds/user/Karnok/my-arch")
    assert resp.status_code == 403


# ── Valid token passes guard ──────────────────────────────────────────────────

def test_post_valid_token_bypasses_guard(client, with_token):
    # Shutdown with no callback registered → 500, but NOT 403 (guard passed).
    resp = client.post("/api/control/shutdown",
                       headers={"X-Bazaar-Coach-Token": _TOKEN})
    assert resp.status_code != 403


def test_assets_refresh_valid_token_accepted(client, with_token, monkeypatch):
    monkeypatch.setattr(asset_refresh, "start_refresh",
                        lambda kind, trigger="manual": True)
    resp = client.post("/api/assets/refresh?kind=builds",
                       headers={"X-Bazaar-Coach-Token": _TOKEN})
    assert resp.status_code == 202


# ── Safe GET methods pass without token ──────────────────────────────────────

def test_get_overlay_state_no_token_allowed(client, with_token):
    resp = client.get("/api/overlay/state")
    assert resp.status_code != 403


def test_get_updates_status_no_token_allowed(client, with_token):
    resp = client.get("/api/updates/status")
    assert resp.status_code != 403


def test_get_assets_refresh_status_no_token_allowed(client, with_token):
    resp = client.get("/api/assets/refresh/status")
    assert resp.status_code != 403


# ── Guard disabled when no token configured ───────────────────────────────────

def test_guard_off_when_no_token_set(client, monkeypatch):
    monkeypatch.setattr(server, "_API_TOKEN", None)
    # No token header — guard is disabled, so route logic runs (not 403).
    resp = client.post("/api/control/shutdown")
    assert resp.status_code != 403


# ── Origin hardening ─────────────────────────────────────────────────────────

def test_suspicious_origin_rejected_even_with_valid_token(client, with_token):
    resp = client.post("/api/control/shutdown", headers={
        "X-Bazaar-Coach-Token": _TOKEN,
        "Origin": "http://evil.example.com",
    })
    assert resp.status_code == 403
    body = resp.get_json()
    assert body["ok"] is False


def test_local_127_origin_allowed(client, with_token):
    resp = client.post("/api/control/shutdown", headers={
        "X-Bazaar-Coach-Token": _TOKEN,
        "Origin": "http://127.0.0.1:5555",
    })
    assert resp.status_code != 403


def test_localhost_origin_allowed(client, with_token):
    resp = client.post("/api/control/shutdown", headers={
        "X-Bazaar-Coach-Token": _TOKEN,
        "Origin": "http://localhost:5555",
    })
    assert resp.status_code != 403


def test_no_origin_with_valid_token_allowed(client, with_token):
    # PyWebView / local clients omit Origin — must be allowed.
    resp = client.post("/api/control/shutdown",
                       headers={"X-Bazaar-Coach-Token": _TOKEN})
    assert resp.status_code != 403


def test_suspicious_origin_missing_token_rejected(client, with_token):
    resp = client.post("/api/control/shutdown", headers={
        "Origin": "http://evil.example.com",
    })
    assert resp.status_code == 403
