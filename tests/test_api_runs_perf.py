"""
tests/test_api_runs_perf.py

Tests for the batched /api/runs route (P1-A chunks 1+2 closeout).

Coverage:
  - test_api_runs_response_shape_unchanged   : payload keys + computed counts are correct
  - test_api_runs_empty_db_returns_empty_list: empty DB -> []
"""

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest
import web.server as server


# ── Fixtures ──────────────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hero TEXT,
    outcome TEXT,
    started_at TEXT,
    ended_at TEXT
);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    decision_seq INTEGER,
    decision_type TEXT,
    game_state TEXT,
    board_section TEXT,
    chosen_id TEXT,
    chosen_template TEXT,
    offered TEXT,
    offered_names TEXT,
    rejected TEXT,
    score_label TEXT,
    score_notes TEXT,
    day INTEGER,
    gold INTEGER,
    health INTEGER,
    api_game_state_id INTEGER,
    board_snapshot_json TEXT
);
CREATE TABLE IF NOT EXISTS combat_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    outcome TEXT,
    combat_type TEXT,
    duration_secs REAL,
    timestamp TEXT
);
CREATE TABLE IF NOT EXISTS api_game_states (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    run_state TEXT,
    victories INTEGER,
    defeats INTEGER,
    hero TEXT,
    day INTEGER,
    hour INTEGER,
    gold INTEGER,
    health INTEGER,
    health_max INTEGER,
    captured_at TEXT
);
CREATE TABLE IF NOT EXISTS card_cache (
    template_id TEXT PRIMARY KEY,
    name TEXT,
    tier TEXT
);
"""

_EXPECTED_KEYS = {
    "id", "hero", "outcome", "started_at", "ended_at",
    "pvp_wins", "pvp_losses", "pve_wins", "pve_losses",
    "decision_count", "archetype",
}


def _make_db(tmp_path: Path) -> Path:
    db = tmp_path / "test.db"
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA_SQL)
    conn.close()
    return db


def _seed_run(conn, hero="Dooley", outcome="defeat", started_at="2025-01-01T00:00:00"):
    cur = conn.execute(
        "INSERT INTO runs (hero, outcome, started_at) VALUES (?, ?, ?)",
        (hero, outcome, started_at),
    )
    return cur.lastrowid


def _seed_combat(conn, run_id, combat_type, outcome):
    conn.execute(
        "INSERT INTO combat_results (run_id, combat_type, outcome) VALUES (?, ?, ?)",
        (run_id, combat_type, outcome),
    )


def _seed_decision(conn, run_id, seq, score_notes=None, api_game_state_id=None):
    conn.execute(
        """INSERT INTO decisions
           (run_id, decision_seq, decision_type, score_notes, api_game_state_id)
           VALUES (?, ?, 'item', ?, ?)""",
        (run_id, seq, score_notes, api_game_state_id),
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_api_runs_empty_db_returns_empty_list(tmp_path, monkeypatch):
    db = _make_db(tmp_path)
    monkeypatch.setattr(server, "_get_db_path", lambda: db)

    resp = server.app.test_client().get("/api/runs")

    assert resp.status_code == 200
    assert resp.get_json() == []


def test_api_runs_response_shape_unchanged(tmp_path, monkeypatch):
    """
    Seed 3 runs with PvP + PvE combats and a COMMITTED score_note.
    Verify:
      - each row has exactly the expected keys
      - pvp/pve counts match per-run truth
      - archetype is extracted from COMMITTED note where present
    """
    db = _make_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row

    # Run 1: 2 pvp wins, 1 pvp loss, 1 pve win, COMMITTED note
    r1 = _seed_run(conn, hero="Dooley", outcome="defeat")
    _seed_combat(conn, r1, "pvp", "opponent_died")
    _seed_combat(conn, r1, "pvp", "opponent_died")
    _seed_combat(conn, r1, "pvp", "player_died")
    _seed_combat(conn, r1, "pve", "opponent_died")
    _seed_decision(conn, r1, 1, score_notes="COMMITTED to Freeze build (early phase).")
    _seed_decision(conn, r1, 2, score_notes="COMMITTED to Freeze build (late phase).")

    # Run 2: 0 pvp, 2 pve wins, 1 pve loss, no COMMITTED
    r2 = _seed_run(conn, hero="Mak", outcome="victory")
    _seed_combat(conn, r2, "pve", "opponent_died")
    _seed_combat(conn, r2, "pve", "opponent_died")
    _seed_combat(conn, r2, "pve", "player_died")
    _seed_decision(conn, r2, 1)

    # Run 3: no combats, no decisions
    r3 = _seed_run(conn, hero="Vanessa", outcome=None)

    conn.commit()
    conn.close()

    monkeypatch.setattr(server, "_get_db_path", lambda: db)
    # Stub infer_archetype to avoid loading build files for runs without COMMITTED.
    monkeypatch.setattr(
        server,
        "infer_archetype_from_decisions",
        lambda conn, run_id, **kwargs: (None, None),
    )

    resp = server.app.test_client().get("/api/runs")
    assert resp.status_code == 200

    rows = resp.get_json()
    # Runs are ordered DESC by id, so: r3, r2, r1.
    assert len(rows) == 3
    by_id = {row["id"]: row for row in rows}

    # All rows must have exactly the expected keys.
    for row in rows:
        assert set(row.keys()) == _EXPECTED_KEYS, f"Unexpected keys in row id={row['id']}: {set(row.keys())}"

    # Run 1 — pvp counts from combat_results (no terminal Mono snapshot).
    assert by_id[r1]["pvp_wins"] == 2
    assert by_id[r1]["pvp_losses"] == 1
    assert by_id[r1]["pve_wins"] == 1
    assert by_id[r1]["pve_losses"] == 0
    assert by_id[r1]["archetype"] == "Freeze build"

    # Run 2 — pve only, no pvp.
    assert by_id[r2]["pvp_wins"] == 0
    assert by_id[r2]["pvp_losses"] == 0
    assert by_id[r2]["pve_wins"] == 2
    assert by_id[r2]["pve_losses"] == 1
    # No COMMITTED note; infer stub returns None.
    assert by_id[r2]["archetype"] is None

    # Run 3 — nothing seeded.
    assert by_id[r3]["pvp_wins"] == 0
    assert by_id[r3]["pve_wins"] == 0
    assert by_id[r3]["decision_count"] == 0
    assert by_id[r3]["archetype"] is None


def test_api_runs_terminal_mono_overrides_combat_counts(tmp_path, monkeypatch):
    """
    When a terminal Mono EndRun snapshot exists for a run, victories/defeats
    from that snapshot replace the derived combat_results counts.
    """
    db = _make_db(tmp_path)
    conn = sqlite3.connect(db)

    r1 = _seed_run(conn, hero="Karnok", outcome="defeat")
    # Combat-derived: 1 pvp win, 0 losses.
    _seed_combat(conn, r1, "pvp", "opponent_died")
    # Decision anchors at api_game_state_id=10.
    _seed_decision(conn, r1, 1, api_game_state_id=10)

    # Seed an EndRun snapshot with id >= 10, hero matching.
    conn.execute(
        """INSERT INTO api_game_states
           (id, run_id, run_state, victories, defeats, hero)
           VALUES (10, ?, 'EndRunDefeat', 3, 2, 'Karnok')""",
        (r1,),
    )

    conn.commit()
    conn.close()

    monkeypatch.setattr(server, "_get_db_path", lambda: db)
    monkeypatch.setattr(
        server,
        "infer_archetype_from_decisions",
        lambda conn, run_id, **kwargs: (None, None),
    )

    resp = server.app.test_client().get("/api/runs")
    assert resp.status_code == 200
    rows = resp.get_json()
    assert len(rows) == 1
    row = rows[0]
    # Terminal Mono snapshot overrides: 3 wins, 2 losses.
    assert row["pvp_wins"] == 3
    assert row["pvp_losses"] == 2


def test_api_runs_prefers_mono_anchored_run_record(tmp_path, monkeypatch):
    """
    Mono snapshots should be the preferred source for run records when they are
    anchored to the run by decision api_game_state_id values.
    """
    db = _make_db(tmp_path)
    conn = sqlite3.connect(db)

    r1 = _seed_run(conn, hero="Karnok", outcome="defeat")
    # Stale pre-Mono rows should not win when Mono can describe the run.
    _seed_combat(conn, r1, "pve", "opponent_died")
    _seed_decision(conn, r1, 1, api_game_state_id=10)

    conn.executemany(
        """INSERT INTO api_game_states
           (id, run_id, run_state, victories, defeats, hero, day, hour)
           VALUES (?, ?, ?, ?, ?, 'Karnok', ?, ?)""",
        [
            (10, r1, "Choice", 0, 0, 1, 0),
            (11, r1, "Combat", 0, 0, 1, 3),
            (12, r1, "Loot", 0, 0, 1, 3),
            (13, r1, "Combat", 0, 0, 2, 3),
            (14, r1, "Choice", 0, 0, 2, 3),
            (15, r1, "PVPCombat", 0, 0, 2, 6),
            (16, r1, "Choice", 1, 0, 3, 0),
            (17, r1, "PVPCombat", 1, 0, 3, 6),
            (18, r1, "EndRunDefeat", 1, 1, 3, 6),
        ],
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(server, "_get_db_path", lambda: db)
    monkeypatch.setattr(
        server,
        "infer_archetype_from_decisions",
        lambda conn, run_id, **kwargs: (None, None),
    )

    resp = server.app.test_client().get("/api/runs")
    assert resp.status_code == 200
    row = resp.get_json()[0]

    assert row["pvp_wins"] == 1
    assert row["pvp_losses"] == 1
    assert row["pve_wins"] == 1
    assert row["pve_losses"] == 1
