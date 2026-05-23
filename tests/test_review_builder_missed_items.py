"""
Regression tests for issue #77 — missed-item rows in the overlay Review tab.

Covers:
- Fallback path (no committed archetype, no enabled archetypes) emits a row
  per economy/utility leftover instead of only the first.
- Mixed economy + utility leftovers both emit, preserving leftover order.
- A miss in shop A survives even when the same item is bought later in shop B
  (the trailing acquired-name filter is per-shop-visit, not per-run).
- Archetype-aware path still emits exactly one (best-ranked) row per shop
  close, even when multiple rejects could match.

_pick_best_review_match and find_committed_archetype are mocked so tests
don't depend on live build catalogs.
"""

import json
import sqlite3

import db
import scorer
import web.review_builder as rb


_FIXED_MATCH = {
    "item_name": "Sword",
    "arch_name": "Bleed Build",
    "bucket": "core",
    "kind": "enable",
    "rank": (1, 3, 1, 1, 0, 0, 1, 1, 0, 1),
}


def _make_decision(seq, dtype, chosen=None, rejected=None, offered=None):
    offered = offered or []
    rejected = rejected if rejected is not None else []
    return {
        "id": seq,
        "decision_seq": seq,
        "decision_type": dtype,
        "game_state": "EncounterState",
        "board_section": "Player",
        "chosen_id": f"itm_{seq}" if chosen else None,
        "chosen_template": f"T_{seq}" if chosen else None,
        "chosen_name": chosen,
        "offered": json.dumps(offered),
        "offered_raw": offered,
        "offered_names": json.dumps(offered),
        "rejected": json.dumps(rejected),
        "score_label": None,
        "score_notes": None,
    }


def _fake_resolve_names(conn, decision, *, resolve_fn, safe_json_fn):
    offered_raw = safe_json_fn(decision.get("offered") or "[]") or []
    rejected_raw = safe_json_fn(decision.get("rejected") or "[]") or []
    return {
        "chosen_template": decision.get("chosen_template") or "",
        "chosen_name": decision.get("chosen_name"),
        "offered_raw": offered_raw,
        "offered_names": offered_raw,
        "rejected_names": rejected_raw,
        "resolved_offered": [n for n in offered_raw if isinstance(n, str)],
        "resolved_rejected": [n for n in rejected_raw if isinstance(n, str)],
    }


_DEFAULT_BUILD_DATA = {"game_phases": {"late": {"archetypes": []}}}


def _run(monkeypatch, tmp_path, decisions, pick_fn, build_data=None,
         committed_arch=None):
    db_path = tmp_path / "bazaar_runs.db"
    monkeypatch.setattr(db, "DB_PATH", db_path)
    db.close_shared_conn()
    db.init_db()

    monkeypatch.setattr(scorer, "_load_board_snapshot_map", lambda conn, run_id: {})
    monkeypatch.setattr(
        scorer, "find_committed_archetype",
        lambda board_names, build_data: (committed_arch, None),
    )
    monkeypatch.setattr(rb, "resolve_overlay_decision_names", _fake_resolve_names)
    monkeypatch.setattr(rb, "_pick_best_review_match", pick_fn)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return rb.build_overlay_review_rows(
            conn,
            run_id=1,
            decisions=decisions,
            build_data=build_data if build_data is not None else _DEFAULT_BUILD_DATA,
            resolve_fn=lambda conn, t: t,
            safe_json_fn=json.loads,
        )
    finally:
        conn.close()


def _pick_for_names(*names):
    """Mock that returns a match (item_name echoed) for the first matching name."""
    def _fn(item_names, board_names, archetypes):
        for n in (item_names or []):
            if n in names:
                match = dict(_FIXED_MATCH)
                match["item_name"] = n
                return match
        return None
    return _fn


# ── Test cases ────────────────────────────────────────────────────────────────

def test_multiple_economy_leftovers_all_emit_as_missed(monkeypatch, tmp_path):
    """Early-run shop, no archetype: every economy leftover surfaces, not just the first."""
    decisions = [
        _make_decision(
            5, "item", chosen="Sharpening Stone",
            rejected=["Hunter's Journal", "Tinderbox"],
            offered=["Sharpening Stone", "Hunter's Journal", "Tinderbox"],
        ),
    ]
    build_data = {
        "game_phases": {
            "early": {
                "archetypes": [],
                "economy_items": ["Hunter's Journal", "Tinderbox"],
                "universal_utility_items": [],
            },
            "late": {"archetypes": []},
        }
    }
    # Archetype matcher returns None — force fallback path.
    rows = _run(monkeypatch, tmp_path, decisions, pick_fn=lambda *a: None,
                build_data=build_data)

    missed = [r for r in rows if r["derived_score_label"] == "missed"]
    assert len(missed) == 2
    titles = [r["review_title"] for r in missed]
    assert "Hunter's Journal" in titles
    assert "Tinderbox" in titles
    assert all(r["decision_seq"] == 5 for r in missed)
    assert all(r["review_kind"] == "economy" for r in missed)


def test_mixed_economy_and_utility_leftovers_all_emit(monkeypatch, tmp_path):
    """Mixed economy + utility leftovers each emit with the correct review_kind."""
    decisions = [
        _make_decision(
            7, "item", chosen="Sharpening Stone",
            rejected=["Hunter's Journal", "Crusher Claw"],
            offered=["Sharpening Stone", "Hunter's Journal", "Crusher Claw"],
        ),
    ]
    build_data = {
        "game_phases": {
            "early": {
                "archetypes": [],
                "economy_items": ["Hunter's Journal"],
                "universal_utility_items": ["Crusher Claw"],
            },
            "late": {"archetypes": []},
        }
    }
    rows = _run(monkeypatch, tmp_path, decisions, pick_fn=lambda *a: None,
                build_data=build_data)

    missed = [r for r in rows if r["derived_score_label"] == "missed"]
    # build_overlay_review_rows returns rows in reverse insertion order; the
    # fallback appends in leftover_names order, so utility comes second pre-reverse.
    kinds_by_title = {r["review_title"]: r["review_kind"] for r in missed}
    assert kinds_by_title == {
        "Hunter's Journal": "economy",
        "Crusher Claw": "utility",
    }


def test_miss_in_shop_a_acquired_in_shop_b_surfaces_miss(monkeypatch, tmp_path):
    """A miss in shop A survives the trailing filter when the same item is bought in shop B."""
    decisions = [
        # Shop A: bought Tinderbox, passed on Hunter's Journal.
        _make_decision(
            1, "item", chosen="Tinderbox", rejected=["Hunter's Journal"],
            offered=["Tinderbox", "Hunter's Journal"],
        ),
        # Shop B (different rejected key → flushes A first): bought Hunter's Journal.
        _make_decision(
            2, "item", chosen="Hunter's Journal", rejected=[],
            offered=["Hunter's Journal"],
        ),
    ]
    rows = _run(monkeypatch, tmp_path, decisions,
                pick_fn=_pick_for_names("Hunter's Journal"))

    missed = [r for r in rows if r["derived_score_label"] == "missed"]
    assert len(missed) == 1
    assert missed[0]["decision_seq"] == 1
    assert missed[0]["review_title"] == "Hunter's Journal"


def test_committed_archetype_single_leftover_emits_one_row(monkeypatch, tmp_path):
    """Archetype path: a single archetype-relevant leftover emits exactly one missed row."""
    decisions = [
        _make_decision(
            3, "item", chosen="Buckler", rejected=["Sword"],
            offered=["Buckler", "Sword"],
        ),
    ]
    committed = {"name": "Bleed Build"}
    rows = _run(monkeypatch, tmp_path, decisions,
                pick_fn=_pick_for_names("Sword"),
                committed_arch=committed)

    missed = [r for r in rows if r["derived_score_label"] == "missed"]
    assert len(missed) == 1
    assert missed[0]["decision_seq"] == 3
    assert missed[0]["review_title"] == "Sword"
    assert missed[0]["review_build_name"] == "Bleed Build"
    assert missed[0]["review_kind"] == "enable"


def test_archetype_path_multiple_matching_rejects_emits_one_row(monkeypatch, tmp_path):
    """Archetype path is single-best-match — multiple matching rejects still emit one row."""
    decisions = [
        _make_decision(
            4, "item", chosen="Buckler",
            rejected=["Sword", "Dagger"],
            offered=["Buckler", "Sword", "Dagger"],
        ),
    ]
    committed = {"name": "Bleed Build"}
    # Both Sword and Dagger would match; the mock returns the first hit.
    rows = _run(monkeypatch, tmp_path, decisions,
                pick_fn=_pick_for_names("Sword", "Dagger"),
                committed_arch=committed)

    missed = [r for r in rows if r["derived_score_label"] == "missed"]
    assert len(missed) == 1
    assert missed[0]["decision_seq"] == 4
