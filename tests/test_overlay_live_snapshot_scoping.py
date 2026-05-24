"""Regression tests for issue #83.

Before the fix, ``_get_latest_live_snapshot`` selected the most recent
non-terminal ``api_game_states`` row in the whole DB — so a prior run that
never wrote a terminal Mono snapshot would bleed its day/victories into the
next run's overlay header. These tests pin the scoping behavior in place.
"""

import sqlite3

import db
from web.overlay_state import (
    _get_in_run_prestige_fallback,
    _get_latest_live_snapshot,
    build_overlay_state,
)


def _point_db_at(tmp_path, monkeypatch):
    path = tmp_path / "bazaar_runs.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    db.close_shared_conn()
    return path


def _insert_run(conn, *, hero, started_at, outcome=None, ended_at=None):
    cur = conn.execute(
        """
        INSERT INTO runs (session_id, account_id, hero, started_at, ended_at, outcome)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (f"sess-{started_at}", "acct-1", hero, started_at, ended_at, outcome),
    )
    return cur.lastrowid


def _insert_snapshot(conn, *, hero, run_state, day=None, victories=None, defeats=None,
                     gold=None, health=None, captured_at="2026-05-23T10:00:00+00:00"):
    cur = conn.execute(
        """
        INSERT INTO api_game_states
            (captured_at, run_state, hero, day, victories, defeats, gold, health)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (captured_at, run_state, hero, day, victories, defeats, gold, health),
    )
    return cur.lastrowid


def _insert_decision(conn, *, run_id, seq, api_game_state_id):
    conn.execute(
        """
        INSERT INTO decisions (run_id, decision_seq, timestamp, game_state,
                               decision_type, api_game_state_id)
        VALUES (?, ?, '2026-05-23T10:00:00+00:00', 'EncounterState', 'item', ?)
        """,
        (run_id, seq, api_game_state_id),
    )


def test_live_snapshot_does_not_bleed_from_prior_run(tmp_path, monkeypatch):
    _point_db_at(tmp_path, monkeypatch)
    db.init_db()
    conn = sqlite3.connect(tmp_path / "bazaar_runs.db")
    conn.row_factory = sqlite3.Row
    try:
        run_a = _insert_run(conn, hero="Karnok", started_at="2026-05-23T09:00:00+00:00")
        a_gs1 = _insert_snapshot(conn, hero="Karnok", run_state="EncounterState",
                                 day=14, victories=9, defeats=4, gold=22, health=10)
        a_gs2 = _insert_snapshot(conn, hero="Karnok", run_state="EncounterState",
                                 day=14, victories=9, defeats=4, gold=18, health=8)
        _insert_decision(conn, run_id=run_a, seq=1, api_game_state_id=a_gs1)
        _insert_decision(conn, run_id=run_a, seq=2, api_game_state_id=a_gs2)

        run_b = _insert_run(conn, hero="Karnok", started_at="2026-05-23T11:00:00+00:00")
        b_gs1 = _insert_snapshot(conn, hero="Karnok", run_state="EncounterState",
                                 day=6, victories=3, defeats=1, gold=12, health=30)
        _insert_decision(conn, run_id=run_b, seq=1, api_game_state_id=b_gs1)
        conn.commit()

        snap = _get_latest_live_snapshot(conn, {"id": run_b, "hero": "Karnok"})
        assert snap is not None, "expected a snapshot for the active run"
        assert snap["id"] == b_gs1
        assert snap["day"] == 6
        assert snap["victories"] == 3
        assert snap["defeats"] == 1
        assert snap["health"] == 30
    finally:
        conn.close()


def test_live_snapshot_returns_none_when_run_has_no_linked_snapshots(tmp_path, monkeypatch):
    """Cold-start: new run has decisions but Mono hasn't snapshotted yet.

    Caller (``build_overlay_state``) then falls back to ``decision_fallback``;
    the helper must not borrow from a prior run.
    """
    _point_db_at(tmp_path, monkeypatch)
    db.init_db()
    conn = sqlite3.connect(tmp_path / "bazaar_runs.db")
    conn.row_factory = sqlite3.Row
    try:
        run_a = _insert_run(conn, hero="Karnok", started_at="2026-05-23T09:00:00+00:00")
        a_gs1 = _insert_snapshot(conn, hero="Karnok", run_state="EncounterState",
                                 day=14, victories=9)
        _insert_decision(conn, run_id=run_a, seq=1, api_game_state_id=a_gs1)

        run_b = _insert_run(conn, hero="Karnok", started_at="2026-05-23T11:00:00+00:00")
        conn.execute(
            """
            INSERT INTO decisions (run_id, decision_seq, timestamp, game_state, decision_type)
            VALUES (?, 1, '2026-05-23T11:00:00+00:00', 'EncounterState', 'item')
            """,
            (run_b,),
        )
        conn.commit()

        snap = _get_latest_live_snapshot(conn, {"id": run_b, "hero": "Karnok"})
        assert snap is None
    finally:
        conn.close()


def test_live_snapshot_excludes_other_hero_rows(tmp_path, monkeypatch):
    _point_db_at(tmp_path, monkeypatch)
    db.init_db()
    conn = sqlite3.connect(tmp_path / "bazaar_runs.db")
    conn.row_factory = sqlite3.Row
    try:
        run_a = _insert_run(conn, hero="Karnok", started_at="2026-05-23T09:00:00+00:00")
        a_gs1 = _insert_snapshot(conn, hero="Karnok", run_state="EncounterState",
                                 day=14, victories=99)
        _insert_decision(conn, run_id=run_a, seq=1, api_game_state_id=a_gs1)

        run_b = _insert_run(conn, hero="Vanessa", started_at="2026-05-23T11:00:00+00:00")
        b_gs1 = _insert_snapshot(conn, hero="Vanessa", run_state="EncounterState",
                                 day=2, victories=1)
        _insert_decision(conn, run_id=run_b, seq=1, api_game_state_id=b_gs1)
        # A Karnok snapshot lands *after* run B's first snapshot (id-wise) —
        # the hero filter must reject it.
        k_late = _insert_snapshot(conn, hero="Karnok", run_state="EncounterState",
                                  day=15, victories=99)
        conn.commit()

        snap = _get_latest_live_snapshot(conn, {"id": run_b, "hero": "Vanessa"})
        assert snap is not None
        assert snap["id"] == b_gs1
        assert snap["victories"] == 1
        # Sanity: that Karnok row exists and is newer in id-order.
        assert k_late > b_gs1
    finally:
        conn.close()


def _insert_decision_with_hour(conn, *, run_id, seq, day, hour, api_game_state_id=None):
    conn.execute(
        """
        INSERT INTO decisions (run_id, decision_seq, timestamp, game_state,
                               decision_type, day, hour, api_game_state_id)
        VALUES (?, ?, '2026-05-23T10:00:00+00:00', 'EncounterState', 'item', ?, ?, ?)
        """,
        (run_id, seq, day, hour, api_game_state_id),
    )


def test_decision_fallback_surfaces_hour_from_latest_decision(tmp_path, monkeypatch):
    """Render-side regression: ``hour`` is stamped on ``decisions`` but the
    ``decision_fallback`` branch in ``build_overlay_state`` previously only
    pulled day/gold/health — hour silently dropped out of the header.
    """
    _point_db_at(tmp_path, monkeypatch)
    db.init_db()
    conn = sqlite3.connect(tmp_path / "bazaar_runs.db")
    conn.row_factory = sqlite3.Row
    try:
        run_a = _insert_run(conn, hero="Karnok", started_at="2026-05-23T11:00:00+00:00")
        # Decision stamped with day/hour from a since-departed live snapshot.
        # ``api_game_state_id`` is NULL because the snapshot has been pruned
        # or never persisted; build_overlay_state must still surface hour.
        _insert_decision_with_hour(conn, run_id=run_a, seq=1, day=3, hour=2)
        conn.commit()

        state = build_overlay_state(
            conn,
            resolve_fn=lambda _c, _t: "",
            safe_json_fn=lambda _r, default: default,
            lookup_image_by_name_fn=lambda _c, _n: None,
        )
        assert state["snapshot_source"] == "decision_fallback"
        assert state["day"] == 3
        assert state["hour"] == 2
    finally:
        conn.close()


def test_prestige_fallback_reads_in_run_snapshot_via_full_json(tmp_path, monkeypatch):
    """When the live-snapshot path is empty but at least one in-run snapshot
    exists with a Prestige field, ``_get_in_run_prestige_fallback`` should
    surface it instead of dropping the field from the header.
    """
    _point_db_at(tmp_path, monkeypatch)
    db.init_db()
    conn = sqlite3.connect(tmp_path / "bazaar_runs.db")
    conn.row_factory = sqlite3.Row
    try:
        run_a = _insert_run(conn, hero="Karnok", started_at="2026-05-23T09:00:00+00:00")
        gs_id = conn.execute(
            """
            INSERT INTO api_game_states
                (captured_at, run_state, hero, day, victories, full_json)
            VALUES ('2026-05-23T10:00:00+00:00', 'EndRunVictory', 'Karnok',
                    14, 10, '{"player": {"Prestige": 7}}')
            RETURNING id
            """
        ).fetchone()[0]
        _insert_decision(conn, run_id=run_a, seq=1, api_game_state_id=gs_id)
        conn.commit()

        prestige = _get_in_run_prestige_fallback(conn, {"id": run_a, "hero": "Karnok"})
        assert prestige == 7
    finally:
        conn.close()


def test_prestige_fallback_does_not_leak_from_prior_run(tmp_path, monkeypatch):
    """When the active run has no in-run snapshot at all, the prestige
    fallback must return None rather than borrowing from a prior run.
    """
    _point_db_at(tmp_path, monkeypatch)
    db.init_db()
    conn = sqlite3.connect(tmp_path / "bazaar_runs.db")
    conn.row_factory = sqlite3.Row
    try:
        run_a = _insert_run(conn, hero="Karnok", started_at="2026-05-23T09:00:00+00:00")
        a_gs1 = conn.execute(
            """
            INSERT INTO api_game_states
                (captured_at, run_state, hero, day, victories, full_json)
            VALUES ('2026-05-23T10:00:00+00:00', 'EncounterState', 'Karnok',
                    14, 9, '{"player": {"Prestige": 99}}')
            RETURNING id
            """
        ).fetchone()[0]
        _insert_decision(conn, run_id=run_a, seq=1, api_game_state_id=a_gs1)

        run_b = _insert_run(conn, hero="Karnok", started_at="2026-05-23T11:00:00+00:00")
        # Run B has decisions but NONE of them carry api_game_state_id (cold
        # start: capture_mono was not producing snapshots).
        conn.execute(
            """
            INSERT INTO decisions (run_id, decision_seq, timestamp, game_state, decision_type)
            VALUES (?, 1, '2026-05-23T11:00:00+00:00', 'EncounterState', 'item')
            """,
            (run_b,),
        )
        conn.commit()

        prestige = _get_in_run_prestige_fallback(conn, {"id": run_b, "hero": "Karnok"})
        assert prestige is None
    finally:
        conn.close()


def test_live_snapshot_skips_terminal_states(tmp_path, monkeypatch):
    """Terminal EndRun rows belong to ``_get_run_end_snapshot``, not live."""
    _point_db_at(tmp_path, monkeypatch)
    db.init_db()
    conn = sqlite3.connect(tmp_path / "bazaar_runs.db")
    conn.row_factory = sqlite3.Row
    try:
        run_a = _insert_run(conn, hero="Karnok", started_at="2026-05-23T09:00:00+00:00")
        live_id = _insert_snapshot(conn, hero="Karnok", run_state="EncounterState",
                                   day=5, victories=2)
        _insert_snapshot(conn, hero="Karnok", run_state="EndRunDefeat",
                         day=5, victories=2)
        _insert_decision(conn, run_id=run_a, seq=1, api_game_state_id=live_id)
        conn.commit()

        snap = _get_latest_live_snapshot(conn, {"id": run_a, "hero": "Karnok"})
        assert snap is not None
        assert snap["id"] == live_id
        assert snap["run_state"] == "EncounterState"
    finally:
        conn.close()
