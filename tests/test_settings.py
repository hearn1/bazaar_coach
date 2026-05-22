import json

import settings


def _point_settings_at(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(settings, "_PATH", path)
    monkeypatch.setattr(settings, "_CACHE", None)
    return path


def test_load_migrates_and_merges_defaults(tmp_path, monkeypatch):
    path = _point_settings_at(tmp_path, monkeypatch)
    path.write_text(
        json.dumps(
            {
                "schema_version": 0,
                "overlay": {"geometry": {"width": 444}},
                "coach": {"web_port": 7777},
            }
        ),
        encoding="utf-8",
    )

    loaded = settings.load()

    assert loaded["schema_version"] == settings.SCHEMA_VERSION
    assert loaded["overlay"]["geometry"]["width"] == 444
    assert loaded["overlay"]["geometry"]["height"] == settings.DEFAULTS["overlay"]["geometry"]["height"]
    assert loaded["coach"]["web_port"] == 7777
    assert "user" in loaded


def test_settings_migration_renames_tracker_to_coach(tmp_path, monkeypatch):
    path = _point_settings_at(tmp_path, monkeypatch)
    path.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "tracker": {"web_port": 8888},
            }
        ),
        encoding="utf-8",
    )

    loaded = settings.load()

    assert loaded["schema_version"] == settings.SCHEMA_VERSION
    assert loaded["coach"]["web_port"] == 8888
    assert "tracker" not in loaded


def test_settings_migration_enables_real_update_source_for_old_default(tmp_path, monkeypatch):
    path = _point_settings_at(tmp_path, monkeypatch)
    path.write_text(
        json.dumps(
            {
                "schema_version": 4,
                "updates": {
                    "enabled": False,
                    "channel": "stable",
                    "manifest_url": None,
                    "github_repo": None,
                    "last_check": None,
                    "dismissed_version": None,
                },
            }
        ),
        encoding="utf-8",
    )

    loaded = settings.load()

    assert loaded["schema_version"] == settings.SCHEMA_VERSION
    assert loaded["updates"]["enabled"] is True
    assert loaded["updates"]["github_repo"] == "hearn1/bazaar_coach"
    assert loaded["updates"]["check_interval_hours"] == 24


def test_save_writes_current_schema_version(tmp_path, monkeypatch):
    path = _point_settings_at(tmp_path, monkeypatch)
    settings.load()
    settings.set("coach.web_port", 6060)

    assert settings.save()

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["schema_version"] == settings.SCHEMA_VERSION
    assert saved["coach"]["web_port"] == 6060


def test_settings_migration_adds_phase6_update_preferences(tmp_path, monkeypatch):
    path = _point_settings_at(tmp_path, monkeypatch)
    path.write_text(
        json.dumps(
            {
                "schema_version": 5,
                "updates": {
                    "enabled": True,
                    "github_repo": "hearn1/bazaar_coach",
                },
            }
        ),
        encoding="utf-8",
    )

    loaded = settings.load()

    assert loaded["schema_version"] == settings.SCHEMA_VERSION
    assert loaded["updates"]["download_on_check"] is False
    assert loaded["updates"]["install_on_quit"] is False
    assert loaded["updates"]["last_portable_apply"] is None


def test_corrupt_settings_are_backed_up(tmp_path, monkeypatch):
    path = _point_settings_at(tmp_path, monkeypatch)
    path.write_text("{not json", encoding="utf-8")

    loaded = settings.load()

    assert loaded["schema_version"] == settings.SCHEMA_VERSION
    assert not path.exists()
    assert list(tmp_path.glob("settings.json.corrupt-*"))
