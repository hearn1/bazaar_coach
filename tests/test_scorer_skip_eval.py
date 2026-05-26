"""
Tests for scorer skip evaluation using rejected_templates_json (issue #130).

Constructs a decision where the rejected card is a build-relevant item.
Asserts that score_label reflects "missed strong item" — i.e. the scorer
uses template-resolved names rather than raw instance IDs when evaluating
whether a skip was a mistake.
"""

import json
import sqlite3

import db
import scorer
import card_cache
from scorer import _resolve_rejected_names, _find_missed_flags


# ── Catalog fixture ────────────────────────────────────────────────────────────

def _load_karnok():
    return scorer.load_builds("Karnok")


# ── Direct scorer function tests ───────────────────────────────────────────────

def test_resolve_rejected_names_uses_templates_when_available():
    """_resolve_rejected_names should prefer rejected_templates_json over
    position-matching when the column is present and aligned.

    Injects names directly into the in-memory cache to avoid touching the DB,
    then cleans up after the test.
    """
    _SENTINEL = object()

    # Inject test entries into the existing cache dict (no replacement)
    cache = card_cache._template_name_cache
    prev_a = cache.get("T_FlyingSquirrel", _SENTINEL)
    prev_b = cache.get("T_Waterskin", _SENTINEL)
    cache["T_FlyingSquirrel"] = "Flying Squirrel"
    cache["T_Waterskin"] = "Waterskin"

    try:
        decision = {
            "rejected": json.dumps(["itm_xyz", "itm_abc"]),
            "rejected_templates_json": json.dumps(["T_FlyingSquirrel", "T_Waterskin"]),
        }
        offered_raw = ["itm_xyz", "itm_abc"]
        offered_names = ["[unresolved:itm_xyz]", "[unresolved:itm_abc]"]

        names = _resolve_rejected_names(decision, offered_raw, offered_names)

        # Raw instance IDs must not appear — template resolution was used.
        assert "itm_xyz" not in names, (
            f"Raw instance ID should not appear when template data is available: {names}"
        )
        assert "itm_abc" not in names, (
            f"Raw instance ID should not appear when template data is available: {names}"
        )
        # The result should contain the human-readable names from the cache.
        assert "Flying Squirrel" in names, f"Expected 'Flying Squirrel' in names: {names}"
        assert "Waterskin" in names, f"Expected 'Waterskin' in names: {names}"
    finally:
        # Restore original cache state
        if prev_a is _SENTINEL:
            cache.pop("T_FlyingSquirrel", None)
        else:
            cache["T_FlyingSquirrel"] = prev_a
        if prev_b is _SENTINEL:
            cache.pop("T_Waterskin", None)
        else:
            cache["T_Waterskin"] = prev_b


def test_resolve_rejected_names_falls_back_without_templates():
    """Without rejected_templates_json, resolution falls back to offered_names
    position matching (existing behavior)."""
    decision = {
        "rejected": json.dumps(["itm_a", "itm_b"]),
        "rejected_templates_json": None,
    }
    offered_raw = ["itm_a", "itm_b", "itm_c"]
    offered_names = ["Item A", "Item B", "Item C"]

    names = _resolve_rejected_names(decision, offered_raw, offered_names)
    assert names == ["Item A", "Item B"], f"Unexpected fallback result: {names}"


def test_find_missed_flags_detects_utility_item_skip():
    """_find_missed_flags should flag 'Flying Squirrel' as Universal utility
    when it appears in offered_names during the early phase."""
    builds = _load_karnok()
    # Flying Squirrel is a universal_utility_item for Karnok early phase
    offered_names = ["Flying Squirrel"]
    missed = _find_missed_flags(
        offered_names, "early", [], None, builds,
    )
    assert missed, f"Expected at least one missed flag for Flying Squirrel, got: {missed}"
    combined = " ".join(missed)
    assert "Flying Squirrel" in combined, (
        f"Expected 'Flying Squirrel' in missed flags: {missed}"
    )


def test_scorer_skip_label_uses_template_resolved_names(tmp_path, monkeypatch):
    """End-to-end: when rejected_templates_json is populated with a build-relevant
    item, the scorer's skip label should be 'warning' (not 'info') and the notes
    should reference the missed item by name."""
    # Point db at temp location
    path = tmp_path / "bazaar_runs.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    db.close_shared_conn()
    db.init_db()

    builds = _load_karnok()

    # Seed the in-memory card_cache directly (avoids a DB round-trip and does
    # not replace the module-level dict, preserving isolation).
    _SENTINEL = object()
    cache = card_cache._template_name_cache
    prev_flying = cache.get("T_FlyingSquirrel", _SENTINEL)
    cache["T_FlyingSquirrel"] = "Flying Squirrel"

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        # Construct a synthetic skip decision.
        # offered = [itm_x]  (raw instance ID — would be unresolvable without template data)
        # rejected = [itm_x] (same, since it's a skip)
        # rejected_templates_json = ["T_FlyingSquirrel"]  <- populated by RunState
        # offered_names = null (not resolved via Mono name path)
        # score_notes has resolved_names=[] (empty — simulates unresolved Mono)
        decision = {
            "decision_seq": 2,
            "decision_type": "skip",
            "game_state": "EncounterState",
            "offered": json.dumps(["itm_x"]),
            "chosen_id": "",
            "chosen_template": "",
            "rejected": json.dumps(["itm_x"]),
            "rejected_templates_json": json.dumps(["T_FlyingSquirrel"]),
            "offered_names": None,
            "board_section": "",
            "score_notes": json.dumps({"resolved_names": [], "rerolls": 0}),
            "day": 2,
            "phase_actual": "early",
        }

        result = scorer._score_single_decision(
            conn,
            decision,
            board={},
            committed_arch=None,
            combat_count=0,
            builds=builds,
        )
        label = result.get("label")
        notes = result.get("notes") or ""

        # Skip with a missed utility item -> label must be "warning"
        assert label == "warning", (
            f"Expected 'warning' for a skip where Flying Squirrel was offered, "
            f"got: {label!r}. Notes: {notes}"
        )
        # Notes must reference the missed item
        assert "Flying Squirrel" in notes or "Universal utility" in notes, (
            f"Expected 'Flying Squirrel' or 'Universal utility' in notes: {notes!r}"
        )
    finally:
        conn.close()
        # Restore original cache state
        if prev_flying is _SENTINEL:
            cache.pop("T_FlyingSquirrel", None)
        else:
            cache["T_FlyingSquirrel"] = prev_flying
