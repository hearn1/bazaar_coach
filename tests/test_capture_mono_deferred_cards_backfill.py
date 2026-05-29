"""
test_capture_mono_deferred_cards_backfill.py

Regression test for the mid-run board-capture gap.

Owned card sets (player_board / stash / skills / opponent_board) enter the DB
ONLY via a heavy GameStateSync / RunInitialized full read, which the JS agent
decodes off the game thread and ships in a later ``deferred_cards`` message.
``capture_mono.handle_deferred_cards`` originally merged those cards only when
``_last_merged_snapshot.id == deferred snapshot_id``. During a Choice burst,
fast-gamesim deltas advance ``_last_merged_snapshot`` past that id before the
decoded cards arrive, so the exact-id match failed and the board was dropped —
it never entered ``_last_merged_snapshot`` and so could never carry forward.
On any mid-run attach that races a Choice, ``board_count`` stayed 0 for the
whole run and purchases (offered->board moves) became invisible.

The fix: when a late ``deferred_cards`` arrives for an *older* snapshot, backfill
the *persistent* (owned) card sets into ``_last_merged_snapshot`` wherever they
are currently empty, so they carry forward to the next snapshot. Transient
``offered`` is NOT backfilled (it belongs to a specific shop window).
"""

import capture_mono


def _reset(monkeypatch):
    monkeypatch.setattr(capture_mono, "_mono_event_adapter", None)
    monkeypatch.setattr(capture_mono, "_deferred_cards_by_snapshot_id", {})
    monkeypatch.setattr(capture_mono, "_do_log", False)
    monkeypatch.setattr(capture_mono, "_do_db", False)
    monkeypatch.setattr(capture_mono, "_VERBOSE_HOOKS", False)


def _merged(snap_id, **lists):
    snap = {
        "id": snap_id,
        "offered": [],
        "player_board": [],
        "player_stash": [],
        "player_skills": [],
        "opponent_board": [],
    }
    snap.update(lists)
    return snap


def test_late_deferred_cards_backfill_board_when_empty(monkeypatch):
    """A late GameStateSync card read (id behind _last_merged_snapshot) must
    backfill the empty board so it carries forward."""
    _reset(monkeypatch)
    # _last_merged_snapshot has advanced to a newer fast-gamesim delta with no
    # owned cards (board never got introduced this process).
    monkeypatch.setattr(capture_mono, "_last_merged_snapshot", _merged(20))

    capture_mono.handle_deferred_cards({
        "snapshot_id": 17,  # older than _last_merged_snapshot.id == 20
        "cards": {
            "player_board": [
                {"instance_id": "itm_sword", "template_id": "T_SWORD"},
            ],
            "player_skills": [
                {"instance_id": "skl_a", "template_id": "T_SKILL"},
            ],
            "offered": [
                {"instance_id": "itm_offer", "template_id": "T_OFFER"},
            ],
        },
    })

    lm = capture_mono._last_merged_snapshot
    assert [c["instance_id"] for c in lm["player_board"]] == ["itm_sword"]
    assert [c["instance_id"] for c in lm["player_skills"]] == ["skl_a"]
    # Transient offered is NOT backfilled from a stale shop window.
    assert lm["offered"] == []


def test_late_deferred_cards_does_not_overwrite_existing_board(monkeypatch):
    """If the board already carried forward, a late stale read must not clobber it."""
    _reset(monkeypatch)
    monkeypatch.setattr(
        capture_mono,
        "_last_merged_snapshot",
        _merged(20, player_board=[{"instance_id": "itm_current", "template_id": "T_CUR"}]),
    )

    capture_mono.handle_deferred_cards({
        "snapshot_id": 17,
        "cards": {
            "player_board": [{"instance_id": "itm_stale", "template_id": "T_STALE"}],
        },
    })

    lm = capture_mono._last_merged_snapshot
    assert [c["instance_id"] for c in lm["player_board"]] == ["itm_current"]


def test_exact_id_match_still_merges_all_sets(monkeypatch):
    """Regression: when ids match, every card set (incl. offered) is merged."""
    _reset(monkeypatch)
    monkeypatch.setattr(capture_mono, "_last_merged_snapshot", _merged(20))

    capture_mono.handle_deferred_cards({
        "snapshot_id": 20,
        "cards": {
            "player_board": [{"instance_id": "itm_b", "template_id": "T_B"}],
            "offered": [{"instance_id": "itm_o", "template_id": "T_O"}],
        },
    })

    lm = capture_mono._last_merged_snapshot
    assert [c["instance_id"] for c in lm["player_board"]] == ["itm_b"]
    assert [c["instance_id"] for c in lm["offered"]] == ["itm_o"]
