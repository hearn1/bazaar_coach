"""
Tests for api_game_state_id_at_offer wiring in RunState.

Per issue #129:
- Insert a synthetic Mono offer snapshot, then a cards_dealt + card_purchased
  event sequence.
- Assert the resulting decisions row has api_game_state_id_at_offer set to
  the offer snapshot.
- Assert that decisions with no matching offer snapshot leave the column NULL.
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


def test_offer_snapshot_attached_to_purchase_decision(tmp_path, monkeypatch):
    """A purchase decision should have api_game_state_id_at_offer pointing to
    the pre-decision offer snapshot that contained the offered items."""
    path = _point_db_at(tmp_path, monkeypatch)
    db.init_db()
    monkeypatch.setattr(run_state._scorer, "LiveScorer", _NoopScorer)

    conn = sqlite3.connect(path)
    try:
        _seed_card(conn, "T_A", "Item A")
        _seed_card(conn, "T_B", "Item B")
        conn.commit()
    finally:
        conn.close()

    state = RunState("Player.log")
    state.process({"event": "run_start", "ts": "10:00:00"})
    state.process({"event": "session_id", "ts": "10:00:00", "session_id": "sess-offer-1"})
    state.process({"event": "account_id", "ts": "10:00:00", "account_id": "acct-1"})
    state.process({"event": "hero", "ts": "10:00:00", "hero": "Karnok"})

    # Insert the offer snapshot AFTER _try_init_run sets the baseline
    conn = sqlite3.connect(path)
    try:
        offer_gs_id = _insert_offer_snapshot(
            conn,
            captured_at="2026-05-01T10:00:30",  # before the purchase timestamp
            instance_ids=["itm_a", "itm_b"],
            template_ids=["T_A", "T_B"],
        )
    finally:
        conn.close()

    state.process({"event": "state_change", "ts": "10:00:00", "to_state": "EncounterState"})
    state.process({"event": "cards_dealt", "instance_ids": ["itm_a", "itm_b"]})
    state.process({
        "event": "card_purchased",
        "ts": "2026-05-01T10:01:00",  # ISO ts after offer snapshot
        "instance_id": "itm_a",
        "template_id": "T_A",
        "target_socket": "PlayerSocket0",
        "section": "Player",
    })
    db.flush()

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        d = conn.execute(
            "SELECT * FROM decisions ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert d is not None
        assert d["api_game_state_id_at_offer"] == offer_gs_id, (
            f"expected offer snapshot {offer_gs_id}, "
            f"got {d['api_game_state_id_at_offer']}"
        )
    finally:
        conn.close()


def test_offer_snapshot_null_when_no_snapshot(tmp_path, monkeypatch):
    """When no Mono snapshots exist for the run, api_game_state_id_at_offer is NULL."""
    path = _point_db_at(tmp_path, monkeypatch)
    db.init_db()
    monkeypatch.setattr(run_state._scorer, "LiveScorer", _NoopScorer)

    conn = sqlite3.connect(path)
    try:
        _seed_card(conn, "T_A", "Item A")
        conn.commit()
    finally:
        conn.close()

    state = RunState("Player.log")
    state.process({"event": "run_start", "ts": "10:00:00"})
    state.process({"event": "session_id", "ts": "10:00:00", "session_id": "sess-no-snap"})
    state.process({"event": "account_id", "ts": "10:00:00", "account_id": "acct-2"})
    state.process({"event": "hero", "ts": "10:00:00", "hero": "Karnok"})
    state.process({"event": "state_change", "ts": "10:00:00", "to_state": "EncounterState"})
    state.process({"event": "cards_dealt", "instance_ids": ["itm_a"]})
    state.process({
        "event": "card_purchased",
        "ts": "10:01:00",
        "instance_id": "itm_a",
        "template_id": "T_A",
        "target_socket": "PlayerSocket0",
        "section": "Player",
    })
    db.flush()

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        d = conn.execute(
            "SELECT * FROM decisions ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert d is not None
        assert d["api_game_state_id_at_offer"] is None
    finally:
        conn.close()


def test_offer_snapshot_not_attached_from_prior_run(tmp_path, monkeypatch):
    """A snapshot belonging to a prior run (below baseline_id) must not be linked."""
    path = _point_db_at(tmp_path, monkeypatch)
    db.init_db()
    monkeypatch.setattr(run_state._scorer, "LiveScorer", _NoopScorer)

    # Insert a prior-run snapshot BEFORE starting the new run
    conn = sqlite3.connect(path)
    try:
        _seed_card(conn, "T_A", "Item A")
        # Insert it with very early timestamp so it's not excluded by time guard
        conn.execute(
            """
            INSERT INTO api_game_states
                (captured_at, run_state, hero, day, hour, gold, health, health_max)
            VALUES ('2026-01-01T00:00:00', 'EncounterState', 'Karnok', 1, 1, 10, 300, 300)
            """
        )
        prior_gs_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO api_cards (game_state_id, instance_id, template_id, category) "
            "VALUES (?, 'itm_a', 'T_A', 'offered')",
            (prior_gs_id,),
        )
        conn.commit()
    finally:
        conn.close()

    state = RunState("Player.log")
    state.process({"event": "run_start", "ts": "10:00:00"})
    state.process({"event": "session_id", "ts": "10:00:00", "session_id": "sess-prior-guard"})
    state.process({"event": "account_id", "ts": "10:00:00", "account_id": "acct-3"})
    state.process({"event": "hero", "ts": "10:00:00", "hero": "Karnok"})

    # Baseline is set AFTER the prior-run snapshot, so it's excluded
    assert state._snapshot_baseline_id >= prior_gs_id, (
        "baseline must exclude the prior-run snapshot"
    )

    state.process({"event": "state_change", "ts": "10:00:00", "to_state": "EncounterState"})
    state.process({"event": "cards_dealt", "instance_ids": ["itm_a"]})
    state.process({
        "event": "card_purchased",
        "ts": "2026-05-01T10:01:00",
        "instance_id": "itm_a",
        "template_id": "T_A",
        "target_socket": "PlayerSocket0",
        "section": "Player",
    })
    db.flush()

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        d = conn.execute(
            "SELECT * FROM decisions ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert d is not None
        assert d["api_game_state_id_at_offer"] is None, (
            "prior-run snapshot must not be linked even if instance IDs match"
        )
    finally:
        conn.close()
