"""
test_mono_event_adapter_sell.py  (issue #128 — sell detection)

player_board card vanishes, gold increases, no cards_disposed in snapshot →
assert card_sold event with correct template_id.

Also covers the Mono-only path (issue #196): GameSimEventCardDisposed arriving
via card_template_events is the authoritative sell signal when the owned-card
board is empty.
"""

from mono_event_adapter import MonoEventAdapter


class _Collector:
    def __init__(self):
        self.events: list[dict] = []

    def process(self, event: dict):
        self.events.append(dict(event))


def _board_snap(board_cards: list, gold: int,
                ts: str = "2026-01-01T00:00:00+00:00") -> dict:
    return {
        "timestamp": ts,
        "state": {"state": "EncounterState"},
        "player": {"hero": "Dooley", "Gold": gold, "Health": 70, "HealthMax": 70},
        "run": {"day": 3, "hour": 2},
        "offered": [],
        "player_board": board_cards,
        "player_stash": [],
        "player_skills": [],
        "opponent_board": [],
    }


def test_sell_event_emitted_when_card_gone_and_gold_up():
    collector = _Collector()
    adapter = MonoEventAdapter(collector, event_source="mono")

    snap1 = _board_snap(
        board_cards=[{"instance_id": "itm_sword", "template_id": "T_SWORD", "socket": "PS0"}],
        gold=10,
        ts="2026-01-01T00:00:01+00:00",
    )
    adapter.process_snapshot(snap1)

    # Card gone, gold increased by 5 (sell price)
    snap2 = _board_snap(
        board_cards=[],
        gold=15,
        ts="2026-01-01T00:00:02+00:00",
    )
    adapter.process_snapshot(snap2)

    sell_events = [e for e in collector.events if e["event"] == "card_sold"]
    assert len(sell_events) == 1, f"Expected 1 card_sold, got {len(sell_events)}"

    s = sell_events[0]
    assert s["instance_id"] == "itm_sword"
    assert s["template_id"] == "T_SWORD"
    assert s["gold"] == 5
    assert s["source"] == "mono"


def test_no_sell_when_gold_unchanged():
    collector = _Collector()
    adapter = MonoEventAdapter(collector, event_source="mono")

    snap1 = _board_snap(
        board_cards=[{"instance_id": "itm_hat", "template_id": "T_HAT", "socket": "PS1"}],
        gold=10,
    )
    adapter.process_snapshot(snap1)

    # Card gone but gold didn't increase (e.g. destroyed in combat)
    snap2 = _board_snap(board_cards=[], gold=10)
    adapter.process_snapshot(snap2)

    sell_events = [e for e in collector.events if e["event"] == "card_sold"]
    assert sell_events == []


def test_no_sell_when_gold_decreased():
    collector = _Collector()
    adapter = MonoEventAdapter(collector, event_source="mono")

    snap1 = _board_snap(
        board_cards=[{"instance_id": "itm_ring", "template_id": "T_RING", "socket": "PS2"}],
        gold=20,
    )
    adapter.process_snapshot(snap1)

    # Card gone, gold decreased (purchase?)
    snap2 = _board_snap(board_cards=[], gold=15)
    adapter.process_snapshot(snap2)

    sell_events = [e for e in collector.events if e["event"] == "card_sold"]
    assert sell_events == []


def test_no_sell_when_nothing_gone():
    collector = _Collector()
    adapter = MonoEventAdapter(collector, event_source="mono")

    card = {"instance_id": "itm_bow", "template_id": "T_BOW", "socket": "PS3"}
    snap1 = _board_snap(board_cards=[card], gold=10)
    adapter.process_snapshot(snap1)

    # Card still there, gold same
    snap2 = _board_snap(board_cards=[card], gold=10)
    adapter.process_snapshot(snap2)

    sell_events = [e for e in collector.events if e["event"] == "card_sold"]
    assert sell_events == []


def test_sell_template_id_from_prior_snapshot():
    """template_id must come from the PREVIOUS snapshot where the card existed."""
    collector = _Collector()
    adapter = MonoEventAdapter(collector, event_source="mono")

    snap1 = _board_snap(
        board_cards=[{"instance_id": "itm_gem", "template_id": "T_RUBY", "socket": "PS4"}],
        gold=5,
    )
    adapter.process_snapshot(snap1)

    snap2 = _board_snap(board_cards=[], gold=10)
    adapter.process_snapshot(snap2)

    sell_events = [e for e in collector.events if e["event"] == "card_sold"]
    assert sell_events[0]["template_id"] == "T_RUBY"


# ---------------------------------------------------------------------------
# Mono card_disposed pathway (issue #196)
# ---------------------------------------------------------------------------

def _snap_with_template_events(
    template_events: list,
    gold: int = 10,
    ts: str = "2026-01-01T00:00:00+00:00",
) -> dict:
    return {
        "timestamp": ts,
        "state": {"state": "EncounterState"},
        "player": {"hero": "Karnok", "Gold": gold, "Health": 70, "HealthMax": 70},
        "run": {"day": 2, "hour": 1},
        "offered": [],
        "player_board": [],
        "player_stash": [],
        "player_skills": [],
        "opponent_board": [],
        "card_template_events": template_events,
    }


def test_card_disposed_itm_emits_card_sold():
    """card_disposed with itm_ prefix → card_sold event emitted by adapter."""
    collector = _Collector()
    adapter = MonoEventAdapter(collector, event_source="mono")

    snap = _snap_with_template_events([
        {"event_type": "card_dealt", "instance_id": "itm_anaconda", "template_id": "T_ANACONDA"},
        {"event_type": "card_disposed", "instance_id": "itm_anaconda", "template_id": "T_ANACONDA"},
    ])
    adapter.process_snapshot(snap)

    sell_events = [e for e in collector.events if e["event"] == "card_sold"]
    assert len(sell_events) == 1
    s = sell_events[0]
    assert s["instance_id"] == "itm_anaconda"
    assert s["template_id"] == "T_ANACONDA"
    assert s["source"] == "mono"


def test_card_disposed_template_id_from_instance_map():
    """template_id resolved from card_dealt mapping when card_disposed carries none."""
    collector = _Collector()
    adapter = MonoEventAdapter(collector, event_source="mono")

    snap = _snap_with_template_events([
        {"event_type": "card_dealt", "instance_id": "itm_sword", "template_id": "T_SWORD"},
        {"event_type": "card_disposed", "instance_id": "itm_sword"},  # no template_id
    ])
    adapter.process_snapshot(snap)

    sell_events = [e for e in collector.events if e["event"] == "card_sold"]
    assert len(sell_events) == 1
    assert sell_events[0]["template_id"] == "T_SWORD"


def test_card_disposed_com_prefix_emits_card_sold():
    """card_disposed with com_ prefix also emits card_sold for companion sells."""
    collector = _Collector()
    adapter = MonoEventAdapter(collector, event_source="mono")

    snap = _snap_with_template_events([
        {"event_type": "card_dealt", "instance_id": "com_bear", "template_id": "T_BEAR"},
        {"event_type": "card_disposed", "instance_id": "com_bear", "template_id": "T_BEAR"},
    ])
    adapter.process_snapshot(snap)

    sell_events = [e for e in collector.events if e["event"] == "card_sold"]
    assert len(sell_events) == 1
    assert sell_events[0]["instance_id"] == "com_bear"


def test_card_disposed_non_player_prefix_no_event():
    """card_disposed with enc_/skl_/etc. prefix → no card_sold event emitted."""
    collector = _Collector()
    adapter = MonoEventAdapter(collector, event_source="mono")

    snap = _snap_with_template_events([
        {"event_type": "card_disposed", "instance_id": "enc_node1", "template_id": "T_ENC"},
        {"event_type": "card_disposed", "instance_id": "skl_dash", "template_id": "T_DASH"},
    ])
    adapter.process_snapshot(snap)

    sell_events = [e for e in collector.events if e["event"] == "card_sold"]
    assert sell_events == []


def test_card_disposed_dedup_across_snapshots():
    """The same instance_id disposed twice (inline + deferred) emits only one card_sold."""
    collector = _Collector()
    adapter = MonoEventAdapter(collector, event_source="mono")

    template_events = [
        {"event_type": "card_dealt", "instance_id": "itm_hat", "template_id": "T_HAT"},
        {"event_type": "card_disposed", "instance_id": "itm_hat", "template_id": "T_HAT"},
    ]
    snap1 = _snap_with_template_events(template_events, ts="2026-01-01T00:00:01+00:00")
    snap2 = _snap_with_template_events(template_events, ts="2026-01-01T00:00:02+00:00")

    adapter.process_snapshot(snap1)
    adapter.process_snapshot(snap2)

    sell_events = [e for e in collector.events if e["event"] == "card_sold"]
    assert len(sell_events) == 1, f"Expected 1 card_sold (dedup), got {len(sell_events)}"


def test_card_disposed_no_event_in_log_source():
    """card_disposed events are ignored when event_source is 'log'."""
    collector = _Collector()
    adapter = MonoEventAdapter(collector, event_source="log")

    snap = _snap_with_template_events([
        {"event_type": "card_disposed", "instance_id": "itm_ring", "template_id": "T_RING"},
    ])
    adapter.process_snapshot(snap)

    sell_events = [e for e in collector.events if e["event"] == "card_sold"]
    assert sell_events == []
