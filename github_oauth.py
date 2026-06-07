"""
github_oauth.py — GitHub OAuth token lifecycle management for Bazaar Coach.

Handles auth-state detection, token validation, token expiry, and sign-out.
Does NOT implement the device-flow sign-in sequence — that belongs to the
core one-click filing work (#168).

Public surface:
  AuthState          — enum of all recognized auth lifecycle states
  TokenRecord        — stored credential shape
  CredentialStore    — load / save / clear token from a local credential file
  validate_token()   — validate a token against the GitHub /user endpoint
  get_auth_status()  — assemble an auth-status dict safe for API responses
  sign_out()         — idempotent local sign-out
  clear_auth_cache() — discard the in-memory validation cache
"""

from __future__ import annotations

import io
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

import app_paths

# ── Auth lifecycle states ──────────────────────────────────────────────────────

class AuthState(str, Enum):
    SIGNED_OUT          = "signed_out"
    SIGNED_IN           = "signed_in"
    DEVICE_FLOW_PENDING = "device_flow_pending"
    DEVICE_FLOW_EXPIRED = "device_flow_expired"
    EXPIRED             = "expired"
    REVOKED_OR_INVALID  = "revoked_or_invalid"
    STORAGE_UNAVAILABLE = "storage_unavailable"
    RATE_LIMITED        = "rate_limited"
    NETWORK_ERROR       = "network_error"


# States that require the user to sign in again
_REAUTH_STATES = frozenset({
    AuthState.EXPIRED,
    AuthState.REVOKED_OR_INVALID,
    AuthState.DEVICE_FLOW_EXPIRED,
    AuthState.STORAGE_UNAVAILABLE,
})

# States where we clear the stored token (confirmed invalid by GitHub)
_CLEAR_TOKEN_STATES = frozenset({
    AuthState.REVOKED_OR_INVALID,
    AuthState.EXPIRED,
})


# ── Stored credential record ───────────────────────────────────────────────────

@dataclass
class TokenRecord:
    access_token: str
    token_type: str = "bearer"
    scope: str = ""
    login: str = ""
    expires_at: Optional[float] = None   # Unix timestamp, or None if unknown
    refresh_token: Optional[str] = None  # Available only if GitHub supplies it


# ── Credential file storage ────────────────────────────────────────────────────

_CRED_FILENAME = "github_credentials.json"


def _default_cred_path() -> Path:
    return app_paths.data_dir() / _CRED_FILENAME


class CredentialStore:
    """
    Load, save, and clear the GitHub access token from a local credential file.

    The file lives in app_paths.data_dir() — separate from settings.json and
    never committed to source control when the gitignore is respected.

    Token values are never logged or echoed to callers.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path: Path = path if path is not None else _default_cred_path()

    def load(self) -> Optional[TokenRecord]:
        """Return the stored TokenRecord, or None if absent or corrupted."""
        try:
            raw = self._path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict) or not data.get("access_token"):
                return None
            return TokenRecord(
                access_token=data["access_token"],
                token_type=data.get("token_type", "bearer"),
                scope=data.get("scope", ""),
                login=data.get("login", ""),
                expires_at=data.get("expires_at"),
                refresh_token=data.get("refresh_token"),
            )
        except (OSError, json.JSONDecodeError, ValueError, KeyError):
            return None

    def save(self, record: TokenRecord) -> bool:
        """Persist the record to disk atomically. Returns True on success."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "access_token": record.access_token,
                "token_type": record.token_type,
                "scope": record.scope,
                "login": record.login,
                "expires_at": record.expires_at,
                "refresh_token": record.refresh_token,
            }
            tmp = self._path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(tmp, self._path)
            return True
        except OSError:
            return False

    def clear(self) -> None:
        """Delete the credential file. Idempotent — no error if absent."""
        try:
            self._path.unlink(missing_ok=True)
        except OSError:
            pass


# ── In-memory validation cache ─────────────────────────────────────────────────

_AUTH_CACHE: Optional[dict] = None
_AUTH_CACHE_EXPIRES: float = 0.0
_CACHE_TTL: float = 60.0  # seconds

# Slot for active device-flow polling state (populated by #168).
# Cleared on sign-out so stale flows don't resume after credential removal.
_DEVICE_FLOW_STATE: Optional[dict] = None


def clear_auth_cache() -> None:
    """Discard the in-memory validation cache and cancel any device-flow state."""
    global _AUTH_CACHE, _AUTH_CACHE_EXPIRES, _DEVICE_FLOW_STATE
    _AUTH_CACHE = None
    _AUTH_CACHE_EXPIRES = 0.0
    _DEVICE_FLOW_STATE = None


# ── Token validation ───────────────────────────────────────────────────────────

_GITHUB_USER_URL = "https://api.github.com/user"
_VALIDATE_TIMEOUT = 8  # seconds


def validate_token(token: str, *, timeout: int = _VALIDATE_TIMEOUT) -> dict:
    """
    Validate a GitHub access token via GET /user.

    Returns a dict with:
      auth_state — AuthState value
      login      — GitHub username (empty string on failure)
      scopes     — list[str] from X-OAuth-Scopes header (empty on failure)
      message    — user-safe error string (empty on success)

    The token value is never written to logs or included in the return dict.
    Scope verification and gist/issues write access are the caller's concern.
    """
    req = urllib.request.Request(
        _GITHUB_USER_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "BazaarCoach",
        },
    )
    # add_unredirected_header keeps the auth header out of redirect chains
    req.add_unredirected_header("Authorization", f"token {token}")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8", errors="replace"))
            login = body.get("login", "")
            scopes_raw = resp.headers.get("X-OAuth-Scopes", "")
            scopes = [s.strip() for s in scopes_raw.split(",") if s.strip()]
            return {
                "auth_state": AuthState.SIGNED_IN,
                "login": login,
                "scopes": scopes,
                "message": "",
            }

    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return {
                "auth_state": AuthState.REVOKED_OR_INVALID,
                "login": "",
                "scopes": [],
                "message": "GitHub authorization revoked or token invalid.",
            }

        if exc.code in (403, 429):
            body_text = ""
            try:
                body_text = exc.read().decode("utf-8", errors="replace").lower()
            except Exception:
                pass
            rate_limited = (
                exc.code == 429
                or "rate limit" in body_text
                or (exc.headers or {}).get("X-RateLimit-Remaining") == "0"
            )
            if rate_limited:
                return {
                    "auth_state": AuthState.RATE_LIMITED,
                    "login": "",
                    "scopes": [],
                    "message": "GitHub rate limit reached. Try again later.",
                }
            # 403 without rate-limit indicators → auth/scope rejection
            return {
                "auth_state": AuthState.REVOKED_OR_INVALID,
                "login": "",
                "scopes": [],
                "message": "GitHub authorization error.",
            }

        # Other HTTP errors (5xx, etc.) — treat as transient; don't clear token
        return {
            "auth_state": AuthState.NETWORK_ERROR,
            "login": "",
            "scopes": [],
            "message": f"GitHub API error ({exc.code}). Try again later.",
        }

    except (urllib.error.URLError, OSError, TimeoutError):
        return {
            "auth_state": AuthState.NETWORK_ERROR,
            "login": "",
            "scopes": [],
            "message": "Could not reach GitHub. Check your connection.",
        }


# ── Manual fallback URL ────────────────────────────────────────────────────────

_FALLBACK_ISSUE_URL = "https://github.com/hearn1/bazaar_coach/issues/new"


def _manual_issue_url() -> str:
    """Return the prefilled manual issue URL, or the bare new-issue URL on failure."""
    try:
        from web.report_issue import issue_info
        return issue_info("", "").get("issue_url", _FALLBACK_ISSUE_URL)
    except Exception:
        return _FALLBACK_ISSUE_URL


# ── Auth status ────────────────────────────────────────────────────────────────

def get_auth_status(
    store: Optional[CredentialStore] = None,
    *,
    force_revalidate: bool = False,
) -> dict:
    """
    Return the auth-status payload for the /api/oauth/status route.

    Response keys:
      ok                — always True (errors surface as auth_state values)
      auth_state        — AuthState string
      login             — GitHub username, or "" if not signed in
      scopes            — list[str] of granted OAuth scopes
      can_one_click_file— True only when auth_state == signed_in
      needs_reauth      — True when the user must sign in again
      message           — user-safe status string
      manual_issue_url  — prefilled GitHub issue URL (always present)

    Token value is never included in the response.
    Caches SIGNED_IN results for _CACHE_TTL seconds to avoid hammering GitHub.
    """
    global _AUTH_CACHE, _AUTH_CACHE_EXPIRES

    if store is None:
        store = CredentialStore()

    now = time.monotonic()

    # Return warm cache for SIGNED_IN state
    if not force_revalidate and _AUTH_CACHE is not None and now < _AUTH_CACHE_EXPIRES:
        return dict(_AUTH_CACHE)

    manual_url = _manual_issue_url()

    record = store.load()

    if record is None:
        return {
            "ok": True,
            "auth_state": AuthState.SIGNED_OUT,
            "login": "",
            "scopes": [],
            "can_one_click_file": False,
            "needs_reauth": False,
            "message": "Sign in with GitHub for one-click issue filing.",
            "manual_issue_url": manual_url,
        }

    # Check local expiry metadata before making any network call
    if record.expires_at is not None and record.expires_at < time.time():
        store.clear()
        clear_auth_cache()
        return {
            "ok": True,
            "auth_state": AuthState.EXPIRED,
            "login": "",
            "scopes": [],
            "can_one_click_file": False,
            "needs_reauth": True,
            "message": "Your GitHub session has expired. Sign in again.",
            "manual_issue_url": manual_url,
        }

    # Live validation against the GitHub API
    validation = validate_token(record.access_token)
    auth_state: AuthState = validation["auth_state"]

    # Clear the stored token only when GitHub has definitively rejected it
    if auth_state in _CLEAR_TOKEN_STATES:
        store.clear()
        clear_auth_cache()

    needs_reauth = auth_state in _REAUTH_STATES
    can_file = auth_state == AuthState.SIGNED_IN

    # Persist updated login/scope metadata on successful validation
    if can_file and validation["login"]:
        try:
            record.login = validation["login"]
            record.scope = ",".join(validation["scopes"])
            store.save(record)
        except Exception:
            pass

    result = {
        "ok": True,
        "auth_state": auth_state,
        "login": validation["login"],
        "scopes": validation["scopes"],
        "can_one_click_file": can_file,
        "needs_reauth": needs_reauth,
        "message": validation["message"],
        "manual_issue_url": manual_url,
    }

    # Cache only confirmed-good state; transient errors should retry quickly
    if can_file:
        _AUTH_CACHE = dict(result)
        _AUTH_CACHE_EXPIRES = now + _CACHE_TTL

    return result


# ── Sign-out ───────────────────────────────────────────────────────────────────

def sign_out(store: Optional[CredentialStore] = None) -> dict:
    """
    Idempotent local sign-out.

    Clears the stored credential file, in-memory validation cache, and any
    active device-flow polling state.

    Does NOT attempt remote GitHub token revocation — the Device Flow for
    desktop apps has no safe no-secret revocation path. UI copy should say
    "Signed out on this device" rather than implying server-side revocation.

    Always succeeds; safe to call when no token is stored.
    """
    if store is None:
        store = CredentialStore()

    store.clear()
    clear_auth_cache()

    return {
        "ok": True,
        "auth_state": AuthState.SIGNED_OUT,
        "login": "",
        "scopes": [],
        "can_one_click_file": False,
        "needs_reauth": False,
        "message": "Signed out on this device.",
        "manual_issue_url": _manual_issue_url(),
    }
