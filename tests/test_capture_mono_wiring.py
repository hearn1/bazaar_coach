"""
test_capture_mono_wiring.py

Regression test for issue #146.

PR #144 deleted `watcher.py`, which had been the only code path that wired a
`MonoEventAdapter` into `capture_mono` via `register_event_adapter()`.
Without that wiring, snapshots produced by `handle_game_state()` were
persisted to `api_game_states` but never reached `RunState.process()`, so no
`runs` / `decisions` rows were created and the overlay stayed on
"Waiting for run to start..." forever.

This test exercises the new `capture_mono._wire_run_state()` factory and
proves that:

  1. After wiring, `_mono_event_adapter` is a real `MonoEventAdapter`.
  2. Bootstrapping a run through `handle_game_state()` opens a `runs` row
     in the shared DB.
  3. An `EndRunVictoryState` snapshot closes that run with
     `outcome == 'victory'`.
"""

import sqlite3

import capture_mono
import db
import run_state
from mono_event_adapter import MonoEventAdapter


class _NullScorer:
    def __init__(self, hero, conn):
        pass
    def score_decision(self, decision, decision_id):
        pass
    def notify_combat(self):
        pass


def _point_db_at(tmp_path, monkeypatch, name="wiring_test.db"):
    path = tmp_path / name
    monkeypatch.setattr(db, "DB_PATH", path)
    db.close_shared_conn()
    return path


def _reset_capture_mono_globals(monkeypatch):
    """Clear capture_mono module-level slots between tests."""
    monkeypatch.setattr(capture_mono, "_mono_event_adapter", None)
    monkeypatch.setattr(capture_mono, "_run_state", None)
    monkeypatch.setattr(capture_mono, "_adapter", None)


def _make_snap(
    state: str = "EncounterState",
    offered: list | None = None,
    board: list | None = None,
    skills: list | None = None,
    gold: int = 10,
    hp: int = 60,
    hp_max: int = 60,
    rerolls: int = 3,
    hero: str = "Karnok",
    ts: str = "2026-01-01T00:00:00+00:00",
) -> dict:
    return {
        "timestamp": ts,
        "state": {"state": state, "rerolls_remaining": rerolls},
        "player": {"hero": hero, "Gold": gold, "Health": hp, "HealthMax": hp_max},
        "run": {"day": 2, "hour": 1},
        "offered": offered or [],
        "player_board": board or [],
        "player_stash": [],
        "player_skills": skills or [],
        "opponent_board": [],
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_wire_run_state_registers_adapter(tmp_path, monkeypatch):
    """_wire_run_state() must install a MonoEventAdapter into _mono_event_adapter."""
    _point_db_at(tmp_path, monkeypatch)
    _reset_capture_mono_globals(monkeypatch)
    monkeypatch.setattr(run_state._scorer, "LiveScorer", _NullScorer)

    capture_mono._wire_run_state()

    try:
        assert isinstance(capture_mono._mono_event_adapter, MonoEventAdapter), (
            "register_event_adapter() must be called by _wire_run_state()"
        )
        assert capture_mono._run_state is not None
        assert capture_mono._adapter is capture_mono._mono_event_adapter
    finally:
        capture_mono.register_event_adapter(None)


def test_wired_adapter_opens_run_on_new_run_snapshot(tmp_path, monkeypatch):
    """Bootstrapping through the wired adapter must create a runs row."""
    path = _point_db_at(tmp_path, monkeypatch)
    _reset_capture_mono_globals(monkeypatch)
    monkeypatch.setattr(run_state._scorer, "LiveScorer", _NullScorer)

    capture_mono._wire_run_state()
    adapter = capture_mono._mono_event_adapter
    state = capture_mono._run_state

    try:
        # NewRun emits run_start; the following EncounterState snapshot
        # delivers the bootstrap events that open the run record.
        adapter.process_snapshot(_make_snap("NewRun", hero="Karnok"))
        adapter.process_snapshot(_make_snap(
            "EncounterState",
            hero="Karnok",
            ts="2026-01-01T00:00:01+00:00",
        ))

        assert state.run_id is not None, "run must initialise from bootstrap snapshots"

        conn = sqlite3.connect(path)
        try:
            row = conn.execute(
                "SELECT id, hero, outcome FROM runs WHERE id = ?",
                (state.run_id,),
            ).fetchone()
        finally:
            conn.close()

        assert row is not None, "wired adapter must persist a runs row"
        assert row[1] == "Karnok"
        assert row[2] is None, "active run must have outcome == NULL"
    finally:
        capture_mono.register_event_adapter(None)


def test_wired_adapter_closes_run_on_end_state(tmp_path, monkeypatch):
    """EndRunVictoryState must propagate through the wired adapter and set outcome."""
    path = _point_db_at(tmp_path, monkeypatch)
    _reset_capture_mono_globals(monkeypatch)
    monkeypatch.setattr(run_state._scorer, "LiveScorer", _NullScorer)

    capture_mono._wire_run_state()
    adapter = capture_mono._mono_event_adapter
    state = capture_mono._run_state

    try:
        adapter.process_snapshot(_make_snap("NewRun", hero="Karnok"))
        adapter.process_snapshot(_make_snap(
            "EncounterState",
            hero="Karnok",
            ts="2026-01-01T00:00:01+00:00",
        ))
        run_id = state.run_id
        assert run_id is not None

        adapter.process_snapshot(_make_snap(
            "EndRunVictoryState",
            hero="Karnok",
            ts="2026-01-01T00:00:02+00:00",
        ))

        conn = sqlite3.connect(path)
        try:
            outcome = conn.execute(
                "SELECT outcome FROM runs WHERE id = ?", (run_id,),
            ).fetchone()[0]
        finally:
            conn.close()

        assert outcome == "victory", f"expected 'victory', got {outcome!r}"
    finally:
        capture_mono.register_event_adapter(None)
