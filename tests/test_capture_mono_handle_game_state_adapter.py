"""
test_capture_mono_handle_game_state_adapter.py

Regression test for issue #148.

Pre-#148, `capture_mono.handle_game_state()` ran two console-rendering
filters (`_should_render_snapshot` + `_render_signature` dedup) BEFORE
dispatching to `MonoEventAdapter`. EncounterState snapshots (where all
shop activity happens) failed `_should_render_snapshot`, so no
card_purchased / card_sold / reroll events ever reached RunState, and
no `decisions` rows were ever written even though the `runs` row opened
correctly via #147.

The existing `test_capture_mono_wiring.py` only proved the adapter slot
was populated; it called `adapter.process_snapshot()` directly and
bypassed `handle_game_state()`'s filter chain, so the filter bug was
invisible.

This test drives `capture_mono.handle_game_state()` end-to-end through
the real filter chain. It fails on `main` pre-fix (the EncounterState
buy snapshot is filtered out) and passes once the adapter dispatch is
moved above the render filters.
"""

import sqlite3

import capture_mono
import db
import run_state


class _NullScorer:
    def __init__(self, hero, conn):
        pass

    def score_decision(self, decision, decision_id):
        pass

    def notify_combat(self):
        pass


def _point_db_at(tmp_path, monkeypatch, name="adapter_filter_test.db"):
    path = tmp_path / name
    monkeypatch.setattr(db, "DB_PATH", path)
    db.close_shared_conn()
    return path


def _reset_capture_mono_globals(monkeypatch):
    """Clear capture_mono module-level slots between tests."""
    monkeypatch.setattr(capture_mono, "_mono_event_adapter", None)
    monkeypatch.setattr(capture_mono, "_run_state", None)
    monkeypatch.setattr(capture_mono, "_adapter", None)
    monkeypatch.setattr(capture_mono, "_seen_snapshot_keys", set())
    monkeypatch.setattr(capture_mono, "_rendered_snapshot_keys", set())
    monkeypatch.setattr(capture_mono, "_snapshot_count", 0)
    monkeypatch.setattr(capture_mono, "_duplicate_snapshot_count", 0)
    monkeypatch.setattr(capture_mono, "_last_merged_snapshot", None)
    monkeypatch.setattr(capture_mono, "_do_log", False)
    monkeypatch.setattr(capture_mono, "_do_db", False)
    monkeypatch.setattr(capture_mono, "_adapter_first_dispatch_logged", False)


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
    snap_id: int | None = None,
) -> dict:
    snap = {
        "timestamp": ts,
        "state": {"state": state, "rerolls_remaining": rerolls},
        "player": {"hero": hero, "Gold": gold, "Health": hp, "HealthMax": hp_max},
        "run": {"day": 2, "hour": 1, "victories": 0, "defeats": 0},
        "offered": offered or [],
        "player_board": board or [],
        "player_stash": [],
        "player_skills": skills or [],
        "opponent_board": [],
    }
    if snap_id is not None:
        snap["id"] = snap_id
    return snap


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_encounter_state_snapshots_reach_adapter(tmp_path, monkeypatch):
    """EncounterState fails `_should_render_snapshot()` but must still reach the adapter.

    Pre-#148 this assertion fails: the adapter never sees the snapshot.
    """
    _point_db_at(tmp_path, monkeypatch)
    _reset_capture_mono_globals(monkeypatch)
    monkeypatch.setattr(run_state._scorer, "LiveScorer", _NullScorer)
    capture_mono._wire_run_state()

    seen = []
    real_process = capture_mono._mono_event_adapter.process_snapshot

    def _spy(snap):
        seen.append(snap.get("state", {}).get("state"))
        return real_process(snap)
    monkeypatch.setattr(capture_mono._mono_event_adapter, "process_snapshot", _spy)

    try:
        # First snapshot is EncounterState. _snapshot_count starts at 0, so
        # the very first one is allowed through `_should_render_snapshot()`
        # regardless. The SECOND EncounterState snapshot is the critical case:
        # it would fail `_should_render_snapshot()` and (pre-#148) be dropped.
        capture_mono.handle_game_state(_make_snap("EncounterState", snap_id=1))
        capture_mono.handle_game_state(_make_snap(
            "EncounterState",
            gold=9,
            snap_id=2,
            ts="2026-01-01T00:00:01+00:00",
        ))

        assert seen == ["EncounterState", "EncounterState"], (
            f"adapter must receive every non-duplicate snapshot regardless of "
            f"console-render filters; got {seen}"
        )
    finally:
        capture_mono.register_event_adapter(None)


def test_rendered_snapshots_reach_adapter_once(tmp_path, monkeypatch):
    """Render-eligible snapshots should not be delivered twice to the adapter."""
    _point_db_at(tmp_path, monkeypatch)
    _reset_capture_mono_globals(monkeypatch)
    monkeypatch.setattr(run_state._scorer, "LiveScorer", _NullScorer)
    capture_mono._wire_run_state()

    seen = []
    real_process = capture_mono._mono_event_adapter.process_snapshot

    def _spy(snap):
        seen.append(snap.get("state", {}).get("state"))
        return real_process(snap)

    monkeypatch.setattr(capture_mono._mono_event_adapter, "process_snapshot", _spy)

    try:
        capture_mono.handle_game_state(_make_snap("Choice", snap_id=1))
        assert seen == ["Choice"], f"rendered snapshots must dispatch once; got {seen}"
    finally:
        capture_mono.register_event_adapter(None)


def test_shop_purchase_writes_decision_through_handle_game_state(tmp_path, monkeypatch):
    """End-to-end through the real filter chain: snapshot a buy, expect a decision row.

    Pre-#148 the EncounterState purchase snapshot is dropped by the render
    filter before it reaches the adapter, so no `decisions` row is written.
    """
    path = _point_db_at(tmp_path, monkeypatch)
    _reset_capture_mono_globals(monkeypatch)
    monkeypatch.setattr(run_state._scorer, "LiveScorer", _NullScorer)
    capture_mono._wire_run_state()

    try:
        # Bootstrap: NewRun to EncounterState opens the run record.
        capture_mono.handle_game_state(_make_snap("NewRun", snap_id=1))
        capture_mono.handle_game_state(_make_snap(
            "EncounterState",
            offered=[
                {"instance_id": "itm_sword", "template_id": "T_SWORD"},
                {"instance_id": "itm_shield", "template_id": "T_SHIELD"},
                {"instance_id": "itm_potion", "template_id": "T_POTION"},
            ],
            board=[],
            gold=10,
            snap_id=2,
            ts="2026-01-01T00:00:01+00:00",
        ))

        state = capture_mono._run_state
        assert state.run_id is not None, "bootstrap must open a run"
        run_id = state.run_id

        # The buy: itm_sword moves from offered to player_board, gold drops.
        # This snapshot is in EncounterState and (pre-#148) would be dropped
        # by `_should_render_snapshot()` before reaching the adapter.
        capture_mono.handle_game_state(_make_snap(
            "EncounterState",
            offered=[
                {"instance_id": "itm_shield", "template_id": "T_SHIELD"},
                {"instance_id": "itm_potion", "template_id": "T_POTION"},
            ],
            board=[
                {"instance_id": "itm_sword", "template_id": "T_SWORD", "socket": "P1"},
            ],
            gold=7,
            snap_id=3,
            ts="2026-01-01T00:00:02+00:00",
        ))

        # Flush the writer thread so the pending decision write is committed
        # before we read via a fresh connection.
        db.flush()

        conn = sqlite3.connect(path)
        try:
            rows = conn.execute(
                "SELECT decision_type, chosen_id, chosen_template "
                "FROM decisions WHERE run_id = ?",
                (run_id,),
            ).fetchall()
        finally:
            conn.close()

        assert len(rows) == 1, (
            f"expected exactly one decision row for the shop purchase, got {len(rows)}: {rows}"
        )
        dtype, chosen_id, chosen_template = rows[0]
        assert dtype == "item", f"expected decision_type='item', got {dtype!r}"
        assert chosen_id == "itm_sword", f"expected chosen_id='itm_sword', got {chosen_id!r}"
        assert chosen_template == "T_SWORD", f"expected chosen_template='T_SWORD', got {chosen_template!r}"
    finally:
        capture_mono.register_event_adapter(None)


def test_byte_identical_duplicate_still_dropped_before_adapter(tmp_path, monkeypatch):
    """The full-payload dedup at the top of handle_game_state must still skip
    byte-identical retransmits (Frida occasionally re-emits the same message).
    The adapter does NOT need to see those; they carry no diff information.
    """
    _point_db_at(tmp_path, monkeypatch)
    _reset_capture_mono_globals(monkeypatch)
    monkeypatch.setattr(run_state._scorer, "LiveScorer", _NullScorer)
    capture_mono._wire_run_state()

    seen = []
    real_process = capture_mono._mono_event_adapter.process_snapshot

    def _spy(snap):
        seen.append(snap.get("state", {}).get("state"))
        return real_process(snap)
    monkeypatch.setattr(capture_mono._mono_event_adapter, "process_snapshot", _spy)

    try:
        snap = _make_snap("EncounterState", snap_id=1)
        capture_mono.handle_game_state(snap)
        # Identical retransmit (same message_id-less payload to same sha1 dedupe key).
        capture_mono.handle_game_state(_make_snap("EncounterState", snap_id=1))

        assert seen == ["EncounterState"], (
            f"byte-identical retransmits must still be deduped before the adapter; got {seen}"
        )
    finally:
        capture_mono.register_event_adapter(None)
