"""
Tests for web.offer_snapshot.find_offer_snapshot.

Covers:
- Returns most-recent snapshot whose offered set is a superset of the request.
- Respects baseline_id (does not return prior-run snapshots).
- Returns None when no snapshot matches.
- Handles mixed captured_at formats (ISO 8601 / Unix ms).
- Excludes snapshots captured after the decision timestamp.
"""

import sqlite3

import db
from web.offer_snapshot import find_offer_snapshot


def _point_db_at(tmp_path, monkeypatch):
    path = tmp_path / "bazaar_runs.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    db.close_shared_conn()
    return path


def _setup(tmp_path, monkeypatch):
    path = _point_db_at(tmp_path, monkeypatch)
    db.init_db()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _insert_gs(conn, *, captured_at, run_state="EncounterState"):
    gs_id = conn.execute(
        """
        INSERT INTO api_game_states (captured_at, run_state, hero, day, hour, gold, health, health_max)
        VALUES (?, ?, 'Karnok', 1, 1, 10, 300, 300)
        RETURNING id
        """,
        (captured_at, run_state),
    ).fetchone()[0]
    return gs_id


def _insert_offered(conn, gs_id, instance_ids):
    for iid in instance_ids:
        conn.execute(
            "INSERT INTO api_cards (game_state_id, instance_id, template_id, category) "
            "VALUES (?, ?, 'T_' || ?, 'offered')",
            (gs_id, iid, iid),
        )


# ── happy-path ────────────────────────────────────────────────────────────────

def test_returns_most_recent_superset_snapshot(tmp_path, monkeypatch):
    conn = _setup(tmp_path, monkeypatch)
    try:
        # Older snapshot (has both items)
        old_id = _insert_gs(conn, captured_at="2026-05-01T10:00:00")
        _insert_offered(conn, old_id, ["itm_a", "itm_b"])

        # Newer snapshot (also has both items)
        new_id = _insert_gs(conn, captured_at="2026-05-01T11:00:00")
        _insert_offered(conn, new_id, ["itm_a", "itm_b", "itm_c"])
        conn.commit()

        # Decision timestamp is after both snapshots
        result = find_offer_snapshot(
            conn,
            baseline_id=0,
            decision_timestamp="2026-05-01T12:00:00",
            offered_instance_ids=["itm_a", "itm_b"],
        )
        assert result == new_id, "should pick the most-recent matching snapshot"
    finally:
        conn.close()


def test_superset_not_equal(tmp_path, monkeypatch):
    """A snapshot with MORE offered cards than requested still matches."""
    conn = _setup(tmp_path, monkeypatch)
    try:
        gs_id = _insert_gs(conn, captured_at="2026-05-01T10:00:00")
        _insert_offered(conn, gs_id, ["itm_a", "itm_b", "itm_c"])
        conn.commit()

        result = find_offer_snapshot(
            conn,
            baseline_id=0,
            decision_timestamp="2026-05-01T12:00:00",
            offered_instance_ids=["itm_a", "itm_b"],
        )
        assert result == gs_id
    finally:
        conn.close()


# ── baseline_id guard ─────────────────────────────────────────────────────────

def test_respects_baseline_id(tmp_path, monkeypatch):
    """Snapshots at or below baseline_id must not be returned."""
    conn = _setup(tmp_path, monkeypatch)
    try:
        prior_id = _insert_gs(conn, captured_at="2026-05-01T09:00:00")
        _insert_offered(conn, prior_id, ["itm_a", "itm_b"])

        post_id = _insert_gs(conn, captured_at="2026-05-01T10:00:00")
        _insert_offered(conn, post_id, ["itm_a", "itm_b"])
        conn.commit()

        # baseline_id excludes prior_id (only post_id is eligible)
        result = find_offer_snapshot(
            conn,
            baseline_id=prior_id,
            decision_timestamp="2026-05-01T12:00:00",
            offered_instance_ids=["itm_a", "itm_b"],
        )
        assert result == post_id

        # baseline_id excludes both
        result_none = find_offer_snapshot(
            conn,
            baseline_id=post_id,
            decision_timestamp="2026-05-01T12:00:00",
            offered_instance_ids=["itm_a", "itm_b"],
        )
        assert result_none is None, "all snapshots above baseline are excluded"
    finally:
        conn.close()


# ── no match ─────────────────────────────────────────────────────────────────

def test_returns_none_when_no_match(tmp_path, monkeypatch):
    """Returns None when no snapshot contains all requested instance IDs."""
    conn = _setup(tmp_path, monkeypatch)
    try:
        gs_id = _insert_gs(conn, captured_at="2026-05-01T10:00:00")
        _insert_offered(conn, gs_id, ["itm_a"])  # only one item, request needs two
        conn.commit()

        result = find_offer_snapshot(
            conn,
            baseline_id=0,
            decision_timestamp="2026-05-01T12:00:00",
            offered_instance_ids=["itm_a", "itm_b"],
        )
        assert result is None
    finally:
        conn.close()


def test_returns_none_for_empty_offered(tmp_path, monkeypatch):
    """Returns None immediately when offered_instance_ids is empty."""
    conn = _setup(tmp_path, monkeypatch)
    try:
        gs_id = _insert_gs(conn, captured_at="2026-05-01T10:00:00")
        _insert_offered(conn, gs_id, ["itm_a"])
        conn.commit()

        result = find_offer_snapshot(
            conn,
            baseline_id=0,
            decision_timestamp="2026-05-01T12:00:00",
            offered_instance_ids=[],
        )
        assert result is None
    finally:
        conn.close()


# ── timestamp guard ───────────────────────────────────────────────────────────

def test_excludes_snapshots_after_decision_timestamp(tmp_path, monkeypatch):
    """A snapshot captured after the decision timestamp must not match."""
    conn = _setup(tmp_path, monkeypatch)
    try:
        # Snapshot captured AFTER the decision
        future_id = _insert_gs(conn, captured_at="2026-05-01T13:00:00")
        _insert_offered(conn, future_id, ["itm_a", "itm_b"])

        # Snapshot captured BEFORE the decision
        past_id = _insert_gs(conn, captured_at="2026-05-01T09:00:00")
        _insert_offered(conn, past_id, ["itm_a", "itm_b"])
        conn.commit()

        result = find_offer_snapshot(
            conn,
            baseline_id=0,
            decision_timestamp="2026-05-01T12:00:00",
            offered_instance_ids=["itm_a", "itm_b"],
        )
        assert result == past_id, "must pick the pre-decision snapshot, not the future one"
    finally:
        conn.close()


# ── EndRun rows excluded ──────────────────────────────────────────────────────

def test_excludes_endrun_snapshots(tmp_path, monkeypatch):
    """Terminal EndRunDefeat/EndRunVictory rows must not be matched."""
    conn = _setup(tmp_path, monkeypatch)
    try:
        end_id = _insert_gs(conn, captured_at="2026-05-01T10:00:00", run_state="EndRunDefeat")
        _insert_offered(conn, end_id, ["itm_a", "itm_b"])
        conn.commit()

        result = find_offer_snapshot(
            conn,
            baseline_id=0,
            decision_timestamp="2026-05-01T12:00:00",
            offered_instance_ids=["itm_a", "itm_b"],
        )
        assert result is None
    finally:
        conn.close()


# ── Unix-ms captured_at ───────────────────────────────────────────────────────

def test_handles_unix_ms_captured_at(tmp_path, monkeypatch):
    """Snapshots with Unix-millisecond captured_at are handled correctly."""
    conn = _setup(tmp_path, monkeypatch)
    try:
        # 2026-05-01T10:00:00 UTC in Unix ms
        unix_ms = str(int(1746086400 * 1000))  # approx 2026-05-01T08:00:00 UTC
        gs_id = _insert_gs(conn, captured_at=unix_ms)
        _insert_offered(conn, gs_id, ["itm_a", "itm_b"])
        conn.commit()

        # Decision at 2026-05-01T12:00:00 (later than the unix-ms snapshot)
        result = find_offer_snapshot(
            conn,
            baseline_id=0,
            decision_timestamp="2026-05-01T12:00:00",
            offered_instance_ids=["itm_a", "itm_b"],
        )
        assert result == gs_id
    finally:
        conn.close()
