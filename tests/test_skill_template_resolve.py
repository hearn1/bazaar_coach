"""
tests/test_skill_template_resolve.py — Issue #131

Verify that skill-pick decisions have chosen_template populated when a Mono
offer snapshot is present (ChoiceState with category='offered' skill cards).
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


def _seed_card(conn, template_id, name):
    conn.execute(
        """
        INSERT INTO card_cache (template_id, name, card_type, tier, tags, raw_json, cached_at)
        VALUES (?, ?, 'Skill', 'A', '[]', '{}', 'now')
        """,
        (template_id, name),
    )


def _insert_choice_snapshot(conn, *, captured_at, skill_cards):
    """Insert a ChoiceState api_game_states row with offered skill cards.

    skill_cards is a list of (instance_id, template_id) tuples.
    Returns the inserted api_game_states.id.
    """
    gs_id = conn.execute(
        """
        INSERT INTO api_game_states
            (captured_at, run_state, hero, day, hour, gold, health, health_max)
        VALUES (?, 'ChoiceState', 'Karnok', 2, 1, 10, 300, 300)
        RETURNING id
        """,
        (captured_at,),
    ).fetchone()[0]
    for iid, tid in skill_cards:
        conn.execute(
            "INSERT INTO api_cards (game_state_id, instance_id, template_id, category) "
            "VALUES (?, ?, ?, 'offered')",
            (gs_id, iid, tid),
        )
    conn.commit()
    return gs_id


def test_chosen_template_populated_from_offer_snapshot(tmp_path, monkeypatch):
    """Skill decision should have chosen_template set from the offer snapshot."""
    path = _point_db_at(tmp_path, monkeypatch)
    db.init_db()
    monkeypatch.setattr(run_state._scorer, "LiveScorer", _NoopScorer)

    conn = sqlite3.connect(path)
    try:
        _seed_card(conn, "skl_T_A", "Skill Alpha")
        _seed_card(conn, "skl_T_B", "Skill Beta")
        _seed_card(conn, "skl_T_C", "Skill Gamma")
        conn.commit()
    finally:
        conn.close()

    state = RunState("Player.log")
    state.process({"event": "run_start", "ts": "10:00:00"})
    state.process({"event": "session_id", "ts": "10:00:00", "session_id": "sess-skill-1"})
    state.process({"event": "account_id", "ts": "10:00:00", "account_id": "acct-skill-1"})
    state.process({"event": "hero", "ts": "10:00:00", "hero": "Karnok"})

    # Insert the offer snapshot AFTER _try_init_run sets the baseline so it is
    # eligible (not filtered out as a prior-run snapshot).
    conn = sqlite3.connect(path)
    try:
        offer_gs_id = _insert_choice_snapshot(
            conn,
            captured_at="2026-05-01T10:00:30",
            skill_cards=[
                ("itm_skill_a", "skl_T_A"),
                ("itm_skill_b", "skl_T_B"),
                ("itm_skill_c", "skl_T_C"),
            ],
        )
    finally:
        conn.close()

    # Offer three skills then select one
    state.process({"event": "state_change", "ts": "10:00:00", "to_state": "ChoiceState"})
    state.process({"event": "cards_dealt", "instance_ids": ["itm_skill_a", "itm_skill_b", "itm_skill_c"]})
    state.process({
        "event": "skill_selected",
        "ts": "2026-05-01T10:01:00",
        "instance_id": "itm_skill_a",
        "socket": "SkillSlot_0",
    })
    db.flush()

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        d = conn.execute(
            "SELECT * FROM decisions WHERE decision_type='skill' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert d is not None, "skill decision must be written"
        assert d["chosen_template"] == "skl_T_A", (
            f"expected 'skl_T_A', got {d['chosen_template']!r}"
        )
        assert d["api_game_state_id_at_offer"] == offer_gs_id, (
            f"expected offer snapshot {offer_gs_id}, "
            f"got {d['api_game_state_id_at_offer']}"
        )
        # offered_templates should cover at least the chosen card
        offered_templates = json.loads(d["offered_templates"]) if d["offered_templates"] else {}
        assert "itm_skill_a" in offered_templates, (
            "itm_skill_a must appear in offered_templates"
        )
        assert offered_templates["itm_skill_a"] == "skl_T_A"
    finally:
        conn.close()


def test_offered_templates_covers_all_resolved_skills(tmp_path, monkeypatch):
    """offered_templates should include all three offered skills, not just the chosen one."""
    path = _point_db_at(tmp_path, monkeypatch)
    db.init_db()
    monkeypatch.setattr(run_state._scorer, "LiveScorer", _NoopScorer)

    conn = sqlite3.connect(path)
    try:
        _seed_card(conn, "skl_T_X", "Skill X")
        _seed_card(conn, "skl_T_Y", "Skill Y")
        _seed_card(conn, "skl_T_Z", "Skill Z")
        conn.commit()
    finally:
        conn.close()

    state = RunState("Player.log")
    state.process({"event": "run_start", "ts": "10:00:00"})
    state.process({"event": "session_id", "ts": "10:00:00", "session_id": "sess-skill-2"})
    state.process({"event": "account_id", "ts": "10:00:00", "account_id": "acct-skill-2"})
    state.process({"event": "hero", "ts": "10:00:00", "hero": "Karnok"})

    conn = sqlite3.connect(path)
    try:
        _insert_choice_snapshot(
            conn,
            captured_at="2026-05-01T10:00:30",
            skill_cards=[
                ("itm_s_x", "skl_T_X"),
                ("itm_s_y", "skl_T_Y"),
                ("itm_s_z", "skl_T_Z"),
            ],
        )
    finally:
        conn.close()

    state.process({"event": "state_change", "ts": "10:00:00", "to_state": "ChoiceState"})
    state.process({"event": "cards_dealt", "instance_ids": ["itm_s_x", "itm_s_y", "itm_s_z"]})
    state.process({
        "event": "skill_selected",
        "ts": "2026-05-01T10:01:00",
        "instance_id": "itm_s_y",
        "socket": "SkillSlot_1",
    })
    db.flush()

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        d = conn.execute(
            "SELECT * FROM decisions WHERE decision_type='skill' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert d is not None
        assert d["chosen_template"] == "skl_T_Y"
        offered_templates = json.loads(d["offered_templates"]) if d["offered_templates"] else {}
        assert offered_templates.get("itm_s_x") == "skl_T_X"
        assert offered_templates.get("itm_s_y") == "skl_T_Y"
        assert offered_templates.get("itm_s_z") == "skl_T_Z"
    finally:
        conn.close()


def test_notify_template_called_for_resolved_skills(tmp_path, monkeypatch):
    """notify_template() must be called so future resolver queries hit cache."""
    path = _point_db_at(tmp_path, monkeypatch)
    db.init_db()
    monkeypatch.setattr(run_state._scorer, "LiveScorer", _NoopScorer)

    conn = sqlite3.connect(path)
    try:
        _seed_card(conn, "skl_T_Q", "Skill Q")
        conn.commit()
    finally:
        conn.close()

    state = RunState("Player.log")
    state.process({"event": "run_start", "ts": "10:00:00"})
    state.process({"event": "session_id", "ts": "10:00:00", "session_id": "sess-skill-3"})
    state.process({"event": "account_id", "ts": "10:00:00", "account_id": "acct-skill-3"})
    state.process({"event": "hero", "ts": "10:00:00", "hero": "Karnok"})

    conn = sqlite3.connect(path)
    try:
        _insert_choice_snapshot(
            conn,
            captured_at="2026-05-01T10:00:30",
            skill_cards=[("itm_q", "skl_T_Q")],
        )
    finally:
        conn.close()

    state.process({"event": "state_change", "ts": "10:00:00", "to_state": "ChoiceState"})
    state.process({"event": "cards_dealt", "instance_ids": ["itm_q"]})
    state.process({
        "event": "skill_selected",
        "ts": "2026-05-01T10:01:00",
        "instance_id": "itm_q",
        "socket": "SkillSlot_0",
    })
    db.flush()

    # After resolution, the resolver's template_map should have the mapping.
    assert state.resolver.get_template_id("itm_q") == "skl_T_Q", (
        "notify_template must have been called so resolver knows itm_q → skl_T_Q"
    )
