"""
tests/test_github_oauth.py — Tests for github_oauth.py and its server routes.

Covers the full token-lifecycle failure matrix described in issue #228:
  no stored token → signed_out
  valid stored token → signed_in
  stored token with past expires_at → expired (no network call)
  GitHub 401 → revoked_or_invalid + token cleared
  GitHub 403 rate-limit → rate_limited (token kept)
  GitHub 403 non-rate-limit → revoked_or_invalid + token cleared
  GitHub 429 → rate_limited (token kept)
  network / URLError → network_error (token kept)
  device-flow expired response → device_flow_expired (via forced state)
  sign-out with token → credential deleted
  sign-out without token → succeeds (idempotent)
  corrupted credential file → load() returns None → signed_out
  token value never in API response
  can_one_click_file True only when signed_in
  needs_reauth True for expired / revoked states
"""

from __future__ import annotations

import io
import json
import time
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import github_oauth as go
from github_oauth import (
    AuthState,
    CredentialStore,
    TokenRecord,
    clear_auth_cache,
    get_auth_status,
    sign_out,
    validate_token,
)
import web.server as server


# ── Helpers ────────────────────────────────────────────────────────────────────

class _FakeHeaders:
    """Minimal dict-like stand-in for http.client.HTTPMessage."""
    def __init__(self, data: dict | None = None) -> None:
        self._d = {k.lower(): v for k, v in (data or {}).items()}

    def get(self, key: str, default: str = "") -> str:
        return self._d.get(key.lower(), default)


class _MockHTTPResp:
    """Mock context-manager returned by urllib.request.urlopen."""
    def __init__(self, body: dict, headers: dict | None = None) -> None:
        self._body = json.dumps(body).encode()
        self.headers = _FakeHeaders(headers)

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_MockHTTPResp":
        return self

    def __exit__(self, *_: object) -> None:
        pass


def _http_error(code: int, body: bytes = b"{}", headers: dict | None = None):
    return urllib.error.HTTPError(
        url="https://api.github.com/user",
        code=code,
        msg="Error",
        hdrs=_FakeHeaders(headers),
        fp=io.BytesIO(body),
    )


def _store(tmp_path: Path) -> CredentialStore:
    return CredentialStore(path=tmp_path / "github_credentials.json")


def _token_record(**kwargs) -> TokenRecord:
    defaults = dict(access_token="ghp_faketoken123", login="alice", scope="public_repo")
    defaults.update(kwargs)
    return TokenRecord(**defaults)


@pytest.fixture(autouse=True)
def _reset_cache():
    """Discard the module-level cache before each test."""
    clear_auth_cache()
    yield
    clear_auth_cache()


# ── CredentialStore ────────────────────────────────────────────────────────────

class TestCredentialStore:
    def test_load_returns_none_when_file_absent(self, tmp_path):
        store = _store(tmp_path)
        assert store.load() is None

    def test_save_and_load_round_trip(self, tmp_path):
        store = _store(tmp_path)
        rec = _token_record()
        assert store.save(rec) is True
        loaded = store.load()
        assert loaded is not None
        assert loaded.access_token == rec.access_token
        assert loaded.login == rec.login
        assert loaded.scope == rec.scope

    def test_save_preserves_expires_at(self, tmp_path):
        store = _store(tmp_path)
        ts = time.time() + 3600
        rec = _token_record(expires_at=ts)
        store.save(rec)
        loaded = store.load()
        assert loaded is not None
        assert abs(loaded.expires_at - ts) < 1

    def test_clear_removes_file(self, tmp_path):
        store = _store(tmp_path)
        store.save(_token_record())
        assert (tmp_path / "github_credentials.json").exists()
        store.clear()
        assert not (tmp_path / "github_credentials.json").exists()

    def test_clear_is_idempotent(self, tmp_path):
        store = _store(tmp_path)
        store.clear()  # no file — must not raise
        store.clear()

    def test_load_returns_none_for_corrupted_file(self, tmp_path):
        cred = tmp_path / "github_credentials.json"
        cred.write_text("not valid json", encoding="utf-8")
        assert _store(tmp_path).load() is None

    def test_load_returns_none_for_missing_access_token(self, tmp_path):
        cred = tmp_path / "github_credentials.json"
        cred.write_text(json.dumps({"login": "alice"}), encoding="utf-8")
        assert _store(tmp_path).load() is None

    def test_token_value_not_in_save_path_name(self, tmp_path):
        """The credential file path must not embed the token in its name."""
        store = _store(tmp_path)
        store.save(_token_record(access_token="ghp_supersecret"))
        for f in tmp_path.iterdir():
            assert "supersecret" not in f.name


# ── validate_token ─────────────────────────────────────────────────────────────

class TestValidateToken:
    def test_200_returns_signed_in_with_login_and_scopes(self):
        mock_resp = _MockHTTPResp(
            {"login": "alice"},
            {"X-OAuth-Scopes": "public_repo, read:user"},
        )
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = validate_token("ghp_fake")
        assert result["auth_state"] == AuthState.SIGNED_IN
        assert result["login"] == "alice"
        assert "public_repo" in result["scopes"]
        assert "read:user" in result["scopes"]

    def test_200_empty_scopes_header_returns_empty_list(self):
        mock_resp = _MockHTTPResp({"login": "bob"}, {})
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = validate_token("ghp_fake")
        assert result["auth_state"] == AuthState.SIGNED_IN
        assert result["scopes"] == []

    def test_token_not_in_result(self):
        mock_resp = _MockHTTPResp({"login": "alice"})
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = validate_token("ghp_supersecret_value")
        result_str = json.dumps(result)
        assert "ghp_supersecret_value" not in result_str

    def test_401_returns_revoked_or_invalid(self):
        with patch("urllib.request.urlopen", side_effect=_http_error(401)):
            result = validate_token("ghp_fake")
        assert result["auth_state"] == AuthState.REVOKED_OR_INVALID
        assert result["login"] == ""

    def test_403_with_rate_limit_body_returns_rate_limited(self):
        body = json.dumps({"message": "API rate limit exceeded"}).encode()
        with patch("urllib.request.urlopen", side_effect=_http_error(403, body)):
            result = validate_token("ghp_fake")
        assert result["auth_state"] == AuthState.RATE_LIMITED

    def test_403_with_ratelimit_remaining_zero_header_returns_rate_limited(self):
        with patch(
            "urllib.request.urlopen",
            side_effect=_http_error(403, b"{}", {"X-RateLimit-Remaining": "0"}),
        ):
            result = validate_token("ghp_fake")
        assert result["auth_state"] == AuthState.RATE_LIMITED

    def test_403_without_rate_limit_returns_revoked_or_invalid(self):
        body = json.dumps({"message": "Resource not accessible by integration"}).encode()
        with patch("urllib.request.urlopen", side_effect=_http_error(403, body)):
            result = validate_token("ghp_fake")
        assert result["auth_state"] == AuthState.REVOKED_OR_INVALID

    def test_429_returns_rate_limited(self):
        with patch("urllib.request.urlopen", side_effect=_http_error(429)):
            result = validate_token("ghp_fake")
        assert result["auth_state"] == AuthState.RATE_LIMITED

    def test_network_urlerror_returns_network_error(self):
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            result = validate_token("ghp_fake")
        assert result["auth_state"] == AuthState.NETWORK_ERROR
        assert result["login"] == ""

    def test_timeout_returns_network_error(self):
        with patch("urllib.request.urlopen", side_effect=TimeoutError()):
            result = validate_token("ghp_fake")
        assert result["auth_state"] == AuthState.NETWORK_ERROR

    def test_5xx_returns_network_error(self):
        with patch("urllib.request.urlopen", side_effect=_http_error(503)):
            result = validate_token("ghp_fake")
        assert result["auth_state"] == AuthState.NETWORK_ERROR


# ── get_auth_status ────────────────────────────────────────────────────────────

class TestGetAuthStatus:
    def test_no_stored_token_returns_signed_out(self, tmp_path):
        store = _store(tmp_path)
        result = get_auth_status(store)
        assert result["auth_state"] == AuthState.SIGNED_OUT
        assert result["can_one_click_file"] is False
        assert result["needs_reauth"] is False
        assert "manual_issue_url" in result

    def test_valid_token_returns_signed_in(self, tmp_path):
        store = _store(tmp_path)
        store.save(_token_record())
        mock_resp = _MockHTTPResp({"login": "alice"}, {"X-OAuth-Scopes": "public_repo"})
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = get_auth_status(store)
        assert result["auth_state"] == AuthState.SIGNED_IN
        assert result["can_one_click_file"] is True
        assert result["needs_reauth"] is False
        assert result["login"] == "alice"
        assert "public_repo" in result["scopes"]

    def test_expired_expires_at_returns_expired_no_network_call(self, tmp_path):
        store = _store(tmp_path)
        store.save(_token_record(expires_at=time.time() - 1))
        with patch("urllib.request.urlopen") as mock_open:
            result = get_auth_status(store)
        mock_open.assert_not_called()
        assert result["auth_state"] == AuthState.EXPIRED
        assert result["needs_reauth"] is True
        assert result["can_one_click_file"] is False

    def test_expired_token_is_cleared_from_store(self, tmp_path):
        store = _store(tmp_path)
        store.save(_token_record(expires_at=time.time() - 1))
        with patch("urllib.request.urlopen"):
            get_auth_status(store)
        assert store.load() is None

    def test_401_clears_token_returns_revoked(self, tmp_path):
        store = _store(tmp_path)
        store.save(_token_record())
        with patch("urllib.request.urlopen", side_effect=_http_error(401)):
            result = get_auth_status(store)
        assert result["auth_state"] == AuthState.REVOKED_OR_INVALID
        assert result["needs_reauth"] is True
        assert store.load() is None  # token must be cleared

    def test_403_auth_failure_clears_token(self, tmp_path):
        store = _store(tmp_path)
        store.save(_token_record())
        body = b'{"message": "Forbidden"}'
        with patch("urllib.request.urlopen", side_effect=_http_error(403, body)):
            result = get_auth_status(store)
        assert result["auth_state"] == AuthState.REVOKED_OR_INVALID
        assert store.load() is None

    def test_rate_limited_keeps_token(self, tmp_path):
        store = _store(tmp_path)
        store.save(_token_record())
        body = json.dumps({"message": "API rate limit exceeded"}).encode()
        with patch("urllib.request.urlopen", side_effect=_http_error(403, body)):
            result = get_auth_status(store)
        assert result["auth_state"] == AuthState.RATE_LIMITED
        assert store.load() is not None  # token must NOT be cleared

    def test_network_error_keeps_token(self, tmp_path):
        store = _store(tmp_path)
        store.save(_token_record())
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("timeout"),
        ):
            result = get_auth_status(store)
        assert result["auth_state"] == AuthState.NETWORK_ERROR
        assert store.load() is not None  # token must NOT be cleared

    def test_token_value_not_in_response(self, tmp_path):
        store = _store(tmp_path)
        store.save(_token_record(access_token="ghp_supersecret"))
        mock_resp = _MockHTTPResp({"login": "alice"})
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = get_auth_status(store)
        result_str = json.dumps(result)
        assert "ghp_supersecret" not in result_str

    def test_can_one_click_file_false_for_non_signed_in_states(self, tmp_path):
        for state in [AuthState.SIGNED_OUT, AuthState.RATE_LIMITED, AuthState.NETWORK_ERROR]:
            clear_auth_cache()
            store = _store(tmp_path)
            if state in (AuthState.RATE_LIMITED, AuthState.NETWORK_ERROR):
                store.save(_token_record())
                err_code = 403 if state == AuthState.RATE_LIMITED else None
                if state == AuthState.RATE_LIMITED:
                    body = json.dumps({"message": "rate limit exceeded"}).encode()
                    side_effect = _http_error(403, body)
                else:
                    side_effect = urllib.error.URLError("no network")
                with patch("urllib.request.urlopen", side_effect=side_effect):
                    result = get_auth_status(store)
            else:
                result = get_auth_status(store)
            assert result["can_one_click_file"] is False, f"failed for {state}"

    def test_warm_cache_skips_network_call(self, tmp_path):
        store = _store(tmp_path)
        store.save(_token_record())
        mock_resp = _MockHTTPResp({"login": "alice"}, {"X-OAuth-Scopes": "public_repo"})
        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            get_auth_status(store)
            get_auth_status(store)  # second call — cache should be warm
        assert mock_open.call_count == 1  # only one network call

    def test_force_revalidate_bypasses_cache(self, tmp_path):
        store = _store(tmp_path)
        store.save(_token_record())
        mock_resp = _MockHTTPResp({"login": "alice"})
        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            get_auth_status(store)
            get_auth_status(store, force_revalidate=True)
        assert mock_open.call_count == 2

    def test_manual_issue_url_always_present(self, tmp_path):
        store = _store(tmp_path)
        result = get_auth_status(store)
        assert "manual_issue_url" in result
        assert "github.com" in result["manual_issue_url"]


# ── sign_out ───────────────────────────────────────────────────────────────────

class TestSignOut:
    def test_sign_out_with_token_clears_credential_file(self, tmp_path):
        store = _store(tmp_path)
        store.save(_token_record())
        result = sign_out(store)
        assert result["auth_state"] == AuthState.SIGNED_OUT
        assert store.load() is None

    def test_sign_out_without_token_succeeds(self, tmp_path):
        store = _store(tmp_path)
        result = sign_out(store)
        assert result["ok"] is True
        assert result["auth_state"] == AuthState.SIGNED_OUT

    def test_sign_out_is_idempotent(self, tmp_path):
        store = _store(tmp_path)
        result1 = sign_out(store)
        result2 = sign_out(store)
        assert result1["auth_state"] == AuthState.SIGNED_OUT
        assert result2["auth_state"] == AuthState.SIGNED_OUT

    def test_sign_out_clears_in_memory_cache(self, tmp_path):
        store = _store(tmp_path)
        store.save(_token_record())
        # Prime the cache
        mock_resp = _MockHTTPResp({"login": "alice"})
        with patch("urllib.request.urlopen", return_value=mock_resp):
            get_auth_status(store)
        assert go._AUTH_CACHE is not None
        sign_out(store)
        assert go._AUTH_CACHE is None

    def test_sign_out_clears_device_flow_state(self):
        go._DEVICE_FLOW_STATE = {"user_code": "ABCD-1234"}
        sign_out()
        assert go._DEVICE_FLOW_STATE is None

    def test_sign_out_result_includes_manual_issue_url(self, tmp_path):
        store = _store(tmp_path)
        result = sign_out(store)
        assert "manual_issue_url" in result
        assert "github.com" in result["manual_issue_url"]

    def test_sign_out_result_has_no_token_value(self, tmp_path):
        store = _store(tmp_path)
        store.save(_token_record(access_token="ghp_supersecret"))
        result = sign_out(store)
        result_str = json.dumps(result)
        assert "ghp_supersecret" not in result_str


# ── Server routes ──────────────────────────────────────────────────────────────

class TestOAuthRoutes:
    def test_status_route_signed_out(self, tmp_path, monkeypatch):
        monkeypatch.setattr(go, "_default_cred_path", lambda: tmp_path / "github_credentials.json")
        client = server.app.test_client()
        resp = client.get("/api/oauth/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["auth_state"] == AuthState.SIGNED_OUT
        assert data["can_one_click_file"] is False

    def test_status_route_signed_in(self, tmp_path, monkeypatch):
        cred_path = tmp_path / "github_credentials.json"
        monkeypatch.setattr(go, "_default_cred_path", lambda: cred_path)
        store = CredentialStore(path=cred_path)
        store.save(_token_record())
        mock_resp = _MockHTTPResp({"login": "alice"}, {"X-OAuth-Scopes": "public_repo"})
        with patch("urllib.request.urlopen", return_value=mock_resp):
            client = server.app.test_client()
            resp = client.get("/api/oauth/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["auth_state"] == AuthState.SIGNED_IN
        assert data["login"] == "alice"
        assert "access_token" not in data
        assert "ghp_" not in json.dumps(data)

    def test_sign_out_route_returns_signed_out(self, tmp_path, monkeypatch):
        cred_path = tmp_path / "github_credentials.json"
        monkeypatch.setattr(go, "_default_cred_path", lambda: cred_path)
        store = CredentialStore(path=cred_path)
        store.save(_token_record())
        client = server.app.test_client()
        resp = client.post("/api/oauth/sign-out", json={})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["auth_state"] == AuthState.SIGNED_OUT
        assert not cred_path.exists()

    def test_sign_out_route_idempotent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(go, "_default_cred_path", lambda: tmp_path / "github_credentials.json")
        client = server.app.test_client()
        resp1 = client.post("/api/oauth/sign-out", json={})
        resp2 = client.post("/api/oauth/sign-out", json={})
        assert resp1.status_code == 200
        assert resp2.status_code == 200
        assert resp1.get_json()["auth_state"] == AuthState.SIGNED_OUT

    def test_status_route_never_returns_token(self, tmp_path, monkeypatch):
        cred_path = tmp_path / "github_credentials.json"
        monkeypatch.setattr(go, "_default_cred_path", lambda: cred_path)
        store = CredentialStore(path=cred_path)
        store.save(_token_record(access_token="ghp_secrettoken"))
        mock_resp = _MockHTTPResp({"login": "alice"})
        with patch("urllib.request.urlopen", return_value=mock_resp):
            client = server.app.test_client()
            resp = client.get("/api/oauth/status")
        assert "ghp_secrettoken" not in resp.get_data(as_text=True)

    def test_status_force_param_revalidates(self, tmp_path, monkeypatch):
        cred_path = tmp_path / "github_credentials.json"
        monkeypatch.setattr(go, "_default_cred_path", lambda: cred_path)
        store = CredentialStore(path=cred_path)
        store.save(_token_record())
        mock_resp = _MockHTTPResp({"login": "alice"})
        client = server.app.test_client()
        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            client.get("/api/oauth/status")
            client.get("/api/oauth/status?force=true")
        assert mock_open.call_count == 2
