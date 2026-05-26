"""Tests for Discord Alpha release fixes (P1-1 through P2-6)."""

import json
import sqlite3
import threading

import pytest

import app_paths
import db


# ── Helpers ──────────────────────────────────────────────────────────────────

def _point_db_at(tmp_path, monkeypatch):
    path = tmp_path / "bazaar_runs.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    db.close_shared_conn()
    return path


# ── P1-3: DB path is never relative to capture_mono __file__ ─────────────────

def test_capture_mono_db_path_is_app_paths():
    import app_paths as ap
    source = (ap.repo_dir() / "capture_mono.py").read_text(encoding="utf-8")
    assert "app_paths.db_path()" in source, (
        "capture_mono._get_mono_conn must use app_paths.db_path(), not a relative path"
    )
    assert 'Path(__file__).parent / "bazaar_runs.db"' not in source, (
        "capture_mono must not write DB relative to __file__ (leaks into _internal in packaged builds)"
    )


def test_packaged_db_path_uses_localappdata(monkeypatch, tmp_path):
    """In packaged mode, db_path() must resolve into LOCALAPPDATA, not the bundle root."""
    import sys
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))

    path = app_paths.db_path()
    # Must be under LOCALAPPDATA\BazaarCoach, not under _MEIPASS
    assert "BazaarCoach" in str(path)
    assert str(tmp_path / "AppData") in str(path)
    assert str(tmp_path) not in str(path).replace(str(tmp_path / "AppData"), "")


# ── P2-5: SQLite connection closed on writer thread, not main thread ──────────

def test_writer_thread_closes_connection_cleanly(tmp_path, monkeypatch):
    """stop_writer() must close the shared connection before the writer thread exits,
    so close_shared_conn() doesn't hit the cross-thread SQLite warning."""
    path = tmp_path / "test.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    db.close_shared_conn()

    db.init_db()
    db.start_writer()

    # Perform a write to ensure the writer thread owns the connection
    db.flush()

    writer_thread_id_before = db._writer_thread_id

    # stop_writer sends sentinel and joins — connection should be closed by writer thread
    db.stop_writer()

    # _shared_conn must be None after stop_writer (closed by writer thread)
    assert db._shared_conn is None, (
        "_shared_conn should be None after stop_writer; writer thread must close it"
    )

    # Calling close_shared_conn on the main thread should not raise
    db.close_shared_conn()


def test_close_shared_conn_without_writer_is_safe(tmp_path, monkeypatch):
    """close_shared_conn with no writer started must not raise."""
    path = tmp_path / "test2.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    db.close_shared_conn()
    db.init_db()

    # Call without ever starting the writer
    db.close_shared_conn()
    # Should complete without error; _shared_conn is already None
    assert db._shared_conn is None


# ── P2-6: Retroactive offered-name resolution ─────────────────────────────────

def test_update_decision_offered_names(tmp_path, monkeypatch):
    """db.update_decision_offered_names must update offered_names in the DB."""
    path = _point_db_at(tmp_path, monkeypatch)
    db.init_db()
    db.start_writer()

    try:
        run_id = db.upsert_run("sess-retro", "", "Karnok", "2025-01-01T00:00:00", str(path))

        dec_id = db.insert_decision(
            run_id=run_id, seq=1, timestamp="2025-01-01T00:00:00",
            game_state="EncounterState", decision_type="buy",
            offered=["itm_abc123"], chosen_id="itm_abc123",
            chosen_template="", rejected=[],
            board_section="Player", target_socket="",
            offered_names=["itm_abc123"],  # unresolved
        )

        db.update_decision_offered_names(dec_id, ["Iron Shield"])
        db.flush()

        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT offered_names FROM decisions WHERE id=?", (dec_id,)).fetchone()
        conn.close()

        assert row is not None
        names = json.loads(row["offered_names"])
        assert names == ["Iron Shield"]
    finally:
        db.close_shared_conn()


def test_register_and_flush_unresolved(tmp_path, monkeypatch):
    """RunState._flush_unresolved must update offered_names when resolver improves."""
    from unittest.mock import patch, MagicMock
    from run_state import RunState
    from name_resolver import is_unresolved

    path = _point_db_at(tmp_path, monkeypatch)
    db.init_db()
    db.start_writer()

    try:
        rs = RunState()
        rs.run_id = db.upsert_run("sess-flush", "", "Karnok", "2025-01-01T00:00:00", str(path))
        rs.resolver.set_run_id(rs.run_id)

        # Insert a decision with an unresolved name
        dec_id = db.insert_decision(
            run_id=rs.run_id, seq=1, timestamp="2025-01-01T00:00:00",
            game_state="EncounterState", decision_type="skip",
            offered=["itm_xyz999"], chosen_id="", chosen_template="",
            rejected=["itm_xyz999"], board_section="", target_socket="",
            offered_names=["itm_xyz999"],  # unresolved
        )
        db.flush()

        # Register as pending
        rs._register_unresolved(dec_id, ["itm_xyz999"], ["itm_xyz999"])
        assert len(rs._pending_unresolved_decisions) == 1

        # Teach the resolver the mapping
        rs.resolver._cache["itm_xyz999"] = "Iron Shield"

        # Flush should resolve and update the DB
        rs._flush_unresolved()
        db.flush()

        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT offered_names FROM decisions WHERE id=?", (dec_id,)).fetchone()
        conn.close()

        names = json.loads(row["offered_names"])
        assert names == ["Iron Shield"]
        assert len(rs._pending_unresolved_decisions) == 0
    finally:
        db.close_shared_conn()


def test_pending_unresolved_capped_at_ten(tmp_path, monkeypatch):
    """_pending_unresolved_decisions must not grow unboundedly."""
    path = _point_db_at(tmp_path, monkeypatch)
    db.init_db()

    from run_state import RunState
    rs = RunState()
    rs.run_id = 1

    for i in range(15):
        rs._register_unresolved(i + 1, [f"itm_{i}"], [f"itm_{i}"])

    assert len(rs._pending_unresolved_decisions) == 10


# ── P1-1: CREATE_NO_WINDOW used for subprocess spawning ──────────────────────

def test_coach_launch_capture_mono_uses_no_window_flag():
    source = (app_paths.repo_dir() / "coach.py").read_text()
    assert "CREATE_NO_WINDOW" in source
