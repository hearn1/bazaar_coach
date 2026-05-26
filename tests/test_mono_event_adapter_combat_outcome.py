"""
test_mono_event_adapter_combat_outcome.py

CombatState → EncounterState transition → assert combat_start + combat_complete emitted.
"""

from mono_event_adapter import MonoEventAdapter


class _Collector:
    def __init__(self):
        self.events: list[dict] = []

    def process(self, event: dict):
        self.events.append(dict(event))

    def event_types(self) -> list[str]:
        return [e["event"] for e in self.events]


def _snap(state: str, hp: int = 60, ts: str = "2026-01-01T00:00:00+00:00") -> dict:
    return {
        "timestamp": ts,
        "state": {"state": state},
        "player": {"hero": "Vanessa", "Gold": 10, "Health": hp, "HealthMax": 80},
        "run": {"day": 4, "hour": 2},
        "offered": [],
        "player_board": [],
        "player_stash": [],
        "player_skills": [],
        "opponent_board": [],
    }


def test_combat_start_emitted_on_entering_combat_state():
    collector = _Collector()
    adapter = MonoEventAdapter(collector, event_source="mono")

    adapter.process_snapshot(_snap("EncounterState", ts="2026-01-01T00:00:01+00:00"))
    adapter.process_snapshot(_snap("CombatState", ts="2026-01-01T00:00:02+00:00"))

    assert "combat_start" in collector.event_types()


def test_combat_complete_emitted_on_leaving_combat():
    collector = _Collector()
    adapter = MonoEventAdapter(collector, event_source="mono")

    adapter.process_snapshot(_snap("EncounterState", hp=80, ts="2026-01-01T00:00:01+00:00"))
    adapter.process_snapshot(_snap("CombatState", hp=80, ts="2026-01-01T00:00:02+00:00"))
    adapter.process_snapshot(_snap("ReplayState", hp=80, ts="2026-01-01T00:00:03+00:00"))
    adapter.process_snapshot(_snap("EncounterState", hp=60, ts="2026-01-01T00:00:04+00:00"))

    types = collector.event_types()
    assert "combat_start" in types
    assert "combat_complete" in types

    # Order: start before complete
    start_idx = types.index("combat_start")
    complete_idx = types.index("combat_complete")
    assert start_idx < complete_idx


def test_combat_start_not_repeated_while_in_combat():
    """Re-entrant combat snapshots should not emit duplicate combat_start."""
    collector = _Collector()
    adapter = MonoEventAdapter(collector, event_source="mono")

    adapter.process_snapshot(_snap("EncounterState", ts="2026-01-01T00:00:01+00:00"))
    adapter.process_snapshot(_snap("CombatState", ts="2026-01-01T00:00:02+00:00"))
    adapter.process_snapshot(_snap("CombatState", ts="2026-01-01T00:00:03+00:00"))
    adapter.process_snapshot(_snap("CombatState", ts="2026-01-01T00:00:04+00:00"))

    combat_starts = [e for e in collector.events if e["event"] == "combat_start"]
    assert len(combat_starts) == 1


def test_pvp_combat_state_also_triggers_combat_start():
    collector = _Collector()
    adapter = MonoEventAdapter(collector, event_source="mono")

    adapter.process_snapshot(_snap("EncounterState", ts="2026-01-01T00:00:01+00:00"))
    adapter.process_snapshot(_snap("PVPCombatState", ts="2026-01-01T00:00:02+00:00"))

    assert "combat_start" in collector.event_types()
