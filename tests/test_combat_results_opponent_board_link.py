"""
tests/test_combat_results_opponent_board_link.py

Coverage for issue #133 — opponent board capture and combat_results linkage.

Tests:
  - combat_results.api_game_state_id is populated when a Mono snapshot exists
    at combat-start time.
  - Opponent board cards (category='opponent_board') are queryable via the
    linked api_game_state_id.
  - get_combat_opponent_board() returns the expected cards.
  - When no Mono snapshot is present, api_game_state_id is NULL and
    get_combat_opponent_board() returns an empty list.
  - _CAPTURE_OPPONENT_BOARD is True by default.
"""

import json
import sqlite3

import db
import run_state
from run_state import RunState
from web.overlay_state import get_combat_opponent_board


class _NoOpLiveScorer:
    def __init__(self, hero, conn):
        pass

    def score_decision(self, decision, decision_id):
        return {"label": None, "notes": ""}

    def notify_combat(self):
        pass


def _point_db_at(tmp_path, monkeypatch):
    path = tmp_path / "bazaar_runs.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    db.close_shared_conn()
    return path


def _seed_snapshot_with_opponent_board(conn, baseline_id, hero="Karnok"):
    """Insert an api_game_states row and opponent board api_cards rows."""
    gs_id = conn.execute(
        """
        INSERT INTO api_game_states
            (captured_at, run_state, hero, day, hour, gold, health, health_max)
        VALUES ('2026-05-01T12:00:00+00:00', 'Combat', ?, 5, 3, 10, 30, 60)
        RETURNING id
        """,
        (hero,),
    ).fetchone()[0]
    # Ensure this snapshot is above the baseline (i.e. belongs to this run).
    assert gs_id > baseline_id, (
        f"Snapshot id {gs_id} must exceed baseline {baseline_id}"
    )
    # Insert opponent board cards.
    opponent_cards = [
        ("opp_inst_1", "T_OPP_A"),
        ("opp_inst_2", "T_OPP_B"),
        ("opp_inst_3", "T_OPP_C"),
    ]
    conn.executemany(
        """
        INSERT INTO api_cards (game_state_id, instance_id, template_id, category)
        VALUES (?, ?, ?, 'opponent_board')
        """,
        [(gs_id, iid, tid) for iid, tid in opponent_cards],
    )
    conn.commit()
    return gs_id


def _run_full_combat(state):
    """Drive RunState through a minimal combat cycle and resolve outcome.

    The outcome is resolved when the state machine leaves ReplayState, so we
    must emit: combat_start → combat_complete → ReplayState → LootState.
    """
    state.process({"event": "combat_start", "ts": "10:05"})
    state.process({
        "event": "combat_complete",
        "ts": "10:06",
        "duration_secs": 12.5,
    })
    # ReplayState is the intermediate state between combat end and outcome.
    state.process({"event": "state_change", "ts": "10:06", "to_state": "ReplayState"})
    # Transition to LootState from ReplayState -> PvE win (opponent_died).
    state.process({"event": "state_change", "ts": "10:07", "to_state": "LootState"})


def test_combat_results_api_game_state_id_populated_when_mono_present(
    tmp_path, monkeypatch
):
    """api_game_state_id is set on the combat_results row when a Mono snapshot
    exists at fight-start time."""
    path = _point_db_at(tmp_path, monkeypatch)
    db.init_db()
    monkeypatch.setattr(run_state._scorer, "LiveScorer", _NoOpLiveScorer)

    state = RunState("Player.log")
    state.process({"event": "run_start", "ts": "10:00"})
    state.process({"event": "session_id", "ts": "10:00", "session_id": "sess-133"})
    state.process({"event": "account_id", "ts": "10:00", "account_id": "acc-1"})
    state.process({"event": "hero", "ts": "10:00", "hero": "Karnok"})

    baseline = state._snapshot_baseline_id

    conn_rw = sqlite3.connect(path)
    conn_rw.row_factory = sqlite3.Row
    gs_id = _seed_snapshot_with_opponent_board(conn_rw, baseline, hero="Karnok")
    conn_rw.close()

    _run_full_combat(state)
    db.flush()

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT api_game_state_id FROM combat_results ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row is not None, "No combat_results row found"
        assert row["api_game_state_id"] == gs_id, (
            f"Expected api_game_state_id={gs_id}, got {row['api_game_state_id']}"
        )
    finally:
        conn.close()


def test_opponent_board_queryable_via_api_game_state_id(tmp_path, monkeypatch):
    """Opponent board cards are retrievable via get_combat_opponent_board()."""
    path = _point_db_at(tmp_path, monkeypatch)
    db.init_db()
    monkeypatch.setattr(run_state._scorer, "LiveScorer", _NoOpLiveScorer)

    state = RunState("Player.log")
    state.process({"event": "run_start", "ts": "10:00"})
    state.process({"event": "session_id", "ts": "10:00", "session_id": "sess-133b"})
    state.process({"event": "account_id", "ts": "10:00", "account_id": "acc-2"})
    state.process({"event": "hero", "ts": "10:00", "hero": "Karnok"})

    baseline = state._snapshot_baseline_id

    conn_rw = sqlite3.connect(path)
    conn_rw.row_factory = sqlite3.Row
    _seed_snapshot_with_opponent_board(conn_rw, baseline, hero="Karnok")
    conn_rw.close()

    _run_full_combat(state)
    db.flush()

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        combat_row = conn.execute(
            "SELECT id FROM combat_results ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert combat_row is not None

        cards = get_combat_opponent_board(conn, combat_row["id"])
        assert len(cards) == 3
        template_ids = {c["template_id"] for c in cards}
        assert "T_OPP_A" in template_ids
        assert "T_OPP_B" in template_ids
        assert "T_OPP_C" in template_ids
        # All cards must have category='opponent_board' (implied by the helper).
        for c in cards:
            assert c["template_id"] is not None
    finally:
        conn.close()


def test_combat_results_api_game_state_id_null_when_mono_absent(
    tmp_path, monkeypatch
):
    """api_game_state_id is NULL and get_combat_opponent_board() returns [] when
    no Mono snapshot has arrived."""
    path = _point_db_at(tmp_path, monkeypatch)
    db.init_db()
    monkeypatch.setattr(run_state._scorer, "LiveScorer", _NoOpLiveScorer)

    state = RunState("Player.log")
    state.process({"event": "run_start", "ts": "10:00"})
    state.process({"event": "session_id", "ts": "10:00", "session_id": "sess-133c"})
    state.process({"event": "account_id", "ts": "10:00", "account_id": "acc-3"})
    state.process({"event": "hero", "ts": "10:00", "hero": "Karnok"})

    # No Mono snapshot seeded — combat-start finds nothing.
    _run_full_combat(state)
    db.flush()

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT id, api_game_state_id FROM combat_results ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row is not None
        assert row["api_game_state_id"] is None

        cards = get_combat_opponent_board(conn, row["id"])
        assert cards == []
    finally:
        conn.close()


def test_capture_opponent_board_flag_is_true_by_default():
    """_CAPTURE_OPPONENT_BOARD must be True so opponent cards flow into snapshots."""
    import capture_mono
    assert capture_mono._CAPTURE_OPPONENT_BOARD is True


def test_schema_has_api_game_state_id_on_combat_results(tmp_path, monkeypatch):
    """Schema migration adds api_game_state_id to combat_results."""
    path = _point_db_at(tmp_path, monkeypatch)
    db.init_db()

    conn = sqlite3.connect(path)
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(combat_results)").fetchall()}
        assert "api_game_state_id" in cols
    finally:
        conn.close()


def test_migration_adds_column_to_existing_v3_db(tmp_path, monkeypatch):
    """Existing v3 databases get api_game_state_id added by the migration."""
    path = tmp_path / "bazaar_runs.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    db.close_shared_conn()

    # Build a v3-style DB without api_game_state_id.
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE combat_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER,
            timestamp TEXT,
            outcome TEXT,
            combat_type TEXT DEFAULT 'pve',
            duration_secs REAL,
            player_board TEXT,
            opponent_board TEXT
        );
        CREATE TABLE runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT UNIQUE,
            account_id TEXT,
            hero TEXT,
            started_at TEXT,
            ended_at TEXT,
            outcome TEXT,
            raw_log_path TEXT
        );
        PRAGMA user_version = 3;
    """)
    conn.commit()
    conn.close()

    # Running migrate_db should add the column.
    conn2 = sqlite3.connect(path)
    db.migrate_db(conn2)
    conn2.close()

    conn3 = sqlite3.connect(path)
    try:
        cols = {row[1] for row in conn3.execute("PRAGMA table_info(combat_results)").fetchall()}
        assert "api_game_state_id" in cols
        version = conn3.execute("PRAGMA user_version").fetchone()[0]
        assert version == db.SCHEMA_VERSION
    finally:
        conn3.close()
