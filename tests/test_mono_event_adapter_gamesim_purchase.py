"""
test_mono_event_adapter_gamesim_purchase.py

The Mono-only flow can't see purchases via the offered→board diff (the owned
board never populates during shops). The game instead emits a discrete
``GameSimEventCardPurchased`` on the chosen instance, decoded into the
``card_template_events`` stream as ``event_type='card_purchased'``. The adapter
must turn those into ``card_purchased`` decisions, recovering the template from
the matching ``card_dealt`` event (the purchase event carries no template).
"""

from mono_event_adapter import MonoEventAdapter


class _Collector:
    def __init__(self):
        self.events: list[dict] = []

    def process(self, event: dict):
        self.events.append(dict(event))


def _snap(state, events, snap_id=1, ts="2026-01-01T00:00:01+00:00"):
    return {
        "timestamp": ts,
        "id": snap_id,
        "state": {"state": state},
        "player": {"hero": "Karnok", "Gold": 11, "Health": 300, "HealthMax": 300},
        "run": {"day": 1, "hour": 1},
        "offered": [],
        "player_board": [],
        "player_stash": [],
        "player_skills": [],
        "opponent_board": [],
        "card_template_events": events,
    }


def _purchases(collector):
    return [e for e in collector.events if e["event"] == "card_purchased"]


def test_gamesim_card_purchased_records_item_with_recovered_template():
    c = _Collector()
    adapter = MonoEventAdapter(c, event_source="mono")
    adapter.process_snapshot(_snap("EncounterState", [
        {"event_type": "card_dealt", "instance_id": "itm_buy", "template_id": "T_DRYAD"},
        {"event_type": "card_dealt", "instance_id": "itm_skip", "template_id": "T_OTHER"},
        {"event_type": "card_purchased", "instance_id": "itm_buy", "template_id": None},
        {"event_type": "card_disposed", "instance_id": "itm_skip", "template_id": None},
    ]))

    p = _purchases(c)
    assert len(p) == 1, f"expected 1 purchase, got {p}"
    assert p[0]["instance_id"] == "itm_buy"
    assert p[0]["template_id"] == "T_DRYAD"   # recovered from card_dealt
    assert p[0]["section"] == "Player"
    assert p[0]["source"] == "mono"


def test_two_purchases_in_one_shop():
    c = _Collector()
    adapter = MonoEventAdapter(c, event_source="mono")
    adapter.process_snapshot(_snap("EncounterState", [
        {"event_type": "card_dealt", "instance_id": "itm_a", "template_id": "T_A"},
        {"event_type": "card_dealt", "instance_id": "itm_b", "template_id": "T_B"},
        {"event_type": "card_dealt", "instance_id": "itm_c", "template_id": "T_C"},
        {"event_type": "card_purchased", "instance_id": "itm_a", "template_id": None},
        {"event_type": "card_purchased", "instance_id": "itm_c", "template_id": None},
    ]))
    p = _purchases(c)
    assert [e["instance_id"] for e in p] == ["itm_a", "itm_c"]
    assert [e["template_id"] for e in p] == ["T_A", "T_C"]


def test_purchase_dedup_across_repeat_snapshots():
    """The same CardPurchased can recur (inline + deferred enrichment); emit once."""
    c = _Collector()
    adapter = MonoEventAdapter(c, event_source="mono")
    events = [
        {"event_type": "card_dealt", "instance_id": "itm_buy", "template_id": "T_X"},
        {"event_type": "card_purchased", "instance_id": "itm_buy", "template_id": None},
    ]
    adapter.process_snapshot(_snap("EncounterState", events, snap_id=1))
    adapter.process_snapshot(_snap("EncounterState", events, snap_id=2,
                                   ts="2026-01-01T00:00:02+00:00"))
    assert len(_purchases(c)) == 1


def test_map_node_pick_is_opponent_section():
    c = _Collector()
    adapter = MonoEventAdapter(c, event_source="mono")
    adapter.process_snapshot(_snap("ChoiceState", [
        {"event_type": "card_dealt", "instance_id": "enc_pick", "template_id": "T_NODE"},
        {"event_type": "card_purchased", "instance_id": "enc_pick", "template_id": None},
    ]))
    p = _purchases(c)
    assert len(p) == 1
    assert p[0]["section"] == "Opponent"


def test_skill_purchase_left_to_skill_path():
    c = _Collector()
    adapter = MonoEventAdapter(c, event_source="mono")
    adapter.process_snapshot(_snap("ChoiceState", [
        {"event_type": "card_purchased", "instance_id": "skl_x", "template_id": None},
    ]))
    assert _purchases(c) == []


def test_ingest_card_events_emits_late_purchases():
    """Deferred template-events payload feeds purchases directly (race-proof path)."""
    c = _Collector()
    adapter = MonoEventAdapter(c, event_source="mono")
    adapter.ingest_card_events([
        {"event_type": "card_dealt", "instance_id": "itm_late", "template_id": "T_LATE"},
        {"event_type": "card_purchased", "instance_id": "itm_late", "template_id": None},
    ], ts="2026-01-01T00:00:03+00:00")
    p = _purchases(c)
    assert len(p) == 1
    assert p[0]["instance_id"] == "itm_late"
    assert p[0]["template_id"] == "T_LATE"
