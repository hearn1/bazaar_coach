"""Tests for POST /api/runs/<id>/force-end (issue #84).

The overlay button posts here when the player wants to manually end a
stuck active run. The route delegates to a registered callback (the
watcher's RunState.force_end) so in-memory and DB state stay in
lock-step; if no callback is registered (dashboard launched without
watcher) it falls back to a direct db.close_run.
"""

import sqlite3
from pathlib import Path

import pytest

import db
import web.server as server


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    db_file: Path = tmp_path / "runs.db"
    monkeypatch.setattr(server, "DB_PATH", db_file)
    monkeypatch.setattr(db, "DB_PATH", db_file)
    db.close_shared_conn()
    db.init_db()
    yield db_file
    db.close_shared_conn()


@pytest.fixture()
def reset_force_end_callback():
    """Clear any callback the watcher (or a prior test) may have registered."""
    server.set_force_end_callback(None)
    yield
    server.set_force_end_callback(None)


@pytest.fixture()
def client():
    return server.app.test_client()


def _insert_run(db_file: Path, *, hero="Karnok", outcome=None, ended_at=None) -> int:
    conn = sqlite3.connect(db_file)
    try:
        cur = conn.execute(
            """
            INSERT INTO runs (session_id, account_id, hero, started_at, ended_at, outcome)
            VALUES ('s1', 'a1', ?, '2026-05-23T10:00:00+00:00', ?, ?)
            """,
            (hero, ended_at, outcome),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def test_force_end_happy_path_invokes_callback(client, tmp_db, reset_force_end_callback):
    run_id = _insert_run(tmp_db)

    calls: list[tuple[int, str]] = []

    def cb(rid: int, ts: str) -> bool:
        calls.append((rid, ts))
        # Simulate RunState writing to DB the way force_end → _on_run_end does.
        db.close_run(rid, ts, "force_ended")
        db.flush()
        return True

    server.set_force_end_callback(cb)

    resp = client.post(f"/api/runs/{run_id}/force-end")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["ok"] is True
    assert payload["outcome"] == "force_ended"
    assert payload.get("fallback") is None  # callback path, not fallback

    assert len(calls) == 1
    assert calls[0][0] == run_id

    conn = sqlite3.connect(tmp_db)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT outcome, ended_at FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
        assert row["outcome"] == "force_ended"
        assert row["ended_at"] == calls[0][1]
    finally:
        conn.close()


def test_force_end_already_ended_is_idempotent(client, tmp_db, reset_force_end_callback):
    run_id = _insert_run(tmp_db, outcome="victory", ended_at="2026-05-23T11:00:00+00:00")

    callback_invocations = []
    server.set_force_end_callback(lambda rid, ts: callback_invocations.append((rid, ts)) or True)

    resp = client.post(f"/api/runs/{run_id}/force-end")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["ok"] is True
    assert payload["already_ended"] is True
    assert payload["outcome"] == "victory"

    # Idempotency contract: callback must NOT be called for an already-closed run.
    assert callback_invocations == []


def test_force_end_missing_run_returns_404(client, tmp_db, reset_force_end_callback):
    resp = client.post("/api/runs/99999/force-end")
    assert resp.status_code == 404
    payload = resp.get_json()
    assert payload["ok"] is False
    assert "not found" in payload["error"].lower()


def test_force_end_without_callback_falls_back_to_direct_close(client, tmp_db, reset_force_end_callback):
    """Dashboard launched without the watcher (e.g. diagnostic mode).

    The route should still close the row directly via db.close_run so the
    POST is useful regardless of whether the watcher is attached.
    """
    run_id = _insert_run(tmp_db)
    # reset_force_end_callback fixture already cleared any registration.

    resp = client.post(f"/api/runs/{run_id}/force-end")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["ok"] is True
    assert payload["outcome"] == "force_ended"
    assert payload["fallback"] is True

    conn = sqlite3.connect(tmp_db)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT outcome FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
        assert row["outcome"] == "force_ended"
    finally:
        conn.close()


def test_force_end_callback_returns_false_falls_back(client, tmp_db, reset_force_end_callback):
    """If the watcher's callback declines (e.g. RunState has rotated to a
    different run_id since the overlay rendered), the route still closes
    the DB row so the user's intent lands."""
    run_id = _insert_run(tmp_db)

    server.set_force_end_callback(lambda rid, ts: False)

    resp = client.post(f"/api/runs/{run_id}/force-end")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["ok"] is True
    assert payload["outcome"] == "force_ended"
    assert payload["fallback"] is True

    conn = sqlite3.connect(tmp_db)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT outcome FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
        assert row["outcome"] == "force_ended"
    finally:
        conn.close()
