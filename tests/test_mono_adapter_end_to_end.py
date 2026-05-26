"""
test_mono_adapter_end_to_end.py

Proves the MonoEventAdapter → RunState path is testable end-to-end by feeding
representative snapshots (purchase, reroll, sell, combat) through the adapter
into a real RunState wired to a temporary SQLite DB.

This is the long-lived harness described in issue #135 step 3.  It does not
depend on watcher.py, parser.py, or any real Player.log file.

Notes on what each category verifies end-to-end:
  purchase — card_purchased event → RunState records 'item' decision row.
  reroll   — reroll event → RunState records the reroll (no extra decision row).
  sell     — card_sold event → RunState pops card from BoardState (no separate
             decision row — sells update the board snapshot, not decisions).
  combat   — CombatState snapshot → RunState sets combat_start_ts (combat_start
             event received).  Full combat_results insert requires a terminal
             state via the log path; here we verify state tracking only.
"""

import sqlite3

import db
import run_state
from run_state import RunState
from mono_event_adapter import MonoEventAdapter


# ---------------------------------------------------------------------------
# Shared test infrastructure
# ---------------------------------------------------------------------------

class _NullScorer:
    def __init__(self, hero, conn):
        pass
    def score_decision(self, decision, decision_id):
        pass
    def notify_combat(self):
        pass


def _point_db_at(tmp_path, monkeypatch, name="e2e_test.db"):
    path = tmp_path / name
    monkeypatch.setattr(db, "DB_PATH", path)
    db.close_shared_conn()
    return path


def _make_snap(
    state: str = "EncounterState",
    offered: list | None = None,
    board: list | None = None,
    skills: list | None = None,
    gold: int = 10,
    hp: int = 60,
    hp_max: int = 60,
    rerolls: int = 3,
    hero: str = "Karnok",
    ts: str = "2026-01-01T00:00:00+00:00",
) -> dict:
    return {
        "timestamp": ts,
        "state": {"state": state, "rerolls_remaining": rerolls},
        "player": {"hero": hero, "Gold": gold, "Health": hp, "HealthMax": hp_max},
        "run": {"day": 2, "hour": 1},
        "offered": offered or [],
        "player_board": board or [],
        "player_stash": [],
        "player_skills": skills or [],
        "opponent_board": [],
    }


def _bootstrap_run(adapter: MonoEventAdapter, state: RunState, hero: str = "Karnok"):
    """Bootstrap a run through the adapter.

    Two snapshots are needed: the NewRun state emits run_start only; the
    following EncounterState snapshot triggers the bootstrap events (hero,
    session_id, account_id, run_init_complete) which cause RunState to open a
    run record.
    """
    adapter.process_snapshot(_make_snap("NewRun", hero=hero, ts="2026-01-01T00:00:00+00:00"))
    adapter.process_snapshot(_make_snap("EncounterState", hero=hero, ts="2026-01-01T00:00:00+00:00"))
    assert state.run_id is not None, "run must initialise from bootstrap snapshots"


def _count_decisions(path, run_id, decision_type=None):
    conn = sqlite3.connect(path)
    try:
        if decision_type:
            return conn.execute(
                "SELECT COUNT(*) FROM decisions WHERE run_id = ? AND decision_type = ?",
                (run_id, decision_type),
            ).fetchone()[0]
        return conn.execute(
            "SELECT COUNT(*) FROM decisions WHERE run_id = ?", (run_id,)
        ).fetchone()[0]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Purchase
# ---------------------------------------------------------------------------

def test_purchase_writes_item_decision(tmp_path, monkeypatch):
    """
    Offered item moves to player_board → adapter emits card_purchased →
    RunState inserts an 'item' decision row with the correct chosen_id.
    """
    path = _point_db_at(tmp_path, monkeypatch)
    db.init_db()
    monkeypatch.setattr(run_state._scorer, "LiveScorer", _NullScorer)

    state = RunState(event_source="mono")
    adapter = MonoEventAdapter(state, event_source="mono")

    _bootstrap_run(adapter, state, hero="Karnok")

    # Snapshot: item offered in shop
    adapter.process_snapshot(_make_snap(
        "EncounterState",
        offered=[{"instance_id": "itm_sword", "template_id": "T_SWORD"}],
        board=[],
        gold=10,
        ts="2026-01-01T00:00:01+00:00",
    ))

    # Snapshot: item purchased (moved to board, gold decreased)
    adapter.process_snapshot(_make_snap(
        "EncounterState",
        offered=[],
        board=[{"instance_id": "itm_sword", "template_id": "T_SWORD",
                "socket": "PlayerSocket_0"}],
        gold=7,
        ts="2026-01-01T00:00:02+00:00",
    ))

    db.flush()

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT decision_type, chosen_id FROM decisions WHERE run_id = ?",
            (state.run_id,),
        ).fetchall()
    finally:
        conn.close()

    item_rows = [r for r in rows if r["decision_type"] == "item"]
    assert len(item_rows) >= 1, f"Expected at least 1 item decision, got {rows}"
    assert item_rows[0]["chosen_id"] == "itm_sword"


# ---------------------------------------------------------------------------
# Reroll
# ---------------------------------------------------------------------------

def test_reroll_produces_skip_decisions(tmp_path, monkeypatch):
    """
    rerolls_remaining drops → adapter emits reroll event → RunState records
    each abandoned offered set as a 'skip' decision.

    Two rerolls → two skip decisions (one for each replaced offered set).
    """
    path = _point_db_at(tmp_path, monkeypatch, name="reroll_e2e.db")
    db.init_db()
    monkeypatch.setattr(run_state._scorer, "LiveScorer", _NullScorer)

    state = RunState(event_source="mono")
    adapter = MonoEventAdapter(state, event_source="mono")

    _bootstrap_run(adapter, state, hero="Mak")

    # Initial shop offering
    adapter.process_snapshot(_make_snap(
        "EncounterState",
        offered=[{"instance_id": "itm_a", "template_id": "T_A"},
                 {"instance_id": "itm_b", "template_id": "T_B"}],
        rerolls=3,
        ts="2026-01-01T00:00:01+00:00",
        hero="Mak",
    ))

    # First reroll: rerolls_remaining drops, offered set replaced
    adapter.process_snapshot(_make_snap(
        "EncounterState",
        offered=[{"instance_id": "itm_x", "template_id": "T_X"},
                 {"instance_id": "itm_y", "template_id": "T_Y"}],
        rerolls=2,
        ts="2026-01-01T00:00:02+00:00",
        hero="Mak",
    ))

    # Second reroll
    adapter.process_snapshot(_make_snap(
        "EncounterState",
        offered=[{"instance_id": "itm_p", "template_id": "T_P"},
                 {"instance_id": "itm_q", "template_id": "T_Q"}],
        rerolls=1,
        ts="2026-01-01T00:00:03+00:00",
        hero="Mak",
    ))
    db.flush()

    skip_count = _count_decisions(path, state.run_id, decision_type="skip")
    # Each reroll abandons the previous offered set, producing one skip per reroll.
    assert skip_count == 2, (
        f"Expected 2 skip decisions (one per reroll), got {skip_count}"
    )


# ---------------------------------------------------------------------------
# Sell
# ---------------------------------------------------------------------------

def test_sell_is_processed_and_removes_card_from_board(tmp_path, monkeypatch):
    """
    card_sold event → RunState pops the card from BoardState.

    Sells are not persisted as decision rows; they update the board snapshot
    on the next decision.  We verify the sell path is reachable and does not
    raise by confirming the card is no longer in board state afterward.
    """
    _point_db_at(tmp_path, monkeypatch, name="sell_e2e.db")
    db.init_db()
    monkeypatch.setattr(run_state._scorer, "LiveScorer", _NullScorer)

    state = RunState(event_source="mono")
    adapter = MonoEventAdapter(state, event_source="mono")

    _bootstrap_run(adapter, state, hero="Dooley")

    # Step 1: offer the card in the shop
    adapter.process_snapshot(_make_snap(
        "EncounterState",
        offered=[{"instance_id": "itm_hat", "template_id": "T_HAT"}],
        board=[],
        gold=15,
        ts="2026-01-01T00:00:01+00:00",
        hero="Dooley",
    ))

    # Step 2: purchase it (move to board, gold down)
    adapter.process_snapshot(_make_snap(
        "EncounterState",
        offered=[],
        board=[{"instance_id": "itm_hat", "template_id": "T_HAT", "socket": "PS1"}],
        gold=10,
        ts="2026-01-01T00:00:02+00:00",
        hero="Dooley",
    ))

    # Verify card is tracked in BoardState after purchase
    assert "itm_hat" in state.board._cards, (
        "itm_hat should be in BoardState after purchase"
    )

    # Step 3: sell it (card gone, gold up)
    adapter.process_snapshot(_make_snap(
        "EncounterState",
        offered=[],
        board=[],
        gold=15,
        ts="2026-01-01T00:00:03+00:00",
        hero="Dooley",
    ))

    # Card should be removed from BoardState after the sell
    assert "itm_hat" not in state.board._cards, (
        "itm_hat should be removed from BoardState after sell"
    )


# ---------------------------------------------------------------------------
# Combat
# ---------------------------------------------------------------------------

def test_combat_start_recorded_on_combat_state_snapshot(tmp_path, monkeypatch):
    """
    CombatState snapshot → adapter emits combat_start → RunState sets
    combat_start_ts.

    This proves the adapter → RunState combat_start path is correctly wired.
    """
    _point_db_at(tmp_path, monkeypatch, name="combat_e2e.db")
    db.init_db()
    monkeypatch.setattr(run_state._scorer, "LiveScorer", _NullScorer)

    state = RunState(event_source="mono")
    adapter = MonoEventAdapter(state, event_source="mono")

    _bootstrap_run(adapter, state, hero="Vanessa")

    # Baseline shop
    adapter.process_snapshot(_make_snap(
        "EncounterState", hp=80, hp_max=80, ts="2026-01-01T00:00:01+00:00",
        hero="Vanessa",
    ))
    assert state.combat_start_ts is None, "combat_start_ts must be None before entering combat"

    # Enter combat: adapter emits combat_start event → RunState records ts
    adapter.process_snapshot(_make_snap(
        "CombatState", hp=80, hp_max=80, ts="2026-01-01T00:00:02+00:00",
        hero="Vanessa",
    ))

    assert state.combat_start_ts is not None, (
        "combat_start_ts should be set after a CombatState snapshot is processed"
    )
    assert state.current_state == "CombatState"
