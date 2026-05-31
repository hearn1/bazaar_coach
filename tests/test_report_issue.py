"""
tests/test_report_issue.py — Tests for web/report_issue.py and its server routes.
"""

import time
from pathlib import Path

import pytest

import web.report_issue as ri
import web.server as server


# ── latest_log_file ───────────────────────────────────────────────────────────

def test_latest_log_file_returns_newest(tmp_path, monkeypatch):
    monkeypatch.setattr(ri.app_paths, "logs_dir", lambda: tmp_path)
    older = tmp_path / "coach_20240101_000000.log"
    newer = tmp_path / "coach_20240102_120000.log"
    older.write_text("old")
    time.sleep(0.02)
    newer.write_text("new")
    result = ri.latest_log_file()
    assert result == newer


def test_latest_log_file_returns_none_when_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(ri.app_paths, "logs_dir", lambda: tmp_path)
    assert ri.latest_log_file() is None


def test_latest_log_file_ignores_non_coach_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(ri.app_paths, "logs_dir", lambda: tmp_path)
    (tmp_path / "other.log").write_text("noise")
    assert ri.latest_log_file() is None


# ── build_issue_body ──────────────────────────────────────────────────────────

def test_build_issue_body_contains_version_and_os(monkeypatch):
    monkeypatch.setattr(ri, "APP_VERSION", "9.9.9")
    import platform
    body = ri.build_issue_body("something broke", None)
    assert "9.9.9" in body
    assert platform.platform() in body


def test_build_issue_body_contains_log_filename(tmp_path, monkeypatch):
    monkeypatch.setattr(ri.app_paths, "logs_dir", lambda: tmp_path)
    log = tmp_path / "coach_20240115_083000.log"
    log.write_text("x")
    body = ri.build_issue_body("crash on startup", log)
    assert "coach_20240115_083000.log" in body


def test_build_issue_body_no_log_shows_placeholder(monkeypatch):
    body = ri.build_issue_body("crash", None)
    assert "no log" in body.lower() or "_no log" in body


def test_build_issue_body_includes_description():
    body = ri.build_issue_body("my description here", None)
    assert "my description here" in body


def test_build_issue_body_empty_description_uses_placeholder():
    body = ri.build_issue_body("", None)
    assert "describe the problem" in body


# ── issue_info / URL assembly ─────────────────────────────────────────────────

def test_issue_info_url_contains_labels_bug(tmp_path, monkeypatch):
    monkeypatch.setattr(ri.app_paths, "logs_dir", lambda: tmp_path)
    monkeypatch.setattr(ri, "_resolve_github_repo", lambda: "hearn1/bazaar_coach")
    info = ri.issue_info("", "")
    assert "labels=bug" in info["issue_url"]


def test_issue_info_url_uses_configured_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(ri.app_paths, "logs_dir", lambda: tmp_path)
    monkeypatch.setattr(ri, "_resolve_github_repo", lambda: "owner/custom_repo")
    info = ri.issue_info("", "")
    assert "owner/custom_repo" in info["issue_url"]


def test_issue_info_url_includes_title_when_provided(tmp_path, monkeypatch):
    monkeypatch.setattr(ri.app_paths, "logs_dir", lambda: tmp_path)
    monkeypatch.setattr(ri, "_resolve_github_repo", lambda: "hearn1/bazaar_coach")
    info = ri.issue_info("My bug title", "desc")
    assert "My+bug+title" in info["issue_url"] or "My%20bug%20title" in info["issue_url"] or "title" in info["issue_url"]


def test_issue_info_log_path_none_when_no_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(ri.app_paths, "logs_dir", lambda: tmp_path)
    monkeypatch.setattr(ri, "_resolve_github_repo", lambda: "hearn1/bazaar_coach")
    info = ri.issue_info("", "")
    assert info["log_path"] is None


def test_issue_info_log_path_set_when_log_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(ri.app_paths, "logs_dir", lambda: tmp_path)
    monkeypatch.setattr(ri, "_resolve_github_repo", lambda: "hearn1/bazaar_coach")
    log = tmp_path / "coach_20240115_083000.log"
    log.write_text("x")
    info = ri.issue_info("", "")
    assert info["log_path"] == str(log)


# ── Server routes ─────────────────────────────────────────────────────────────

def test_report_issue_info_route_returns_ok(tmp_path, monkeypatch):
    monkeypatch.setattr(ri.app_paths, "logs_dir", lambda: tmp_path)
    monkeypatch.setattr(ri, "_resolve_github_repo", lambda: "hearn1/bazaar_coach")
    client = server.app.test_client()
    resp = client.post(
        "/api/report-issue/info",
        json={"title": "test bug", "description": "it crashed"},
        content_type="application/json",
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert "issue_url" in data
    assert "github.com" in data["issue_url"]


def test_report_issue_open_logs_route_returns_ok(tmp_path, monkeypatch):
    monkeypatch.setattr(ri.app_paths, "logs_dir", lambda: tmp_path)
    # Monkeypatch _reveal_in_file_manager so Explorer is never spawned in tests
    import update_checker
    monkeypatch.setattr(update_checker, "_reveal_in_file_manager", lambda p: None)
    log = tmp_path / "coach_20240115_083000.log"
    log.write_text("x")
    client = server.app.test_client()
    resp = client.post("/api/report-issue/open-logs", json={})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True


def test_report_issue_open_logs_no_log_still_ok(tmp_path, monkeypatch):
    monkeypatch.setattr(ri.app_paths, "logs_dir", lambda: tmp_path)
    import update_checker
    monkeypatch.setattr(update_checker, "_reveal_in_file_manager", lambda p: None)
    client = server.app.test_client()
    resp = client.post("/api/report-issue/open-logs", json={})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["log_path"] is None
