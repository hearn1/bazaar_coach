"""
Tests for event/map-choice template_id resolution via Mono snapshots.

Per issue #132:
- Synthetic ChoiceState snapshot with 3 offered event cards (enc_ IDs) inserted
  into api_game_states / api_cards; RunState processes a card_purchased event
  that picks one of them.
- Asserts chosen_template and offered_templates are populated from the snapshot.
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


def _insert_choice_snapshot(conn, *, captured_at, offered_cards):
    """Insert a ChoiceState api_game_states row with offered event cards.

    offered_cards: list of (instance_id, template_id) tuples.
    Returns the gs_id.
    """
    gs_id = conn.execute(
        """
        INSERT INTO api_game_states
            (captured_at, run_state, hero, day, hour, gold, health, health_max)
        VALUES (?, 'ChoiceState', 'Karnok', 3, 1, 10, 300, 300)
        RETURNING id
        """,
        (captured_at,),
    ).fetchone()[0]
    for iid, tid in offered_cards:
        conn.execute(
            "INSERT INTO api_cards (game_state_id, instance_id, template_id, category) "
            "VALUES (?, ?, ?, 'offered')",
            (gs_id, iid, tid),
        )
    conn.commit()
    return gs_id


def test_event_choice_templates_resolved_from_mono_snapshot(tmp_path, monkeypatch):
    """chosen_template and offered_templates should be populated from the Mono
    ChoiceState snapshot when a map-node decision is recorded."""
    path = _point_db_at(tmp_path, monkeypatch)
    db.init_db()
    monkeypatch.setattr(run_state._scorer, "LiveScorer", _NoopScorer)

    state = RunState()
    state.process({"event": "run_start", "ts": "10:00:00"})
    state.process({"event": "session_id", "ts": "10:00:00", "session_id": "sess-evt-1"})
    state.process({"event": "account_id", "ts": "10:00:00", "account_id": "acct-evt-1"})
    state.process({"event": "hero", "ts": "10:00:00", "hero": "Karnok"})

    # Insert a ChoiceState snapshot AFTER the run baseline is set
    conn = sqlite3.connect(path)
    try:
        offered_cards = [
            ("enc_alpha", "ENC_TEMPLATE_A"),
            ("enc_beta",  "ENC_TEMPLATE_B"),
            ("ste_gamma", "STE_TEMPLATE_C"),
        ]
        gs_id = _insert_choice_snapshot(
            conn,
            captured_at="2026-05-01T10:00:30",  # before the decision timestamp
            offered_cards=offered_cards,
        )
    finally:
        conn.close()

    # RunState events: state → ChoiceState, cards offered, one chosen
    state.process({"event": "state_change", "ts": "10:00:00", "to_state": "ChoiceState"})
    state.process({"event": "cards_dealt", "instance_ids": ["enc_alpha", "enc_beta", "ste_gamma"]})
    state.process({
        "event": "card_purchased",
        "ts": "2026-05-01T10:01:00",
        "instance_id": "enc_alpha",
        "template_id": "",           # log line carries no template for event choices
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
        assert d is not None, "event_choice decision should have been inserted"

        # chosen_template must be resolved from the Mono snapshot
        assert d["chosen_template"] == "ENC_TEMPLATE_A", (
            f"chosen_template should be 'ENC_TEMPLATE_A', got {d['chosen_template']!r}"
        )

        # offered_templates must contain all three event options
        offered_templates = json.loads(d["offered_templates"]) if d["offered_templates"] else {}
        assert offered_templates.get("enc_alpha") == "ENC_TEMPLATE_A"
        assert offered_templates.get("enc_beta") == "ENC_TEMPLATE_B"
        assert offered_templates.get("ste_gamma") == "STE_TEMPLATE_C"

        # api_game_state_id_at_offer must point to the ChoiceState snapshot
        assert d["api_game_state_id_at_offer"] == gs_id, (
            f"expected offer snapshot {gs_id}, got {d['api_game_state_id_at_offer']}"
        )
    finally:
        conn.close()
