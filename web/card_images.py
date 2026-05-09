"""Card image manifest loader and lookup helpers.

The manifest is built by ``extract_bazaar_bundle_pngs.py --cards-only`` and
lives at ``static_cache/images/manifest.json``. This module caches the manifest
in memory and reloads it automatically when the file's mtime changes (e.g. after
``tracker.py refresh-images`` runs in a separate process). No server restart is
required to pick up new entries.
"""

from __future__ import annotations

import json
import re
import sys
import threading
from pathlib import Path
from typing import Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import app_paths

IMAGE_DIR = app_paths.image_cache_dir()
MANIFEST_PATH = IMAGE_DIR / "manifest.json"

# Manual aliases for cases where the Unity asset folder name doesn't normalize
# to the same string as card_cache.name. Keys and values are both already-
# normalized strings (lowercase, alphanumeric only).
#
# Format: normalized_db_name -> normalized_manifest_key. Generated aliases in
# manifest.json use the same shape and are checked before this manual fallback.
# Discovered by comparing card_cache display names against Unity asset folder
# names extracted from Steam card bundles.
NAME_ALIASES: dict[str, str] = {
    # Plural / singular mismatches
    "bagpipes": "bagpipe",
    "busybee": "busybees",
    "cinders": "cinder",
    "fang": "fangs",
    "golfclubs": "golfclub",
    "nanobot": "nanobots",
    "schematics": "schematic",
    "strawberries": "strawberry",
    # Typos / misspellings in Unity asset folder names
    "ballista": "balista",
    "beasttooth": "beaststooth",
    "businesscard": "buisnesscard",
    "colander": "collander",
    "inertialdampener": "inertiadampener",
    "jabaliandagger": "jaballiandagger",
    "jabaliandrum": "jaballiandrum",
    "ouroborosstatue": "ouroborusstatue",
    "pillbuggy": "pilbuggy",
    "sapphire": "saphire",
    # "Sat-Comm" → "satcomm" (dash stripped); asset has double-t
    "satcomm": "sattcomm",
    # Cyrillic С in asset name strips away, leaving "seafoodracker"
    "seafoodcracker": "seafoodracker",
    # Cyrillic С at the start of "Cleaver" strips away in the asset name
    "cleaver": "leaver",
    # Game renamed these items after the Unity assets were built
    "bluenanas": "bluebananas",
    "dooltron": "dootron",
    "dooltronmainframe": "dootronmainframe",
    "dragontooth": "dragonstooth",
    "frozenflame": "frozenfire",
    "harkuvianlauncher": "hakurvanlauncher",
    "runicblade": "runeblade",
    "tommoogun": "tommygun",
    "trollosaur": "trollolor",
    "weaselpede": "iceweaselpede",
    # Word-form differences
    "banuleaves": "banuleaf",
    # "Mortar & Pestle" → "mortarpestle"; asset spells out "and"
    "mortarpestle": "mortarandpestle",
    "recyclingbin": "recyclebin",
}

_lock = threading.Lock()
_manifest_cache: Optional[dict] = None
_manifest_mtime: Optional[float] = None


def normalize_card_name(value: str) -> str:
    """Lowercase and strip everything except alphanumerics. Idempotent."""
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def invalidate_manifest_cache() -> None:
    """Clear the in-memory manifest cache under the module lock.

    The next call to ``_load_manifest`` (or any lookup function) will reload
    the manifest from disk.  This is called automatically when the manifest
    file's mtime changes, but can also be invoked directly after an in-process
    refresh.
    """
    global _manifest_cache, _manifest_mtime
    with _lock:
        _manifest_cache = None
        _manifest_mtime = None


def _load_manifest() -> dict:
    """Load and memoize the manifest, reloading when the file's mtime changes.

    The outer check is a cheap fast-path: if the cache is populated and the
    file mtime has not changed, return immediately without acquiring the lock.
    Under the lock we re-check both conditions before doing any I/O.

    Returns ``{'by_card_key': {...}}`` (possibly empty) — never ``None``.
    """
    global _manifest_cache, _manifest_mtime

    # Fast-path: cache present and mtime unchanged.
    try:
        current_mtime: Optional[float] = MANIFEST_PATH.stat().st_mtime if MANIFEST_PATH.is_file() else None
    except OSError:
        current_mtime = None

    if _manifest_cache is not None and current_mtime == _manifest_mtime:
        return _manifest_cache

    with _lock:
        # Re-check under the lock.
        try:
            current_mtime = MANIFEST_PATH.stat().st_mtime if MANIFEST_PATH.is_file() else None
        except OSError:
            current_mtime = None

        if _manifest_cache is not None and current_mtime == _manifest_mtime:
            return _manifest_cache

        if current_mtime is None:
            print(f"[CardImages] manifest not found at {MANIFEST_PATH}")
            _manifest_cache = {"by_card_key": {}}
            _manifest_mtime = None
            return _manifest_cache
        try:
            data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or "by_card_key" not in data:
                data = {"by_card_key": {}}
            count = len(data.get("by_card_key", {}))
            print(f"[CardImages] loaded manifest with {count} entries")
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[CardImages] manifest load failed: {exc}")
            data = {"by_card_key": {}}
        _manifest_cache = data
        _manifest_mtime = current_mtime
        return _manifest_cache


def lookup_image_file(card_name: str) -> Optional[str]:
    """Return the bare image filename for a card name, or None."""
    if not card_name:
        return None
    manifest = _load_manifest()
    by_card_key = manifest.get("by_card_key", {})
    aliases = manifest.get("aliases", {})
    normalized = normalize_card_name(card_name)
    entry = by_card_key.get(normalized)
    if entry is None and normalized in aliases:
        entry = by_card_key.get(aliases[normalized])
    if entry is None and normalized in NAME_ALIASES:
        entry = by_card_key.get(NAME_ALIASES[normalized])
    if not entry:
        return None
    return entry.get("image_file") or None


def lookup_image_url(card_name: str) -> Optional[str]:
    """Return the public URL ('/cards/<filename>') for a card name, or None."""
    image_file = lookup_image_file(card_name)
    if not image_file:
        return None
    return f"/cards/{image_file}"
