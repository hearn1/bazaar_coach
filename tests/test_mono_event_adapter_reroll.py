"""
test_mono_event_adapter_reroll.py  (issue #128 — reroll detection)

rerolls_remaining drops by 1, offered set replaced → assert single reroll event.
"""

from mono_event_adapter import MonoEventAdapter


class _Collector:
    def __init__(self):
        self.events: list[dict] = []

    def process(self, event: dict):
        self.events.append(dict(event))


def _shop_snap(rerolls_remaining: int, offered_ids: list[str],
               gold: int = 10, ts: str = "2026-01-01T00:00:00+00:00") -> dict:
    return {
        "timestamp": ts,
        "state": {
            "state": "EncounterState",
            "rerolls_remaining": rerolls_remaining,
        },
        "player": {"hero": "Mak", "Gold": gold, "Health": 60, "HealthMax": 60},
        "run": {"day": 2, "hour": 1},
        "offered": [{"instance_id": iid, "template_id": f"T_{iid}"} for iid in offered_ids],
        "player_board": [],
        "player_stash": [],
        "player_skills": [],
        "opponent_board": [],
    }


def test_reroll_event_emitted_on_rerolls_remaining_decrement():
    collector = _Collector()
    adapter = MonoEventAdapter(collector, event_source="mono")

    # Snapshot 1: shop open, 3 rerolls left
    adapter.process_snapshot(_shop_snap(
        rerolls_remaining=3,
        offered_ids=["itm_a", "itm_b", "itm_c"],
        ts="2026-01-01T00:00:01+00:00",
    ))

    # Snapshot 2: player rerolled — rerolls_remaining = 2, wholly new offered set
    adapter.process_snapshot(_shop_snap(
        rerolls_remaining=2,
        offered_ids=["itm_x", "itm_y", "itm_z"],
        ts="2026-01-01T00:00:02+00:00",
    ))

    reroll_events = [e for e in collector.events if e["event"] == "reroll"]
    assert len(reroll_events) == 1, f"Expected 1 reroll event, got {len(reroll_events)}"
    assert reroll_events[0]["source"] == "mono"


def test_reroll_not_emitted_when_rerolls_remaining_unchanged():
    collector = _Collector()
    adapter = MonoEventAdapter(collector, event_source="mono")

    # Same rerolls_remaining — no reroll happened
    adapter.process_snapshot(_shop_snap(rerolls_remaining=3, offered_ids=["itm_a"],
                                        ts="2026-01-01T00:00:01+00:00"))
    adapter.process_snapshot(_shop_snap(rerolls_remaining=3, offered_ids=["itm_b"],
                                        ts="2026-01-01T00:00:02+00:00"))

    reroll_events = [e for e in collector.events if e["event"] == "reroll"]
    assert reroll_events == []


def test_reroll_not_emitted_outside_encounter_state():
    collector = _Collector()
    adapter = MonoEventAdapter(collector, event_source="mono")

    # First snapshot: ChoiceState
    snap1 = {
        "timestamp": "2026-01-01T00:00:01+00:00",
        "state": {"state": "ChoiceState", "rerolls_remaining": 3},
        "player": {"hero": "Mak", "Gold": 10, "Health": 60, "HealthMax": 60},
        "run": {},
        "offered": [{"instance_id": "itm_a"}],
        "player_board": [], "player_stash": [], "player_skills": [], "opponent_board": [],
    }
    snap2 = {
        "timestamp": "2026-01-01T00:00:02+00:00",
        "state": {"state": "ChoiceState", "rerolls_remaining": 2},
        "player": {"hero": "Mak", "Gold": 10, "Health": 60, "HealthMax": 60},
        "run": {},
        "offered": [{"instance_id": "itm_z"}],
        "player_board": [], "player_stash": [], "player_skills": [], "opponent_board": [],
    }
    adapter.process_snapshot(snap1)
    adapter.process_snapshot(snap2)

    assert all(e["event"] != "reroll" for e in collector.events)


def test_multiple_rerolls_in_sequence():
    collector = _Collector()
    adapter = MonoEventAdapter(collector, event_source="mono")

    adapter.process_snapshot(_shop_snap(3, ["itm_a", "itm_b", "itm_c"],
                                        ts="2026-01-01T00:00:01+00:00"))
    adapter.process_snapshot(_shop_snap(2, ["itm_x", "itm_y", "itm_z"],
                                        ts="2026-01-01T00:00:02+00:00"))
    adapter.process_snapshot(_shop_snap(1, ["itm_p", "itm_q", "itm_r"],
                                        ts="2026-01-01T00:00:03+00:00"))

    reroll_events = [e for e in collector.events if e["event"] == "reroll"]
    assert len(reroll_events) == 2
