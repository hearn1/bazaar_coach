"""
web/report_log_safety.py — Log preparation/redaction for future authenticated issue filing.

Produces a sanitized upload candidate from the latest coach log without making
any network calls. This is a prerequisite for gist-based issue filing (#168/#229).

Public surface:
  prepare_log_for_upload(log_path, *, max_bytes) -> PreparedLog
  redact_log_text(text) -> RedactionResult
  summarize_prepared_log(prepared) -> dict
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import app_paths

# Default upload cap: 512 KiB
DEFAULT_MAX_LOG_BYTES: int = 512 * 1024

# Lines shown in the preview snippet
_PREVIEW_LINES = 50
# Chars cap on the preview snippet
_PREVIEW_CHAR_CAP = 8_000


@dataclass
class RedactionResult:
    text: str
    counts: dict[str, int]


@dataclass
class PreparedLog:
    ok: bool
    path: Optional[str] = None
    filename: Optional[str] = None
    original_bytes: int = 0
    prepared_bytes: int = 0
    was_truncated: bool = False
    redaction_counts: dict[str, int] = field(default_factory=dict)
    preview_text: str = ""
    upload_text: str = ""
    error: Optional[str] = None


# ── Redaction rules ────────────────────────────────────────────────────────────

# Pattern: C:\Users\<name>\...  — replaces just the username segment.
_RE_WIN_USER_PATH = re.compile(
    r"(?i)(C:\\Users\\)[^\\\s/\"'<>:;,\r\n]+",
    re.IGNORECASE,
)

# BazaarCoach app-data roots expanded from %LOCALAPPDATA% or %APPDATA%
_APPDATA_ROOTS: list[tuple[str, str]] = []


def _appdata_roots() -> list[tuple[str, str]]:
    """Build (literal_prefix, replacement_prefix) pairs for app-data normalisation."""
    if _APPDATA_ROOTS:
        return _APPDATA_ROOTS
    pairs = []
    for env_var, env_token in (
        ("LOCALAPPDATA", "%LOCALAPPDATA%"),
        ("APPDATA", "%APPDATA%"),
    ):
        val = os.environ.get(env_var, "")
        if val:
            pairs.append((
                os.path.join(val, "BazaarCoach"),
                f"{env_token}\\BazaarCoach",
            ))
    _APPDATA_ROOTS.extend(pairs)
    return _APPDATA_ROOTS


# GitHub / API tokens
_RE_GITHUB_TOKEN = re.compile(r"\bghp_[A-Za-z0-9]{10,}\b")
_RE_GITHUB_PAT = re.compile(r"\bgithub_pat_[A-Za-z0-9_]{10,}\b")
_RE_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-._~+/]+=*\b")

# Bazaar Coach local API token in headers or log lines
_RE_COACH_TOKEN_HEADER = re.compile(
    r"(?i)(X-Bazaar-Coach-Token\s*[:=]\s*)[^\s\r\n\"']+",
)
_RE_COACH_TOKEN_ENV = re.compile(
    r"(?i)(BAZAAR_COACH_API_TOKEN\s*[:=]\s*)[^\s\r\n\"']+",
)

# Authorization header values
_RE_AUTH_HEADER = re.compile(
    r"(?i)(Authorization\s*[:=]\s*)[^\s\r\n\"']+",
)

# Secret-like key=value pairs (only when the key looks sensitive)
_SECRET_KEYS = r"(?:token|secret|authorization|password|credential)"
_RE_SECRET_KV = re.compile(
    rf'(?i)({_SECRET_KEYS}\s*[=:]\s*)["\']?([A-Za-z0-9\-._~+/]{{16,}})["\']?',
)

# Email addresses — bounded quantifiers prevent O(n²) backtracking on long non-email lines
_RE_EMAIL = re.compile(r"[a-zA-Z0-9._%+\-]{1,64}@[a-zA-Z0-9.\-]{1,255}\.[a-zA-Z]{2,10}")


def redact_log_text(text: str) -> RedactionResult:
    """Apply all redaction rules and return the cleaned text plus per-category counts."""
    counts: dict[str, int] = {}

    def _sub(pattern: re.Pattern, replacement: str, key: str) -> None:
        nonlocal text
        new, n = pattern.subn(replacement, text)
        if n:
            counts[key] = counts.get(key, 0) + n
        text = new

    # App-data root normalisation (literal string replace, not regex)
    for literal, token in _appdata_roots():
        if literal in text:
            new = text.replace(literal, token)
            n = text.count(literal)
            if n:
                counts["appdata_path"] = counts.get("appdata_path", 0) + n
            text = new

    # Windows user-profile paths (after app-data so the username is still present)
    _sub(_RE_WIN_USER_PATH, r"\1<user>", "user_path")

    # GitHub tokens
    _sub(_RE_GITHUB_TOKEN, "<github-token>", "github_token")
    _sub(_RE_GITHUB_PAT, "<github-pat>", "github_token")

    # Bearer tokens
    _sub(_RE_BEARER, "Bearer <token>", "bearer_token")

    # Auth header
    _sub(_RE_AUTH_HEADER, r"\1<redacted>", "auth_header")

    # Bazaar Coach local API token
    _sub(_RE_COACH_TOKEN_HEADER, r"\1<redacted>", "coach_token")
    _sub(_RE_COACH_TOKEN_ENV, r"\1<redacted>", "coach_token")

    # Generic secret KV pairs
    _sub(_RE_SECRET_KV, r"\1<redacted>", "secret_kv")

    # Email addresses
    _sub(_RE_EMAIL, "<email redacted>", "email")

    return RedactionResult(text=text, counts=counts)


# ── Truncation ─────────────────────────────────────────────────────────────────

def _truncate_tail(text: str, max_bytes: int) -> tuple[str, bool, int]:
    """Return (tail_text, was_truncated, original_byte_count).

    Preserves the tail of the text (most recent log lines), prefixed with a
    truncation notice if the original exceeded max_bytes.
    """
    encoded = text.encode("utf-8", errors="replace")
    original_bytes = len(encoded)
    if original_bytes <= max_bytes:
        return text, False, original_bytes
    tail_bytes = encoded[-max_bytes:]
    # Decode; drop incomplete leading UTF-8 sequence if any
    tail = tail_bytes.decode("utf-8", errors="replace")
    prefix = (
        f"[Bazaar Coach log truncated: original {original_bytes / 1024:.1f} KiB, "
        f"uploading last {max_bytes // 1024} KiB after redaction]\n\n"
    )
    return prefix + tail, True, original_bytes


# ── Main entry point ───────────────────────────────────────────────────────────

def prepare_log_for_upload(
    log_path: Optional[Path] = None,
    *,
    max_bytes: int = DEFAULT_MAX_LOG_BYTES,
) -> PreparedLog:
    """Read, truncate, and redact the log for future gist upload.

    Args:
        log_path: explicit path; defaults to the latest coach log.
        max_bytes: upload cap in bytes (tail-preserving).
    """
    if log_path is None:
        log_path = app_paths.logs_dir()
        # Resolve to latest coach_*.log
        try:
            from web.report_issue import latest_log_file
            log_path = latest_log_file()
        except Exception:
            log_path = None

    if log_path is None:
        return PreparedLog(ok=False, error="no coach log found")

    log_path = Path(log_path)
    if not log_path.exists():
        return PreparedLog(ok=False, path=str(log_path), error="log file not found")

    try:
        raw = log_path.read_bytes()
    except OSError as exc:
        return PreparedLog(ok=False, path=str(log_path), filename=log_path.name, error=str(exc))

    # Decode tolerantly
    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception as exc:
        return PreparedLog(ok=False, path=str(log_path), filename=log_path.name, error=f"decode error: {exc}")

    original_bytes = len(raw)

    # Truncate first (tail-preserving), then redact the upload candidate
    upload_candidate, was_truncated, _ = _truncate_tail(text, max_bytes)
    redaction = redact_log_text(upload_candidate)
    upload_text = redaction.text
    prepared_bytes = len(upload_text.encode("utf-8", errors="replace"))

    # Build a preview from the tail of the upload text
    lines = upload_text.splitlines()
    preview_lines = lines[-_PREVIEW_LINES:]
    preview_text = "\n".join(preview_lines)
    if len(preview_text) > _PREVIEW_CHAR_CAP:
        preview_text = preview_text[-_PREVIEW_CHAR_CAP:]

    # Redact the display path for the preview payload
    display_path = str(log_path)
    display_path, _ = _RE_WIN_USER_PATH.subn(r"\1<user>", display_path)

    return PreparedLog(
        ok=True,
        path=display_path,
        filename=log_path.name,
        original_bytes=original_bytes,
        prepared_bytes=prepared_bytes,
        was_truncated=was_truncated,
        redaction_counts=redaction.counts,
        preview_text=preview_text,
        upload_text=upload_text,
    )


def summarize_prepared_log(prepared: PreparedLog) -> dict:
    """Return the preview/summary payload shape for the overlay API route."""
    warning = (
        "Automated redaction is best-effort. Review the preview before confirming upload."
    )
    return {
        "ok": prepared.ok,
        "log_found": prepared.ok,
        "path": prepared.path,
        "filename": prepared.filename,
        "original_bytes": prepared.original_bytes,
        "prepared_bytes": prepared.prepared_bytes,
        "was_truncated": prepared.was_truncated,
        "redaction_counts": prepared.redaction_counts,
        "preview_text": prepared.preview_text,
        "warning": warning,
        "error": prepared.error,
    }
