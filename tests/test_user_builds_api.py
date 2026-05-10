"""
tests/test_user_builds_api.py — Flask test-client tests for the
/api/builds/user/<hero> CRUD routes added in Group B.

Style mirrors tests/test_server_round2.py.

Dependency note: relies on app_paths.user_builds_path() and
web.build_helpers.invalidate_catalog_cache() being present.  Both are
already implemented (app_paths has the functions; build_helpers has
invalidate_catalog_cache).  If either symbol is missing, collection will
fail with an ImportError — document that as an A-side dependency.
"""

import copy
import json
import os
from pathlib import Path

import pytest

import web.server as server

# ---------------------------------------------------------------------------
# Minimal valid catalog used by PUT tests
# ---------------------------------------------------------------------------

_VALID_ARCHETYPE = {
    "name": "Test Carry",
    "carry_items": ["Sword of Tests"],
    "support_items": ["Shield of Tests"],
}

_VALID_ARCHETYPE_LATE = {
    "name": "Test Late Carry",
    "carry_items": ["Late Sword"],
    "support_items": [],
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_catalog_cache():
    """Clear lru_cache state before and after each test."""
    from web.build_helpers import invalidate_catalog_cache
    invalidate_catalog_cache()
    yield
    invalidate_catalog_cache()


@pytest.fixture()
def tmp_data_dir(tmp_path, monkeypatch):
    """Point BAZAAR_COACH_DATA_DIR at a fresh temp dir for each test."""
    monkeypatch.setenv("BAZAAR_COACH_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture()
def stub_invalidate(monkeypatch):
    """Replace invalidate_catalog_cache with a recorder so we can assert calls."""
    calls = []
    monkeypatch.setattr(
        "web.build_helpers.invalidate_catalog_cache",
        lambda hero=None: calls.append(hero),
        raising=False,
    )
    # Also patch the reference that server.py holds (it imported the name directly)
    monkeypatch.setattr(server, "invalidate_catalog_cache", lambda hero=None: calls.append(hero))
    return calls


@pytest.fixture()
def client():
    return server.app.test_client()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _put_archetype(client, hero: str, archetype: dict, phase: str = "early_mid"):
    body = copy.deepcopy(archetype)
    body["phase"] = phase
    return client.put(
        f"/api/builds/user/{hero}",
        data=json.dumps({"archetype": body}),
        content_type="application/json",
    )


def _user_file_path(tmp_path: Path, hero_slug: str) -> Path:
    return tmp_path / "user_builds" / f"{hero_slug}_user.json"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestGetUserCatalog:
    def test_get_user_catalog_empty_returns_bundled(self, client, tmp_data_dir):
        """GET when no user file exists returns 200 with catalog and provenance."""
        resp = client.get("/api/builds/user/Karnok")
        assert resp.status_code == 200
        payload = resp.get_json()
        assert payload["ok"] is True
        assert "catalog" in payload
        assert "provenance" in payload
        assert payload["provenance"]["user_file_exists"] is False

    def test_get_unknown_hero_returns_404(self, client, tmp_data_dir):
        resp = client.get("/api/builds/user/NotAHero")
        assert resp.status_code == 404
        payload = resp.get_json()
        assert payload["ok"] is False
        assert payload["errors"]


class TestPutArchetype:
    def test_put_archetype_creates_file(self, client, tmp_data_dir):
        """PUT a valid archetype; assert 200, file on disk."""
        resp = _put_archetype(client, "Karnok", _VALID_ARCHETYPE)
        assert resp.status_code == 200
        payload = resp.get_json()
        assert payload["ok"] is True
        assert _user_file_path(tmp_data_dir, "karnok").exists()

    def test_put_archetype_invalid_schema_returns_400(self, client, tmp_data_dir):
        """PUT an archetype missing required carry_items → 400."""
        bad_arch = {"name": "Broken", "support_items": []}
        resp = _put_archetype(client, "Karnok", bad_arch)
        assert resp.status_code == 400
        payload = resp.get_json()
        assert payload["ok"] is False
        assert payload["errors"]

    def test_put_archetype_replaces_by_name(self, client, tmp_data_dir):
        """PUT same archetype name twice; only one entry should exist."""
        _put_archetype(client, "Karnok", _VALID_ARCHETYPE)
        modified = copy.deepcopy(_VALID_ARCHETYPE)
        modified["carry_items"] = ["Different Sword"]
        resp = _put_archetype(client, "Karnok", modified)
        assert resp.status_code == 200

        user_file = _user_file_path(tmp_data_dir, "karnok")
        data = json.loads(user_file.read_text(encoding="utf-8"))
        early_mid_archs = data["game_phases"]["early_mid"]["archetypes"]
        names = [a["name"] for a in early_mid_archs]
        assert names.count(_VALID_ARCHETYPE["name"]) == 1
        # And the updated carry_items should be present
        stored = next(a for a in early_mid_archs if a["name"] == _VALID_ARCHETYPE["name"])
        assert stored["carry_items"] == ["Different Sword"]

    def test_put_archetype_bad_phase_returns_400(self, client, tmp_data_dir):
        """PUT with phase='early' (structurally different) returns 400."""
        resp = _put_archetype(client, "Karnok", _VALID_ARCHETYPE, phase="early")
        assert resp.status_code == 400

    def test_put_unknown_hero_returns_404(self, client, tmp_data_dir):
        resp = _put_archetype(client, "NotAHero", _VALID_ARCHETYPE)
        assert resp.status_code == 404

    def test_put_archetype_phase_key_not_stored(self, client, tmp_data_dir):
        """The routing 'phase' key must NOT appear in the stored archetype dict."""
        _put_archetype(client, "Karnok", _VALID_ARCHETYPE)
        user_file = _user_file_path(tmp_data_dir, "karnok")
        data = json.loads(user_file.read_text(encoding="utf-8"))
        for arch in data["game_phases"]["early_mid"]["archetypes"]:
            assert "phase" not in arch


class TestDeleteArchetype:
    def test_delete_archetype_removes_entry(self, client, tmp_data_dir):
        """PUT then DELETE removes the archetype."""
        _put_archetype(client, "Karnok", _VALID_ARCHETYPE)
        resp = client.delete(f"/api/builds/user/Karnok/{_VALID_ARCHETYPE['name']}")
        assert resp.status_code == 200
        payload = resp.get_json()
        assert payload["ok"] is True

        user_file = _user_file_path(tmp_data_dir, "karnok")
        data = json.loads(user_file.read_text(encoding="utf-8"))
        all_archs = []
        for pd in data["game_phases"].values():
            all_archs.extend(pd.get("archetypes", []))
        assert not any(a["name"] == _VALID_ARCHETYPE["name"] for a in all_archs)

    def test_delete_unknown_archetype_returns_404(self, client, tmp_data_dir):
        """DELETE a name that was never PUT → 404."""
        # First create the file so we don't hit the "no user catalog" 404
        _put_archetype(client, "Karnok", _VALID_ARCHETYPE)
        resp = client.delete("/api/builds/user/Karnok/DoesNotExistArch")
        assert resp.status_code == 404

    def test_delete_no_file_returns_404(self, client, tmp_data_dir):
        """DELETE when no user file exists → 404."""
        resp = client.delete("/api/builds/user/Karnok/SomeName")
        assert resp.status_code == 404

    def test_delete_unknown_hero_returns_404(self, client, tmp_data_dir):
        resp = client.delete("/api/builds/user/NotAHero/SomeName")
        assert resp.status_code == 404


class TestEnableDisable:
    def test_disable_when_no_file_returns_ok(self, client, tmp_data_dir):
        """Disabling when no user file is a no-op returning ok=True, enabled=False."""
        resp = client.post("/api/builds/user/Karnok/disable")
        assert resp.status_code == 200
        payload = resp.get_json()
        assert payload["ok"] is True
        assert payload["enabled"] is False
        # Should NOT create a file
        assert not _user_file_path(tmp_data_dir, "karnok").exists()

    def test_enable_creates_skeleton_if_absent(self, client, tmp_data_dir):
        """Enable with no existing file creates a skeleton with enabled=True."""
        resp = client.post("/api/builds/user/Karnok/enable")
        assert resp.status_code == 200
        payload = resp.get_json()
        assert payload["ok"] is True
        assert payload["enabled"] is True
        user_file = _user_file_path(tmp_data_dir, "karnok")
        assert user_file.exists()
        data = json.loads(user_file.read_text(encoding="utf-8"))
        assert data["enabled"] is True

    def test_disable_then_enable_roundtrip(self, client, tmp_data_dir):
        """PUT → disable → enable returns enabled=True on the last call."""
        _put_archetype(client, "Karnok", _VALID_ARCHETYPE)

        resp_dis = client.post("/api/builds/user/Karnok/disable")
        assert resp_dis.status_code == 200
        assert resp_dis.get_json()["enabled"] is False

        user_file = _user_file_path(tmp_data_dir, "karnok")
        data = json.loads(user_file.read_text(encoding="utf-8"))
        assert data["enabled"] is False

        resp_en = client.post("/api/builds/user/Karnok/enable")
        assert resp_en.status_code == 200
        assert resp_en.get_json()["enabled"] is True

        data2 = json.loads(user_file.read_text(encoding="utf-8"))
        assert data2["enabled"] is True

    def test_disable_unknown_hero_returns_404(self, client, tmp_data_dir):
        resp = client.post("/api/builds/user/NotAHero/disable")
        assert resp.status_code == 404

    def test_enable_unknown_hero_returns_404(self, client, tmp_data_dir):
        resp = client.post("/api/builds/user/NotAHero/enable")
        assert resp.status_code == 404


class TestInvalidateCalled:
    def test_invalidate_called_on_put(self, client, tmp_data_dir, stub_invalidate):
        _put_archetype(client, "Karnok", _VALID_ARCHETYPE)
        assert len(stub_invalidate) >= 1

    def test_invalidate_called_on_delete(self, client, tmp_data_dir, stub_invalidate):
        # Create the file first without stub (use a real PUT)
        # We need to bypass the stub for the setup PUT because the stub
        # replaces invalidate for the whole test.  Just write the file manually.
        user_file = _user_file_path(tmp_data_dir, "karnok")
        user_file.parent.mkdir(parents=True, exist_ok=True)
        skeleton = {
            "schema_version": 1,
            "hero": "Karnok",
            "season": None,
            "last_updated": None,
            "notes": "",
            "enabled": True,
            "item_tier_list": {},
            "pivot_signals": {"signals": []},
            "scoring_weights": {"core": 0.50, "carry": 0.35, "support": 0.15},
            "game_phases": {
                "early": {
                    "day_range": "Days 1-4",
                    "description": "",
                    "universal_utility_items": [],
                    "economy_items": [],
                },
                "early_mid": {
                    "day_range": "Days 5-9",
                    "description": "",
                    "archetypes": [copy.deepcopy(_VALID_ARCHETYPE)],
                },
                "late": {
                    "day_range": "Days 10+",
                    "description": "",
                    "archetypes": [],
                },
            },
        }
        user_file.write_text(json.dumps(skeleton), encoding="utf-8")

        stub_invalidate.clear()
        client.delete(f"/api/builds/user/Karnok/{_VALID_ARCHETYPE['name']}")
        assert len(stub_invalidate) >= 1

    def test_invalidate_called_on_enable(self, client, tmp_data_dir, stub_invalidate):
        client.post("/api/builds/user/Karnok/enable")
        assert len(stub_invalidate) >= 1

    def test_invalidate_called_on_disable_with_file(self, client, tmp_data_dir, stub_invalidate):
        # Create a file first, then disable
        user_file = _user_file_path(tmp_data_dir, "karnok")
        user_file.parent.mkdir(parents=True, exist_ok=True)
        skeleton = {
            "schema_version": 1,
            "hero": "Karnok",
            "season": None,
            "last_updated": None,
            "notes": "",
            "enabled": True,
            "item_tier_list": {},
            "pivot_signals": {"signals": []},
            "scoring_weights": {"core": 0.50, "carry": 0.35, "support": 0.15},
            "game_phases": {
                "early": {"day_range": "Days 1-4", "description": "", "universal_utility_items": [], "economy_items": []},
                "early_mid": {"day_range": "Days 5-9", "description": "", "archetypes": []},
                "late": {"day_range": "Days 10+", "description": "", "archetypes": []},
            },
        }
        user_file.write_text(json.dumps(skeleton), encoding="utf-8")
        stub_invalidate.clear()
        client.post("/api/builds/user/Karnok/disable")
        assert len(stub_invalidate) >= 1

    def test_invalidate_not_called_on_disable_no_file(self, client, tmp_data_dir, stub_invalidate):
        """Disabling when no file is a no-op — should NOT call invalidate."""
        client.post("/api/builds/user/Karnok/disable")
        assert len(stub_invalidate) == 0


class TestUnknownHeroAllEndpoints:
    """All endpoints with an unknown hero should return 404."""

    def test_get_unknown_hero(self, client, tmp_data_dir):
        assert client.get("/api/builds/user/NotAHero").status_code == 404

    def test_put_unknown_hero(self, client, tmp_data_dir):
        resp = client.put(
            "/api/builds/user/NotAHero",
            data=json.dumps({"archetype": {**_VALID_ARCHETYPE, "phase": "early_mid"}}),
            content_type="application/json",
        )
        assert resp.status_code == 404

    def test_delete_unknown_hero(self, client, tmp_data_dir):
        assert client.delete("/api/builds/user/NotAHero/SomeName").status_code == 404

    def test_disable_unknown_hero(self, client, tmp_data_dir):
        assert client.post("/api/builds/user/NotAHero/disable").status_code == 404

    def test_enable_unknown_hero(self, client, tmp_data_dir):
        assert client.post("/api/builds/user/NotAHero/enable").status_code == 404


class TestProvenanceDetails:
    def test_provenance_user_file_enabled_true_after_enable(self, client, tmp_data_dir):
        client.post("/api/builds/user/Karnok/enable")
        resp = client.get("/api/builds/user/Karnok")
        assert resp.status_code == 200
        prov = resp.get_json()["provenance"]
        assert prov["user_file_exists"] is True
        assert prov["user_file_enabled"] is True

    def test_provenance_user_archetype_names_after_put(self, client, tmp_data_dir):
        _put_archetype(client, "Karnok", _VALID_ARCHETYPE)
        resp = client.get("/api/builds/user/Karnok")
        prov = resp.get_json()["provenance"]
        assert _VALID_ARCHETYPE["name"] in prov["user_archetype_names"]
