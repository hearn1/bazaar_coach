"""
test_mono_event_adapter_purchase.py

Card moves from offered → player_board between two snapshots.
Asserts card_purchased event with correct template_id / section / target_socket.
"""

from mono_event_adapter import MonoEventAdapter


class _Collector:
    def __init__(self):
        self.events: list[dict] = []

    def process(self, event: dict):
        self.events.append(dict(event))


def _make_snap(state: str, offered: list, board: list, gold: int = 10,
               ts: str = "2026-01-01T00:00:00+00:00", snap_id=None) -> dict:
    snap = {
        "timestamp": ts,
        "state": {"state": state},
        "player": {"hero": "Karnok", "Gold": gold, "Health": 60, "HealthMax": 60},
        "run": {"day": 1, "hour": 1},
        "offered": offered,
        "player_board": board,
        "player_stash": [],
        "player_skills": [],
        "opponent_board": [],
    }
    if snap_id is not None:
        snap["id"] = snap_id
    return snap


def test_card_purchased_emitted_on_offered_to_board_move():
    collector = _Collector()
    adapter = MonoEventAdapter(collector, event_source="mono")

    # Snapshot 1: item offered
    snap1 = _make_snap(
        "EncounterState",
        offered=[{"instance_id": "itm_abc", "template_id": "T_SWORD"}],
        board=[],
        gold=10,
        ts="2026-01-01T00:00:01+00:00",
    )
    adapter.process_snapshot(snap1)

    # Snapshot 2: item now on player_board, offered is empty, gold decreased
    snap2 = _make_snap(
        "EncounterState",
        offered=[],
        board=[{"instance_id": "itm_abc", "template_id": "T_SWORD",
                "socket": "PlayerSocket_0"}],
        gold=7,  # spent 3 gold
        ts="2026-01-01T00:00:02+00:00",
    )
    adapter.process_snapshot(snap2)

    purchases = [e for e in collector.events if e["event"] == "card_purchased"]
    assert len(purchases) == 1, f"Expected 1 card_purchased, got {len(purchases)}"

    p = purchases[0]
    assert p["instance_id"] == "itm_abc"
    assert p["template_id"] == "T_SWORD"
    assert p["section"] == "Player"
    assert p["target_socket"] == "PlayerSocket_0"
    assert p["source"] == "mono"


def test_card_purchased_includes_template_id_from_board_snap():
    """template_id must be read from the snapshot where the card now lives."""
    collector = _Collector()
    adapter = MonoEventAdapter(collector, event_source="mono")

    snap1 = _make_snap(
        "EncounterState",
        offered=[{"instance_id": "itm_zz", "template_id": "T_SHIELD"}],
        board=[],
        ts="2026-01-01T00:00:01+00:00",
    )
    adapter.process_snapshot(snap1)

    snap2 = _make_snap(
        "EncounterState",
        offered=[],
        board=[{"instance_id": "itm_zz", "template_id": "T_SHIELD",
                "socket": "PlayerSocket_2"}],
        ts="2026-01-01T00:00:02+00:00",
    )
    adapter.process_snapshot(snap2)

    purchases = [e for e in collector.events if e["event"] == "card_purchased"]
    assert purchases[0]["template_id"] == "T_SHIELD"


def test_late_enriched_offered_enables_purchase_detection():
    """Deferred offered data that lands *after* the snapshot was processed must
    still let the next snapshot's offered→board diff detect the purchase.

    Reproduces the live-capture gap: handle_game_state dispatches the snapshot
    while its offered set is still empty; deferred card data arrives later and
    only note_enriched_snapshot re-points the adapter's prev state.
    """
    collector = _Collector()
    adapter = MonoEventAdapter(collector, event_source="mono")

    # Snapshot 1 dispatched THIN — offered collection had not decoded yet.
    snap1_thin = _make_snap(
        "EncounterState", offered=[], board=[], gold=10, snap_id=1,
        ts="2026-01-01T00:00:01+00:00",
    )
    adapter.process_snapshot(snap1_thin)

    # Deferred cards land for snapshot 1: the offered item is now known.
    snap1_enriched = _make_snap(
        "EncounterState",
        offered=[{"instance_id": "itm_abc", "template_id": "T_SWORD"}],
        board=[], gold=10, snap_id=1,
        ts="2026-01-01T00:00:01+00:00",
    )
    adapter.note_enriched_snapshot(snap1_enriched)

    # Snapshot 2: item bought → on board, gold dropped.
    snap2 = _make_snap(
        "EncounterState",
        offered=[],
        board=[{"instance_id": "itm_abc", "template_id": "T_SWORD",
                "socket": "PlayerSocket_0"}],
        gold=7, snap_id=2,
        ts="2026-01-01T00:00:02+00:00",
    )
    adapter.process_snapshot(snap2)

    purchases = [e for e in collector.events if e["event"] == "card_purchased"]
    assert len(purchases) == 1, f"Expected 1 card_purchased, got {len(purchases)}"
    assert purchases[0]["instance_id"] == "itm_abc"
    assert purchases[0]["template_id"] == "T_SWORD"


def test_without_enrichment_purchase_is_missed():
    """Control: without note_enriched_snapshot the late offered set is lost, so
    the offered→board move cannot be diffed (documents the gap the fix closes)."""
    collector = _Collector()
    adapter = MonoEventAdapter(collector, event_source="mono")

    adapter.process_snapshot(_make_snap(
        "EncounterState", offered=[], board=[], gold=10, snap_id=1,
        ts="2026-01-01T00:00:01+00:00",
    ))
    adapter.process_snapshot(_make_snap(
        "EncounterState",
        offered=[],
        board=[{"instance_id": "itm_abc", "template_id": "T_SWORD",
                "socket": "PlayerSocket_0"}],
        gold=7, snap_id=2,
        ts="2026-01-01T00:00:02+00:00",
    ))

    purchases = [e for e in collector.events if e["event"] == "card_purchased"]
    assert purchases == []


def test_note_enriched_snapshot_ignores_id_mismatch():
    """A late enrichment for a snapshot the adapter has already moved past must
    not clobber the current prev state."""
    collector = _Collector()
    adapter = MonoEventAdapter(collector, event_source="mono")

    adapter.process_snapshot(_make_snap(
        "EncounterState",
        offered=[{"instance_id": "itm_now", "template_id": "T_NOW"}],
        board=[], gold=10, snap_id=5,
        ts="2026-01-01T00:00:01+00:00",
    ))
    before = adapter._prev_offered

    # Enrichment for an older snapshot id → no-op.
    adapter.note_enriched_snapshot(_make_snap(
        "EncounterState",
        offered=[{"instance_id": "itm_old", "template_id": "T_OLD"}],
        board=[], gold=10, snap_id=2,
        ts="2026-01-01T00:00:00+00:00",
    ))
    assert adapter._prev_offered == before
