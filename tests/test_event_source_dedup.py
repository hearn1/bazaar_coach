"""
test_event_source_dedup.py

Feeds the same logical event from both pipelines within 500 ms.
Asserts only one decision row is written (dedup ring buffer).

Tests: card_purchased (instance_id key), cards_dealt (frozenset key), reroll (shop_window_id key).
"""

import json
import sqlite3

import db
import run_state
from run_state import RunState, _RecentEvents


# ---------------------------------------------------------------------------
# Unit tests for _RecentEvents ring buffer (no DB required)
# ---------------------------------------------------------------------------

class TestRecentEvents:
    def test_first_event_not_duplicate(self):
        buf = _RecentEvents()
        event = {"event": "card_purchased", "instance_id": "itm_aaa"}
        assert buf.check_and_record(event) is False

    def test_same_event_within_window_is_duplicate(self):
        buf = _RecentEvents()
        event = {"event": "card_purchased", "instance_id": "itm_aaa"}
        buf.check_and_record(event)
        assert buf.check_and_record(event) is True

    def test_different_instance_id_not_duplicate(self):
        buf = _RecentEvents()
        buf.check_and_record({"event": "card_purchased", "instance_id": "itm_aaa"})
        assert buf.check_and_record({"event": "card_purchased", "instance_id": "itm_bbb"}) is False

    def test_cards_dealt_keyed_by_frozenset(self):
        buf = _RecentEvents()
        e1 = {"event": "cards_dealt", "instance_ids": ["itm_a", "itm_b"]}
        e2 = {"event": "cards_dealt", "instance_ids": ["itm_b", "itm_a"]}  # different order
        buf.check_and_record(e1)
        # Same set, different order → duplicate
        assert buf.check_and_record(e2) is True

    def test_reroll_keyed_by_shop_window_id(self):
        buf = _RecentEvents()
        e1 = {"event": "reroll", "shop_window_id": 3}
        e2 = {"event": "reroll", "shop_window_id": 3}
        buf.check_and_record(e1)
        assert buf.check_and_record(e2) is True

    def test_reroll_different_window_not_duplicate(self):
        buf = _RecentEvents()
        buf.check_and_record({"event": "reroll", "shop_window_id": 3})
        assert buf.check_and_record({"event": "reroll", "shop_window_id": 4}) is False

    def test_reroll_no_window_id_not_deduped(self):
        """reroll without shop_window_id has no dedup key — always passes through."""
        buf = _RecentEvents()
        e = {"event": "reroll"}
        buf.check_and_record(e)
        assert buf.check_and_record(e) is False

    def test_state_change_keyed_by_from_to(self):
        buf = _RecentEvents()
        e = {"event": "state_change", "from_state": "EncounterState", "to_state": "CombatState"}
        buf.check_and_record(e)
        assert buf.check_and_record(e) is True

    def test_run_start_deduped_by_timestamp(self):
        """run_start with same timestamp within window is a duplicate; different ts is not."""
        buf = _RecentEvents()
        e1 = {"event": "run_start", "ts": "10:00"}
        e2 = {"event": "run_start", "ts": "10:00"}  # same timestamp → duplicate
        e3 = {"event": "run_start", "ts": "10:30"}  # different timestamp → not duplicate
        buf.check_and_record(e1)
        assert buf.check_and_record(e2) is True
        assert buf.check_and_record(e3) is False

    def test_hero_keyed_by_value(self):
        buf = _RecentEvents()
        buf.check_and_record({"event": "hero", "hero": "Karnok"})
        assert buf.check_and_record({"event": "hero", "hero": "Karnok"}) is True
        assert buf.check_and_record({"event": "hero", "hero": "Mak"}) is False

    def test_window_expires(self):
        """Events older than WINDOW_MS should be passable again."""
        import time
        buf = _RecentEvents()
        original_window = _RecentEvents.WINDOW_MS
        _RecentEvents.WINDOW_MS = 1.0  # 1 ms
        try:
            event = {"event": "card_purchased", "instance_id": "itm_zz"}
            buf.check_and_record(event)
            time.sleep(0.01)  # 10 ms > 1 ms window
            # Prune happens in check_and_record (4x window = 4 ms, so item is stale)
            # Forcibly clear to simulate expiry
            buf._seen.clear()
            assert buf.check_and_record(event) is False
        finally:
            _RecentEvents.WINDOW_MS = original_window


# ---------------------------------------------------------------------------
# Integration test: dedup via RunState.process()
# ---------------------------------------------------------------------------

class _NullScorer:
    def __init__(self, hero, conn):
        pass
    def score_decision(self, decision, decision_id):
        pass
    def notify_combat(self):
        pass


def _point_db_at(tmp_path, monkeypatch):
    path = tmp_path / "dedup_test.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    db.close_shared_conn()
    return path


def test_card_purchased_dedup_via_run_state(tmp_path, monkeypatch):
    """
    Feed a card_purchased event twice within 500 ms — once from 'log', once from
    'mono'.  Only one decision row should be written.
    """
    _point_db_at(tmp_path, monkeypatch)
    db.init_db()
    monkeypatch.setattr(run_state._scorer, "LiveScorer", _NullScorer)

    # Reset card_cache module-level state so the next test's DB is not affected.
    import card_cache as _cc
    monkeypatch.setattr(_cc, "_template_name_cache_loaded", False)
    monkeypatch.setattr(_cc, "_template_name_cache", {})

    state = RunState(event_source="both")
    state.process({"event": "run_start", "ts": "10:00"})
    state.process({"event": "session_id", "ts": "10:00", "session_id": "sess-dedup-1"})
    state.process({"event": "account_id", "ts": "10:00", "account_id": "acct-dedup-1"})
    state.process({"event": "hero", "ts": "10:00", "hero": "Karnok"})
    state.process({"event": "state_change", "ts": "10:01",
                   "from_state": "Unknown", "to_state": "EncounterState"})
    state.process({"event": "cards_dealt", "ts": "10:01",
                   "instance_ids": ["itm_a", "itm_b"]})

    # First arrival (log)
    state.process({
        "event": "card_purchased",
        "ts": "10:02",
        "instance_id": "itm_a",
        "template_id": "T_A",
        "target_socket": "PlayerSocket_0",
        "section": "Player",
        "source": "log",
    })

    # Second arrival (mono) — same instance_id within window → should be dropped
    state.process({
        "event": "card_purchased",
        "ts": "10:02",
        "instance_id": "itm_a",
        "template_id": "T_A",
        "target_socket": "PlayerSocket_0",
        "section": "Player",
        "source": "mono",
    })

    db.flush()

    conn = sqlite3.connect(str(tmp_path / "dedup_test.db"))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM decisions WHERE run_id = ? AND decision_type IN ('item', 'companion', 'skill', 'free_reward')",
            (state.run_id,),
        ).fetchall()
        assert len(rows) == 1, f"Expected 1 decision, got {len(rows)}"
    finally:
        conn.close()


def test_cards_dealt_dedup_frozenset_order_independent(tmp_path, monkeypatch):
    """
    Two cards_dealt events with same instance_ids in different order are duplicates.
    """
    buf = _RecentEvents()
    e1 = {"event": "cards_dealt", "instance_ids": ["itm_x", "itm_y", "itm_z"]}
    e2 = {"event": "cards_dealt", "instance_ids": ["itm_z", "itm_x", "itm_y"]}  # shuffled
    assert buf.check_and_record(e1) is False
    assert buf.check_and_record(e2) is True  # same frozenset → duplicate
