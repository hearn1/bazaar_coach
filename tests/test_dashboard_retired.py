"""
Phase 2 static checks for issue #192: dashboard removal.

Verifies that:
- Retired dashboard HTML page routes no longer exist.
- Retained overlay and API routes are still registered.
- No source references to open_dashboard, data-open-dashboard, or index.html remain.
"""
from pathlib import Path

import app_paths
from web.server import app


def _url_rules():
    return {rule.rule for rule in app.url_map.iter_rules()}


def test_dashboard_page_routes_removed():
    rules = _url_rules()
    assert "/" not in rules, "GET / (dashboard root) should have been removed"
    assert "/builds" not in rules, "GET /builds (dashboard page) should have been removed"
    assert "/my-builds" not in rules, "GET /my-builds (dashboard page) should have been removed"


def test_overlay_route_retained():
    assert "/overlay" in _url_rules()


def test_api_routes_retained():
    rules = _url_rules()
    api_routes = [r for r in rules if r.startswith("/api/")]
    assert api_routes, "All /api/* routes should be retained"
    required = [
        "/api/overlay/state",
        "/api/runs",
        "/api/builds/archetypes/<hero>",
        "/api/updates/status",
        "/api/report-issue/info",
        "/api/control/shutdown",
    ]
    for route in required:
        assert route in rules, f"Required API route missing: {route}"


def test_card_image_route_retained():
    assert "/cards/<path:filename>" in _url_rules()


def test_index_html_deleted():
    static = Path(app.static_folder)
    assert not (static / "index.html").exists(), "web/static/index.html should be deleted"


def test_no_open_dashboard_in_overlay_py():
    src = (app_paths.repo_dir() / "overlay.py").read_text(encoding="utf-8")
    assert "open_dashboard" not in src


def test_no_data_open_dashboard_in_overlay_html():
    src = (app_paths.repo_dir() / "web" / "static" / "overlay.html").read_text(encoding="utf-8")
    assert "data-open-dashboard" not in src
    assert "open_dashboard" not in src


def test_no_dashboard_running_message_in_server():
    src = (app_paths.repo_dir() / "web" / "server.py").read_text(encoding="utf-8")
    assert "Dashboard running" not in src
