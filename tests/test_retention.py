"""Tests for db.prune_old_runs (P1-B retention loop)."""

import sqlite3
from datetime import datetime, timedelta, timezone

import db


def _point_db_at(tmp_path, monkeypatch):
    path = tmp_path / "bazaar_runs.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    db.close_shared_conn()
    return path


def _insert_run(conn, session_id, ended_at):
    cur = conn.execute(
        "INSERT INTO runs (session_id, hero, started_at, ended_at) VALUES (?, ?, ?, ?)",
        (session_id, "Karnok", "2025-01-01T00:00:00", ended_at),
    )
    return cur.lastrowid


def _insert_decision(conn, run_id, seq=1):
    conn.execute(
        "INSERT INTO decisions (run_id, decision_seq, timestamp, game_state, decision_type, "
        "offered, chosen_id, chosen_template, rejected, board_section, target_socket) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, seq, "2025-01-01T00:00:00", "Shop", "buy",
         "[]", "id1", "tmpl1", "[]", "hand", ""),
    )


def _insert_combat(conn, run_id):
    conn.execute(
        "INSERT INTO combat_results (run_id, timestamp, outcome, combat_type) VALUES (?, ?, ?, ?)",
        (run_id, "2025-01-01T00:00:00", "win", "pve"),
    )


def _now_utc():
    return datetime.now(timezone.utc)


def test_prune_guard_zero(tmp_path, monkeypatch):
    _point_db_at(tmp_path, monkeypatch)
    db.init_db()
    result = db.prune_old_runs(0)
    assert result["skipped"] is True
    assert result["deleted_runs"] == 0
    assert result["deleted_decisions"] == 0
    assert result["deleted_combats"] == 0
    assert result["cutoff"] is None


def test_prune_guard_30(tmp_path, monkeypatch):
    _point_db_at(tmp_path, monkeypatch)
    db.init_db()
    result = db.prune_old_runs(30)
    assert result["skipped"] is True
    assert result["deleted_runs"] == 0


def test_prune_guard_89(tmp_path, monkeypatch):
    _point_db_at(tmp_path, monkeypatch)
    db.init_db()
    result = db.prune_old_runs(89)
    assert result["skipped"] is True


def test_prune_deletes_old_run_and_children(tmp_path, monkeypatch):
    path = _point_db_at(tmp_path, monkeypatch)
    db.init_db()

    now = _now_utc()
    old_ended = (now - timedelta(days=200)).strftime("%Y-%m-%dT%H:%M:%S")
    recent_ended = (now - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%S")
    today_ended = now.strftime("%Y-%m-%dT%H:%M:%S")

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row

    # Run 1: today (fresh, should be kept)
    run_today = _insert_run(conn, "sess-today", today_ended)
    _insert_decision(conn, run_today, seq=1)
    _insert_combat(conn, run_today)

    # Run 2: 30 days ago (recent, should be kept)
    run_recent = _insert_run(conn, "sess-recent", recent_ended)
    _insert_decision(conn, run_recent, seq=1)
    _insert_combat(conn, run_recent)

    # Run 3: 200 days ago (old, should be deleted)
    run_old = _insert_run(conn, "sess-old", old_ended)
    _insert_decision(conn, run_old, seq=1)
    _insert_decision(conn, run_old, seq=2)
    _insert_combat(conn, run_old)

    # Run 4: in-progress (ended_at IS NULL, should be kept)
    run_inprogress = _insert_run(conn, "sess-inprogress", None)
    _insert_decision(conn, run_inprogress, seq=1)

    conn.commit()
    conn.close()

    result = db.prune_old_runs(90, _now=now)

    assert result["skipped"] is False
    assert result["deleted_runs"] == 1
    assert result["deleted_decisions"] == 2
    assert result["deleted_combats"] == 1
    assert result["cutoff"] is not None

    # Verify DB state
    conn2 = sqlite3.connect(path)
    remaining_runs = {row[0] for row in conn2.execute("SELECT session_id FROM runs").fetchall()}
    assert "sess-old" not in remaining_runs
    assert "sess-today" in remaining_runs
    assert "sess-recent" in remaining_runs
    assert "sess-inprogress" in remaining_runs

    # Old run's decisions and combats gone
    dec_for_old = conn2.execute(
        "SELECT COUNT(*) FROM decisions WHERE run_id=?", (run_old,)
    ).fetchone()[0]
    assert dec_for_old == 0

    combat_for_old = conn2.execute(
        "SELECT COUNT(*) FROM combat_results WHERE run_id=?", (run_old,)
    ).fetchone()[0]
    assert combat_for_old == 0

    conn2.close()


def test_prune_inprogress_run_untouched(tmp_path, monkeypatch):
    """Runs with ended_at IS NULL must never be deleted."""
    path = _point_db_at(tmp_path, monkeypatch)
    db.init_db()

    conn = sqlite3.connect(path)
    run_id = _insert_run(conn, "sess-active", None)
    _insert_decision(conn, run_id, seq=1)
    conn.commit()
    conn.close()

    now = _now_utc()
    result = db.prune_old_runs(90, _now=now)

    assert result["deleted_runs"] == 0
    assert result["deleted_decisions"] == 0


def test_prune_cutoff_format(tmp_path, monkeypatch):
    """Cutoff should be an ISO 8601 string (no timezone suffix in storage format)."""
    _point_db_at(tmp_path, monkeypatch)
    db.init_db()

    result = db.prune_old_runs(90)
    assert result["skipped"] is False
    # Basic sanity: should look like a date
    assert "T" in result["cutoff"]
    assert len(result["cutoff"]) >= 19
