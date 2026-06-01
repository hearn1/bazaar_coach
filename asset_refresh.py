"""Transport-agnostic asset-refresh orchestration (issue #175).

Owns the threaded refresh of the repo's refreshable assets so that *any*
transport can drive it without duplicating the worker/lock bookkeeping:

  * the Flask routes in ``web/server.py`` call it today, and
  * a future PyWebView ``js_api`` method (the north-star: drop the Flask host)
    can call the very same ``start_refresh`` / ``status`` helpers directly,
    since the overlay and the refresh worker live in the same process.

Only ``builds`` and ``content`` are wired. Card images are intentionally out of
scope (and slated for removal), so ``refresh_images`` is deliberately not
imported here.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

import card_cache
import refresh_builds
import scorer

KINDS = ("builds", "content")

_lock = threading.Lock()
_state: dict[str, dict] = {kind: {"running": False, "last_result": None} for kind in KINDS}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ── Per-kind runners ───────────────────────────────────────────────────────────

def _run_builds() -> dict:
    """Refresh every hero catalog. Reuses the existing builds summary vocabulary
    (``updated`` / ``unchanged`` / ``failed``)."""
    results = refresh_builds.refresh_builds()
    payload = refresh_builds.summarize_results(results)
    updated = payload.get("updated", 0)
    unchanged = payload.get("unchanged", 0)
    skipped = payload.get("skipped", 0)
    if skipped:
        message = f"{skipped} catalog(s) skipped (errors); bundled catalogs remain active."
    elif updated:
        message = f"Updated {updated} catalog(s)."
    else:
        message = f"All {unchanged} catalog(s) already up to date."
    payload["message"] = message
    return payload


def _run_content() -> dict:
    """Refresh the static card content cache and normalize ``card_cache``'s
    summary shape into the shared status vocabulary."""
    summary = card_cache.refresh_cache(versioned=True)
    summary.pop("cards", None)  # large; not needed for the status payload
    endpoint_diff = summary.get("endpoint_diff") or {}
    card_diff = summary.get("card_diff") or {}
    warnings = summary.get("warnings") or []
    endpoints_fetched = summary.get("endpoints_fetched") or []

    added = card_diff.get("added_count", 0)
    removed = card_diff.get("removed_count", 0)
    changed = card_diff.get("changed_count", 0)
    endpoints_changed = len(endpoint_diff.get("added") or []) + len(endpoint_diff.get("changed") or [])
    has_change = bool(added or removed or changed or endpoints_changed)

    if not endpoints_fetched:
        status = "skipped"
        message = "No static endpoints were refreshed; previous cache remains active."
    elif has_change:
        status = "updated"
        if added or removed or changed:
            message = f"Cards: {added} added, {removed} removed, {changed} changed."
        else:
            message = f"Refreshed {len(endpoints_fetched)} endpoint(s); card set unchanged."
    else:
        status = "unchanged"
        message = "Card content already up to date."

    return {
        "ok": status != "skipped",
        "status": status,
        "message": message,
        "endpoints_fetched": endpoints_fetched,
        "endpoint_diff": endpoint_diff,
        "card_diff": card_diff,
        "warnings": warnings,
    }


_RUNNERS = {
    "builds": _run_builds,
    "content": _run_content,
}


def _failure_payload(kind: str, exc: Exception) -> dict:
    skipped = len(scorer.CATALOG_FILENAMES) if kind == "builds" else 1
    return {
        "ok": False,
        "status": "failed",
        "message": f"Refresh failed: {exc}",
        "skipped": skipped,
        "error": str(exc),
    }


# ── State / public API ─────────────────────────────────────────────────────────

def _finish(kind: str, trigger: str, payload: dict) -> None:
    payload = dict(payload)
    payload["trigger"] = trigger
    payload["checked_at"] = _utc_now_iso()
    with _lock:
        _state[kind]["running"] = False
        _state[kind]["last_result"] = payload


def _worker(kind: str, trigger: str) -> None:
    """Run a kind's refresh, never raising; failures are recorded, not raised."""
    try:
        payload = _RUNNERS[kind]()
    except Exception as exc:  # noqa: BLE001 — surface as status, don't crash the thread
        payload = _failure_payload(kind, exc)
    _finish(kind, trigger, payload)


def status(kind: str | None = None) -> dict:
    """Status for one kind, or ``{"kinds": {...}}`` for all kinds."""
    with _lock:
        if kind is not None:
            entry = _state.get(kind)
            if entry is None:
                return {"running": False, "last_result": None}
            return {"running": bool(entry["running"]), "last_result": entry["last_result"]}
        return {
            "kinds": {
                k: {"running": bool(v["running"]), "last_result": v["last_result"]}
                for k, v in _state.items()
            }
        }


def start_refresh(kind: str, trigger: str = "manual") -> bool:
    """Begin an async refresh for ``kind``.

    Returns ``False`` for an unknown kind or if that kind is already running
    (per-kind single-flight: the dashboard and overlay cannot double-refresh the
    same asset; different kinds may run concurrently)."""
    if kind not in _RUNNERS:
        return False
    with _lock:
        if _state[kind]["running"]:
            return False
        _state[kind]["running"] = True
        _state[kind]["last_result"] = {
            "ok": None,
            "status": "checking",
            "message": "Refreshing...",
            "trigger": trigger,
            "checked_at": _utc_now_iso(),
        }
    threading.Thread(
        target=_worker,
        args=(kind, trigger),
        daemon=True,
        name=f"asset-refresh-{kind}-{trigger}",
    ).start()
    return True
