import sys
from pathlib import Path

import web.server as server
import web.review_builder as review_builder


class _FakeConn:
    closed = False

    def close(self):
        self.closed = True


def test_overlay_state_no_runs_returns_no_runs_state(monkeypatch):
    conn = _FakeConn()
    monkeypatch.setattr(server, "_conn", lambda: conn)
    monkeypatch.setattr(
        server,
        "build_overlay_state",
        lambda *args, **kwargs: {"error": "No runs found"},
    )

    response = server.app.test_client().get("/api/overlay/state")

    assert response.status_code == 200
    assert response.get_json() == {"state": "no_runs"}
    assert conn.closed


def test_build_items_error_returns_renderable_payload_and_logs(monkeypatch, capsys):
    def fail_load_builds(hero):
        raise RuntimeError(f"catalog exploded for {hero}")

    monkeypatch.setattr(server, "load_builds", fail_load_builds)

    response = server.app.test_client().get("/api/builds/items/Dooley")

    payload = response.get_json()
    captured = capsys.readouterr()
    assert response.status_code == 500
    assert payload["items"] == {}
    assert payload["error"]["code"] == "build_items_load_failed"
    assert payload["error"]["hero"] == "Dooley"
    assert "catalog exploded for Dooley" in payload["error"]["message"]
    assert "[CardImages] load_builds failed for 'Dooley'" in captured.out
    assert "RuntimeError: catalog exploded for Dooley" in captured.err


def test_server_cli_no_refresh_builds_passes_false(monkeypatch):
    calls = []

    def fake_start_web_server(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(sys, "argv", ["server.py", "--no-refresh-builds"])
    monkeypatch.setattr(server, "start_web_server", fake_start_web_server)

    server.main()

    assert calls == [{
        "port": server.DEFAULT_PORT,
        "db_path": None,
        "background": False,
        "auto_refresh_builds": False,
    }]


def test_review_builder_uses_package_build_helpers_import():
    source = Path(review_builder.__file__).read_text(encoding="utf-8")

    assert "from web.build_helpers import extract_insights" in source
    assert "from build_helpers import extract_insights" not in source
