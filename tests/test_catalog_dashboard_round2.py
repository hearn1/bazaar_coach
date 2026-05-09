from pathlib import Path

import pytest

import scorer


HTML = Path(__file__).resolve().parents[1] / "web" / "static" / "index.html"


@pytest.fixture(autouse=True)
def clear_scorer_cache():
    scorer._load_builds_cached.cache_clear()
    yield
    scorer._load_builds_cached.cache_clear()


def test_scorer_unknown_non_empty_hero_does_not_probe_slug_paths(monkeypatch):
    def fail_path_probe(hero):
        raise AssertionError(f"unexpected path probe for {hero}")

    monkeypatch.setattr(scorer, "_writable_builds_path", fail_path_probe)
    monkeypatch.setattr(scorer, "_builds_path", fail_path_probe)

    builds = scorer.load_builds("HeroX")

    assert builds["hero"] == "HeroX"
    assert builds["last_updated"] is None
    assert not scorer.has_build_catalog(builds)


def test_scorer_catalog_source_status_unknown_hero_has_stable_code():
    status = scorer.catalog_source_status("HeroX")

    assert status["ok"] is False
    assert status["code"] == "unknown_hero"
    assert status["hero"] == "HeroX"
    assert status["filename"] is None
    assert status["message"] == "No build catalog for HeroX"
    assert status["candidates"] == []


def test_scorer_blank_hero_still_uses_default_catalog_filename():
    assert (
        scorer._hero_catalog_filename(" ")
        == scorer.CATALOG_FILENAMES[scorer.DEFAULT_HERO.casefold()]
    )


def test_dashboard_refresh_messages_render_per_hero_result_messages():
    html = HTML.read_text(encoding="utf-8")

    assert "function buildPerHeroRefreshMessages(result)" in html
    assert "result.results" in html
    assert ".filter(r => r && r.message)" in html
    assert "r.message" in html


def test_dashboard_refresh_incompatible_schema_banner_copy():
    html = HTML.read_text(encoding="utf-8")

    assert "function incompatibleRefreshResults(result)" in html
    assert "validation failed" in html
    assert "schema_version" in html
    assert "outside supported range" in html
    assert (
        "Refreshed catalog was skipped as incompatible; bundled catalog remains active."
        in html
    )
