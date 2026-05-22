"""
app_paths.py — Centralized filesystem paths for Bazaar Coach.

Development defaults stay repo-local so running from a checkout behaves like it
has historically. Packaged builds, or runs with path override env vars, use
per-user data directories so the installed app directory remains read-only.

Override env vars:
  BAZAAR_COACH_DATA_DIR      Base dir for DB, logs, and static cache
  BAZAAR_COACH_SETTINGS_DIR  Directory containing settings.json
  BAZAAR_COACH_DB_PATH       Exact SQLite DB path
  BAZAAR_COACH_CACHE_DIR     Static content cache directory
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

APP_NAME = "BazaarCoach"
BAZAAR_COACH_INSTALL_DIR = "Bazaar Coach"
COACH_EXE_NAME = "BazaarCoach.exe"


def coach_exe_path() -> Path:
    """Resolved path to the running BazaarCoach executable."""
    return Path(sys.executable).resolve()


def is_packaged() -> bool:
    """True when running from a PyInstaller-style frozen build."""
    return bool(getattr(sys, "frozen", False))


def bundled_root() -> Path:
    """Return the read-only app/bundle root for code and packaged assets."""
    if is_packaged():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent


def repo_dir() -> Path:
    """Return the source checkout / app root directory, or bundled root when frozen."""
    return bundled_root()


def bundled_asset_path(*parts: str) -> Path:
    """Return a path to a read-only asset shipped with the app."""
    return bundled_root().joinpath(*parts)


def _env_path(name: str) -> Optional[Path]:
    value = os.environ.get(name)
    if not value:
        return None
    return Path(value).expanduser()


def _windows_known_folder(env_name: str) -> Optional[Path]:
    value = os.environ.get(env_name)
    if value:
        return Path(value) / APP_NAME
    return None


def _platform_data_dir() -> Path:
    """Return the production/user-data base directory for mutable data."""
    if os.name == "nt":
        return _windows_known_folder("LOCALAPPDATA") or (Path.home() / "AppData" / "Local" / APP_NAME)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP_NAME


def _platform_settings_dir() -> Path:
    """Return the production/user-data settings directory."""
    if os.name == "nt":
        return _windows_known_folder("APPDATA") or (Path.home() / "AppData" / "Roaming" / APP_NAME)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_NAME


def _path_is_inside(path: Path, directory: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(directory.resolve(strict=False))
        return True
    except (OSError, ValueError):
        return False


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


def is_inno_installed_runtime() -> bool:
    """True when the frozen exe lives under Program Files\\Bazaar Coach\\{version}."""
    if not is_packaged():
        return False
    exe = coach_exe_path()
    for root in _program_files_roots():
        install_root = root / BAZAAR_COACH_INSTALL_DIR
        if _path_is_inside(exe, install_root):
            return True
    return False


def portable_root() -> Optional[Path]:
    """Root folder for a portable onedir build, or None for dev/Inno installs."""
    if not is_packaged() or is_inno_installed_runtime():
        return None
    return coach_exe_path().parent


def is_portable_runtime() -> bool:
    return portable_root() is not None


def user_data_mode() -> bool:
    """Use user-data paths when packaged or when any path override is supplied."""
    if is_packaged():
        return True
    return any(
        os.environ.get(name)
        for name in (
            "BAZAAR_COACH_DATA_DIR",
            "BAZAAR_COACH_SETTINGS_DIR",
            "BAZAAR_COACH_DB_PATH",
            "BAZAAR_COACH_CACHE_DIR",
        )
    )


def data_dir() -> Path:
    """Base directory for mutable data: DB, logs, and cache."""
    override = _env_path("BAZAAR_COACH_DATA_DIR")
    if override:
        return override
    if user_data_mode():
        return _platform_data_dir()
    return repo_dir()


def settings_dir() -> Path:
    """Directory containing settings.json."""
    override = _env_path("BAZAAR_COACH_SETTINGS_DIR")
    if override:
        return override
    if user_data_mode():
        return _platform_settings_dir()
    return repo_dir()


def settings_path() -> Path:
    return settings_dir() / "settings.json"


def db_path() -> Path:
    override = _env_path("BAZAAR_COACH_DB_PATH")
    if override:
        return override
    return data_dir() / "bazaar_runs.db"


def logs_dir() -> Path:
    return data_dir() / "logs"


def static_cache_dir() -> Path:
    override = _env_path("BAZAAR_COACH_CACHE_DIR")
    if override:
        return override
    return data_dir() / "static_cache"


def image_cache_dir() -> Path:
    return static_cache_dir() / "images"


def user_builds_dir() -> Path:
    return data_dir() / "user_builds"


def user_builds_path(hero_slug: str) -> Path:
    return user_builds_dir() / f"{hero_slug}_user.json"


_PLAYER_LOG_SUFFIX = ("Tempo Storm", "The Bazaar", "Player.log")


def find_player_log() -> Path:
    """Return the first existing Player.log candidate, or candidate #1 when none exist.

    Search order (handles relocated AppData and OneDrive-redirected profiles):
    1. LOCALAPPDATA/../LocalLow/...
    2. USERPROFILE/AppData/LocalLow/...
    3. Path.home()/AppData/LocalLow/...
    4. ./Player.log (CWD fallback)
    """
    candidates: list[Path] = []

    localappdata = os.environ.get("LOCALAPPDATA", "")
    if localappdata:
        candidates.append(Path(localappdata, "..", "LocalLow", *_PLAYER_LOG_SUFFIX))

    userprofile = os.environ.get("USERPROFILE", "")
    if userprofile:
        candidates.append(Path(userprofile, "AppData", "LocalLow", *_PLAYER_LOG_SUFFIX))

    candidates.append(Path(str(Path.home()), "AppData", "LocalLow", *_PLAYER_LOG_SUFFIX))
    candidates.append(Path("Player.log"))

    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.is_file():
            return resolved

    # Fall back to candidate #1 (resolved) so callers get a useful probable path.
    return candidates[0].resolve()
