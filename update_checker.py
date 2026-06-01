"""Non-blocking release update checks for Bazaar Coach."""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlparse
from typing import Any, Callable, Optional

import app_paths
import settings
from version import APP_VERSION

DEFAULT_RELEASE_MANIFEST_URL = None
DEFAULT_GITHUB_REPO = "hearn1/bazaar_coach"
INSTALLER_ASSET_RE = re.compile(r"^BazaarCoachSetup-.*\.exe$", re.IGNORECASE)
PORTABLE_ASSET_RE = re.compile(r"^BazaarCoach-Portable-.*\.zip$", re.IGNORECASE)
STABLE_START_MENU_SHORTCUT_NAME = "Bazaar Coach.lnk"
CHANNELS = {"stable", "beta", "dev"}
UPDATE_ASSET_INSTALLER = "installer"
UPDATE_ASSET_PORTABLE = "portable"
DEFAULT_DOWNLOAD_TIMEOUT = 30.0
DEFAULT_DOWNLOAD_MAX_BYTES = 256 * 1024 * 1024
DOWNLOAD_HANDOFF_STATUSES = {"verified", "downloaded"}
INSTALL_PENDING_STATUSES = {"launched"}
INSTALL_STALE_DAYS = 7
INSTALL_LAUNCH_ENV = "BAZAAR_COACH_ALLOW_INSTALL_LAUNCH"
COACH_EXE_NAME = "BazaarCoach.exe"
BAZAAR_COACH_INSTALL_DIR = "Bazaar Coach"
WATCH_INSTALL_RELAUNCH_TIMEOUT_SEC = 30 * 60
WATCH_INSTALL_POLL_INTERVAL_SEC = 2.0
_WIN_STILL_ACTIVE = 259
_WIN_DETACHED_PROCESS = 0x00000008
_WIN_CREATE_NEW_PROCESS_GROUP = 0x00000200


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _parse_version(value: str) -> tuple:
    raw = value.strip().lower()
    if raw.startswith("v"):
        raw = raw[1:]
    main, _, suffix = raw.partition("-")
    parts: list[Any] = []
    for piece in main.split("."):
        try:
            parts.append(int(piece))
        except ValueError:
            parts.append(piece)
    if suffix:
        parts.append(suffix)
    return tuple(parts)


def is_newer_version(latest: str, current: str = APP_VERSION) -> bool:
    try:
        return _parse_version(latest) > _parse_version(current)
    except Exception:
        return latest.strip() != current.strip()


def version_at_least(current: str, target: str) -> bool:
    """True when current version is greater than or equal to target."""
    return not is_newer_version(target, current)


def _load_manifest_from_url(url: str, timeout: float) -> dict:
    if url.startswith("file://"):
        return json.loads(Path(url[7:]).read_text(encoding="utf-8"))
    request = urllib.request.Request(url, headers={"User-Agent": f"BazaarCoach/{APP_VERSION}"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return json.loads(response.read().decode(charset))


def _urlopen(request: urllib.request.Request, timeout: float):
    return urllib.request.urlopen(request, timeout=timeout)


def _github_latest_release_url(repo: str) -> str:
    repo = str(repo or "").strip().strip("/")
    if not repo or "/" not in repo:
        raise ValueError("updates.github_repo must be in owner/repo form")
    return f"https://api.github.com/repos/{repo}/releases/latest"


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "required", "mandatory"}
    return False


def _normalize_sha256(value: Any) -> Optional[str]:
    if not value:
        return None
    text = str(value).strip()
    if text.lower().startswith("sha256:"):
        text = text.split(":", 1)[1].strip()
    return text or None


def _safe_asset_name(asset_name: Any, download_url: str, latest_version: Any = None) -> str:
    name = str(asset_name or "").strip()
    if not name:
        path_name = Path(unquote(urlparse(download_url).path)).name
        name = path_name.strip()
    if not name:
        version = str(latest_version or "update").strip() or "update"
        name = f"BazaarCoachSetup-{version}.exe"
    name = Path(name).name
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" .")
    return name or "BazaarCoachSetup-update.exe"


def _download_result_base(manifest: dict) -> dict:
    sha256 = _normalize_sha256(manifest.get("sha256"))
    return {
        "ok": False,
        "status": "failed",
        "download_url": manifest.get("download_url"),
        "latest_version": manifest.get("latest_version"),
        "asset_name": manifest.get("asset_name"),
        "file_path": None,
        "file_size": 0,
        "sha256": sha256,
        "sha256_verified": False,
        "error": None,
    }


def _save_download_result(result: dict, persist: bool) -> dict:
    if persist:
        settings.set("updates.last_download", result)
        settings.save()
    return result


def _save_install_result(result: dict, persist: bool) -> dict:
    if persist:
        settings.set("updates.last_install", result)
        settings.save()
    return result


def _save_portable_apply_result(result: dict, persist: bool) -> dict:
    if persist:
        settings.set("updates.last_portable_apply", result)
        settings.save()
    return result


def preferred_update_asset_kind(*, prefer_portable: Optional[bool] = None) -> str:
    if prefer_portable is True:
        return UPDATE_ASSET_PORTABLE
    if prefer_portable is False:
        return UPDATE_ASSET_INSTALLER
    if app_paths.is_portable_runtime():
        return UPDATE_ASSET_PORTABLE
    return UPDATE_ASSET_INSTALLER


def upgrade_blocked_reason(manifest: Optional[dict]) -> Optional[str]:
    if not isinstance(manifest, dict):
        return None
    minimum = str(manifest.get("minimum_supported_version") or "").strip()
    if not minimum:
        return None
    if is_newer_version(minimum, APP_VERSION):
        return (
            f"This update requires Bazaar Coach {minimum} or newer "
            f"(current: {APP_VERSION})."
        )
    return None


def _install_launch_allowed(*, allow_launch: bool = False) -> bool:
    if allow_launch:
        return True
    if os.environ.get(INSTALL_LAUNCH_ENV, "").strip().lower() in {"1", "true", "yes"}:
        return True
    return app_paths.is_packaged()


def relaunch_after_install_enabled() -> bool:
    """Default on for packaged builds, off for dev checkouts."""
    settings.load()
    configured = settings.get("updates.relaunch_after_install")
    if configured is None:
        return app_paths.is_packaged()
    return bool(configured)


def _program_files_roots() -> list[Path]:
    roots: list[Path] = []
    seen: set[str] = set()
    for env_name in ("ProgramFiles", "ProgramFiles(x86)"):
        raw = os.environ.get(env_name)
        if not raw:
            continue
        key = raw.casefold()
        if key in seen:
            continue
        seen.add(key)
        roots.append(Path(raw))
    return roots


def _bazaar_coach_install_roots() -> list[Path]:
    return [root / BAZAAR_COACH_INSTALL_DIR for root in _program_files_roots()]


def _path_is_under_bazaar_coach_install(path: Path) -> bool:
    resolved = path.resolve(strict=False)
    for install_root in _bazaar_coach_install_roots():
        if _path_is_inside(resolved, install_root):
            return True
    return False


def resolve_installed_coach_exe(target_version: str) -> Optional[Path]:
    """Locate BazaarCoach.exe for target_version under Program Files.

    Exact folder match is preferred. If missing, returns the highest versioned
    install directory whose version is >= target_version.
    """
    target = str(target_version or "").strip()
    if not target:
        return None

    target_key = _parse_version(target)
    exact: Optional[Path] = None
    eligible: list[tuple[tuple, Path]] = []

    for install_root in _bazaar_coach_install_roots():
        if not install_root.is_dir():
            continue
        for entry in install_root.iterdir():
            if not entry.is_dir():
                continue
            exe = entry / COACH_EXE_NAME
            if not exe.is_file():
                continue
            resolved = exe.resolve()
            folder_key = _parse_version(entry.name)
            if folder_key == target_key:
                exact = resolved
            if version_at_least(entry.name, target):
                eligible.append((folder_key, resolved))

    if exact is not None:
        return exact
    if not eligible:
        return None
    return max(eligible, key=lambda item: item[0])[1]


def _resolve_relaunch_target_version(
    *,
    explicit: Optional[str] = None,
    last_install: Optional[dict] = None,
    last_download: Optional[dict] = None,
    last_check: Optional[dict] = None,
) -> Optional[str]:
    if explicit and str(explicit).strip():
        return str(explicit).strip()
    for record in (last_install, last_download, last_check):
        if not isinstance(record, dict):
            continue
        version = str(
            record.get("target_version")
            or record.get("latest_version")
            or ""
        ).strip()
        if version:
            return version
    return None


def enrich_update_handoff_state(payload: dict) -> dict:
    """Add relaunch discovery fields used by /api/updates/status."""
    out = dict(payload or {})
    settings.load()
    last_install = settings.get("updates.last_install")
    last_download = settings.get("updates.last_download")
    last_portable_apply = settings.get("updates.last_portable_apply")
    last_check = out if out.get("latest_version") else settings.get("updates.last_check")
    if not isinstance(last_install, dict):
        last_install = out.get("last_install")
    if not isinstance(last_download, dict):
        last_download = out.get("last_download")
    if not isinstance(last_check, dict):
        last_check = None

    target_version = _resolve_relaunch_target_version(
        last_install=last_install if isinstance(last_install, dict) else None,
        last_download=last_download if isinstance(last_download, dict) else None,
        last_check=last_check if isinstance(last_check, dict) else None,
    )
    installed_exe = (
        resolve_installed_coach_exe(target_version)
        if target_version
        else None
    )
    launch_allowed = _install_launch_allowed()
    out["installed_exe_path"] = str(installed_exe) if installed_exe else None
    out["relaunch_available"] = bool(installed_exe) and launch_allowed
    out["relaunch_after_install"] = relaunch_after_install_enabled()
    out["portable_runtime"] = app_paths.is_portable_runtime()
    out["portable_root"] = (
        str(app_paths.portable_root()) if app_paths.portable_root() else None
    )
    out["preferred_asset_kind"] = preferred_update_asset_kind()
    out["upgrade_blocked_reason"] = upgrade_blocked_reason(
        last_check if isinstance(last_check, dict) else out
    )
    out["download_on_check"] = bool(settings.get("updates.download_on_check", False))
    out["install_on_quit"] = bool(settings.get("updates.install_on_quit", False))
    if isinstance(last_portable_apply, dict):
        out["last_portable_apply"] = last_portable_apply
    portable_ready = _portable_apply_ready(last_download, last_portable_apply)
    out["portable_apply_ready"] = portable_ready
    return out


def _detached_launch_exe(exe_path: Path) -> None:
    if os.name != "nt":
        raise OSError("detached relaunch is only supported on Windows")
    if not _path_is_under_bazaar_coach_install(exe_path):
        raise OSError("refusing to launch executable outside Bazaar Coach install dirs")
    creationflags = _WIN_DETACHED_PROCESS | _WIN_CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        [str(exe_path)],
        cwd=str(exe_path.parent),
        close_fds=True,
        creationflags=creationflags,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _merge_relaunch_into_last_install(
    *,
    target_version: str,
    installed_exe: Optional[Path],
    relaunch_status: str,
    relaunch_error: Optional[str],
    persist: bool,
) -> dict:
    settings.load()
    last_install = settings.get("updates.last_install")
    if not isinstance(last_install, dict):
        last_install = _install_record_base(target_version=target_version)
    updated = {
        **last_install,
        "target_version": target_version,
        "relaunch_attempted_at": _utc_now(),
        "relaunch_status": relaunch_status,
        "relaunch_error": relaunch_error,
        "installed_exe_path": str(installed_exe) if installed_exe else last_install.get("installed_exe_path"),
    }
    return _save_install_result(updated, persist)


def relaunch_installed_coach(
    target_version: str,
    *,
    allow_launch: bool = False,
    persist: bool = True,
) -> dict:
    """Launch the installed build for target_version without blocking coach."""
    target = str(target_version or "").strip()
    result = {
        "ok": False,
        "action": "relaunch",
        "target_version": target or None,
        "installed_exe_path": None,
        "relaunch_status": "failed",
        "relaunch_attempted_at": _utc_now(),
        "relaunch_error": None,
    }
    if not target:
        result["relaunch_error"] = "missing target_version"
        return result
    if not _install_launch_allowed(allow_launch=allow_launch):
        result["relaunch_error"] = "relaunch requires a packaged build"
        return result

    installed_exe = resolve_installed_coach_exe(target)
    result["installed_exe_path"] = str(installed_exe) if installed_exe else None
    if installed_exe is None:
        result["relaunch_error"] = f"installed build for {target} was not found"
        saved = _merge_relaunch_into_last_install(
            target_version=target,
            installed_exe=None,
            relaunch_status="failed",
            relaunch_error=result["relaunch_error"],
            persist=persist,
        )
        return {**saved, **result}

    try:
        _detached_launch_exe(installed_exe)
    except OSError as exc:
        result["relaunch_error"] = str(exc)
        saved = _merge_relaunch_into_last_install(
            target_version=target,
            installed_exe=installed_exe,
            relaunch_status="failed",
            relaunch_error=result["relaunch_error"],
            persist=persist,
        )
        return {**saved, **result}

    result.update({
        "ok": True,
        "relaunch_status": "launched",
        "relaunch_error": None,
    })
    saved = _merge_relaunch_into_last_install(
        target_version=target,
        installed_exe=installed_exe,
        relaunch_status="launched",
        relaunch_error=None,
        persist=persist,
    )
    return {**saved, **result}


def _parse_iso_datetime(value: Any) -> Optional[_dt.datetime]:
    if not value:
        return None
    try:
        parsed = _dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed


def _install_record_base(*, target_version: Any = None, file_path: Any = None) -> dict:
    return {
        "ok": False,
        "status": "failed",
        "action": "install",
        "target_version": target_version,
        "file_path": file_path,
        "previous_version": APP_VERSION,
        "launched_at": None,
        "verified_at": None,
        "error": None,
    }


def _validate_downloaded_installer_handoff(
    last_download: Optional[dict],
) -> tuple[Optional[Path], dict]:
    """Return (installer_path, result) where result is ok=False on validation failure."""
    if not isinstance(last_download, dict):
        return None, {
            **_install_record_base(),
            "error": "missing last_download",
        }

    file_path = str(last_download.get("file_path") or "").strip()
    status = str(last_download.get("status") or "").strip().lower()
    result = _install_record_base(
        target_version=last_download.get("latest_version"),
        file_path=file_path or None,
    )
    result["action"] = "install"

    if not last_download.get("ok") or status not in DOWNLOAD_HANDOFF_STATUSES:
        result["error"] = "last_download is not verified or downloaded"
        return None, result
    if not file_path:
        result["error"] = "last_download is missing file_path"
        return None, result

    updates_dir = app_paths.data_dir() / "updates"
    installer_path = Path(file_path).expanduser()
    if not _path_is_inside(installer_path, updates_dir):
        result["error"] = "installer path is outside updates directory"
        return None, result
    if not installer_path.is_file():
        result["error"] = "installer file is missing"
        return None, result

    return installer_path.resolve(), result


def _path_is_inside(path: Path, directory: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(directory.resolve(strict=False))
        return True
    except (OSError, ValueError):
        return False


def _reveal_in_file_manager(file_path: Path) -> None:
    if os.name == "nt":
        subprocess.Popen(["explorer", f"/select,{str(file_path)}"])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(file_path)])
    else:
        subprocess.Popen(["xdg-open", str(file_path.parent)])


def _open_in_file_manager(dir_path: Path) -> None:
    """Open a folder in the OS file manager (no item selected).

    Sibling of ``_reveal_in_file_manager`` for the case where there is no
    specific file to highlight. Kept as its own seam so callers (and tests)
    have a single place to mock instead of spawning Explorer inline.
    """
    if os.name == "nt":
        subprocess.Popen(["explorer", str(dir_path)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(dir_path)])
    else:
        subprocess.Popen(["xdg-open", str(dir_path)])


def _select_installer_asset(release: dict) -> Optional[dict]:
    assets = release.get("assets")
    if not isinstance(assets, list):
        return None
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or "")
        if INSTALLER_ASSET_RE.match(name):
            return asset
    return None


def _select_portable_asset(release: dict) -> Optional[dict]:
    assets = release.get("assets")
    if not isinstance(assets, list):
        return None
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or "")
        if PORTABLE_ASSET_RE.match(name):
            return asset
    return None


def _asset_fields_from_release(release: dict) -> dict:
    installer_asset = _select_installer_asset(release)
    portable_asset = _select_portable_asset(release)
    installer_url = release.get("download_url")
    installer_name = release.get("asset_name")
    installer_sha = (
        release.get("sha256")
        or release.get("checksum_sha256")
        or release.get("installer_sha256")
    )
    portable_url = release.get("portable_download_url")
    portable_name = release.get("portable_asset_name")
    portable_sha = release.get("portable_sha256")
    if installer_asset:
        installer_url = installer_url or installer_asset.get("browser_download_url")
        installer_name = installer_name or installer_asset.get("name")
        installer_sha = installer_sha or installer_asset.get("sha256") or installer_asset.get("digest")
    if portable_asset:
        portable_url = portable_url or portable_asset.get("browser_download_url")
        portable_name = portable_name or portable_asset.get("name")
        portable_sha = portable_sha or portable_asset.get("sha256") or portable_asset.get("digest")
    return {
        "installer_download_url": installer_url,
        "installer_asset_name": installer_name,
        "installer_sha256": _normalize_sha256(installer_sha),
        "portable_download_url": portable_url,
        "portable_asset_name": portable_name,
        "portable_sha256": _normalize_sha256(portable_sha),
    }


def resolve_download_manifest(
    manifest: dict,
    *,
    prefer_portable: Optional[bool] = None,
) -> tuple[str, dict]:
    """Return (asset_kind, manifest slice) with download_url/asset_name/sha256 set."""
    base = dict(manifest or {})
    kind = preferred_update_asset_kind(prefer_portable=prefer_portable)
    if kind == UPDATE_ASSET_PORTABLE:
        download_url = base.get("portable_download_url") or base.get("download_url")
        asset_name = base.get("portable_asset_name") or base.get("asset_name")
        sha256 = base.get("portable_sha256") or base.get("sha256")
        if not (download_url and PORTABLE_ASSET_RE.match(str(asset_name or ""))):
            kind = UPDATE_ASSET_INSTALLER
    if kind == UPDATE_ASSET_INSTALLER:
        download_url = base.get("installer_download_url") or base.get("download_url")
        asset_name = base.get("installer_asset_name") or base.get("asset_name")
        sha256 = base.get("installer_sha256") or base.get("sha256")
    resolved = {
        **base,
        "download_url": download_url,
        "asset_name": asset_name,
        "sha256": sha256,
        "asset_kind": kind,
    }
    return kind, resolved


def normalize_manifest(data: dict, channel: str) -> dict:
    if not isinstance(data, dict):
        raise ValueError("release manifest root must be an object")

    selected = data.get(channel)
    if isinstance(selected, dict):
        release = selected
    else:
        releases = data.get("channels")
        if isinstance(releases, dict) and isinstance(releases.get(channel), dict):
            release = releases[channel]
        else:
            release = data

    latest_version = str(
        release.get("latest_version")
        or release.get("version")
        or release.get("tag_name")
        or ""
    ).strip()
    if not latest_version:
        raise ValueError("release manifest is missing latest_version")

    assets = _asset_fields_from_release(release)
    preferred_kind = preferred_update_asset_kind()
    if preferred_kind == UPDATE_ASSET_PORTABLE and assets["portable_download_url"]:
        download_url = assets["portable_download_url"]
        asset_name = assets["portable_asset_name"]
        sha256 = assets["portable_sha256"]
    else:
        preferred_kind = UPDATE_ASSET_INSTALLER
        download_url = assets["installer_download_url"]
        asset_name = assets["installer_asset_name"]
        sha256 = assets["installer_sha256"]

    return {
        "channel": str(release.get("channel") or channel),
        "latest_version": latest_version,
        "release_notes_url": release.get("release_notes_url") or release.get("notes_url") or release.get("html_url"),
        "download_url": download_url,
        "asset_name": asset_name,
        "sha256": sha256,
        "asset_kind": preferred_kind,
        "installer_download_url": assets["installer_download_url"],
        "installer_asset_name": assets["installer_asset_name"],
        "installer_sha256": assets["installer_sha256"],
        "portable_download_url": assets["portable_download_url"],
        "portable_asset_name": assets["portable_asset_name"],
        "portable_sha256": assets["portable_sha256"],
        "mandatory": _truthy(release.get("mandatory")),
        "compatibility_notes": release.get("compatibility_notes") or release.get("compatibility") or release.get("body") or "",
        "minimum_supported_version": release.get("minimum_supported_version"),
        "published_at": release.get("published_at"),
    }


def check_for_updates(
    manifest_url: Optional[str] = None,
    channel: Optional[str] = None,
    timeout: float = 2.5,
    persist: bool = True,
) -> dict:
    settings.load()
    update_settings = settings.get("updates", {})
    configured_repo = update_settings.get("github_repo") or DEFAULT_GITHUB_REPO
    explicit_source = manifest_url or update_settings.get("manifest_url") or configured_repo
    enabled = bool(manifest_url) or (bool(update_settings.get("enabled", False)) and bool(explicit_source))
    selected_channel = channel or update_settings.get("channel", "stable")
    if selected_channel not in CHANNELS:
        selected_channel = "stable"
    if manifest_url or update_settings.get("manifest_url"):
        url = manifest_url or update_settings.get("manifest_url")
    elif configured_repo:
        url = None
    else:
        url = DEFAULT_RELEASE_MANIFEST_URL

    result = {
        "ok": False,
        "enabled": enabled,
        "checked_at": _utc_now(),
        "channel": selected_channel,
        "current_version": APP_VERSION,
        "manifest_url": url,
        "update_available": False,
        "dismissed": False,
        "latest_version": None,
        "release_notes_url": None,
        "download_url": None,
        "asset_name": None,
        "sha256": None,
        "mandatory": False,
        "compatibility_notes": "",
        "minimum_supported_version": None,
        "published_at": None,
        "error": None,
    }
    if not enabled:
        result["ok"] = True
        result["manifest_url"] = None
        return result

    try:
        if url is None and configured_repo:
            url = _github_latest_release_url(configured_repo)
            result["manifest_url"] = url
        manifest = normalize_manifest(_load_manifest_from_url(url, timeout), selected_channel)
        result.update(manifest)
        result["ok"] = True
        result["update_available"] = is_newer_version(manifest["latest_version"], APP_VERSION)
        dismissed_version = update_settings.get("dismissed_version")
        result["dismissed"] = bool(dismissed_version and dismissed_version == manifest["latest_version"])
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as exc:
        result["error"] = str(exc)

    if persist:
        settings.set("updates.last_check", result)
        settings.save()
    if persist and result.get("ok") and result.get("update_available"):
        maybe_auto_download_after_check(result)
    return result


def maybe_auto_download_after_check(check_result: dict) -> Optional[dict]:
    """Opt-in: download verified update payload after a successful check."""
    settings.load()
    if not settings.get("updates.download_on_check", False):
        return None
    if not isinstance(check_result, dict) or not check_result.get("update_available"):
        return None
    if check_result.get("dismissed") and not check_result.get("mandatory"):
        return None
    if upgrade_blocked_reason(check_result):
        return None
    last_download = settings.get("updates.last_download")
    latest = str(check_result.get("latest_version") or "").strip()
    if isinstance(last_download, dict) and last_download.get("ok"):
        if str(last_download.get("latest_version") or "").strip() == latest:
            status = str(last_download.get("status") or "").lower()
            if status in DOWNLOAD_HANDOFF_STATUSES:
                return last_download
    return download_update(manifest=check_result, persist=True)


def dismiss_update(version: str) -> dict:
    settings.load()
    settings.set("updates.dismissed_version", version)
    settings.save()
    return {"ok": True, "dismissed_version": version}


def download_update(
    manifest: Optional[dict] = None,
    *,
    prefer_portable: Optional[bool] = None,
    timeout: float = DEFAULT_DOWNLOAD_TIMEOUT,
    max_bytes: int = DEFAULT_DOWNLOAD_MAX_BYTES,
    persist: bool = True,
) -> dict:
    """Download an update asset (installer or portable zip) without executing it."""
    settings.load()
    if manifest is None:
        manifest = settings.get("updates.last_check") or {}
    if not isinstance(manifest, dict):
        manifest = {}

    blocked = upgrade_blocked_reason(manifest)
    if blocked:
        result = _download_result_base(manifest)
        result["error"] = blocked
        return _save_download_result(result, persist)

    asset_kind, manifest = resolve_download_manifest(manifest, prefer_portable=prefer_portable)
    if asset_kind == UPDATE_ASSET_PORTABLE and not manifest.get("download_url"):
        asset_kind = UPDATE_ASSET_INSTALLER
        manifest = resolve_download_manifest(manifest, prefer_portable=False)[1]
        manifest["asset_kind"] = asset_kind
        manifest["portable_fallback"] = True

    result = _download_result_base(manifest)
    result["asset_kind"] = asset_kind
    download_url = str(manifest.get("download_url") or "").strip()
    if not download_url:
        result["error"] = "missing download_url"
        return _save_download_result(result, persist)

    asset_name = _safe_asset_name(manifest.get("asset_name"), download_url, manifest.get("latest_version"))
    result["asset_name"] = asset_name
    updates_dir = app_paths.data_dir() / "updates"
    destination = updates_dir / asset_name
    tmp_path = updates_dir / f"{asset_name}.tmp"
    hasher = hashlib.sha256()
    total = 0

    try:
        updates_dir.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(download_url, headers={"User-Agent": f"BazaarCoach/{APP_VERSION}"})
        with _urlopen(request, timeout=timeout) as response:
            length = response.headers.get("Content-Length")
            if length:
                try:
                    declared_size = int(length)
                except ValueError:
                    declared_size = None
                if declared_size is not None and declared_size > max_bytes:
                    raise ValueError(f"download too large: {declared_size} bytes")

            with tmp_path.open("wb") as fh:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError(f"download too large: exceeded {max_bytes} bytes")
                    hasher.update(chunk)
                    fh.write(chunk)

        actual_sha256 = hasher.hexdigest()
        expected_sha256 = _normalize_sha256(manifest.get("sha256"))
        result["file_size"] = total
        if expected_sha256 and actual_sha256.lower() != expected_sha256.lower():
            result["sha256"] = expected_sha256
            result["error"] = f"sha256 mismatch: expected {expected_sha256}, got {actual_sha256}"
            try:
                tmp_path.unlink()
            except OSError:
                pass
            return _save_download_result(result, persist)

        tmp_path.replace(destination)
        result.update({
            "ok": True,
            "status": "verified" if expected_sha256 else "downloaded",
            "file_path": str(destination),
            "sha256": expected_sha256 or actual_sha256,
            "sha256_verified": bool(expected_sha256),
            "error": None,
        })
        if asset_kind == UPDATE_ASSET_PORTABLE:
            try:
                staging = extract_portable_staging(
                    destination,
                    str(manifest.get("latest_version") or "update"),
                )
                result["staging_dir"] = str(staging)
            except (OSError, ValueError) as exc:
                result.update({"ok": False, "status": "failed", "error": str(exc)})
    except (OSError, ValueError, urllib.error.URLError) as exc:
        result["error"] = str(exc)
        try:
            tmp_path.unlink()
        except OSError:
            pass

    return _save_download_result(result, persist)


def download_update_installer(
    manifest: Optional[dict] = None,
    *,
    timeout: float = DEFAULT_DOWNLOAD_TIMEOUT,
    max_bytes: int = DEFAULT_DOWNLOAD_MAX_BYTES,
    persist: bool = True,
) -> dict:
    """Backward-compatible alias: download installer asset only."""
    return download_update(
        manifest=manifest,
        prefer_portable=False,
        timeout=timeout,
        max_bytes=max_bytes,
        persist=persist,
    )


def _portable_staging_root(version: str) -> Path:
    safe_version = re.sub(r"[^A-Za-z0-9._ -]+", "_", str(version or "update")).strip(" .") or "update"
    return app_paths.data_dir() / "updates" / "staging" / safe_version


def _find_coach_exe_in_tree(root: Path) -> Optional[Path]:
    direct = root / COACH_EXE_NAME
    if direct.is_file():
        return direct.resolve()
    for candidate in root.rglob(COACH_EXE_NAME):
        if candidate.is_file():
            return candidate.resolve()
    return None


def extract_portable_staging(zip_path: Path, version: str) -> Path:
    """Extract a portable zip into updates/staging/{version} and verify layout."""
    updates_dir = app_paths.data_dir() / "updates"
    if not _path_is_inside(zip_path.resolve(), updates_dir):
        raise ValueError("portable zip is outside updates directory")
    staging_dir = _portable_staging_root(version)
    if staging_dir.exists():
        shutil.rmtree(staging_dir, ignore_errors=True)
    staging_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            member_path = Path(member.filename)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise ValueError(f"unsafe zip entry: {member.filename}")
        archive.extractall(staging_dir)
    if _find_coach_exe_in_tree(staging_dir) is None:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise ValueError("portable zip is missing BazaarCoach.exe")
    return staging_dir.resolve()


def _portable_apply_ready(
    last_download: Optional[dict],
    last_portable_apply: Optional[dict] = None,
) -> bool:
    if not isinstance(last_download, dict) or not last_download.get("ok"):
        return False
    if str(last_download.get("asset_kind") or "").lower() != UPDATE_ASSET_PORTABLE:
        return False
    staging = str(last_download.get("staging_dir") or "").strip()
    if not staging:
        return False
    staging_path = Path(staging)
    if not staging_path.is_dir() or _find_coach_exe_in_tree(staging_path) is None:
        return False
    if isinstance(last_portable_apply, dict):
        status = str(last_portable_apply.get("status") or "").lower()
        if status in {"launched", "verified"}:
            apply_version = str(last_portable_apply.get("target_version") or "").strip()
            download_version = str(last_download.get("latest_version") or "").strip()
            if apply_version and apply_version == download_version:
                return False
    return True


def _validate_portable_apply_handoff(
    last_download: Optional[dict],
) -> tuple[Optional[Path], Optional[Path], dict]:
    record = {
        "ok": False,
        "status": "failed",
        "action": "apply_portable",
        "target_version": None,
        "staging_dir": None,
        "portable_root": None,
        "error": None,
    }
    if not app_paths.is_portable_runtime():
        record["error"] = "portable apply requires a portable build"
        return None, None, record
    portable_root = app_paths.portable_root()
    if portable_root is None:
        record["error"] = "portable root could not be resolved"
        return None, None, record
    if not isinstance(last_download, dict) or not last_download.get("ok"):
        record["error"] = "missing verified portable download"
        return None, None, record
    blocked = upgrade_blocked_reason(last_download)
    if blocked:
        record["error"] = blocked
        return None, None, record
    staging_raw = str(last_download.get("staging_dir") or "").strip()
    if not staging_raw:
        record["error"] = "portable staging directory is missing"
        return None, None, record
    staging_dir = Path(staging_raw).resolve()
    updates_dir = app_paths.data_dir() / "updates"
    if not _path_is_inside(staging_dir, updates_dir / "staging"):
        record["error"] = "staging path is outside updates staging directory"
        return None, None, record
    coach_exe = _find_coach_exe_in_tree(staging_dir)
    if coach_exe is None:
        record["error"] = "staging directory is missing BazaarCoach.exe"
        return None, None, record
    record.update({
        "target_version": last_download.get("latest_version"),
        "staging_dir": str(staging_dir),
        "portable_root": str(portable_root.resolve()),
    })
    return staging_dir, portable_root.resolve(), record


def _copy_portable_tree(source_root: Path, target_root: Path) -> None:
    """Copy extracted portable files into the live portable root."""
    if not _path_is_inside(source_root, app_paths.data_dir() / "updates" / "staging"):
        raise OSError("refusing to copy from staging outside updates/staging")
    target_root = target_root.resolve()
    portable_root = app_paths.portable_root()
    if portable_root is None or target_root != portable_root.resolve():
        raise OSError("refusing to copy outside resolved portable root")
    for item in source_root.iterdir():
        destination = target_root / item.name
        if item.is_dir():
            if destination.exists():
                shutil.rmtree(destination, ignore_errors=True)
            shutil.copytree(item, destination)
        else:
            shutil.copy2(item, destination)


def _detached_launch_portable_exe(exe_path: Path) -> None:
    portable_root = app_paths.portable_root()
    if portable_root is None or not _path_is_inside(exe_path, portable_root):
        raise OSError("refusing to launch executable outside portable root")
    if os.name != "nt":
        raise OSError("detached portable relaunch is only supported on Windows")
    creationflags = _WIN_DETACHED_PROCESS | _WIN_CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        [str(exe_path)],
        cwd=str(exe_path.parent),
        close_fds=True,
        creationflags=creationflags,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def perform_portable_swap(
    staging_dir: Path,
    target_root: Path,
    parent_pid: Optional[int] = None,
    *,
    target_version: Optional[str] = None,
    timeout_sec: float = WATCH_INSTALL_RELAUNCH_TIMEOUT_SEC,
    persist: bool = True,
) -> dict:
    """Wait for parent_pid (if any), copy staging into portable root, relaunch."""
    import time

    result = {
        "ok": False,
        "action": "apply_portable",
        "target_version": target_version,
        "staging_dir": str(staging_dir),
        "portable_root": str(target_root),
        "status": "failed",
        "error": None,
    }
    updates_staging = app_paths.data_dir() / "updates" / "staging"
    if not _path_is_inside(staging_dir.resolve(), updates_staging):
        result["error"] = "staging path is outside updates staging directory"
        return _save_portable_apply_result(result, persist)
    portable_root = app_paths.portable_root()
    if portable_root is None or target_root.resolve() != portable_root.resolve():
        result["error"] = "target root does not match resolved portable root"
        return _save_portable_apply_result(result, persist)
    if parent_pid:
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            if not _windows_pid_running(parent_pid):
                break
            time.sleep(WATCH_INSTALL_POLL_INTERVAL_SEC)
    try:
        _copy_portable_tree(staging_dir, target_root)
        coach_exe = target_root / COACH_EXE_NAME
        if not coach_exe.is_file():
            coach_exe = _find_coach_exe_in_tree(target_root) or coach_exe
        _detached_launch_portable_exe(coach_exe)
        result.update({
            "ok": True,
            "status": "verified",
            "verified_at": _utc_now(),
            "relaunch_status": "launched",
            "error": None,
        })
    except OSError as exc:
        result["error"] = str(exc)
    return _save_portable_apply_result(result, persist)


def spawn_portable_swap_watcher(
    staging_dir: Path,
    target_root: Path,
    parent_pid: int,
    target_version: str,
) -> None:
    if os.name != "nt":
        return
    cmd = [
        sys.executable,
        "-m",
        "update_checker",
        "--apply-portable-swap",
        str(staging_dir),
        str(target_root),
        str(parent_pid),
        str(target_version or ""),
    ]
    creationflags = _WIN_DETACHED_PROCESS | _WIN_CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        cmd,
        close_fds=True,
        creationflags=creationflags,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def apply_portable_update(
    last_download: Optional[dict] = None,
    *,
    allow_launch: bool = False,
    shutdown_first: Optional[bool] = None,
    shutdown_callback: Optional[Callable[[], None]] = None,
    persist: bool = True,
) -> dict:
    """Quit-and-swap portable build from verified staging, then relaunch."""
    settings.load()
    if last_download is None:
        last_download = settings.get("updates.last_download")
    staging_dir, portable_root, result = _validate_portable_apply_handoff(last_download)
    if staging_dir is None or portable_root is None:
        return _save_portable_apply_result(result, persist)

    if not _install_launch_allowed(allow_launch=allow_launch):
        result["error"] = "portable apply requires a packaged build"
        return _save_portable_apply_result(result, persist)

    if shutdown_first is None:
        shutdown_first = bool(shutdown_callback) or app_paths.is_packaged()
    target_version = str(result.get("target_version") or "")
    launched_at = _utc_now()
    result.update({
        "ok": True,
        "status": "launched",
        "launched_at": launched_at,
        "previous_version": APP_VERSION,
        "shutdown_first": bool(shutdown_first),
        "error": None,
    })
    _save_portable_apply_result(result, persist)

    if shutdown_first:
        parent_pid = os.getpid()

        def _spawn_after_shutdown() -> None:
            if shutdown_callback:
                shutdown_callback()
            spawn_portable_swap_watcher(
                staging_dir,
                portable_root,
                parent_pid,
                target_version,
            )

        if shutdown_callback:
            import threading

            threading.Thread(target=_spawn_after_shutdown, daemon=True).start()
            return result
        spawn_portable_swap_watcher(staging_dir, portable_root, parent_pid, target_version)
        return result

    return perform_portable_swap(
        staging_dir,
        portable_root,
        None,
        target_version=target_version,
        persist=persist,
    )


def run_pending_update_on_quit() -> Optional[dict]:
    """Opt-in: install or portable swap during graceful shutdown."""
    settings.load()
    if not settings.get("updates.install_on_quit", False):
        return None
    if not _install_launch_allowed():
        return None
    last_download = settings.get("updates.last_download")
    if not isinstance(last_download, dict) or not last_download.get("ok"):
        return None
    if upgrade_blocked_reason(last_download):
        return None
    if app_paths.is_portable_runtime() and _portable_apply_ready(
        last_download,
        settings.get("updates.last_portable_apply"),
    ):
        staging_dir, portable_root, record = _validate_portable_apply_handoff(last_download)
        if staging_dir is None or portable_root is None:
            return record
        spawn_portable_swap_watcher(
            staging_dir,
            portable_root,
            os.getpid(),
            str(record.get("target_version") or ""),
        )
        pending = {
            **record,
            "ok": True,
            "status": "launched",
            "launched_at": _utc_now(),
            "install_on_quit": True,
        }
        return _save_portable_apply_result(pending, True)
    if str(last_download.get("asset_kind") or "").lower() == UPDATE_ASSET_PORTABLE:
        return None
    installer_path, validation = _validate_downloaded_installer_handoff(last_download)
    if installer_path is None:
        return validation
    silent = bool(settings.get("updates.install_silent", False))
    try:
        proc = _spawn_installer(installer_path, silent=silent)
        spawn_post_install_relaunch_watcher(proc.pid, str(last_download.get("latest_version") or ""))
        launched = {
            "ok": True,
            "status": "launched",
            "action": "install",
            "target_version": last_download.get("latest_version"),
            "file_path": str(installer_path),
            "previous_version": APP_VERSION,
            "launched_at": _utc_now(),
            "silent": silent,
            "install_on_quit": True,
            "error": None,
        }
        return _save_install_result(launched, True)
    except OSError as exc:
        failed = _install_record_base(
            target_version=last_download.get("latest_version"),
            file_path=str(installer_path),
        )
        failed["error"] = str(exc)
        return _save_install_result(failed, True)


def _stable_start_menu_shortcut_path() -> Optional[Path]:
    if os.name != "nt":
        return None
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return (
        Path(appdata)
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / STABLE_START_MENU_SHORTCUT_NAME
    )


def refresh_stable_start_menu_shortcut(target_version: str) -> dict:
    """Write a per-user Start Menu shortcut targeting the newest installed build."""
    result = {
        "ok": False,
        "action": "refresh_shortcut",
        "target_version": str(target_version or "").strip() or None,
        "shortcut_path": None,
        "installed_exe_path": None,
        "error": None,
    }
    shortcut_path = _stable_start_menu_shortcut_path()
    if shortcut_path is None:
        result["error"] = "Start Menu shortcut path is unavailable"
        return result
    target = str(target_version or "").strip()
    if not target:
        result["error"] = "missing target_version"
        return result
    installed_exe = resolve_installed_coach_exe(target)
    if installed_exe is None:
        result["error"] = f"installed build for {target} was not found"
        return result
    result["installed_exe_path"] = str(installed_exe)
    result["shortcut_path"] = str(shortcut_path)
    if os.name != "nt":
        result["error"] = "shortcut refresh is only supported on Windows"
        return result
    shortcut_path.parent.mkdir(parents=True, exist_ok=True)
    ps = (
        "$shell = New-Object -ComObject WScript.Shell; "
        f"$link = $shell.CreateShortcut('{shortcut_path}'); "
        f"$link.TargetPath = '{installed_exe}'; "
        f"$link.WorkingDirectory = '{installed_exe.parent}'; "
        "$link.Description = 'Bazaar Coach (latest installed)'; "
        "$link.Save()"
    )
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except OSError as exc:
        result["error"] = str(exc)
        return result
    if completed.returncode != 0:
        stderr = (completed.stderr or completed.stdout or "").strip()
        result["error"] = stderr or f"shortcut creation failed with code {completed.returncode}"
        return result
    result["ok"] = True
    return result


def _spawn_installer(installer_path: Path, *, silent: bool = False) -> subprocess.Popen:
    """Launch the Inno Setup installer (interactive by default).

    Silent installs use /VERYSILENT, /CLOSEAPPLICATIONS, and /SUPPRESSMSGBOXES
    per Inno Setup 6 unattended guidance.
    """
    if os.name != "nt":
        raise OSError("installer launch is only supported on Windows")
    args = [str(installer_path)]
    if silent:
        args.extend(["/VERYSILENT", "/CLOSEAPPLICATIONS", "/SUPPRESSMSGBOXES"])
    return subprocess.Popen(args, close_fds=True)


def _windows_pid_running(pid: int) -> bool:
    if os.name != "nt" or pid <= 0:
        return False
    import ctypes

    synchronize = 0x00100000
    handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, pid)
    if not handle:
        return False
    exit_code = ctypes.c_ulong()
    still_running = True
    try:
        if ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            still_running = exit_code.value == _WIN_STILL_ACTIVE
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)
    return still_running


def _wait_for_installer_exit(pid: Optional[int], timeout_sec: float) -> bool:
    import time

    if not pid:
        return False
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if not _windows_pid_running(pid):
            return True
        time.sleep(WATCH_INSTALL_POLL_INTERVAL_SEC)
    return False


def _wait_for_installed_exe(target_version: str, timeout_sec: float) -> Optional[Path]:
    import time

    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        installed_exe = resolve_installed_coach_exe(target_version)
        if installed_exe is not None:
            return installed_exe
        time.sleep(WATCH_INSTALL_POLL_INTERVAL_SEC)
    return None


def watch_install_and_relaunch(
    target_version: str,
    installer_pid: Optional[int] = None,
    *,
    timeout_sec: float = WATCH_INSTALL_RELAUNCH_TIMEOUT_SEC,
) -> dict:
    """Detached helper: wait for installer exit, then relaunch installed build."""
    target = str(target_version or "").strip()
    if not target:
        return {"ok": False, "error": "missing target_version"}

    remaining = float(timeout_sec)
    if installer_pid:
        import time

        wait_start = time.monotonic()
        _wait_for_installer_exit(installer_pid, remaining)
        remaining = max(0.0, remaining - (time.monotonic() - wait_start))

    installed_exe = _wait_for_installed_exe(target, remaining)
    if installed_exe is None:
        return {
            "ok": False,
            "error": f"installed build for {target} was not found before timeout",
        }
    return relaunch_installed_coach(target, allow_launch=True, persist=True)


def spawn_post_install_relaunch_watcher(
    installer_pid: Optional[int],
    target_version: str,
) -> None:
    """Spawn a detached watcher that relaunches coach after Inno finishes."""
    if not relaunch_after_install_enabled():
        return
    if not app_paths.is_packaged():
        return
    if os.name != "nt":
        return

    target = str(target_version or "").strip()
    if not target:
        return

    pid_arg = str(installer_pid or "")
    cmd = [
        sys.executable,
        "-m",
        "update_checker",
        "--watch-install-relaunch",
        target,
        pid_arg,
    ]
    creationflags = _WIN_DETACHED_PROCESS | _WIN_CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        cmd,
        close_fds=True,
        creationflags=creationflags,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def reveal_downloaded_installer(last_download: Optional[dict] = None) -> dict:
    """Reveal a previously downloaded installer without executing it."""
    settings.load()
    if last_download is None:
        last_download = settings.get("updates.last_download")
    if not isinstance(last_download, dict):
        return {
            "ok": False,
            "status": "failed",
            "action": "reveal",
            "file_path": None,
            "error": "missing last_download",
        }

    installer_path, result = _validate_downloaded_installer_handoff(last_download)
    reveal_result = {
        "ok": False,
        "status": "failed",
        "action": "reveal",
        "file_path": result.get("file_path"),
        "latest_version": last_download.get("latest_version"),
        "asset_name": last_download.get("asset_name"),
        "error": result.get("error"),
    }
    if installer_path is None:
        return reveal_result

    try:
        _reveal_in_file_manager(installer_path)
        reveal_result.update({
            "ok": True,
            "status": "revealed",
            "file_path": str(installer_path),
            "error": None,
        })
    except OSError as exc:
        reveal_result["error"] = str(exc)
    return reveal_result


def launch_downloaded_installer(
    last_download: Optional[dict] = None,
    *,
    allow_launch: bool = False,
    shutdown_first: Optional[bool] = None,
    shutdown_callback: Optional[Callable[[], None]] = None,
    persist: bool = True,
) -> dict:
    """Launch a verified downloaded installer after optional graceful shutdown."""
    settings.load()
    if last_download is None:
        last_download = settings.get("updates.last_download")

    blocked = upgrade_blocked_reason(last_download if isinstance(last_download, dict) else None)
    if blocked:
        result = _install_record_base()
        result["error"] = blocked
        return _save_install_result(result, persist)

    installer_path, result = _validate_downloaded_installer_handoff(last_download)
    if installer_path is None:
        result["action"] = "install"
        return _save_install_result(result, persist)

    if not _install_launch_allowed(allow_launch=allow_launch):
        result["error"] = "installer launch requires a packaged build"
        return _save_install_result(result, persist)

    if shutdown_first is None:
        shutdown_first = app_paths.is_packaged()

    silent = bool(settings.get("updates.install_silent", False))
    target_version = last_download.get("latest_version")
    launched_at = _utc_now()
    result.update({
        "ok": True,
        "status": "launched",
        "file_path": str(installer_path),
        "target_version": target_version,
        "previous_version": APP_VERSION,
        "launched_at": launched_at,
        "verified_at": None,
        "error": None,
        "shutdown_first": bool(shutdown_first),
        "silent": silent,
    })
    _save_install_result(result, persist)

    def _launch_after_shutdown() -> None:
        if shutdown_callback:
            shutdown_callback()
        try:
            proc = _spawn_installer(installer_path, silent=silent)
            spawn_post_install_relaunch_watcher(proc.pid, str(target_version or ""))
        except OSError as exc:
            failed = dict(result)
            failed.update({"ok": False, "status": "failed", "error": str(exc)})
            _save_install_result(failed, persist)

    if shutdown_first and shutdown_callback:
        import threading

        threading.Thread(target=_launch_after_shutdown, daemon=True).start()
        return result

    try:
        proc = _spawn_installer(installer_path, silent=silent)
        spawn_post_install_relaunch_watcher(proc.pid, str(target_version or ""))
    except OSError as exc:
        result.update({"ok": False, "status": "failed", "error": str(exc)})
        return _save_install_result(result, persist)
    return result


def verify_pending_install_on_startup(*, persist: bool = True) -> Optional[dict]:
    """Mark launched installs as verified or stale on the next coach startup."""
    settings.load()
    last_install = settings.get("updates.last_install")
    if not isinstance(last_install, dict):
        return None

    status = str(last_install.get("status") or "").strip().lower()
    if status not in INSTALL_PENDING_STATUSES:
        return last_install

    target_version = str(last_install.get("target_version") or "").strip()
    if not target_version:
        return last_install

    last_download = settings.get("updates.last_download")
    if isinstance(last_download, dict):
        download_version = str(last_download.get("latest_version") or "").strip()
        if download_version and download_version != target_version:
            return last_install

    launched_at = _parse_iso_datetime(last_install.get("launched_at"))
    now = _dt.datetime.now(_dt.timezone.utc)
    if launched_at and (now - launched_at) >= _dt.timedelta(days=INSTALL_STALE_DAYS):
        if is_newer_version(target_version, APP_VERSION):
            last_install = {
                **last_install,
                "ok": False,
                "status": "stale",
                "error": (
                    f"install to {target_version} was launched over "
                    f"{INSTALL_STALE_DAYS} days ago but app is still {APP_VERSION}"
                ),
            }
            return _save_install_result(last_install, persist)

    if version_at_least(APP_VERSION, target_version):
        verified = {
            **last_install,
            "ok": True,
            "status": "verified",
            "verified_at": _utc_now(),
            "error": None,
        }
        if isinstance(last_download, dict) and last_download.get("ok"):
            handoff = dict(last_download)
            handoff["status"] = "installed"
            settings.set("updates.last_download", handoff)
        saved = _save_install_result(verified, persist)
        try:
            refresh_stable_start_menu_shortcut(target_version)
        except Exception:
            pass
        return saved

    return last_install


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Check Bazaar Coach release manifest")
    parser.add_argument("--manifest-url", default=None)
    parser.add_argument("--channel", choices=sorted(CHANNELS), default=None)
    parser.add_argument("--timeout", type=float, default=2.5)
    parser.add_argument("--no-save", action="store_true")
    parser.add_argument(
        "--watch-install-relaunch",
        nargs=2,
        metavar=("TARGET_VERSION", "INSTALLER_PID"),
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--apply-portable-swap",
        nargs=4,
        metavar=("STAGING_DIR", "TARGET_ROOT", "PARENT_PID", "TARGET_VERSION"),
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)
    if args.apply_portable_swap:
        staging_dir, target_root, pid_raw, target_version = args.apply_portable_swap
        parent_pid = int(pid_raw) if str(pid_raw).strip().isdigit() else None
        result = perform_portable_swap(
            Path(staging_dir),
            Path(target_root),
            parent_pid,
            target_version=str(target_version or "").strip() or None,
            persist=True,
        )
        return 0 if result.get("ok") else 1
    if args.watch_install_relaunch:
        target_version, pid_raw = args.watch_install_relaunch
        installer_pid = int(pid_raw) if str(pid_raw).strip().isdigit() else None
        result = watch_install_and_relaunch(target_version, installer_pid)
        return 0 if result.get("ok") else 1
    result = check_for_updates(
        manifest_url=args.manifest_url,
        channel=args.channel,
        timeout=args.timeout,
        persist=not args.no_save,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
