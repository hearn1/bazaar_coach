"""
test_run_state_completion.py

Run-completion coverage that drives RunState.process() directly — no watcher,
no log file.  This is the long-term replacement for test_watcher_completion_legacy.py;
it survives the removal of watcher.py / parser.py in #136.
"""

import db
import run_state
from run_state import RunState


def test_run_complete_callback_fires_on_run_victory(tmp_path, monkeypatch):
    """on_run_complete must be called when a run_victory event is processed."""
    path = tmp_path / "bazaar_runs.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    db.close_shared_conn()
    db.init_db()

    # Stub LiveScorer so no catalog I/O is needed.
    class _NullScorer:
        def __init__(self, hero, conn):
            pass
        def score_decision(self, decision, decision_id):
            pass
        def notify_combat(self):
            pass

    monkeypatch.setattr(run_state._scorer, "LiveScorer", _NullScorer)

    completed: list[dict] = []

    state = RunState(on_run_complete=lambda info: completed.append(dict(info)))
    state.process({"event": "run_start", "ts": "10:00"})
    state.process({"event": "session_id", "ts": "10:00", "session_id": "s-1"})
    state.process({"event": "account_id", "ts": "10:00", "account_id": "acc-1"})
    state.process({"event": "hero", "ts": "10:00", "hero": "Karnok"})

    assert state.run_id is not None, "run must be initialized before victory"
    run_id = state.run_id

    state.process({"event": "run_victory", "ts": "11:00"})

    assert len(completed) == 1
    assert completed[0]["run_id"] == run_id
    assert completed[0]["hero"] == "Karnok"


def test_run_complete_callback_fires_on_run_defeat(tmp_path, monkeypatch):
    """on_run_complete must also fire for run_defeat."""
    path = tmp_path / "bazaar_runs.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    db.close_shared_conn()
    db.init_db()

    class _NullScorer:
        def __init__(self, hero, conn):
            pass
        def score_decision(self, decision, decision_id):
            pass
        def notify_combat(self):
            pass

    monkeypatch.setattr(run_state._scorer, "LiveScorer", _NullScorer)

    completed: list[dict] = []

    state = RunState(on_run_complete=lambda info: completed.append(dict(info)))
    state.process({"event": "run_start", "ts": "10:00"})
    state.process({"event": "session_id", "ts": "10:00", "session_id": "s-2"})
    state.process({"event": "account_id", "ts": "10:00", "account_id": "acc-2"})
    state.process({"event": "hero", "ts": "10:00", "hero": "Mak"})

    run_id = state.run_id
    state.process({"event": "run_defeat", "ts": "11:30"})

    assert len(completed) == 1
    assert completed[0]["run_id"] == run_id
    assert completed[0]["hero"] == "Mak"


def test_no_callback_without_on_run_complete(tmp_path, monkeypatch):
    """RunState constructed without on_run_complete must not raise on victory."""
    path = tmp_path / "bazaar_runs.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    db.close_shared_conn()
    db.init_db()

    class _NullScorer:
        def __init__(self, hero, conn):
            pass
        def score_decision(self, decision, decision_id):
            pass
        def notify_combat(self):
            pass

    monkeypatch.setattr(run_state._scorer, "LiveScorer", _NullScorer)

    # No on_run_complete, no log_path — both are now optional.
    state = RunState()
    state.process({"event": "run_start", "ts": "10:00"})
    state.process({"event": "session_id", "ts": "10:00", "session_id": "s-3"})
    state.process({"event": "account_id", "ts": "10:00", "account_id": "acc-3"})
    state.process({"event": "hero", "ts": "10:00", "hero": "Vanessa"})
    # Should not raise even though no callback is wired.
    state.process({"event": "run_victory", "ts": "12:00"})
