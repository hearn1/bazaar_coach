"""
Tests for event/map-choice template resolution when no Mono snapshot exists.

Per issue #132:
- No matching snapshot → no exception, empty templates persisted.
"""

import json
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


def test_event_choice_no_exception_when_snapshot_absent(tmp_path, monkeypatch):
    """When no Mono snapshots exist, event_choice decisions must still insert
    without error, and chosen_template / offered_templates remain empty."""
    path = _point_db_at(tmp_path, monkeypatch)
    db.init_db()
    monkeypatch.setattr(run_state._scorer, "LiveScorer", _NoopScorer)

    state = RunState("Player.log")
    state.process({"event": "run_start", "ts": "10:00:00"})
    state.process({"event": "session_id", "ts": "10:00:00", "session_id": "sess-evt-missing"})
    state.process({"event": "account_id", "ts": "10:00:00", "account_id": "acct-evt-missing"})
    state.process({"event": "hero", "ts": "10:00:00", "hero": "Karnok"})

    # No Mono snapshots inserted — pure log-only path
    state.process({"event": "state_change", "ts": "10:00:00", "to_state": "ChoiceState"})
    state.process({"event": "cards_dealt", "instance_ids": ["enc_x", "enc_y"]})
    state.process({
        "event": "card_purchased",
        "ts": "2026-05-01T10:01:00",
        "instance_id": "enc_x",
        "template_id": "",
        "target_socket": "OpponentSocket0",
        "section": "Opponent",
    })
    db.flush()

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        d = conn.execute(
            "SELECT * FROM decisions WHERE decision_type='event_choice' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert d is not None, "event_choice decision should have been inserted even without Mono"

        # Templates remain empty — no snapshot to resolve from
        assert (d["chosen_template"] or "") == "", (
            f"chosen_template should be empty when no snapshot exists, got {d['chosen_template']!r}"
        )
        offered_templates = json.loads(d["offered_templates"]) if d["offered_templates"] else {}
        assert offered_templates == {}, (
            f"offered_templates should be empty when no snapshot exists, got {offered_templates!r}"
        )

        # No exception path: api_game_state_id_at_offer is NULL
        assert d["api_game_state_id_at_offer"] is None
    finally:
        conn.close()
