"""
tests/test_user_builds_resolver.py — Contract tests for the user_builds resolver tier.

Covers scorer._load_builds_cached precedence and catalog_source_status shape changes
introduced by the Local Build Override Editor (Group A).
"""

import copy
import json
from pathlib import Path

import pytest

import app_paths
import scorer
from web.build_helpers import invalidate_catalog_cache


# ── Shared minimal valid catalog fixture ──────────────────────────────────────

_MINIMAL_VALID = {
    "schema_version": 1,
    "hero": "Karnok",
    "season": 1,
    "last_updated": "2026-05-04",
    "notes": "Test catalog.",
    "item_tier_list": {
        "description": "Test tier list.",
        "S": ["Best Item"],
    },
    "game_phases": {
        "early": {
            "day_range": "Days 1-4",
            "description": "Early phase.",
            "universal_utility_items": ["Best Item"],
            "economy_items": [],
        },
        "early_mid": {
            "day_range": "Days 5-7",
            "description": "Early-mid phase.",
            "archetypes": [
                {
                    "name": "TestArch",
                    "carry_items": ["Best Item"],
                    "support_items": [],
                }
            ],
        },
        "late": {
            "day_range": "Day 8+",
            "description": "Late phase.",
            "archetypes": [
                {
                    "name": "TestLateArch",
                    "core_items": ["Best Item"],
                    "carry_items": ["Best Item"],
                    "support_items": [],
                    "timing_profile": "tempo",
                }
            ],
        },
    },
}


def _catalog(hero: str = "Karnok", *, last_updated: str = "2026-05-04") -> dict:
    data = copy.deepcopy(_MINIMAL_VALID)
    data["hero"] = hero
    data["last_updated"] = last_updated
    return data


# ── Autouse fixture — always clear caches before and after each test ──────────

@pytest.fixture(autouse=True)
def clear_scorer_caches():
    scorer._load_builds_cached.cache_clear()
    scorer._load_builds_schema.cache_clear()
    yield
    scorer._load_builds_cached.cache_clear()
    scorer._load_builds_schema.cache_clear()


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_user_catalog_overrides_bundled(tmp_path, monkeypatch):
    """A valid user catalog in user_builds/ is loaded in preference to bundled/writable."""
    bundled_dir = tmp_path / "bundled"
    bundled_dir.mkdir()
    (bundled_dir / "karnok_builds.json").write_text(
        json.dumps(_catalog("Karnok", last_updated="bundled")),
        encoding="utf-8",
    )

    user_dir = tmp_path / "user_builds"
    user_dir.mkdir()
    (user_dir / "karnok_user.json").write_text(
        json.dumps(_catalog("Karnok", last_updated="user_override")),
        encoding="utf-8",
    )

    monkeypatch.setenv("BAZAAR_COACH_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(scorer, "BUILD_GUIDE_DIR", bundled_dir)
    monkeypatch.setattr(scorer, "validate_builds_catalog", lambda data: (True, ""))

    builds = scorer.load_builds("Karnok")

    assert builds["last_updated"] == "user_override"


def test_user_catalog_disabled_falls_through(tmp_path, monkeypatch):
    """A user catalog with ``enabled: false`` is skipped; bundled catalog is used."""
    bundled_dir = tmp_path / "bundled"
    bundled_dir.mkdir()
    (bundled_dir / "karnok_builds.json").write_text(
        json.dumps(_catalog("Karnok", last_updated="bundled")),
        encoding="utf-8",
    )

    user_dir = tmp_path / "user_builds"
    user_dir.mkdir()
    disabled_catalog = _catalog("Karnok", last_updated="user_disabled")
    disabled_catalog["enabled"] = False
    (user_dir / "karnok_user.json").write_text(
        json.dumps(disabled_catalog),
        encoding="utf-8",
    )

    monkeypatch.setenv("BAZAAR_COACH_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(scorer, "BUILD_GUIDE_DIR", bundled_dir)
    monkeypatch.setattr(scorer, "validate_builds_catalog", lambda data: (True, ""))

    builds = scorer.load_builds("Karnok")

    assert builds["last_updated"] == "bundled"


def test_user_catalog_malformed_falls_through(tmp_path, monkeypatch):
    """A malformed (invalid JSON) user catalog is skipped; bundled catalog is used."""
    bundled_dir = tmp_path / "bundled"
    bundled_dir.mkdir()
    (bundled_dir / "karnok_builds.json").write_text(
        json.dumps(_catalog("Karnok", last_updated="bundled")),
        encoding="utf-8",
    )

    user_dir = tmp_path / "user_builds"
    user_dir.mkdir()
    (user_dir / "karnok_user.json").write_text("{not valid json", encoding="utf-8")

    monkeypatch.setenv("BAZAAR_COACH_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(scorer, "BUILD_GUIDE_DIR", bundled_dir)
    monkeypatch.setattr(scorer, "validate_builds_catalog", lambda data: (True, ""))

    builds = scorer.load_builds("Karnok")

    assert builds["last_updated"] == "bundled"


def test_user_catalog_schema_fail_falls_through(tmp_path, monkeypatch):
    """A user catalog that fails schema validation is skipped; bundled catalog is used."""
    bundled_dir = tmp_path / "bundled"
    bundled_dir.mkdir()
    (bundled_dir / "karnok_builds.json").write_text(
        json.dumps(_catalog("Karnok", last_updated="bundled")),
        encoding="utf-8",
    )
    # Copy the real schema so validation actually runs.
    real_schema = Path(__file__).resolve().parents[1] / "builds_schema.json"
    (bundled_dir / "builds_schema.json").write_text(
        real_schema.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    user_dir = tmp_path / "user_builds"
    user_dir.mkdir()
    # Missing required "hero" key — schema validation should reject this.
    invalid_catalog = {"schema_version": 1, "last_updated": "user_no_hero"}
    (user_dir / "karnok_user.json").write_text(
        json.dumps(invalid_catalog),
        encoding="utf-8",
    )

    monkeypatch.setenv("BAZAAR_COACH_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(scorer, "BUILD_GUIDE_DIR", bundled_dir)
    # Use real validation so the missing-hero schema failure is exercised.

    builds = scorer.load_builds("Karnok")

    assert builds["last_updated"] == "bundled"


def test_catalog_source_status_includes_user_tier(tmp_path, monkeypatch):
    """catalog_source_status returns 3 candidates with user_builds first and a disabled field."""
    bundled_dir = tmp_path / "bundled"
    bundled_dir.mkdir()
    (bundled_dir / "karnok_builds.json").write_text(
        json.dumps(_catalog("Karnok", last_updated="bundled")),
        encoding="utf-8",
    )

    monkeypatch.setenv("BAZAAR_COACH_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(scorer, "BUILD_GUIDE_DIR", bundled_dir)

    status = scorer.catalog_source_status("Karnok")
    candidates = status["candidates"]

    assert len(candidates) == 3
    assert candidates[0]["source"] == "user_builds"
    assert "disabled" in candidates[0]


def test_catalog_source_status_disabled_user_marks_disabled(tmp_path, monkeypatch):
    """A disabled user catalog is reflected in candidates[0] and selected falls to bundled/writable."""
    bundled_dir = tmp_path / "bundled"
    bundled_dir.mkdir()
    (bundled_dir / "karnok_builds.json").write_text(
        json.dumps(_catalog("Karnok", last_updated="bundled")),
        encoding="utf-8",
    )

    user_dir = tmp_path / "user_builds"
    user_dir.mkdir()
    disabled_catalog = _catalog("Karnok", last_updated="user_disabled")
    disabled_catalog["enabled"] = False
    (user_dir / "karnok_user.json").write_text(
        json.dumps(disabled_catalog),
        encoding="utf-8",
    )

    monkeypatch.setenv("BAZAAR_COACH_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(scorer, "BUILD_GUIDE_DIR", bundled_dir)
    monkeypatch.setattr(scorer, "validate_builds_catalog", lambda data: (True, ""))

    status = scorer.catalog_source_status("Karnok")
    candidates = status["candidates"]

    assert candidates[0]["disabled"] is True
    assert status["selected"]["source"] in ("writable", "bundled")


def test_invalidate_catalog_cache_clears_scorer_cache(tmp_path, monkeypatch):
    """After invalidate_catalog_cache(), scorer picks up a newly written user catalog."""
    bundled_dir = tmp_path / "bundled"
    bundled_dir.mkdir()
    (bundled_dir / "karnok_builds.json").write_text(
        json.dumps(_catalog("Karnok", last_updated="bundled")),
        encoding="utf-8",
    )

    monkeypatch.setenv("BAZAAR_COACH_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(scorer, "BUILD_GUIDE_DIR", bundled_dir)
    monkeypatch.setattr(scorer, "validate_builds_catalog", lambda data: (True, ""))

    # Initial load — should get bundled.
    builds_before = scorer.load_builds("Karnok")
    assert builds_before["last_updated"] == "bundled"

    # Now write a user catalog.
    user_dir = tmp_path / "user_builds"
    user_dir.mkdir(exist_ok=True)
    (user_dir / "karnok_user.json").write_text(
        json.dumps(_catalog("Karnok", last_updated="user_new")),
        encoding="utf-8",
    )

    # Without cache invalidation the old result is still cached.
    builds_cached = scorer.load_builds("Karnok")
    assert builds_cached["last_updated"] == "bundled"

    # After invalidation, the user catalog is picked up.
    invalidate_catalog_cache()

    builds_after = scorer.load_builds("Karnok")
    assert builds_after["last_updated"] == "user_new"
