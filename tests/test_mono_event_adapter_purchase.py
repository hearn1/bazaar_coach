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
               ts: str = "2026-01-01T00:00:00+00:00") -> dict:
    return {
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
