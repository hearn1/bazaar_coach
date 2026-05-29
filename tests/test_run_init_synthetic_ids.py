"""
test_run_init_synthetic_ids.py

--event-source mono: synthetic session_id/account_id are populated and
_try_init_run completes from run_init_complete alone (via MonoEventAdapter
bootstrap sequence + run_init_complete event).
"""

import sqlite3

import db
import run_state
from run_state import RunState
from mono_event_adapter import MonoEventAdapter, _SYNTHETIC_SESSION_ID, _SYNTHETIC_ACCOUNT_ID


class _NullScorer:
    def __init__(self, hero, conn):
        pass
    def score_decision(self, decision, decision_id):
        pass
    def notify_combat(self):
        pass


def _point_db_at(tmp_path, monkeypatch):
    path = tmp_path / "synthetic_ids_test.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    db.close_shared_conn()
    return path


def _make_snap(state_name: str = "EncounterState", hero: str = "Stelle",
               ts: str = "2026-01-01T00:00:00+00:00") -> dict:
    return {
        "timestamp": ts,
        "state": {"state": state_name},
        "player": {"hero": hero, "Gold": 10, "Health": 60, "HealthMax": 60},
        "run": {"day": 1, "hour": 1},
        "offered": [],
        "player_board": [],
        "player_stash": [],
        "player_skills": [],
        "opponent_board": [],
    }


def test_synthetic_ids_populated_on_mono_source(tmp_path, monkeypatch):
    """
    With event_source='mono', MonoEventAdapter emits synthetic session_id and
    account_id before run_init_complete, causing RunState._try_init_run to
    open a run record.
    """
    _point_db_at(tmp_path, monkeypatch)
    db.init_db()
    monkeypatch.setattr(run_state._scorer, "LiveScorer", _NullScorer)

    state = RunState(event_source="mono")
    adapter = MonoEventAdapter(state, event_source="mono")

    # Feed a single snapshot — adapter emits bootstrap events then run_init_complete
    adapter.process_snapshot(_make_snap("EncounterState", hero="Stelle"))

    # run_id should now be set (run was initialised via synthetic ids)
    assert state.run_id is not None, "run_id should be set after Mono bootstrap"
    assert state.session_id == _SYNTHETIC_SESSION_ID
    assert state.account_id == _SYNTHETIC_ACCOUNT_ID
    assert state.hero == "Stelle"


def test_synthetic_session_id_constant_exists_for_first_bootstrap():
    """_SYNTHETIC_SESSION_ID remains available as the initial bootstrap id."""
    from mono_event_adapter import _SYNTHETIC_SESSION_ID as sid1
    from mono_event_adapter import _SYNTHETIC_SESSION_ID as sid2
    assert sid1 == sid2
    assert len(sid1) == 36  # standard UUID length


def test_synthetic_account_id_starts_with_mono_prefix():
    assert _SYNTHETIC_ACCOUNT_ID.startswith("mono-")


def test_run_init_from_run_init_complete_event(tmp_path, monkeypatch):
    """
    When event_source='mono', a run_init_complete event causes _try_init_run
    to fire (provided session_id + account_id are already set).
    """
    _point_db_at(tmp_path, monkeypatch)
    db.init_db()
    monkeypatch.setattr(run_state._scorer, "LiveScorer", _NullScorer)

    state = RunState(event_source="mono")

    # Manually set synthetic ids (normally done by MonoEventAdapter bootstrap)
    state.process({"event": "run_start", "ts": "10:00", "source": "mono"})
    state.process({"event": "session_id", "ts": "10:00",
                   "session_id": _SYNTHETIC_SESSION_ID, "source": "mono"})
    state.process({"event": "account_id", "ts": "10:00",
                   "account_id": _SYNTHETIC_ACCOUNT_ID, "source": "mono"})
    state.process({"event": "hero", "ts": "10:00", "hero": "Pygmalien", "source": "mono"})

    # run_id not yet set (still need run_init_complete or another trigger)
    # Now send run_init_complete — should trigger _try_init_run for 'mono' source
    state.process({"event": "run_init_complete", "ts": "10:00", "source": "mono"})

    assert state.run_id is not None, "run_id should be set after run_init_complete"
    assert state.hero == "Pygmalien"


def test_new_run_boundary_gets_fresh_synthetic_session_id(tmp_path, monkeypatch):
    """Multiple Mono runs in one capture process must create distinct DB rows."""
    _point_db_at(tmp_path, monkeypatch)
    db.init_db()
    monkeypatch.setattr(run_state._scorer, "LiveScorer", _NullScorer)

    state = RunState(event_source="mono")
    adapter = MonoEventAdapter(state, event_source="mono")

    adapter.process_snapshot(_make_snap("NewRun", hero="Karnok", ts="2026-01-01T00:00:00+00:00"))
    adapter.process_snapshot(_make_snap("Encounter", hero="Karnok", ts="2026-01-01T00:00:01+00:00"))
    first_run_id = state.run_id
    first_session_id = state.session_id

    adapter.process_snapshot(_make_snap("EndRunVictory", hero="Karnok", ts="2026-01-01T00:10:00+00:00"))
    adapter.process_snapshot(_make_snap("NewRun", hero="Karnok", ts="2026-01-01T00:11:00+00:00"))
    adapter.process_snapshot(_make_snap("Encounter", hero="Karnok", ts="2026-01-01T00:11:01+00:00"))

    assert state.run_id is not None
    assert state.run_id != first_run_id
    assert state.session_id != first_session_id

    conn = sqlite3.connect(db.DB_PATH)
    try:
        rows = conn.execute(
            "SELECT id, session_id FROM runs ORDER BY id"
        ).fetchall()
    finally:
        conn.close()

    assert len(rows) == 2
    assert rows[0][1] != rows[1][1]
