import copy
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

import app_paths
import refresh_builds
import scorer


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


def _catalog(hero: str = "Karnok", *, schema_version: int = 1, last_updated: str = "2026-05-04") -> dict:
    data = copy.deepcopy(_MINIMAL_VALID)
    data["hero"] = hero
    data["schema_version"] = schema_version
    data["last_updated"] = last_updated
    return data


def _catalog_bytes(hero: str, *, schema_version: int = 1, last_updated: str = "2026-05-04") -> bytes:
    return json.dumps(
        _catalog(hero, schema_version=schema_version, last_updated=last_updated),
        sort_keys=True,
    ).encode("utf-8")


def _response(status_code: int, content: bytes = b"") -> SimpleNamespace:
    return SimpleNamespace(status_code=status_code, content=content)


@pytest.fixture(autouse=True)
def clear_scorer_caches():
    scorer._load_builds_cached.cache_clear()
    scorer._load_builds_schema.cache_clear()
    yield
    scorer._load_builds_cached.cache_clear()
    scorer._load_builds_schema.cache_clear()


def test_refresh_builds_happy_path_writes_all_catalogs(tmp_path, monkeypatch):
    out_dir = tmp_path / "builds"

    def fake_get(url, timeout):
        filename = url.rsplit("/", 1)[-1]
        hero = filename.removesuffix("_builds.json").title()
        return _response(200, _catalog_bytes(hero))

    monkeypatch.setattr(refresh_builds.requests, "get", fake_get)

    code = refresh_builds.main(["--out", str(out_dir)])

    assert code == 0
    for filename in scorer.CATALOG_FILENAMES.values():
        assert (out_dir / filename).is_file()


def test_refresh_builds_404_skips_one_and_returns_nonzero(tmp_path, monkeypatch):
    out_dir = tmp_path / "builds"
    calls = []

    def fake_get(url, timeout):
        filename = url.rsplit("/", 1)[-1]
        calls.append(filename)
        if filename == "mak_builds.json":
            return _response(404, b"not found")
        return _response(200, _catalog_bytes(filename.removesuffix("_builds.json")))

    monkeypatch.setattr(refresh_builds.requests, "get", fake_get)

    code = refresh_builds.main(["--out", str(out_dir)])

    assert code == 1
    assert set(calls) == set(scorer.CATALOG_FILENAMES.values())
    assert not (out_dir / "mak_builds.json").exists()
    assert sum(1 for path in out_dir.glob("*_builds.json")) == len(scorer.CATALOG_FILENAMES) - 1


def test_refresh_builds_connection_error_still_attempts_remaining(tmp_path, monkeypatch):
    out_dir = tmp_path / "builds"
    calls = []
    # Track which filename is the first one attempted (sorted order).
    first_hero_filename: list[str] = []

    def fake_get(url, timeout):
        filename = url.rsplit("/", 1)[-1]
        calls.append(filename)
        if not first_hero_filename:
            first_hero_filename.append(filename)
        # First hero fails on both initial attempt and retry; all others succeed.
        if filename == first_hero_filename[0]:
            raise requests.ConnectionError("offline")
        return _response(200, _catalog_bytes(filename.removesuffix("_builds.json")))

    monkeypatch.setattr(refresh_builds.requests, "get", fake_get)
    monkeypatch.setattr(refresh_builds.time, "sleep", lambda _: None)

    code = refresh_builds.main(["--out", str(out_dir)])

    assert code == 1
    # First hero is called twice (initial + retry), all others once.
    assert len(calls) == len(scorer.CATALOG_FILENAMES) + 1
    assert sum(1 for path in out_dir.glob("*_builds.json")) == len(scorer.CATALOG_FILENAMES) - 1


def test_refresh_builds_rejects_too_new_schema_without_writing(tmp_path, monkeypatch):
    out_dir = tmp_path / "builds"

    def fake_get(url, timeout):
        return _response(200, _catalog_bytes("Karnok", schema_version=999))

    monkeypatch.setattr(scorer, "CATALOG_FILENAMES", {"karnok": "karnok_builds.json"})
    monkeypatch.setattr(refresh_builds.requests, "get", fake_get)

    code = refresh_builds.main(["--out", str(out_dir)])

    assert code == 1
    assert not (out_dir / "karnok_builds.json").exists()


def test_refresh_builds_unchanged_content_does_not_write(tmp_path, monkeypatch):
    out_dir = tmp_path / "builds"
    out_dir.mkdir()
    content = _catalog_bytes("Karnok")
    destination = out_dir / "karnok_builds.json"
    destination.write_bytes(content)
    original_mtime = destination.stat().st_mtime_ns

    monkeypatch.setattr(scorer, "CATALOG_FILENAMES", {"karnok": "karnok_builds.json"})
    monkeypatch.setattr(refresh_builds.requests, "get", lambda url, timeout: _response(200, content))

    def fail_if_called(path, content):
        raise AssertionError("unchanged catalogs should not be rewritten")

    monkeypatch.setattr(refresh_builds, "_atomic_write_bytes", fail_if_called)

    code = refresh_builds.main(["--out", str(out_dir)])

    assert code == 0
    assert destination.stat().st_mtime_ns == original_mtime


def test_refresh_builds_summary_statuses():
    assert refresh_builds.summarize_results([
        refresh_builds.HeroRefreshResult("Karnok", "karnok_builds.json", "updated", "updated")
    ])["status"] == "updated"
    assert refresh_builds.summarize_results([
        refresh_builds.HeroRefreshResult("Karnok", "karnok_builds.json", "unchanged", "unchanged")
    ])["status"] == "unchanged"
    failed = refresh_builds.summarize_results([
        refresh_builds.HeroRefreshResult("Karnok", "karnok_builds.json", "skipped", "offline")
    ])
    assert failed["status"] == "failed"
    assert failed["ok"] is False


def test_build_refresh_route_reports_manual_result(monkeypatch):
    import web.server as server
    import asset_refresh

    with asset_refresh._lock:
        asset_refresh._state["builds"]["running"] = False
        asset_refresh._state["builds"]["last_result"] = None

    monkeypatch.setattr(
        server.refresh_builds,
        "refresh_builds",
        lambda: [
            refresh_builds.HeroRefreshResult(
                "Karnok",
                "karnok_builds.json",
                "updated",
                "updated: karnok_builds.json",
            )
        ],
    )
    monkeypatch.setattr(server, "_build_catalog_notes", lambda: [{
        "hero": "Karnok",
        "filename": "karnok_builds.json",
        "source": "writable",
        "last_updated": "2026-05-04",
        "season": 1,
        "notes": "Route test notes.",
    }])

    client = server.app.test_client()
    response = client.post("/api/builds/refresh")

    assert response.status_code == 202
    payload = response.get_json()
    assert "catalogs" in payload

    for _ in range(50):
        payload = client.get("/api/builds/refresh/status").get_json()
        if not payload["running"]:
            break
        time.sleep(0.01)

    assert payload["running"] is False
    assert payload["last_result"]["status"] == "updated"
    assert payload["last_result"]["updated"] == 1
    assert payload["catalogs"][0]["notes"] == "Route test notes."


def test_atomic_write_failure_leaves_no_partial_file(tmp_path, monkeypatch):
    destination = tmp_path / "builds" / "karnok_builds.json"

    def fail_replace(src, dst):
        raise OSError("rename failed")

    monkeypatch.setattr(os, "replace", fail_replace)

    with pytest.raises(OSError):
        refresh_builds._atomic_write_bytes(destination, b'{"partial": false}')

    assert not destination.exists()
    assert list(destination.parent.glob(".karnok_builds.json.*.tmp")) == []


def test_load_builds_prefers_valid_writable_catalog(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    bundled_dir = tmp_path / "bundled"
    writable_dir = data_dir / "builds"
    bundled_dir.mkdir()
    writable_dir.mkdir(parents=True)
    (bundled_dir / "karnok_builds.json").write_text(
        json.dumps(_catalog("Karnok", last_updated="bundled")),
        encoding="utf-8",
    )
    (writable_dir / "karnok_builds.json").write_text(
        json.dumps(_catalog("Karnok", last_updated="writable")),
        encoding="utf-8",
    )
    monkeypatch.setattr(app_paths, "data_dir", lambda: data_dir)
    monkeypatch.setattr(scorer, "BUILD_GUIDE_DIR", bundled_dir)
    monkeypatch.setattr(scorer, "validate_builds_catalog", lambda data: (True, ""))

    builds = scorer.load_builds("Karnok")

    assert builds["last_updated"] == "writable"


def test_load_builds_falls_back_to_bundled_when_writable_malformed(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    bundled_dir = tmp_path / "bundled"
    writable_dir = data_dir / "builds"
    bundled_dir.mkdir()
    writable_dir.mkdir(parents=True)
    (bundled_dir / "karnok_builds.json").write_text(
        json.dumps(_catalog("Karnok", last_updated="bundled")),
        encoding="utf-8",
    )
    (writable_dir / "karnok_builds.json").write_text("{malformed", encoding="utf-8")
    monkeypatch.setattr(app_paths, "data_dir", lambda: data_dir)
    monkeypatch.setattr(scorer, "BUILD_GUIDE_DIR", bundled_dir)
    monkeypatch.setattr(scorer, "validate_builds_catalog", lambda data: (True, ""))

    builds = scorer.load_builds("Karnok")

    assert builds["last_updated"] == "bundled"


def test_load_builds_returns_empty_when_no_catalog_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(app_paths, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(scorer, "BUILD_GUIDE_DIR", tmp_path / "bundled")

    builds = scorer.load_builds("Karnok")

    assert builds["hero"] == "Karnok"
    assert builds["last_updated"] is None
    assert not scorer.has_build_catalog(builds)


def test_refresh_one_retries_once_on_429_then_succeeds(tmp_path, monkeypatch):
    """A 429 on the first attempt triggers exactly one retry; success on retry → updated."""
    out_dir = tmp_path / "builds"
    calls = []
    content = _catalog_bytes("Karnok")

    def fake_get(url, timeout):
        calls.append(url)
        if len(calls) == 1:
            return SimpleNamespace(
                status_code=429,
                content=b"rate limited",
                headers={"Retry-After": "1"},
            )
        return _response(200, content)

    monkeypatch.setattr(refresh_builds.requests, "get", fake_get)
    monkeypatch.setattr(refresh_builds.time, "sleep", lambda _: None)

    result = refresh_builds._refresh_one("karnok", "karnok_builds.json", out_dir=out_dir)

    assert result.status == "updated", f"expected updated, got: {result}"
    assert len(calls) == 2, f"expected exactly 2 attempts, got {len(calls)}"
    assert (out_dir / "karnok_builds.json").is_file()


def test_refresh_one_no_retry_on_404(tmp_path, monkeypatch):
    """A 404 is non-retryable — exactly one attempt, skipped result."""
    calls = []

    def fake_get(url, timeout):
        calls.append(url)
        return _response(404, b"not found")

    monkeypatch.setattr(refresh_builds.requests, "get", fake_get)
    monkeypatch.setattr(refresh_builds.time, "sleep", lambda _: None)

    result = refresh_builds._refresh_one("karnok", "karnok_builds.json", out_dir=tmp_path)

    assert result.status == "skipped"
    assert len(calls) == 1


def test_refresh_one_retry_after_header_clamped(monkeypatch):
    """Retry-After values above the cap are clamped to _RETRY_AFTER_MAX."""
    slept: list[float] = []
    calls = []
    content = _catalog_bytes("Karnok")

    def fake_get(url, timeout):
        calls.append(url)
        if len(calls) == 1:
            return SimpleNamespace(
                status_code=503,
                content=b"busy",
                headers={"Retry-After": "999"},
            )
        return _response(200, content)

    monkeypatch.setattr(refresh_builds.requests, "get", fake_get)
    monkeypatch.setattr(refresh_builds.time, "sleep", lambda s: slept.append(s))

    out_dir = None
    # Use a simple tmp path via monkeypatching builds_dir isn't needed; pass a real tmp.
    import tempfile as _tf
    with _tf.TemporaryDirectory() as td:
        result = refresh_builds._refresh_one("karnok", "karnok_builds.json", out_dir=Path(td))

    assert result.status == "updated"
    assert len(slept) == 1
    assert slept[0] <= refresh_builds._RETRY_AFTER_MAX
