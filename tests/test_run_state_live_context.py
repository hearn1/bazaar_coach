import json
import sqlite3

import db
import run_state
from run_state import RunState


class RecordingLiveScorer:
    calls = []

    def __init__(self, hero, conn):
        self.hero = hero

    def score_decision(self, decision, decision_id):
        self.__class__.calls.append((decision_id, dict(decision)))
        db.update_decision_score(decision_id, decision.get("phase_actual"), "scored live")
        return {"label": decision.get("phase_actual"), "notes": "scored live"}

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


def test_run_state_decision_insert_includes_live_context(tmp_path, monkeypatch):
    path = _point_db_at(tmp_path, monkeypatch)
    db.init_db()
    monkeypatch.setattr(run_state._scorer, "LiveScorer", RecordingLiveScorer)
    RecordingLiveScorer.calls = []

    conn = sqlite3.connect(path)
    try:
        _seed_card(conn, "T_A", "Cool Item")
        _seed_card(conn, "T_B", "Warm Item")
        conn.commit()
    finally:
        conn.close()

    state = RunState()
    state.process({"event": "run_start", "ts": "10:00"})
    state.process({"event": "session_id", "ts": "10:00", "session_id": "session-1"})
    state.process({"event": "account_id", "ts": "10:00", "account_id": "account-1"})
    state.process({"event": "hero", "ts": "10:00", "hero": "Karnok"})

    # Snapshot must land AFTER _try_init_run sets the baseline, otherwise it
    # is treated as belonging to a prior run (latent half of #83 guard).
    conn = sqlite3.connect(path)
    try:
        gs_id = conn.execute(
            """
            INSERT INTO api_game_states
                (captured_at, run_state, hero, day, hour, gold, health, health_max)
            VALUES ('2026-05-01T12:00:00+00:00', 'EncounterState', 'Karnok', 8, 2, 13, 44, 60)
            RETURNING id
            """
        ).fetchone()[0]
        conn.executemany(
            """
            INSERT INTO api_cards (game_state_id, instance_id, template_id, category)
            VALUES (?, ?, ?, 'offered')
            """,
            [(gs_id, "itm_a", "T_A"), (gs_id, "itm_b", "T_B")],
        )
        conn.commit()
    finally:
        conn.close()

    state.process({"event": "state_change", "ts": "10:01", "to_state": "EncounterState"})
    state.process({"event": "cards_dealt", "instance_ids": ["itm_a", "itm_b"]})
    state.process({
        "event": "card_purchased",
        "ts": "10:02",
        "instance_id": "itm_a",
        "template_id": "T_A",
        "target_socket": "PlayerSocket0",
        "section": "Player",
    })
    db.flush()

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        d = conn.execute("SELECT * FROM decisions ORDER BY id DESC LIMIT 1").fetchone()
        assert d["day"] == 8
        assert d["hour"] == 2
        assert d["gold"] == 13
        assert d["health"] == 44
        assert d["health_max"] == 60
        assert d["api_game_state_id"] == gs_id
        assert d["phase_actual"] == "late"
        assert json.loads(d["offered_names"]) == ["Cool Item", "Warm Item"]
        assert json.loads(d["offered_templates"]) == {"itm_a": "T_A", "itm_b": "T_B"}
        assert d["score_label"] == "late"
    finally:
        conn.close()

    assert RecordingLiveScorer.calls
    _decision_id, scored_decision = RecordingLiveScorer.calls[-1]
    assert scored_decision["day"] == 8
    assert scored_decision["phase_actual"] == "late"
    assert json.loads(scored_decision["offered_names"]) == ["Cool Item", "Warm Item"]


def test_prior_run_snapshot_does_not_stamp_new_run_decision(tmp_path, monkeypatch):
    """The latent half of #83: capture_mono can be dead at the start of a
    new run while a prior run's terminal snapshot still sits at MAX(id).
    Without the baseline guard, every fresh decision was stamped with that
    prior snapshot — poisoning ``decisions.api_game_state_id`` and bleeding
    day/hour values onto the overlay header.
    """
    path = _point_db_at(tmp_path, monkeypatch)
    db.init_db()
    monkeypatch.setattr(run_state._scorer, "LiveScorer", RecordingLiveScorer)
    RecordingLiveScorer.calls = []

    conn = sqlite3.connect(path)
    try:
        _seed_card(conn, "T_A", "Cool Item")
        # Pre-existing prior-run terminal snapshot at MAX(id).
        conn.execute(
            """
            INSERT INTO api_game_states
                (captured_at, run_state, hero, day, hour, gold, health, health_max,
                 victories, defeats)
            VALUES ('2026-05-22T23:00:00+00:00', 'EndRunVictory', 'Karnok',
                    14, 6, 5, 9150, 0, 10, 4)
            """
        )
        conn.commit()
    finally:
        conn.close()

    state = RunState()
    state.process({"event": "run_start", "ts": "10:00"})
    state.process({"event": "session_id", "ts": "10:00", "session_id": "session-new"})
    state.process({"event": "account_id", "ts": "10:00", "account_id": "account-new"})
    state.process({"event": "hero", "ts": "10:00", "hero": "Karnok"})
    state.process({"event": "state_change", "ts": "10:01", "to_state": "EncounterState"})
    state.process({"event": "cards_dealt", "instance_ids": ["itm_a"]})
    state.process({
        "event": "card_purchased",
        "ts": "10:02",
        "instance_id": "itm_a",
        "template_id": "T_A",
        "target_socket": "PlayerSocket0",
        "section": "Player",
    })
    db.flush()

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        d = conn.execute("SELECT * FROM decisions ORDER BY id DESC LIMIT 1").fetchone()
        assert d["api_game_state_id"] is None, "must not stamp prior-run snapshot"
        assert d["day"] is None
        assert d["hour"] is None
    finally:
        conn.close()


def test_post_init_snapshot_attaches_even_with_prior_terminal(tmp_path, monkeypatch):
    """Companion to the guard test: a fresh snapshot that lands AFTER the run
    initializes is still picked up correctly, even when a prior-run terminal
    snapshot precedes it.
    """
    path = _point_db_at(tmp_path, monkeypatch)
    db.init_db()
    monkeypatch.setattr(run_state._scorer, "LiveScorer", RecordingLiveScorer)
    RecordingLiveScorer.calls = []

    conn = sqlite3.connect(path)
    try:
        _seed_card(conn, "T_A", "Cool Item")
        conn.execute(
            """
            INSERT INTO api_game_states
                (captured_at, run_state, hero, day, hour, gold, health, health_max,
                 victories, defeats)
            VALUES ('2026-05-22T23:00:00+00:00', 'EndRunVictory', 'Karnok',
                    14, 6, 5, 9150, 0, 10, 4)
            """
        )
        conn.commit()
    finally:
        conn.close()

    state = RunState()
    state.process({"event": "run_start", "ts": "10:00"})
    state.process({"event": "session_id", "ts": "10:00", "session_id": "session-x"})
    state.process({"event": "account_id", "ts": "10:00", "account_id": "account-x"})
    state.process({"event": "hero", "ts": "10:00", "hero": "Karnok"})

    conn = sqlite3.connect(path)
    try:
        fresh_id = conn.execute(
            """
            INSERT INTO api_game_states
                (captured_at, run_state, hero, day, hour, gold, health, health_max)
            VALUES ('2026-05-24T05:00:00+00:00', 'EncounterState', 'Karnok',
                    1, 1, 10, 300, 300)
            RETURNING id
            """
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO api_cards (game_state_id, instance_id, template_id, category) "
            "VALUES (?, 'itm_a', 'T_A', 'offered')",
            (fresh_id,),
        )
        conn.commit()
    finally:
        conn.close()

    state.process({"event": "state_change", "ts": "10:01", "to_state": "EncounterState"})
    state.process({"event": "cards_dealt", "instance_ids": ["itm_a"]})
    state.process({
        "event": "card_purchased",
        "ts": "10:02",
        "instance_id": "itm_a",
        "template_id": "T_A",
        "target_socket": "PlayerSocket0",
        "section": "Player",
    })
    db.flush()

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        d = conn.execute("SELECT * FROM decisions ORDER BY id DESC LIMIT 1").fetchone()
        assert d["api_game_state_id"] == fresh_id
        assert d["day"] == 1
        assert d["hour"] == 1
    finally:
        conn.close()


def test_terminal_snapshot_after_init_still_excluded(tmp_path, monkeypatch):
    """Even a fresh snapshot above the baseline must not be stamped if it is
    a terminal EndRun row — those belong to the overlay end-snapshot path.
    """
    path = _point_db_at(tmp_path, monkeypatch)
    db.init_db()
    monkeypatch.setattr(run_state._scorer, "LiveScorer", RecordingLiveScorer)
    RecordingLiveScorer.calls = []

    conn = sqlite3.connect(path)
    try:
        _seed_card(conn, "T_A", "Cool Item")
        conn.commit()
    finally:
        conn.close()

    state = RunState()
    state.process({"event": "run_start", "ts": "10:00"})
    state.process({"event": "session_id", "ts": "10:00", "session_id": "session-t"})
    state.process({"event": "account_id", "ts": "10:00", "account_id": "account-t"})
    state.process({"event": "hero", "ts": "10:00", "hero": "Karnok"})

    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            INSERT INTO api_game_states
                (captured_at, run_state, hero, day, hour, gold, health, health_max,
                 victories, defeats)
            VALUES ('2026-05-24T05:00:00+00:00', 'EndRunDefeat', 'Karnok',
                    7, 3, 0, 0, 300, 4, 6)
            """
        )
        conn.commit()
    finally:
        conn.close()

    state.process({"event": "state_change", "ts": "10:01", "to_state": "EncounterState"})
    state.process({"event": "cards_dealt", "instance_ids": ["itm_a"]})
    state.process({
        "event": "card_purchased",
        "ts": "10:02",
        "instance_id": "itm_a",
        "template_id": "T_A",
        "target_socket": "PlayerSocket0",
        "section": "Player",
    })
    db.flush()

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        d = conn.execute("SELECT * FROM decisions ORDER BY id DESC LIMIT 1").fetchone()
        assert d["api_game_state_id"] is None
        assert d["day"] is None
        assert d["hour"] is None
    finally:
        conn.close()


def test_on_run_start_closes_prior_open_run(tmp_path, monkeypatch):
    """A second run_start within the same session must close the prior open run.

    Regression for #83: when a new run begins without an EndRun event for the
    prior one, the prior runs row was left with ``outcome IS NULL`` and its
    Mono trail bled into the new run's overlay header.
    """
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

    state = RunState()
    state.process({"event": "run_start", "ts": "10:00"})
    state.process({"event": "session_id", "ts": "10:00", "session_id": "session-1"})
    state.process({"event": "account_id", "ts": "10:00", "account_id": "account-1"})
    state.process({"event": "hero", "ts": "10:00", "hero": "Karnok"})
    first_run_id = state.run_id
    assert first_run_id is not None
    assert not close_calls, "no run should be closed yet"

    # Second run_start arrives without a session change (game started a new run
    # within the same play session). The prior run must be closed defensively.
    state.process({"event": "run_start", "ts": "10:30"})

    assert close_calls == [(first_run_id, "10:30", "interrupted")]
    assert state.run_id is None
    assert state.hero == "Karnok"  # carried over by _on_run_start
    db.flush()

    conn = sqlite3.connect(tmp_path / "bazaar_runs.db")
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT outcome, ended_at FROM runs WHERE id = ?", (first_run_id,)
        ).fetchone()
        assert row["outcome"] == "interrupted"
        assert row["ended_at"] == "10:30"
    finally:
        conn.close()
