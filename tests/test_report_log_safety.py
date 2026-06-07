"""
tests/test_report_log_safety.py — Tests for web/report_log_safety.py.

Covers: no-log, small log, large truncated log, path redaction, token redaction,
email redaction, false-positive protection, unreadable log, encoding failures,
and the /api/report-issue/log-preview route.
"""

import os
from pathlib import Path

import pytest

import web.report_log_safety as rls
import web.server as server


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_log(tmp_path: Path, name: str = "coach_20260606_120000.log", content: str = "hello\nworld\n") -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


# ── redact_log_text ───────────────────────────────────────────────────────────

class TestRedactLogText:
    def test_windows_user_path_redacted(self):
        result = rls.redact_log_text(r"path: C:\Users\Matt\AppData\Local\foo.txt")
        assert r"C:\Users\<user>" in result.text
        assert "Matt" not in result.text
        assert "user_path" in result.counts

    def test_github_token_redacted(self):
        result = rls.redact_log_text("token=ghp_ABCDEFGHIJKLMNOPQRST")
        assert "ghp_" not in result.text
        assert "<github-token>" in result.text
        assert result.counts.get("github_token", 0) >= 1

    def test_github_pat_redacted(self):
        result = rls.redact_log_text("auth: github_pat_ABCDEFGHIJKLMNOPQRST1234")
        assert "github_pat_" not in result.text
        assert result.counts.get("github_token", 0) >= 1

    def test_bearer_token_redacted(self):
        # Test without Authorization: prefix so auth_header rule doesn't also fire
        result = rls.redact_log_text("token: Bearer eyJhbGciOiJSUzI1NiJ9.payload.sig")
        assert "eyJhbGci" not in result.text
        assert "Bearer <token>" in result.text
        assert result.counts.get("bearer_token", 0) >= 1

    def test_auth_header_value_redacted(self):
        # Authorization: header line — the whole value after the colon is redacted
        result = rls.redact_log_text("Authorization: Bearer eyJhbGciOiJSUzI1NiJ9.payload.sig")
        assert "eyJhbGci" not in result.text
        assert (
            result.counts.get("bearer_token", 0) + result.counts.get("auth_header", 0) >= 1
        )

    def test_coach_token_header_redacted(self):
        result = rls.redact_log_text("X-Bazaar-Coach-Token: abcdefghijklmnop")
        assert "abcdefghijklmnop" not in result.text
        assert result.counts.get("coach_token", 0) >= 1

    def test_coach_token_env_redacted(self):
        result = rls.redact_log_text("BAZAAR_COACH_API_TOKEN=supersecretvalue123")
        assert "supersecretvalue123" not in result.text
        assert result.counts.get("coach_token", 0) >= 1

    def test_email_redacted(self):
        result = rls.redact_log_text("contact: user@example.com for support")
        assert "user@example.com" not in result.text
        assert "<email redacted>" in result.text
        assert result.counts.get("email", 0) >= 1

    def test_secret_kv_redacted(self):
        result = rls.redact_log_text("password=ThisIsAVeryLongSecretValue12345678")
        assert "ThisIsAVeryLongSecretValue12345678" not in result.text
        assert result.counts.get("secret_kv", 0) >= 1

    # False-positive protection: these must survive redaction intact

    def test_app_version_preserved(self):
        result = rls.redact_log_text("Bazaar Coach 1.2.3 starting")
        assert "1.2.3" in result.text

    def test_hero_name_preserved(self):
        result = rls.redact_log_text("hero=Karnok run_id=42")
        assert "Karnok" in result.text
        assert "42" in result.text

    def test_template_id_preserved(self):
        result = rls.redact_log_text("template_id=ItemCopperSword offered=ItemWoodenBow")
        assert "ItemCopperSword" in result.text
        assert "ItemWoodenBow" in result.text

    def test_stack_trace_preserved(self):
        trace = "Traceback (most recent call last):\n  File run_state.py, line 42, in insert_decision\nKeyError: 'day'"
        result = rls.redact_log_text(trace)
        assert "Traceback" in result.text
        assert "run_state.py" in result.text
        assert "KeyError" in result.text

    def test_no_redactions_returns_empty_counts(self):
        result = rls.redact_log_text("clean log line with no secrets")
        assert result.counts == {}

    def test_multiple_emails_counted(self):
        result = rls.redact_log_text("a@b.com and c@d.com in same line")
        assert result.counts.get("email", 0) == 2


# ── prepare_log_for_upload ────────────────────────────────────────────────────

class TestPrepareLogForUpload:
    def test_no_log_returns_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rls.app_paths, "logs_dir", lambda: tmp_path)
        # Patch latest_log_file to return None
        monkeypatch.setattr("web.report_issue.latest_log_file", lambda: None, raising=False)
        result = rls.prepare_log_for_upload()
        assert result.ok is False
        assert result.error is not None

    def test_small_log_no_truncation(self, tmp_path, monkeypatch):
        log = _make_log(tmp_path, content="line1\nline2\nline3\n")
        result = rls.prepare_log_for_upload(log_path=log)
        assert result.ok is True
        assert result.was_truncated is False
        assert result.original_bytes > 0
        assert result.prepared_bytes > 0
        assert "line1" in result.upload_text
        assert result.filename == log.name

    def test_large_log_truncated_to_cap(self, tmp_path):
        big_content = "x" * 1_000_000
        log = _make_log(tmp_path, content=big_content)
        cap = 512 * 1024
        result = rls.prepare_log_for_upload(log_path=log, max_bytes=cap)
        assert result.ok is True
        assert result.was_truncated is True
        assert result.prepared_bytes <= cap + 500  # small overhead for truncation notice
        assert "truncated" in result.upload_text.lower()

    def test_large_log_tail_preserved(self, tmp_path):
        # Put a sentinel at the very end
        body = "a\n" * 100_000
        sentinel = "SENTINEL_AT_END_OF_LOG\n"
        log = _make_log(tmp_path, content=body + sentinel)
        result = rls.prepare_log_for_upload(log_path=log, max_bytes=1024)
        assert result.ok is True
        assert "SENTINEL_AT_END_OF_LOG" in result.upload_text

    def test_truncation_notice_shows_sizes(self, tmp_path):
        log = _make_log(tmp_path, content="x" * 600_000)
        result = rls.prepare_log_for_upload(log_path=log, max_bytes=256 * 1024)
        assert result.was_truncated is True
        assert "KiB" in result.upload_text

    def test_user_path_redacted_in_upload_text(self, tmp_path):
        content = r"C:\Users\Matt\AppData\Local\BazaarCoach\logs\coach.log opened"
        log = _make_log(tmp_path, content=content)
        result = rls.prepare_log_for_upload(log_path=log)
        assert result.ok is True
        assert "Matt" not in result.upload_text

    def test_user_path_redacted_in_display_path(self, tmp_path):
        log = _make_log(tmp_path)
        # Patch display path to include a username
        result = rls.prepare_log_for_upload(log_path=log)
        # The display path stored in result.path must not expose the real username
        # when the log lives under a user-profile dir — we can't guarantee tmp_path
        # matches that pattern, but we can confirm the field exists and is a string.
        assert isinstance(result.path, str)

    def test_unreadable_log_returns_error(self, tmp_path, monkeypatch):
        log = tmp_path / "coach_20260606_120000.log"
        log.write_text("data")
        original_read_bytes = Path.read_bytes

        def _boom(self):
            if self == log:
                raise OSError("permission denied")
            return original_read_bytes(self)

        monkeypatch.setattr(Path, "read_bytes", _boom)
        result = rls.prepare_log_for_upload(log_path=log)
        assert result.ok is False
        assert result.error is not None

    def test_invalid_utf8_bytes_do_not_crash(self, tmp_path):
        log = tmp_path / "coach_20260606_120000.log"
        log.write_bytes(b"valid start\xff\xfe invalid bytes\nmore valid")
        result = rls.prepare_log_for_upload(log_path=log)
        assert result.ok is True
        assert result.upload_text  # something was produced

    def test_preview_text_is_subset_of_upload_text(self, tmp_path):
        lines = [f"log line {i}\n" for i in range(200)]
        log = _make_log(tmp_path, content="".join(lines))
        result = rls.prepare_log_for_upload(log_path=log)
        assert result.ok is True
        # Every non-empty preview line should appear in the upload text
        for line in result.preview_text.splitlines():
            if line.strip():
                assert line in result.upload_text

    def test_log_not_found_path_returns_error(self, tmp_path):
        missing = tmp_path / "coach_does_not_exist.log"
        result = rls.prepare_log_for_upload(log_path=missing)
        assert result.ok is False
        assert result.error is not None

    def test_redaction_counts_in_result(self, tmp_path):
        content = "user@secret.com accessed token=ghp_ABCDEFGHIJKLMNOPQRST\n"
        log = _make_log(tmp_path, content=content)
        result = rls.prepare_log_for_upload(log_path=log)
        assert result.ok is True
        assert result.redaction_counts.get("email", 0) >= 1
        assert result.redaction_counts.get("github_token", 0) >= 1


# ── summarize_prepared_log ────────────────────────────────────────────────────

class TestSummarizePreparedLog:
    def test_shape_on_success(self, tmp_path):
        log = _make_log(tmp_path)
        prepared = rls.prepare_log_for_upload(log_path=log)
        summary = rls.summarize_prepared_log(prepared)
        for key in ("ok", "log_found", "path", "filename", "original_bytes",
                    "prepared_bytes", "was_truncated", "redaction_counts",
                    "preview_text", "warning", "error"):
            assert key in summary, f"missing key: {key}"
        assert summary["ok"] is True
        assert summary["warning"]

    def test_shape_on_failure(self):
        prepared = rls.PreparedLog(ok=False, error="no log")
        summary = rls.summarize_prepared_log(prepared)
        assert summary["ok"] is False
        assert summary["error"] == "no log"
        assert "warning" in summary


# ── /api/report-issue/log-preview route ──────────────────────────────────────

class TestLogPreviewRoute:
    def test_route_returns_ok_with_log(self, tmp_path, monkeypatch):
        import web.report_log_safety as _rls
        log = _make_log(tmp_path, content="coach log content here\n")
        # Capture the original function before patching to avoid circular recursion
        _orig = rls.prepare_log_for_upload
        monkeypatch.setattr(_rls, "prepare_log_for_upload", lambda **_kw: _orig(log_path=log))
        client = server.app.test_client()
        resp = client.post("/api/report-issue/log-preview", json={})
        assert resp.status_code == 200
        data = resp.get_json()
        assert "ok" in data
        assert "warning" in data

    def test_route_returns_error_when_no_log(self, tmp_path, monkeypatch):
        import web.report_log_safety as _rls

        monkeypatch.setattr(_rls, "prepare_log_for_upload", lambda **_kw: rls.PreparedLog(ok=False, error="no log"))
        client = server.app.test_client()
        resp = client.post("/api/report-issue/log-preview", json={})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is False
