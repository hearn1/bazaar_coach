import json
import hashlib
import threading
import urllib.error
import zipfile
from pathlib import Path

import app_paths
import settings
import update_checker
from version import APP_VERSION


def _reset_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("BAZAAR_COACH_SETTINGS_DIR", str(tmp_path))
    monkeypatch.setenv("BAZAAR_COACH_DATA_DIR", str(tmp_path))
    settings._CACHE = None
    settings._PATH = None


def _fake_program_files(tmp_path: Path, monkeypatch) -> Path:
    pf = tmp_path / "ProgramFiles"
    pf.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ProgramFiles", str(pf))
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    return pf


def _install_coach_exe(pf_root: Path, version: str) -> Path:
    install_dir = pf_root / "Bazaar Coach" / version
    install_dir.mkdir(parents=True, exist_ok=True)
    exe = install_dir / "BazaarCoach.exe"
    exe.write_bytes(b"coach")
    return exe


def test_file_manifest_reports_update_and_persists_last_check(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)
    manifest_path = tmp_path / "release.json"
    manifest_path.write_text(
        json.dumps({
            "stable": {
                "latest_version": "99.0.0",
                "release_notes_url": "https://example.invalid/releases/99.0.0",
                "download_url": "https://example.invalid/BazaarCoachSetup-99.0.0.exe",
                "sha256": "abc123",
                "asset_name": "BazaarCoachSetup-99.0.0.exe",
                "mandatory": True,
                "minimum_supported_version": "0.1.0",
                "compatibility_notes": "Portable and installer builds are compatible.",
            }
        }),
        encoding="utf-8",
    )

    result = update_checker.check_for_updates(
        manifest_url=f"file://{manifest_path}",
        channel="stable",
        persist=True,
    )

    assert result["ok"] is True
    assert result["update_available"] is True
    assert result["latest_version"] == "99.0.0"
    assert result["download_url"].endswith("BazaarCoachSetup-99.0.0.exe")
    assert result["sha256"] == "abc123"
    assert result["asset_name"] == "BazaarCoachSetup-99.0.0.exe"
    assert result["mandatory"] is True
    assert result["minimum_supported_version"] == "0.1.0"
    assert settings.get("updates.last_check.latest_version") == "99.0.0"


def test_default_update_check_uses_github_releases_without_placeholder_url(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)

    def fake_fetch(url, _timeout):
        assert url == "https://api.github.com/repos/hearn1/bazaar_coach/releases/latest"
        return {
            "tag_name": "v0.2.0-alpha.1",
            "html_url": "https://github.com/hearn1/bazaar_coach/releases/tag/v0.2.0-alpha.1",
            "assets": [],
        }

    monkeypatch.setattr(update_checker, "_load_manifest_from_url", fake_fetch)

    result = update_checker.check_for_updates(persist=False)

    assert result["ok"] is True
    assert result["enabled"] is True
    assert result["manifest_url"] == "https://api.github.com/repos/hearn1/bazaar_coach/releases/latest"
    assert "example.com" not in json.dumps(result)


def test_explicitly_disabled_update_check_does_not_fetch(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)
    settings.load()
    settings.set("updates.enabled", False)

    def unexpected_fetch(*_args, **_kwargs):
        raise AssertionError("disabled update check should not fetch")

    monkeypatch.setattr(update_checker, "_load_manifest_from_url", unexpected_fetch)

    result = update_checker.check_for_updates(persist=False)

    assert result["ok"] is True
    assert result["enabled"] is False
    assert result["manifest_url"] is None


def test_github_release_response_reports_update(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)
    settings.load()
    settings.set("updates.enabled", True)
    settings.set("updates.github_repo", "owner/repo")

    def fake_fetch(url, _timeout):
        assert url == "https://api.github.com/repos/owner/repo/releases/latest"
        return {
            "tag_name": "v99.0.0",
            "html_url": "https://github.com/owner/repo/releases/tag/v99.0.0",
            "body": "Portable build available.",
            "published_at": "2026-04-30T00:00:00Z",
            "assets": [
                {
                    "name": "BazaarCoach-Portable-99.0.0.zip",
                    "browser_download_url": "https://github.com/owner/repo/releases/download/v99.0.0/BazaarCoach-Portable-99.0.0.zip",
                },
                {
                    "name": "BazaarCoachSetup-99.0.0.exe",
                    "browser_download_url": "https://github.com/owner/repo/releases/download/v99.0.0/BazaarCoachSetup-99.0.0.exe",
                    "digest": "sha256:def456",
                },
            ],
        }

    monkeypatch.setattr(update_checker, "_load_manifest_from_url", fake_fetch)

    result = update_checker.check_for_updates(persist=False)

    assert result["ok"] is True
    assert result["update_available"] is True
    assert result["latest_version"] == "v99.0.0"
    assert result["release_notes_url"].endswith("/v99.0.0")
    assert result["asset_name"] == "BazaarCoachSetup-99.0.0.exe"
    assert result["download_url"].endswith("/BazaarCoachSetup-99.0.0.exe")
    assert result["sha256"] == "def456"


def test_normalize_manifest_prefers_channel_and_preserves_updater_fields():
    result = update_checker.normalize_manifest(
        {
            "channels": {
                "stable": {
                    "version": "3.0.0",
                    "download_url": "https://example.invalid/BazaarCoachSetup-3.0.0.exe",
                    "sha256": "123",
                    "asset_name": "BazaarCoachSetup-3.0.0.exe",
                    "mandatory": "yes",
                    "minimum_supported_version": "2.0.0",
                    "published_at": "2026-05-22T00:00:00Z",
                },
                "dev": {"version": "4.0.0"},
            }
        },
        "stable",
    )

    assert result["latest_version"] == "3.0.0"
    assert result["download_url"].endswith("BazaarCoachSetup-3.0.0.exe")
    assert result["sha256"] == "123"
    assert result["asset_name"] == "BazaarCoachSetup-3.0.0.exe"
    assert result["mandatory"] is True
    assert result["minimum_supported_version"] == "2.0.0"
    assert result["published_at"] == "2026-05-22T00:00:00Z"


def test_malformed_github_repo_returns_error_payload(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)
    settings.load()
    settings.set("updates.enabled", True)
    settings.set("updates.github_repo", "bad")

    def unexpected_fetch(*_args, **_kwargs):
        raise AssertionError("malformed github_repo should not fetch")

    monkeypatch.setattr(update_checker, "_load_manifest_from_url", unexpected_fetch)

    result = update_checker.check_for_updates(persist=False)

    assert result["ok"] is False
    assert result["enabled"] is True
    assert result["update_available"] is False
    assert "updates.github_repo" in result["error"]


def test_updates_status_route_returns_json_for_malformed_github_repo(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)
    settings.load()
    settings.set("updates.enabled", True)
    settings.set("updates.github_repo", "bad")

    from web.server import app

    response = app.test_client().get("/api/updates/status?force=1")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["ok"] is False
    assert payload["enabled"] is True
    assert "updates.github_repo" in payload["error"]


def test_updates_status_route_includes_phase_one_download_payload(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)
    settings.load()
    settings.set("updates.enabled", True)
    settings.set("updates.github_repo", "owner/repo")

    def fake_fetch(_url, _timeout):
        return {
            "tag_name": "v99.0.0",
            "html_url": "https://github.com/owner/repo/releases/tag/v99.0.0",
            "assets": [
                {
                    "name": "BazaarCoachSetup-99.0.0.exe",
                    "browser_download_url": "https://github.com/owner/repo/releases/download/v99.0.0/BazaarCoachSetup-99.0.0.exe",
                    "digest": "sha256:feedface",
                }
            ],
        }

    monkeypatch.setattr(update_checker, "_load_manifest_from_url", fake_fetch)

    from web.server import app

    response = app.test_client().get("/api/updates/status?force=1")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["update_available"] is True
    assert payload["download_url"].endswith("BazaarCoachSetup-99.0.0.exe")
    assert payload["asset_name"] == "BazaarCoachSetup-99.0.0.exe"
    assert payload["sha256"] == "feedface"


def test_network_failure_is_non_blocking(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)

    result = update_checker.check_for_updates(
        manifest_url="file://C:/definitely/missing/release.json",
        persist=False,
    )

    assert result["ok"] is False
    assert result["update_available"] is False
    assert result["error"]


def test_dismiss_update_records_version(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)

    result = update_checker.dismiss_update("2.0.0")

    assert result == {"ok": True, "dismissed_version": "2.0.0"}
    assert settings.get("updates.dismissed_version") == "2.0.0"


def test_download_update_installer_success(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)
    source = tmp_path / "BazaarCoachSetup-99.0.0.exe"
    content = b"installer bytes"
    source.write_bytes(content)
    expected_sha = hashlib.sha256(content).hexdigest()

    result = update_checker.download_update_installer(
        {
            "latest_version": "99.0.0",
            "download_url": source.as_uri(),
            "asset_name": source.name,
            "sha256": expected_sha,
        },
        persist=False,
    )

    assert result["ok"] is True
    assert result["status"] == "verified"
    assert result["sha256_verified"] is True
    assert result["file_size"] == len(content)
    assert (tmp_path / "updates" / source.name).read_bytes() == content


def test_download_update_installer_hash_mismatch(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)
    source = tmp_path / "BazaarCoachSetup-99.0.0.exe"
    source.write_bytes(b"installer bytes")

    result = update_checker.download_update_installer(
        {
            "latest_version": "99.0.0",
            "download_url": source.as_uri(),
            "asset_name": source.name,
            "sha256": "0" * 64,
        },
        persist=False,
    )

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert "sha256 mismatch" in result["error"]
    assert not (tmp_path / "updates" / source.name).exists()


def test_download_update_installer_network_failure(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)

    def fail_open(*_args, **_kwargs):
        raise urllib.error.URLError("network down")

    monkeypatch.setattr(update_checker, "_urlopen", fail_open)

    result = update_checker.download_update_installer(
        {
            "latest_version": "99.0.0",
            "download_url": "https://example.invalid/BazaarCoachSetup-99.0.0.exe",
            "asset_name": "BazaarCoachSetup-99.0.0.exe",
        },
        persist=False,
    )

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert "network down" in result["error"]


def test_download_update_installer_missing_download_url(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)

    result = update_checker.download_update_installer({"latest_version": "99.0.0"}, persist=False)

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["error"] == "missing download_url"


def test_updates_download_route_uses_payload(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)
    calls = []

    def fake_download(manifest=None, persist=True, **_kwargs):
        calls.append((manifest, persist))
        return {
            "ok": True,
            "status": "verified",
            "download_url": manifest["download_url"],
            "latest_version": manifest["latest_version"],
            "asset_name": manifest["asset_name"],
            "file_path": str(tmp_path / "updates" / manifest["asset_name"]),
            "file_size": 12,
            "sha256": manifest["sha256"],
            "sha256_verified": True,
            "error": None,
        }

    monkeypatch.setattr(update_checker, "download_update", fake_download)

    from web.server import app

    response = app.test_client().post(
        "/api/updates/download",
        json={
            "latest_version": "99.0.0",
            "download_url": "https://example.invalid/BazaarCoachSetup-99.0.0.exe",
            "asset_name": "BazaarCoachSetup-99.0.0.exe",
            "sha256": "feedface",
        },
    )
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["status"] == "verified"
    assert calls[0][0]["download_url"].endswith("BazaarCoachSetup-99.0.0.exe")
    assert calls[0][1] is True


def test_updates_download_route_missing_download_url(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)

    from web.server import app

    response = app.test_client().post("/api/updates/download", json={})
    payload = response.get_json()

    assert response.status_code == 400
    assert payload["ok"] is False
    assert payload["error"] == "missing download_url"


def test_reveal_downloaded_installer_success(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)
    installer = tmp_path / "updates" / "BazaarCoachSetup-99.0.0.exe"
    installer.parent.mkdir()
    installer.write_bytes(b"installer bytes")
    revealed = []

    def fake_reveal(path):
        revealed.append(path)

    monkeypatch.setattr(update_checker, "_reveal_in_file_manager", fake_reveal)
    settings.load()
    settings.set("updates.last_download", {
        "ok": True,
        "status": "verified",
        "latest_version": "99.0.0",
        "asset_name": installer.name,
        "file_path": str(installer),
    })

    result = update_checker.reveal_downloaded_installer()

    assert result["ok"] is True
    assert result["status"] == "revealed"
    assert result["action"] == "reveal"
    assert result["file_path"] == str(installer.resolve())
    assert revealed == [installer.resolve()]


def test_reveal_downloaded_installer_missing_last_download(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)

    result = update_checker.reveal_downloaded_installer()

    assert result["ok"] is False
    assert result["error"] == "missing last_download"


def test_reveal_downloaded_installer_rejects_failed_or_unverified_download(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)
    installer = tmp_path / "updates" / "BazaarCoachSetup-99.0.0.exe"
    installer.parent.mkdir()
    installer.write_bytes(b"installer bytes")
    settings.load()
    settings.set("updates.last_download", {
        "ok": False,
        "status": "failed",
        "file_path": str(installer),
    })

    result = update_checker.reveal_downloaded_installer()

    assert result["ok"] is False
    assert result["error"] == "last_download is not verified or downloaded"


def test_reveal_downloaded_installer_rejects_path_outside_updates_dir(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)
    installer = tmp_path / "BazaarCoachSetup-99.0.0.exe"
    installer.write_bytes(b"installer bytes")
    settings.load()
    settings.set("updates.last_download", {
        "ok": True,
        "status": "verified",
        "file_path": str(installer),
    })

    result = update_checker.reveal_downloaded_installer()

    assert result["ok"] is False
    assert result["error"] == "installer path is outside updates directory"


def test_updates_reveal_installer_route_payload_and_status(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)
    installer = tmp_path / "updates" / "BazaarCoachSetup-99.0.0.exe"
    installer.parent.mkdir()
    installer.write_bytes(b"installer bytes")
    revealed = []

    def fake_reveal(path):
        revealed.append(path)

    monkeypatch.setattr(update_checker, "_reveal_in_file_manager", fake_reveal)
    settings.load()
    settings.set("updates.last_download", {
        "ok": True,
        "status": "downloaded",
        "latest_version": "99.0.0",
        "asset_name": installer.name,
        "file_path": str(installer),
    })

    from web.server import app

    response = app.test_client().post("/api/updates/reveal-installer", json={})
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["status"] == "revealed"
    assert payload["action"] == "reveal"
    assert payload["asset_name"] == installer.name
    assert revealed == [installer.resolve()]


def test_updates_reveal_installer_route_reports_missing_download(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)

    from web.server import app

    response = app.test_client().post("/api/updates/reveal-installer", json={})
    payload = response.get_json()

    assert response.status_code == 400
    assert payload["ok"] is False
    assert payload["action"] == "reveal"
    assert payload["error"] == "missing last_download"


def test_updates_status_route_includes_last_download(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)
    settings.load()
    settings.set("updates.last_check", {
        "ok": True,
        "enabled": True,
        "checked_at": "2999-01-01T00:00:00+00:00",
        "update_available": True,
        "latest_version": "99.0.0",
    })
    settings.set("updates.last_download", {
        "ok": True,
        "status": "downloaded",
        "latest_version": "99.0.0",
        "file_path": str(tmp_path / "updates" / "BazaarCoachSetup-99.0.0.exe"),
    })

    from web.server import app

    response = app.test_client().get("/api/updates/status")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["last_download"]["status"] == "downloaded"
    assert payload["last_download"]["latest_version"] == "99.0.0"


def _verified_last_download(tmp_path, version="99.0.0"):
    installer = tmp_path / "updates" / f"BazaarCoachSetup-{version}.exe"
    installer.parent.mkdir(parents=True, exist_ok=True)
    installer.write_bytes(b"installer bytes")
    return {
        "ok": True,
        "status": "verified",
        "latest_version": version,
        "asset_name": installer.name,
        "file_path": str(installer),
    }


def test_launch_downloaded_installer_success(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)
    monkeypatch.setattr(app_paths, "is_packaged", lambda: True)
    launched = []
    shutdowns = []
    watchers = []

    class _FakeProc:
        pid = 4242

    def fake_spawn(path, *, silent=False):
        launched.append((path, silent))
        return _FakeProc()

    def fake_shutdown():
        shutdowns.append(True)

    monkeypatch.setattr(update_checker, "_spawn_installer", fake_spawn)
    monkeypatch.setattr(update_checker, "spawn_post_install_relaunch_watcher", lambda pid, ver: watchers.append((pid, ver)))
    settings.load()
    settings.set("updates.last_download", _verified_last_download(tmp_path))

    result = update_checker.launch_downloaded_installer(
        allow_launch=True,
        shutdown_first=True,
        shutdown_callback=fake_shutdown,
        persist=True,
    )

    assert result["ok"] is True
    assert result["status"] == "launched"
    assert result["action"] == "install"
    assert result["target_version"] == "99.0.0"
    assert result["previous_version"] == APP_VERSION
    assert result["launched_at"]
    assert settings.get("updates.last_install.status") == "launched"
    threading.Event().wait(0.05)
    assert shutdowns == [True]
    assert len(launched) == 1
    assert watchers == [(4242, "99.0.0")]


def test_launch_downloaded_installer_without_shutdown(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)
    launched = []

    class _FakeProc:
        pid = 99

    monkeypatch.setattr(update_checker, "_spawn_installer", lambda path, **_: (launched.append(path) or _FakeProc()))
    monkeypatch.setattr(update_checker, "spawn_post_install_relaunch_watcher", lambda *_a, **_k: None)
    settings.load()
    settings.set("updates.last_download", _verified_last_download(tmp_path))

    result = update_checker.launch_downloaded_installer(
        allow_launch=True,
        shutdown_first=False,
        persist=False,
    )

    assert result["ok"] is True
    assert result["status"] == "launched"
    assert len(launched) == 1


def test_launch_downloaded_installer_refuses_dev_checkout(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)
    monkeypatch.setattr(app_paths, "is_packaged", lambda: False)
    monkeypatch.delenv(update_checker.INSTALL_LAUNCH_ENV, raising=False)
    settings.load()
    settings.set("updates.last_download", _verified_last_download(tmp_path))

    result = update_checker.launch_downloaded_installer(persist=False)

    assert result["ok"] is False
    assert "packaged build" in result["error"]


def test_launch_downloaded_installer_missing_last_download(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)

    result = update_checker.launch_downloaded_installer(allow_launch=True, persist=False)

    assert result["ok"] is False
    assert result["error"] == "missing last_download"


def test_launch_downloaded_installer_rejects_failed_or_unverified_download(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)
    settings.load()
    settings.set("updates.last_download", {
        "ok": False,
        "status": "failed",
        "file_path": str(tmp_path / "updates" / "bad.exe"),
    })

    result = update_checker.launch_downloaded_installer(allow_launch=True, persist=False)

    assert result["ok"] is False
    assert result["error"] == "last_download is not verified or downloaded"


def test_launch_downloaded_installer_rejects_path_outside_updates_dir(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)
    installer = tmp_path / "BazaarCoachSetup-99.0.0.exe"
    installer.write_bytes(b"x")
    settings.load()
    settings.set("updates.last_download", {
        "ok": True,
        "status": "verified",
        "file_path": str(installer),
        "latest_version": "99.0.0",
    })

    result = update_checker.launch_downloaded_installer(allow_launch=True, persist=False)

    assert result["ok"] is False
    assert result["error"] == "installer path is outside updates directory"


def test_updates_install_route_success(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)
    monkeypatch.setattr(app_paths, "is_packaged", lambda: True)
    calls = []

    def fake_launch(**kwargs):
        calls.append(kwargs)
        return {
            "ok": True,
            "status": "launched",
            "action": "install",
            "target_version": "99.0.0",
            "file_path": str(tmp_path / "updates" / "BazaarCoachSetup-99.0.0.exe"),
            "error": None,
        }

    monkeypatch.setattr(update_checker, "launch_downloaded_installer", fake_launch)
    settings.load()
    settings.set("updates.last_download", _verified_last_download(tmp_path))

    from web.server import app, set_shutdown_callback

    set_shutdown_callback(lambda: None)
    response = app.test_client().post("/api/updates/install", json={"shutdown_first": True})
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["status"] == "launched"
    assert payload["action"] == "install"
    assert calls[0]["shutdown_first"] is True
    assert calls[0]["shutdown_callback"] is not None


def test_updates_install_route_reports_validation_failure(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)

    from web.server import app

    response = app.test_client().post("/api/updates/install", json={})
    payload = response.get_json()

    assert response.status_code == 400
    assert payload["ok"] is False
    assert payload["action"] == "install"
    assert payload["error"] == "missing last_download"


def test_updates_status_route_includes_last_install(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)
    settings.load()
    settings.set("updates.last_install", {
        "ok": True,
        "status": "verified",
        "target_version": "99.0.0",
        "verified_at": "2999-01-01T00:00:00+00:00",
    })

    from web.server import app

    response = app.test_client().get("/api/updates/status")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["last_install"]["status"] == "verified"
    assert payload["install_launch_available"] is False


def test_verify_pending_install_marks_verified(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)
    settings.load()
    settings.set("updates.last_download", _verified_last_download(tmp_path, version=APP_VERSION))
    settings.set("updates.last_install", {
        "ok": True,
        "status": "launched",
        "target_version": APP_VERSION,
        "launched_at": "2026-05-22T00:00:00+00:00",
    })

    result = update_checker.verify_pending_install_on_startup(persist=True)

    assert result["status"] == "verified"
    assert result["verified_at"]
    assert settings.get("updates.last_install.status") == "verified"
    assert settings.get("updates.last_download.status") == "installed"


def test_verify_pending_install_marks_stale(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)
    settings.load()
    settings.set("updates.last_download", _verified_last_download(tmp_path))
    settings.set("updates.last_install", {
        "ok": True,
        "status": "launched",
        "target_version": "99.0.0",
        "launched_at": "2020-01-01T00:00:00+00:00",
    })

    result = update_checker.verify_pending_install_on_startup(persist=True)

    assert result["status"] == "stale"
    assert "99.0.0" in result["error"]
    assert APP_VERSION in result["error"]


def test_version_at_least_matches_current_and_newer():
    assert update_checker.version_at_least("2.0.0", "2.0.0") is True
    assert update_checker.version_at_least("2.1.0", "2.0.0") is True
    assert update_checker.version_at_least("1.0.0", "2.0.0") is False


def test_resolve_installed_coach_exe_exact_match(tmp_path, monkeypatch):
    pf = _fake_program_files(tmp_path, monkeypatch)
    exact = _install_coach_exe(pf, "v2.0.0")
    _install_coach_exe(pf, "1.0.0")

    resolved = update_checker.resolve_installed_coach_exe("2.0.0")

    assert resolved == exact.resolve()


def test_resolve_installed_coach_exe_fallback_highest_compatible(tmp_path, monkeypatch):
    pf = _fake_program_files(tmp_path, monkeypatch)
    _install_coach_exe(pf, "1.5.0")
    newer = _install_coach_exe(pf, "2.5.0")

    resolved = update_checker.resolve_installed_coach_exe("2.0.0")

    assert resolved == newer.resolve()


def test_resolve_installed_coach_exe_missing(tmp_path, monkeypatch):
    _fake_program_files(tmp_path, monkeypatch)

    assert update_checker.resolve_installed_coach_exe("9.9.9") is None


def test_relaunch_installed_coach_success(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)
    pf = _fake_program_files(tmp_path, monkeypatch)
    exe = _install_coach_exe(pf, "2.0.0")
    launched = []

    monkeypatch.setattr(app_paths, "is_packaged", lambda: True)
    monkeypatch.setattr(update_checker, "_detached_launch_exe", lambda path: launched.append(path))

    result = update_checker.relaunch_installed_coach("2.0.0", allow_launch=True, persist=True)

    assert result["ok"] is True
    assert result["relaunch_status"] == "launched"
    assert result["installed_exe_path"] == str(exe.resolve())
    assert launched == [exe.resolve()]
    assert settings.get("updates.last_install.relaunch_status") == "launched"


def test_relaunch_installed_coach_refuses_dev_checkout(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)
    pf = _fake_program_files(tmp_path, monkeypatch)
    _install_coach_exe(pf, "2.0.0")
    monkeypatch.setattr(app_paths, "is_packaged", lambda: False)
    monkeypatch.delenv(update_checker.INSTALL_LAUNCH_ENV, raising=False)

    result = update_checker.relaunch_installed_coach("2.0.0", persist=False)

    assert result["ok"] is False
    assert "packaged build" in result["relaunch_error"]


def test_relaunch_installed_coach_rejects_outside_install_dirs(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)
    outside = tmp_path / "evil" / "BazaarCoach.exe"
    outside.parent.mkdir(parents=True)
    outside.write_bytes(b"x")
    monkeypatch.setattr(app_paths, "is_packaged", lambda: True)
    monkeypatch.setattr(
        update_checker,
        "resolve_installed_coach_exe",
        lambda _target: outside,
    )

    result = update_checker.relaunch_installed_coach("2.0.0", allow_launch=True, persist=False)

    assert result["ok"] is False
    assert "outside Bazaar Coach" in result["relaunch_error"]


def test_spawn_installer_silent_argv(tmp_path, monkeypatch):
    captured = []

    class _FakePopen:
        pid = 1

        def __init__(self, args, **kwargs):
            captured.append((list(args), kwargs))

    monkeypatch.setattr(update_checker.subprocess, "Popen", _FakePopen)

    update_checker._spawn_installer(Path("C:/tmp/setup.exe"), silent=True)

    assert captured[0][0] == [
        "C:\\tmp\\setup.exe",
        "/VERYSILENT",
        "/CLOSEAPPLICATIONS",
        "/SUPPRESSMSGBOXES",
    ]


def test_spawn_installer_interactive_argv(tmp_path, monkeypatch):
    captured = []

    class _FakePopen:
        pid = 1

        def __init__(self, args, **kwargs):
            captured.append(list(args))

    monkeypatch.setattr(update_checker.subprocess, "Popen", _FakePopen)

    update_checker._spawn_installer(Path("C:/tmp/setup.exe"), silent=False)

    assert captured[0] == ["C:\\tmp\\setup.exe"]


def test_watch_install_and_relaunch_after_exe_appears(tmp_path, monkeypatch):
    pf = _fake_program_files(tmp_path, monkeypatch)

    def fake_wait_pid(_pid, _timeout):
        return True

    def fake_wait_exe(target, _timeout):
        return _install_coach_exe(pf, target)

    monkeypatch.setattr(update_checker, "_wait_for_installer_exit", fake_wait_pid)
    monkeypatch.setattr(update_checker, "_wait_for_installed_exe", fake_wait_exe)
    monkeypatch.setattr(
        update_checker,
        "relaunch_installed_coach",
        lambda target, **kwargs: {"ok": True, "target_version": target},
    )

    result = update_checker.watch_install_and_relaunch("2.0.0", installer_pid=123, timeout_sec=5)

    assert result["ok"] is True


def test_spawn_post_install_relaunch_watcher_packaged_only(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)
    spawned = []

    class _FakePopen:
        def __init__(self, cmd, **kwargs):
            spawned.append(cmd)

    monkeypatch.setattr(update_checker.subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(app_paths, "is_packaged", lambda: False)
    settings.load()
    settings.set("updates.relaunch_after_install", True)
    update_checker.spawn_post_install_relaunch_watcher(42, "2.0.0")
    assert spawned == []

    monkeypatch.setattr(app_paths, "is_packaged", lambda: True)
    update_checker.spawn_post_install_relaunch_watcher(42, "2.0.0")
    assert spawned
    assert spawned[0][-2:] == ["2.0.0", "42"]


def test_enrich_update_handoff_state_includes_relaunch_fields(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)
    pf = _fake_program_files(tmp_path, monkeypatch)
    exe = _install_coach_exe(pf, "3.0.0")
    monkeypatch.setattr(app_paths, "is_packaged", lambda: True)
    settings.load()
    settings.set("updates.last_install", {"target_version": "3.0.0", "status": "verified"})

    enriched = update_checker.enrich_update_handoff_state({})

    assert enriched["installed_exe_path"] == str(exe.resolve())
    assert enriched["relaunch_available"] is True


def test_updates_relaunch_route_success(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)
    monkeypatch.setattr(
        update_checker,
        "relaunch_installed_coach",
        lambda target, **kwargs: {
            "ok": True,
            "action": "relaunch",
            "target_version": target,
            "relaunch_status": "launched",
        },
    )

    from web.server import app

    response = app.test_client().post("/api/updates/relaunch", json={"target_version": "2.0.0"})
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["relaunch_status"] == "launched"


def test_updates_relaunch_route_missing_target(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)

    from web.server import app

    response = app.test_client().post("/api/updates/relaunch", json={})
    payload = response.get_json()

    assert response.status_code == 400
    assert payload["ok"] is False


def test_updates_status_route_includes_relaunch_fields(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)
    pf = _fake_program_files(tmp_path, monkeypatch)
    _install_coach_exe(pf, "5.0.0")
    monkeypatch.setattr(app_paths, "is_packaged", lambda: True)
    settings.load()
    settings.set("updates.last_check", {
        "ok": True,
        "enabled": True,
        "checked_at": "2999-01-01T00:00:00+00:00",
        "update_available": False,
        "latest_version": "5.0.0",
    })
    settings.set("updates.last_install", {"target_version": "5.0.0", "status": "verified"})

    from web.server import app

    response = app.test_client().get("/api/updates/status")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["relaunch_available"] is True
    assert payload["installed_exe_path"].endswith("BazaarCoach.exe")


def test_updates_install_route_persists_install_silent(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)

    def fake_launch(**kwargs):
        return {"ok": True, "status": "launched", "silent": settings.get("updates.install_silent")}

    monkeypatch.setattr(update_checker, "launch_downloaded_installer", fake_launch)

    from web.server import app

    response = app.test_client().post(
        "/api/updates/install",
        json={"install_silent": True, "shutdown_first": False},
    )
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert settings.get("updates.install_silent") is True


def test_launch_downloaded_installer_records_silent_flag(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)
    monkeypatch.setattr(app_paths, "is_packaged", lambda: True)

    class _FakeProc:
        pid = 7

    monkeypatch.setattr(update_checker, "_spawn_installer", lambda *_a, **_k: _FakeProc())
    monkeypatch.setattr(update_checker, "spawn_post_install_relaunch_watcher", lambda *_a, **_k: None)
    settings.load()
    settings.set("updates.install_silent", True)
    settings.set("updates.last_download", _verified_last_download(tmp_path))

    result = update_checker.launch_downloaded_installer(
        allow_launch=True,
        shutdown_first=False,
        persist=True,
    )

    assert result["silent"] is True
    assert settings.get("updates.last_install.silent") is True


def test_normalize_manifest_includes_portable_asset_fields():
    result = update_checker.normalize_manifest(
        {
            "tag_name": "v2.0.0",
            "assets": [
                {
                    "name": "BazaarCoach-Portable-2.0.0.zip",
                    "browser_download_url": "https://example.invalid/portable.zip",
                    "digest": "sha256:portable123",
                },
                {
                    "name": "BazaarCoachSetup-2.0.0.exe",
                    "browser_download_url": "https://example.invalid/setup.exe",
                    "digest": "sha256:setup456",
                },
            ],
        },
        "stable",
    )

    assert result["portable_download_url"].endswith("portable.zip")
    assert result["portable_asset_name"] == "BazaarCoach-Portable-2.0.0.zip"
    assert result["portable_sha256"] == "portable123"
    assert result["installer_download_url"].endswith("setup.exe")


def test_resolve_download_manifest_prefers_portable_when_requested():
    manifest = {
        "latest_version": "2.0.0",
        "installer_download_url": "https://example.invalid/setup.exe",
        "installer_asset_name": "BazaarCoachSetup-2.0.0.exe",
        "portable_download_url": "https://example.invalid/portable.zip",
        "portable_asset_name": "BazaarCoach-Portable-2.0.0.zip",
    }
    kind, resolved = update_checker.resolve_download_manifest(manifest, prefer_portable=True)
    assert kind == update_checker.UPDATE_ASSET_PORTABLE
    assert resolved["download_url"].endswith("portable.zip")


def test_upgrade_blocked_reason_when_below_minimum():
    reason = update_checker.upgrade_blocked_reason({
        "minimum_supported_version": "99.0.0",
    })
    assert reason is not None
    assert "99.0.0" in reason


def test_download_update_blocks_when_below_minimum(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)
    result = update_checker.download_update(
        {
            "latest_version": "9.0.0",
            "download_url": "https://example.invalid/setup.exe",
            "minimum_supported_version": "99.0.0",
        },
        persist=True,
    )
    assert result["ok"] is False
    assert "requires" in (result.get("error") or "").lower()


def test_extract_portable_staging_rejects_unsafe_zip(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)
    updates_dir = tmp_path / "updates"
    updates_dir.mkdir(parents=True, exist_ok=True)
    zip_path = updates_dir / "BazaarCoach-Portable-9.0.0.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("../evil.txt", "nope")
    try:
        update_checker.extract_portable_staging(zip_path, "9.0.0")
        raise AssertionError("expected unsafe zip to fail")
    except ValueError as exc:
        assert "unsafe" in str(exc).lower()


def test_extract_portable_staging_finds_nested_exe(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)
    updates_dir = tmp_path / "updates"
    updates_dir.mkdir(parents=True, exist_ok=True)
    zip_path = updates_dir / "BazaarCoach-Portable-9.0.0.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("BazaarCoach/BazaarCoach.exe", b"coach")
    staging = update_checker.extract_portable_staging(zip_path, "9.0.0")
    assert (staging / "BazaarCoach" / "BazaarCoach.exe").is_file()


def test_refresh_stable_start_menu_shortcut_uses_powershell(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)
    pf = _fake_program_files(tmp_path, monkeypatch)
    exe = _install_coach_exe(pf, "3.1.0")
    shortcut = tmp_path / "Start Menu" / "Programs" / update_checker.STABLE_START_MENU_SHORTCUT_NAME
    monkeypatch.setenv("APPDATA", str(tmp_path))
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        class _Done:
            returncode = 0
            stdout = ""
            stderr = ""
        return _Done()

    monkeypatch.setattr(update_checker.subprocess, "run", fake_run)
    result = update_checker.refresh_stable_start_menu_shortcut("3.1.0")
    assert result["ok"] is True
    ps_command = calls[0][-1]
    assert str(exe) in ps_command
    assert "Bazaar Coach.lnk" in ps_command
    assert "Microsoft" in ps_command


def test_portable_apply_ready_when_staging_present(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)
    staging = update_checker._portable_staging_root("4.0.0")
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "BazaarCoach.exe").write_bytes(b"coach")
    last_download = {
        "ok": True,
        "asset_kind": "portable",
        "latest_version": "4.0.0",
        "staging_dir": str(staging),
    }
    assert update_checker._portable_apply_ready(last_download) is True


def test_apply_portable_update_spawns_swap_watcher(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)
    portable_root = tmp_path / "portable"
    portable_root.mkdir()
    (portable_root / "BazaarCoach.exe").write_bytes(b"old")
    staging = update_checker._portable_staging_root("5.0.0")
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "BazaarCoach.exe").write_bytes(b"new")
    monkeypatch.setattr(app_paths, "is_packaged", lambda: True)
    monkeypatch.setattr(app_paths, "is_portable_runtime", lambda: True)
    monkeypatch.setattr(app_paths, "portable_root", lambda: portable_root)
    monkeypatch.setattr(update_checker, "_install_launch_allowed", lambda **_: True)
    spawned = []
    monkeypatch.setattr(
        update_checker,
        "spawn_portable_swap_watcher",
        lambda *args, **kwargs: spawned.append(args),
    )
    shutdown_called = []
    result = update_checker.apply_portable_update(
        {
            "ok": True,
            "asset_kind": "portable",
            "latest_version": "5.0.0",
            "staging_dir": str(staging),
        },
        allow_launch=True,
        shutdown_first=True,
        shutdown_callback=lambda: shutdown_called.append(True),
        persist=False,
    )
    assert result["ok"] is True
    import time
    time.sleep(0.1)
    assert shutdown_called == [True]
    assert spawned


def test_maybe_auto_download_after_check_respects_setting(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)
    settings.load()
    settings.set("updates.download_on_check", False)
    called = []
    monkeypatch.setattr(
        update_checker,
        "download_update",
        lambda *a, **k: called.append(True) or {"ok": True},
    )
    assert update_checker.maybe_auto_download_after_check({
        "ok": True,
        "update_available": True,
        "latest_version": "9.0.0",
        "download_url": "https://example.invalid/x.exe",
    }) is None
    assert called == []


def test_run_pending_update_on_quit_launches_installer(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)
    settings.load()
    settings.set("updates.install_on_quit", True)
    monkeypatch.setattr(app_paths, "is_packaged", lambda: True)
    monkeypatch.setattr(app_paths, "is_portable_runtime", lambda: False)
    monkeypatch.setattr(update_checker, "_install_launch_allowed", lambda **_: True)
    launched = []
    monkeypatch.setattr(
        update_checker,
        "_spawn_installer",
        lambda *_a, **_k: launched.append(True) or type("P", (), {"pid": 9})(),
    )
    monkeypatch.setattr(update_checker, "spawn_post_install_relaunch_watcher", lambda *_a, **_k: None)
    settings.set("updates.last_download", _verified_last_download(tmp_path))
    result = update_checker.run_pending_update_on_quit()
    assert result["ok"] is True
    assert launched


def test_updates_apply_portable_route(tmp_path, monkeypatch):
    _reset_settings(tmp_path, monkeypatch)
    monkeypatch.setattr(
        update_checker,
        "apply_portable_update",
        lambda **kwargs: {"ok": True, "status": "launched"},
    )
    from web.server import app

    response = app.test_client().post(
        "/api/updates/apply-portable",
        json={"shutdown_first": True},
    )
    assert response.status_code == 200
    assert response.get_json()["ok"] is True
