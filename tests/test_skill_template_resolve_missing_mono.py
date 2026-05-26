"""
tests/test_skill_template_resolve_missing_mono.py — Issue #131

Verify graceful degradation: when no Mono snapshot is present, skill decisions
still write to the DB with chosen_template='' and no exception is raised.
"""

import sqlite3

import db
import run_state
from run_state import RunState


class _NoopScorer:
    def __init__(self, hero, conn):
        pass

    def score_decision(self, decision, decision_id):
        pass

    def notify_combat(self):
        pass


def _point_db_at(tmp_path, monkeypatch):
    path = tmp_path / "bazaar_runs.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    db.close_shared_conn()
    return path


def test_skill_decision_written_without_mono(tmp_path, monkeypatch):
    """When no Mono snapshots exist, skill decision still inserts; chosen_template is ''."""
    path = _point_db_at(tmp_path, monkeypatch)
    db.init_db()
    monkeypatch.setattr(run_state._scorer, "LiveScorer", _NoopScorer)

    state = RunState()
    state.process({"event": "run_start", "ts": "10:00:00"})
    state.process({"event": "session_id", "ts": "10:00:00", "session_id": "sess-no-mono"})
    state.process({"event": "account_id", "ts": "10:00:00", "account_id": "acct-no-mono"})
    state.process({"event": "hero", "ts": "10:00:00", "hero": "Karnok"})

    # No Mono snapshot inserted — capture_mono is absent.
    state.process({"event": "state_change", "ts": "10:00:00", "to_state": "ChoiceState"})
    state.process({"event": "cards_dealt", "instance_ids": ["itm_skill_x", "itm_skill_y"]})
    state.process({
        "event": "skill_selected",
        "ts": "10:01:00",
        "instance_id": "itm_skill_x",
        "socket": "SkillSlot_0",
    })
    db.flush()

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        d = conn.execute(
            "SELECT * FROM decisions WHERE decision_type='skill' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert d is not None, "skill decision must be written even without Mono"
        assert d["chosen_template"] == "" or d["chosen_template"] is None, (
            f"chosen_template must be empty when Mono absent, got {d['chosen_template']!r}"
        )
        assert d["api_game_state_id_at_offer"] is None, (
            "api_game_state_id_at_offer must be NULL when no snapshot exists"
        )
        assert d["chosen_id"] == "itm_skill_x"
        assert d["decision_type"] == "skill"
    finally:
        conn.close()


def test_skill_decision_no_exception_on_missing_mono(tmp_path, monkeypatch):
    """Processing a skill_selected event with no Mono must not raise."""
    path = _point_db_at(tmp_path, monkeypatch)
    db.init_db()
    monkeypatch.setattr(run_state._scorer, "LiveScorer", _NoopScorer)

    state = RunState()
    state.process({"event": "run_start", "ts": "10:00:00"})
    state.process({"event": "session_id", "ts": "10:00:00", "session_id": "sess-no-exc"})
    state.process({"event": "account_id", "ts": "10:00:00", "account_id": "acct-no-exc"})
    state.process({"event": "hero", "ts": "10:00:00", "hero": "Karnok"})
    state.process({"event": "state_change", "ts": "10:00:00", "to_state": "ChoiceState"})
    state.process({"event": "cards_dealt", "instance_ids": ["itm_skl_1"]})

    # This must not raise even though no Mono snapshot exists.
    state.process({
        "event": "skill_selected",
        "ts": "10:01:00",
        "instance_id": "itm_skl_1",
        "socket": "SkillSlot_0",
    })
    db.flush()

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM decisions WHERE decision_type='skill'"
        ).fetchone()[0]
        assert count == 1, "exactly one skill decision must exist"
    finally:
        conn.close()


def test_skill_decision_fallback_when_no_offer_snapshot_match(tmp_path, monkeypatch):
    """Even if a Mono snapshot exists but doesn't cover the skill instance IDs,
    the decision still inserts with chosen_template=''."""
    path = _point_db_at(tmp_path, monkeypatch)
    db.init_db()
    monkeypatch.setattr(run_state._scorer, "LiveScorer", _NoopScorer)

    state = RunState()
    state.process({"event": "run_start", "ts": "10:00:00"})
    state.process({"event": "session_id", "ts": "10:00:00", "session_id": "sess-no-match"})
    state.process({"event": "account_id", "ts": "10:00:00", "account_id": "acct-no-match"})
    state.process({"event": "hero", "ts": "10:00:00", "hero": "Karnok"})

    # Insert a snapshot that does NOT contain the offered skill instance IDs.
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            INSERT INTO api_game_states
                (captured_at, run_state, hero, day, hour, gold, health, health_max)
            VALUES ('2026-05-01T10:00:30', 'ChoiceState', 'Karnok', 2, 1, 10, 300, 300)
            """
        )
        gs_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        # Different instance IDs from what will be offered
        conn.execute(
            "INSERT INTO api_cards (game_state_id, instance_id, template_id, category) "
            "VALUES (?, 'itm_other', 'skl_T_other', 'offered')",
            (gs_id,),
        )
        conn.commit()
    finally:
        conn.close()

    state.process({"event": "state_change", "ts": "10:00:00", "to_state": "ChoiceState"})
    state.process({"event": "cards_dealt", "instance_ids": ["itm_skill_unknown"]})
    state.process({
        "event": "skill_selected",
        "ts": "2026-05-01T10:01:00",
        "instance_id": "itm_skill_unknown",
        "socket": "SkillSlot_0",
    })
    db.flush()

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        d = conn.execute(
            "SELECT * FROM decisions WHERE decision_type='skill' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert d is not None
        assert d["chosen_template"] == "" or d["chosen_template"] is None, (
            "chosen_template must remain empty when offer snapshot has no match"
        )
        assert d["chosen_id"] == "itm_skill_unknown"
    finally:
        conn.close()
