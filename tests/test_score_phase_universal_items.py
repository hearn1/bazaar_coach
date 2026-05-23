"""Regression tests for #81: universal_utility_items and economy_items must
not be flagged suboptimal in score_late_decision or score_early_mid_decision.

Function under test:
  scorer.score_late_decision(item_name, board_names, committed_arch, builds, *, day=None)
  scorer.score_early_mid_decision(item_name, board_names, builds, *, day=None)

Before the fix, items listed under any phase's universal_utility_items or
economy_items fell through to "Doesn't fit committed build / Likely wasted
pick" once an archetype was committed (and the non-committed and early_mid
paths similarly never consulted those lists).
"""
import pytest
import scorer


CARRY_ARCH = {
    "name": "Big Friends",
    "core_items": ["Friend Egg"],
    "carry_items": ["Friend Carry"],
    "support_items": ["Friend Buff"],
    "timing_profile": "neutral",
}

OTHER_ARCH = {
    "name": "Other Build",
    "core_items": ["Other Core"],
    "carry_items": ["Other Carry"],
    "support_items": ["Other Support"],
    "timing_profile": "neutral",
}


def _builds() -> dict:
    return {
        "item_tier_list": {"S": [], "A": [], "B": [], "C": [], "D": [], "F": []},
        "game_phases": {
            "early": {
                "universal_utility_items": ["Healing Potion"],
                "economy_items": ["Coin Pouch"],
            },
            "early_mid": {
                "archetypes": [
                    {
                        "name": "EM Build",
                        "carry_items": ["EM Carry"],
                        "support_items": ["EM Support"],
                    }
                ],
            },
            "late": {
                "archetypes": [CARRY_ARCH, OTHER_ARCH],
            },
        },
    }


# ---------------- score_late_decision: committed branch ----------------

def test_late_committed_universal_utility_returns_good():
    label, notes = scorer.score_late_decision(
        "Healing Potion", [], CARRY_ARCH, _builds()
    )
    assert label == "good"
    assert notes == "Universal utility — strong pickup regardless of archetype."


def test_late_committed_economy_item_returns_good():
    label, notes = scorer.score_late_decision(
        "Coin Pouch", [], CARRY_ARCH, _builds()
    )
    assert label == "good"
    assert notes == "Economy item — strong pickup regardless of archetype."


def test_late_committed_archetype_carry_still_wins_over_universal():
    """An item in the committed archetype's carry list should stay optimal,
    even if it were also listed as universal. Guards the ordering of the
    new short-circuit (archetype hit comes first)."""
    label, notes = scorer.score_late_decision(
        "Friend Carry", [], CARRY_ARCH, _builds()
    )
    assert label == "optimal"
    assert "Big Friends" in notes


def test_late_committed_unknown_item_still_suboptimal():
    """Regression guard: items not in any list keep existing suboptimal label."""
    label, notes = scorer.score_late_decision(
        "Random Junk", [], CARRY_ARCH, _builds()
    )
    assert label == "suboptimal"
    assert "Doesn't fit committed build" in notes


# ---------------- score_late_decision: non-committed branch ----------------

def test_late_uncommitted_universal_utility_returns_good():
    label, notes = scorer.score_late_decision(
        "Healing Potion", [], None, _builds()
    )
    assert label == "good"
    assert notes == "Universal utility — strong pickup regardless of archetype."


def test_late_uncommitted_economy_item_returns_good():
    label, notes = scorer.score_late_decision(
        "Coin Pouch", [], None, _builds()
    )
    assert label == "good"
    assert notes == "Economy item — strong pickup regardless of archetype."


def test_late_uncommitted_archetype_carry_still_uses_existing_path():
    """Regression guard: items that match a late archetype's carry slot still
    go through _rank_late_item_matches and get the 'Fits ...' message."""
    label, notes = scorer.score_late_decision(
        "Friend Carry", [], None, _builds()
    )
    # neutral timing profile + single late-archetype match → "situational"
    # via the _rank_late_item_matches single-match fallback.
    assert label == "situational"
    assert "Big Friends" in notes


# ---------------- score_early_mid_decision ----------------

def test_early_mid_universal_utility_returns_good():
    label, notes = scorer.score_early_mid_decision(
        "Healing Potion", [], _builds()
    )
    assert label == "good"
    assert notes == "Universal utility — strong pickup regardless of archetype."


def test_early_mid_economy_item_returns_good():
    label, notes = scorer.score_early_mid_decision(
        "Coin Pouch", [], _builds()
    )
    assert label == "good"
    assert notes == "Economy item — strong pickup regardless of archetype."


def test_early_mid_archetype_carry_still_uses_existing_path():
    """Regression guard: items that fit an early_mid archetype use the
    existing overlap-based scoring rather than the universal short-circuit."""
    label, notes = scorer.score_early_mid_decision(
        "EM Carry", [], _builds()
    )
    # Existing behavior: overlap == 1, board empty, no late_core_matches →
    # "Only fits EM Build and board has no other EM Build items yet."
    assert label == "situational"
    assert "EM Build" in notes
