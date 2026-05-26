import sqlite3

import db


REQUIRED_COLUMNS = {
    "runs": {"session_id", "account_id", "hero", "started_at", "ended_at", "outcome", "raw_log_path"},
    "decisions": {
        "board_snapshot_json",
        "offered_names",
        "offered_templates",
        "day",
        "hour",
        "gold",
        "health",
        "health_max",
        "api_game_state_id",
        "phase_actual",
        "api_game_state_id_at_offer",
    },
    "combat_results": {"combat_type"},
}

REQUIRED_TABLES = {
    "runs",
    "decisions",
    "combat_results",
    "card_cache",
    "api_messages",
    "api_game_states",
    "api_cards",
    "api_player_attrs",
}


def _point_db_at(tmp_path, monkeypatch):
    path = tmp_path / "bazaar_runs.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    db.close_shared_conn()
    return path


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _tables(conn):
    return {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def test_init_db_creates_latest_schema(tmp_path, monkeypatch):
    path = _point_db_at(tmp_path, monkeypatch)

    db.init_db()

    conn = sqlite3.connect(path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
        assert REQUIRED_TABLES.issubset(_tables(conn))
        for table, required in REQUIRED_COLUMNS.items():
            assert required.issubset(_columns(conn, table))
    finally:
        conn.close()


def test_v3_to_v4_migration_adds_offer_snapshot_column(tmp_path, monkeypatch):
    """An existing v3 database gains api_game_state_id_at_offer after migration."""
    path = _point_db_at(tmp_path, monkeypatch)

    # Simulate a v3 database: create the decisions table WITHOUT the new column
    # and stamp user_version = 3.
    conn = sqlite3.connect(path)
    try:
        conn.executescript("""
            CREATE TABLE decisions (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id              INTEGER,
                decision_seq        INTEGER,
                timestamp           TEXT,
                game_state          TEXT,
                decision_type       TEXT,
                offered             TEXT,
                chosen_id           TEXT,
                chosen_template     TEXT,
                rejected            TEXT,
                board_section       TEXT,
                target_socket       TEXT,
                score_label         TEXT,
                score_notes         TEXT DEFAULT '',
                board_snapshot_json TEXT,
                offered_names       TEXT,
                offered_templates   TEXT,
                day                 INTEGER,
                hour                INTEGER,
                gold                INTEGER,
                health              INTEGER,
                health_max          INTEGER,
                api_game_state_id   INTEGER,
                phase_actual        TEXT
            );
            PRAGMA user_version = 3;
        """)
        conn.commit()
    finally:
        conn.close()

    db.init_db()

    conn = sqlite3.connect(path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
        cols = _columns(conn, "decisions")
        assert "api_game_state_id_at_offer" in cols
    finally:
        conn.close()
