"""
Tests for decisions.rejected_templates_json population (issue #130).

Synthetic snapshot with 3 offered cards (A, B, C); decision picks A;
rejects = [B, C].  Asserts that rejected_templates_json round-trips through
the DB and contains the template_ids for B and C.
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
        VALUES (?, ?, 'Item', 'A', '[]', '{}', 'now')
        """,
        (template_id, name),
    )


def _insert_offer_snapshot(conn, *, captured_at, instance_ids, template_ids):
    """Insert an api_game_states row with offered api_cards and return its id."""
    gs_id = conn.execute(
        """
        INSERT INTO api_game_states
            (captured_at, run_state, hero, day, hour, gold, health, health_max)
        VALUES (?, 'EncounterState', 'Karnok', 3, 1, 10, 300, 300)
        RETURNING id
        """,
        (captured_at,),
    ).fetchone()[0]
    for iid, tid in zip(instance_ids, template_ids):
        conn.execute(
            "INSERT INTO api_cards (game_state_id, instance_id, template_id, category) "
            "VALUES (?, ?, ?, 'offered')",
            (gs_id, iid, tid),
        )
    conn.commit()
    return gs_id


def test_rejected_templates_populated_for_shop_purchase(tmp_path, monkeypatch):
    """After a shop purchase with 3 offered items, rejected_templates_json must
    contain the template_ids for the 2 unchosen items (B and C)."""
    path = _point_db_at(tmp_path, monkeypatch)
    db.init_db()
    monkeypatch.setattr(run_state._scorer, "LiveScorer", _NoopScorer)

    conn = sqlite3.connect(path)
    try:
        # Use unique template IDs (not T_A/T_B) to avoid polluting the module-level
        # card_cache that other tests may share (card_cache._template_name_cache is
        # a module-level dict; _prime_template_name_cache fills it from the current
        # DB_PATH and the cached entries persist across tests in the same process).
        _seed_card(conn, "RT_ITEM_A", "RT Item A")
        _seed_card(conn, "RT_ITEM_B", "RT Item B")
        _seed_card(conn, "RT_ITEM_C", "RT Item C")
        conn.commit()
    finally:
        conn.close()

    state = RunState("Player.log")
    state.process({"event": "run_start", "ts": "10:00:00"})
    state.process({"event": "session_id", "ts": "10:00:00", "session_id": "sess-rej-tpl-1"})
    state.process({"event": "account_id", "ts": "10:00:00", "account_id": "acct-1"})
    state.process({"event": "hero", "ts": "10:00:00", "hero": "Karnok"})

    # Insert offer snapshot after run baseline is established
    conn = sqlite3.connect(path)
    try:
        offer_gs_id = _insert_offer_snapshot(
            conn,
            captured_at="2026-05-01T10:00:30",
            instance_ids=["itm_a", "itm_b", "itm_c"],
            template_ids=["RT_ITEM_A", "RT_ITEM_B", "RT_ITEM_C"],
        )
    finally:
        conn.close()

    state.process({"event": "state_change", "ts": "10:00:00", "to_state": "EncounterState"})
    state.process({"event": "cards_dealt", "instance_ids": ["itm_a", "itm_b", "itm_c"]})
    state.process({
        "event": "card_purchased",
        "ts": "2026-05-01T10:01:00",
        "instance_id": "itm_a",
        "template_id": "RT_ITEM_A",
        "target_socket": "PlayerSocket0",
        "section": "Player",
    })
    # Trigger shop finalization (state exit)
    state.process({"event": "state_change", "ts": "2026-05-01T10:01:30", "to_state": "ChoiceState"})
    db.flush()

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        d = conn.execute(
            "SELECT * FROM decisions WHERE decision_type IN ('item', 'companion') ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert d is not None, "Expected a decision row"

        # The decision should have rejected = [itm_b, itm_c]
        rejected = json.loads(d["rejected"])
        assert set(rejected) == {"itm_b", "itm_c"}, f"Unexpected rejected: {rejected}"

        # rejected_templates_json must be populated with RT_ITEM_B and RT_ITEM_C
        assert d["rejected_templates_json"] is not None, (
            "rejected_templates_json must be populated when an offer snapshot exists"
        )
        rej_tpls = json.loads(d["rejected_templates_json"])
        assert isinstance(rej_tpls, list), "rejected_templates_json must be a JSON array"
        assert len(rej_tpls) == 2, f"Expected 2 templates, got {len(rej_tpls)}: {rej_tpls}"
        assert set(rej_tpls) == {"RT_ITEM_B", "RT_ITEM_C"}, (
            f"Expected template_ids for B and C, got: {rej_tpls}"
        )
    finally:
        conn.close()


def test_rejected_templates_null_when_no_offer_snapshot(tmp_path, monkeypatch):
    """When no offer snapshot exists, rejected_templates_json remains NULL."""
    path = _point_db_at(tmp_path, monkeypatch)
    db.init_db()
    monkeypatch.setattr(run_state._scorer, "LiveScorer", _NoopScorer)

    conn = sqlite3.connect(path)
    try:
        # Use unique template IDs to avoid polluting the module-level card_cache
        _seed_card(conn, "RT2_ITEM_A", "RT2 Item A")
        _seed_card(conn, "RT2_ITEM_B", "RT2 Item B")
        conn.commit()
    finally:
        conn.close()

    state = RunState("Player.log")
    state.process({"event": "run_start", "ts": "10:00:00"})
    state.process({"event": "session_id", "ts": "10:00:00", "session_id": "sess-no-snap-2"})
    state.process({"event": "account_id", "ts": "10:00:00", "account_id": "acct-2"})
    state.process({"event": "hero", "ts": "10:00:00", "hero": "Karnok"})

    # No offer snapshot inserted

    state.process({"event": "state_change", "ts": "10:00:00", "to_state": "EncounterState"})
    state.process({"event": "cards_dealt", "instance_ids": ["itm_a", "itm_b"]})
    state.process({
        "event": "card_purchased",
        "ts": "10:01:00",
        "instance_id": "itm_a",
        "template_id": "RT2_ITEM_A",
        "target_socket": "PlayerSocket0",
        "section": "Player",
    })
    state.process({"event": "state_change", "ts": "10:01:30", "to_state": "ChoiceState"})
    db.flush()

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        d = conn.execute(
            "SELECT * FROM decisions WHERE decision_type IN ('item', 'companion') ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert d is not None
        # rejected_templates_json may be None or a list of empty strings — both acceptable
        if d["rejected_templates_json"] is not None:
            rej_tpls = json.loads(d["rejected_templates_json"])
            assert all(t == "" for t in rej_tpls), (
                f"Without offer snapshot, all templates should be empty strings: {rej_tpls}"
            )
    finally:
        conn.close()


def test_rejected_templates_via_resolve_helper_directly(tmp_path, monkeypatch):
    """Unit test resolve_rejected_templates directly against a synthetic snapshot."""
    path = _point_db_at(tmp_path, monkeypatch)
    db.init_db()

    from web.offer_snapshot import resolve_rejected_templates

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        # Insert offer snapshot with 3 items
        gs_id = conn.execute(
            """
            INSERT INTO api_game_states
                (captured_at, run_state, hero, day, hour, gold, health, health_max)
            VALUES ('2026-05-01T10:00:00', 'EncounterState', 'Karnok', 3, 1, 10, 300, 300)
            RETURNING id
            """
        ).fetchone()[0]
        for iid, tid in [("itm_a", "T_A"), ("itm_b", "T_B"), ("itm_c", "T_C")]:
            conn.execute(
                "INSERT INTO api_cards (game_state_id, instance_id, template_id, category) "
                "VALUES (?, ?, ?, 'offered')",
                (gs_id, iid, tid),
            )
        conn.commit()

        # Rejected = B and C
        result = resolve_rejected_templates(conn, gs_id, ["itm_b", "itm_c"])
        assert result == ["T_B", "T_C"], f"Expected ['T_B', 'T_C'], got {result}"

        # Rejected item not in snapshot → empty string
        result_missing = resolve_rejected_templates(conn, gs_id, ["itm_x"])
        assert result_missing == [""], f"Expected [''], got {result_missing}"

        # No snapshot → all empty strings
        result_no_snap = resolve_rejected_templates(conn, None, ["itm_b", "itm_c"])
        assert result_no_snap == ["", ""], f"Expected ['', ''], got {result_no_snap}"

        # Empty input → empty list
        result_empty = resolve_rejected_templates(conn, gs_id, [])
        assert result_empty == [], f"Expected [], got {result_empty}"
    finally:
        conn.close()
