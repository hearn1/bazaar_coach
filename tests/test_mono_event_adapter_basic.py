"""
test_mono_event_adapter_basic.py

Synthetic 3-snapshot stream: NewRun → EncounterState (shop) → ChoiceState.
Asserts the emitted event sequence matches expectations.
"""

from mono_event_adapter import MonoEventAdapter


class _Collector:
    """Fake RunState that records every event fed to process()."""

    def __init__(self):
        self.events: list[dict] = []

    def process(self, event: dict):
        self.events.append(dict(event))

    def event_types(self) -> list[str]:
        return [e["event"] for e in self.events]


def _snap(state_name: str, hero: str = "Karnok", offered=None, ts: str = "2026-01-01T00:00:00+00:00") -> dict:
    return {
        "timestamp": ts,
        "state": {"state": state_name},
        "player": {"hero": hero, "Gold": 10, "Health": 60, "HealthMax": 60},
        "run": {"day": 1, "hour": 1},
        "offered": offered or [],
        "player_board": [],
        "player_stash": [],
        "player_skills": [],
        "opponent_board": [],
    }


def test_basic_stream_emits_expected_events():
    collector = _Collector()
    adapter = MonoEventAdapter(collector, event_source="mono")

    # Snapshot 1: NewRun (triggers run boundary reset + run_start)
    adapter.process_snapshot(_snap("NewRun"))

    # Snapshot 2: EncounterState with items offered
    snap2 = _snap("EncounterState", ts="2026-01-01T00:00:01+00:00")
    snap2["offered"] = [
        {"instance_id": "itm_aaa", "template_id": "T_A"},
        {"instance_id": "itm_bbb", "template_id": "T_B"},
    ]
    adapter.process_snapshot(snap2)

    # Snapshot 3: ChoiceState (state change, no new offered)
    snap3 = _snap("ChoiceState", ts="2026-01-01T00:00:02+00:00")
    adapter.process_snapshot(snap3)

    types = collector.event_types()

    # Must contain run_start, hero, session_id, account_id, run_init_complete
    assert "run_start" in types
    assert "hero" in types
    assert "session_id" in types
    assert "account_id" in types
    assert "run_init_complete" in types

    # EncounterState snapshot should emit cards_dealt for the 2 offered items
    assert "cards_dealt" in types

    # Transition EncounterState → ChoiceState should emit state_change
    assert "state_change" in types

    # Check ordering: bootstrap events come first
    first_run_start = next(i for i, e in enumerate(collector.events) if e["event"] == "run_start")
    first_cards_dealt = next(i for i, e in enumerate(collector.events) if e["event"] == "cards_dealt")
    assert first_run_start < first_cards_dealt

    # hero value correct
    hero_evt = next(e for e in collector.events if e["event"] == "hero")
    assert hero_evt["hero"] == "Karnok"

    # state_change fields: there may be multiple (NewRun→EncounterState and
    # EncounterState→ChoiceState).  The EncounterState→ChoiceState one must exist.
    state_changes = [e for e in collector.events if e["event"] == "state_change"]
    sc_encounter_to_choice = next(
        (e for e in state_changes
         if e.get("from_state") == "EncounterState" and e.get("to_state") == "ChoiceState"),
        None,
    )
    assert sc_encounter_to_choice is not None, (
        f"Expected EncounterState→ChoiceState state_change, got: {state_changes}"
    )


def test_bootstrap_events_emitted_only_once():
    """Bootstrap events (run_start, hero, session_id …) must not repeat each snapshot."""
    collector = _Collector()
    adapter = MonoEventAdapter(collector, event_source="mono")

    for i in range(5):
        adapter.process_snapshot(_snap("EncounterState", ts=f"2026-01-01T00:00:0{i}+00:00"))

    bootstrap_types = [e["event"] for e in collector.events
                       if e["event"] in ("run_start", "hero", "session_id", "account_id", "run_init_complete")]
    for btype in ("run_start", "hero", "session_id", "account_id", "run_init_complete"):
        assert bootstrap_types.count(btype) == 1, f"{btype} emitted more than once"


def test_log_source_is_noop():
    """event_source='log' means the adapter is entirely inactive."""
    collector = _Collector()
    adapter = MonoEventAdapter(collector, event_source="log")
    adapter.process_snapshot(_snap("EncounterState"))
    assert collector.events == []


def test_cards_dealt_instance_ids_correct():
    collector = _Collector()
    adapter = MonoEventAdapter(collector, event_source="mono")

    snap = _snap("EncounterState")
    snap["offered"] = [
        {"instance_id": "itm_x", "template_id": "T_X"},
        {"instance_id": "itm_y", "template_id": "T_Y"},
    ]
    adapter.process_snapshot(snap)

    dealt = [e for e in collector.events if e["event"] == "cards_dealt"]
    assert len(dealt) == 1
    assert set(dealt[0]["instance_ids"]) == {"itm_x", "itm_y"}
