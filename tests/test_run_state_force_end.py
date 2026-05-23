"""Tests for RunState.force_end (issue #84).

The Flask /api/runs/<id>/force-end route calls RunState.force_end so the
in-memory _run_closed flag and the DB row flip in lock-step. These tests
pin the public contract: idempotent, no-op before any run, and produces
a closed runs row with outcome='force_ended'.
"""

import sqlite3

import db
import run_state
from run_state import RunState

from tests.test_run_state_live_context import RecordingLiveScorer, _point_db_at


def _bootstrap_active_run(state: RunState, ts: str = "10:00"):
    """Drive the state machine to a fully-initialized active run."""
    state.process({"event": "run_start", "ts": ts})
    state.process({"event": "session_id", "ts": ts, "session_id": "session-1"})
    state.process({"event": "account_id", "ts": ts, "account_id": "account-1"})
    state.process({"event": "hero", "ts": ts, "hero": "Karnok"})


def test_force_end_closes_active_run(tmp_path, monkeypatch):
    _point_db_at(tmp_path, monkeypatch)
    db.init_db()
    monkeypatch.setattr(run_state._scorer, "LiveScorer", RecordingLiveScorer)
    RecordingLiveScorer.calls = []

    close_calls: list[tuple[int, str, str]] = []
    real_close_run = db.close_run

    def recording_close_run(run_id, ended_at, outcome):
        close_calls.append((run_id, ended_at, outcome))
        return real_close_run(run_id, ended_at, outcome)

    monkeypatch.setattr(db, "close_run", recording_close_run)

    state = RunState("Player.log")
    _bootstrap_active_run(state)
    run_id = state.run_id
    assert run_id is not None
    assert state._run_closed is False
    assert not close_calls

    result = state.force_end("10:30")
    assert result is True
    assert state._run_closed is True
    assert close_calls == [(run_id, "10:30", "force_ended")]

    db.flush()
    conn = sqlite3.connect(tmp_path / "bazaar_runs.db")
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT outcome, ended_at FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
        assert row["outcome"] == "force_ended"
        assert row["ended_at"] == "10:30"
    finally:
        conn.close()


def test_force_end_is_idempotent(tmp_path, monkeypatch):
    """A second force_end after the run is already closed must not double-write."""
    _point_db_at(tmp_path, monkeypatch)
    db.init_db()
    monkeypatch.setattr(run_state._scorer, "LiveScorer", RecordingLiveScorer)
    RecordingLiveScorer.calls = []

    close_calls: list[tuple[int, str, str]] = []
    real_close_run = db.close_run
    monkeypatch.setattr(
        db,
        "close_run",
        lambda rid, ts, outcome: (
            close_calls.append((rid, ts, outcome)) or real_close_run(rid, ts, outcome)
        ),
    )

    state = RunState("Player.log")
    _bootstrap_active_run(state)

    assert state.force_end("10:30") is True
    assert state.force_end("10:45") is False  # already closed → no-op
    assert state.force_end("10:50") is False  # still a no-op

    assert len(close_calls) == 1
    assert close_calls[0][2] == "force_ended"


def test_force_end_before_any_run_is_noop(tmp_path, monkeypatch):
    """force_end called before a run is initialized must not touch the DB."""
    _point_db_at(tmp_path, monkeypatch)
    db.init_db()
    monkeypatch.setattr(run_state._scorer, "LiveScorer", RecordingLiveScorer)
    RecordingLiveScorer.calls = []

    close_calls: list[tuple[int, str, str]] = []
    real_close_run = db.close_run
    monkeypatch.setattr(
        db,
        "close_run",
        lambda rid, ts, outcome: (
            close_calls.append((rid, ts, outcome)) or real_close_run(rid, ts, outcome)
        ),
    )

    state = RunState("Player.log")
    assert state.run_id is None

    assert state.force_end("10:00") is False
    assert close_calls == []

    db.flush()
    conn = sqlite3.connect(tmp_path / "bazaar_runs.db")
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT COUNT(*) AS c FROM runs").fetchone()
        assert row["c"] == 0
    finally:
        conn.close()


def test_force_end_then_run_start_does_not_double_close(tmp_path, monkeypatch):
    """After force_end, the PR #88 defensive close in _on_run_start must no-op.

    Regression guard: _on_run_start checks `not self._run_closed` before
    closing the prior run. force_end already set _run_closed=True, so the
    next run_start should silently rotate to a fresh run without trying to
    re-close the already-closed prior row.
    """
    _point_db_at(tmp_path, monkeypatch)
    db.init_db()
    monkeypatch.setattr(run_state._scorer, "LiveScorer", RecordingLiveScorer)
    RecordingLiveScorer.calls = []

    close_calls: list[tuple[int, str, str]] = []
    real_close_run = db.close_run
    monkeypatch.setattr(
        db,
        "close_run",
        lambda rid, ts, outcome: (
            close_calls.append((rid, ts, outcome)) or real_close_run(rid, ts, outcome)
        ),
    )

    state = RunState("Player.log")
    _bootstrap_active_run(state)
    first_run_id = state.run_id

    assert state.force_end("10:30") is True
    assert close_calls == [(first_run_id, "10:30", "force_ended")]

    # Player starts a new game in the same session. _on_run_start should
    # NOT re-close the prior run (already closed), but should rotate to a
    # fresh active run.
    state.process({"event": "run_start", "ts": "11:00"})
    state.process({"event": "session_id", "ts": "11:00", "session_id": "session-2"})
    state.process({"event": "account_id", "ts": "11:00", "account_id": "account-1"})
    state.process({"event": "hero", "ts": "11:00", "hero": "Karnok"})

    # Only the single force_ended close should have happened.
    assert close_calls == [(first_run_id, "10:30", "force_ended")]
    assert state.run_id is not None
    assert state.run_id != first_run_id
    assert state._run_closed is False
