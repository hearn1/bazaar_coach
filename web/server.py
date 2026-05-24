"""
web/server.py — Flask API + static file server for the Bazaar Coach dashboard.

This file owns only route definitions and the server lifecycle. All business
logic has been extracted to focused modules:

  web/build_helpers.py   — build catalog loading, archetype scoring, phase notes,
                           run-tier classification, insight extraction
  web/overlay_state.py   — /api/overlay/state payload assembly
  web/review_builder.py  — overlay review row construction

Usage:
    cd bazaar_coach
    python -m web.server                  # standalone on port 5555
    python -m web.server --port 8080      # custom port

Or import and start from coach.py:
    from web.server import start_web_server
    start_web_server(port=5555, db_path="bazaar_runs.db")
"""

import json
import os
import re
import sqlite3
import argparse
import tempfile
import threading
import sys
import traceback
from datetime import datetime, timedelta, timezone
from typing import Optional, Callable
from pathlib import Path
from urllib.parse import unquote

import app_paths

ROOT_DIR = app_paths.bundled_root()
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import card_cache
import db
import first_run
import refresh_builds
import scorer
import update_checker

from flask import Flask, jsonify, request, send_from_directory
from name_resolver import is_unresolved, make_resolver

from web.build_helpers import (
    load_builds,
    condition_items_for_archetype,
    infer_archetype_from_decisions,
    build_run_summary,
    invalidate_catalog_cache,
)
from web.overlay_state import build_overlay_state, _get_run_record
from web.review_builder import format_decision_row
from web.card_images import IMAGE_DIR as CARD_IMAGE_DIR, lookup_image_url

DEFAULT_PORT = 5555
DB_PATH: Optional[Path] = None
_shutdown_callback: Optional[Callable] = None
# Registered by the watcher so /api/runs/<id>/force-end can flip RunState's
# in-memory _run_closed flag in lock-step with the DB write. Signature:
# (run_id: int, ts_iso: str) -> bool, where True means the request actually
# closed the run (False means it was already closed or did not match).
_force_end_callback: Optional[Callable[[int, str], bool]] = None
_build_refresh_lock = threading.Lock()
_build_refresh_state = {
    "running": False,
    "last_result": None,
}

app = Flask(
    __name__,
    static_folder=str(app_paths.bundled_asset_path("web", "static")),
    static_url_path="/static",
)


# ── DB helpers ────────────────────────────────────────────────────────────────

def _get_db_path() -> Path:
    if DB_PATH:
        return DB_PATH
    return app_paths.db_path()

def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_get_db_path(), timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _rows_to_dicts(rows) -> list[dict]:
    return [dict(r) for r in rows]


def _resolve(conn, template_id: str) -> str:
    if not template_id:
        return "Unknown"
    row = conn.execute(
        "SELECT name FROM card_cache WHERE template_id=?", (template_id,)
    ).fetchone()
    if row and row["name"] and row["name"] != "Unknown":
        return row["name"]
    return template_id


def _get_tier(conn, template_id: str) -> Optional[str]:
    if not template_id:
        return None
    row = conn.execute(
        "SELECT tier FROM card_cache WHERE template_id=?", (template_id,)
    ).fetchone()
    return row["tier"] if row else None


def _resolve_image(conn, template_id: str) -> Optional[str]:
    """Return /cards/<filename> URL for a template_id, or None.

    Chain: template_id -> card_cache.name -> normalize -> manifest -> URL.
    """
    if not template_id:
        return None
    name = _resolve(conn, template_id)
    if not name:
        return None
    return lookup_image_url(name)


def _resolve_instance_ids_via_api_cards(conn, instance_ids: list[str]) -> dict[str, str]:
    if not instance_ids:
        return {}
    resolver = make_resolver()
    mapping = resolver.bulk_resolve(instance_ids)
    return {iid: name for iid, name in mapping.items() if not is_unresolved(name)}


_ARCHETYPE_PLACEHOLDER_LOWERS = {"", "none", "null", "unknown", "no archetype fit", "no fit"}


def _clean_archetype_label(archetype: Optional[str]) -> Optional[str]:
    if isinstance(archetype, str) and archetype.strip().lower() not in _ARCHETYPE_PLACEHOLDER_LOWERS:
        return archetype.strip()
    return None


# ── Routes — static ───────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/builds")
def builds_page():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/my-builds")
def my_builds_page():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/overlay")
def overlay_page():
    return send_from_directory(app.static_folder, "overlay.html")


@app.route("/cards/<path:filename>")
def card_image(filename: str):
    if "/" in filename or "\\" in filename or ".." in filename:
        return ("", 404)
    path = CARD_IMAGE_DIR / filename
    if not path.is_file():
        return ("", 404)
    response = send_from_directory(CARD_IMAGE_DIR, filename)
    response.cache_control.max_age = 86400 * 30
    response.cache_control.public = True
    return response


# ── Routes — builds ───────────────────────────────────────────────────────────

@app.route("/api/builds/archetypes", defaults={"hero": None})
@app.route("/api/builds/archetypes/<hero>")
def api_builds_archetypes(hero: Optional[str]):
    if hero is None:
        conn = _conn()
        try:
            latest_run = conn.execute(
                "SELECT hero FROM runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
            hero = latest_run["hero"] if latest_run else None
        finally:
            conn.close()

    build_data, relevant_items = load_builds(hero)
    archetypes = []
    for phase, phase_data in build_data.get("game_phases", {}).items():
        for arch in phase_data.get("archetypes", []):
            archetypes.append({
                "name": arch["name"],
                "phase": phase,
                "core_items": arch.get("core_items", []),
                "carry_items": arch.get("carry_items", []),
                "support_items": arch.get("support_items", []),
                "condition": arch.get("condition"),
                "condition_items": condition_items_for_archetype(
                    arch, relevant_items=relevant_items,
                ),
                "notes": arch.get("notes"),
                "pivot_from": arch.get("pivot_from", []),
            })
    return jsonify({
        "hero": build_data.get("hero"),
        "archetypes": archetypes,
        "item_tier_list": build_data.get("item_tier_list", {}),
        "hero_notes": build_data.get("notes", ""),
        "pivot_signals": build_data.get("pivot_signals", {}).get("signals", []),
    })


@app.route("/api/builds/items/<hero>")
def api_builds_items(hero: str):
    """Return {item_name: '/cards/<filename>'} for every build-relevant item.

    Items missing from the manifest are omitted (not nulled). The overlay's
    lookup is dict.get(name), so absence and null fall through identically.
    """
    try:
        _build_data, relevant_items = load_builds(hero)
    except Exception as exc:
        print(f"[CardImages] load_builds failed for {hero!r}: {exc}")
        traceback.print_exc()
        return jsonify({
            "items": {},
            "error": {
                "code": "build_items_load_failed",
                "hero": hero,
                "message": str(exc),
            },
        }), 500

    result: dict[str, str] = {}
    for item_name in relevant_items or []:
        url = lookup_image_url(item_name)
        if url:
            result[item_name] = url
    return jsonify(result)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _build_catalog_notes() -> list[dict]:
    catalogs = []
    for hero_key in sorted(scorer.CATALOG_FILENAMES):
        hero = hero_key.title()
        source = scorer.catalog_source_status(hero)
        build_data = scorer.load_builds(hero)
        catalogs.append({
            "hero": build_data.get("hero") or hero,
            "filename": source["filename"],
            "source": source["source"],
            "last_updated": source.get("last_updated"),
            "season": build_data.get("season"),
            "notes": build_data.get("notes") or "",
        })
    return catalogs


def _build_refresh_status_payload() -> dict:
    with _build_refresh_lock:
        running = bool(_build_refresh_state["running"])
        last_result = _build_refresh_state["last_result"]
    return {
        "running": running,
        "last_result": last_result,
        "catalogs": _build_catalog_notes(),
    }


def _finish_build_refresh(trigger: str, payload: dict) -> None:
    payload = dict(payload)
    payload["trigger"] = trigger
    payload["checked_at"] = _utc_now_iso()
    with _build_refresh_lock:
        _build_refresh_state["running"] = False
        _build_refresh_state["last_result"] = payload


def _run_build_refresh(trigger: str) -> None:
    try:
        results = refresh_builds.refresh_builds()
        payload = refresh_builds.summarize_results(results)
    except Exception as exc:
        payload = {
            "ok": False,
            "status": "failed",
            "updated": 0,
            "unchanged": 0,
            "skipped": len(scorer.CATALOG_FILENAMES),
            "results": [],
            "error": str(exc),
        }
    _finish_build_refresh(trigger, payload)


def _start_build_refresh(trigger: str) -> bool:
    with _build_refresh_lock:
        if _build_refresh_state["running"]:
            return False
        _build_refresh_state["running"] = True
        _build_refresh_state["last_result"] = {
            "ok": None,
            "status": "checking",
            "updated": 0,
            "unchanged": 0,
            "skipped": 0,
            "results": [],
            "trigger": trigger,
            "checked_at": _utc_now_iso(),
        }
    threading.Thread(
        target=_run_build_refresh,
        args=(trigger,),
        daemon=True,
        name=f"build-refresh-{trigger}",
    ).start()
    return True


@app.route("/api/builds/refresh/status")
def api_builds_refresh_status():
    return jsonify(_build_refresh_status_payload())


@app.route("/api/builds/refresh", methods=["POST"])
def api_builds_refresh():
    _start_build_refresh("manual")
    return jsonify(_build_refresh_status_payload()), 202


# ── Routes — user build overrides ─────────────────────────────────────────────

def _atomic_write_user_builds_bytes(path: Path, content: bytes) -> None:
    """Atomically write ``content`` to ``path`` using a temp-file + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            temp_name = handle.name
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        temp_name = None
    finally:
        if temp_name:
            try:
                Path(temp_name).unlink(missing_ok=True)
            except OSError:
                pass


def _user_builds_skeleton(hero_display: str) -> dict:
    """Return a minimal valid user catalog skeleton for the given hero.

    Uses empty strings for season/last_updated so the catalog passes
    validate_builds_catalog (null is not accepted by the schema's oneOf/type).
    """
    return {
        "schema_version": 1,
        "hero": hero_display,
        "season": "",
        "last_updated": "",
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
                "archetypes": [],
            },
            "late": {
                "day_range": "Days 10+",
                "description": "",
                "archetypes": [],
            },
        },
    }


def _load_user_file(path: Path, hero_display: str) -> dict:
    """Load user builds file if it exists, otherwise return a fresh skeleton."""
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return _user_builds_skeleton(hero_display)


def _user_builds_hero_or_404(hero: str):
    """Normalize hero and return (hero_slug, hero_display, path) or None on unknown hero."""
    hero_slug = scorer.normalize_hero_name(hero).lower()
    if scorer._catalog_filename_or_none(hero_slug) is None:
        return None
    hero_display = hero_slug.title()
    path = app_paths.user_builds_path(hero_slug)
    return hero_slug, hero_display, path


@app.route("/api/builds/user/<hero>", methods=["GET"])
def api_user_builds_get(hero: str):
    """Return merged catalog with provenance metadata for the given hero."""
    try:
        resolved = _user_builds_hero_or_404(hero)
        if resolved is None:
            return jsonify({"ok": False, "errors": [f"unknown hero: {hero}"], "catalog": {}}), 404
        hero_slug, hero_display, user_path = resolved

        # Merged catalog (follows resolver precedence: user -> writable -> bundled)
        try:
            build_data, _relevant = load_builds(hero_slug)
        except Exception as exc:
            build_data = {}
            _ = exc  # non-fatal; provenance still reported

        # Provenance
        status = scorer.catalog_source_status(hero_slug)
        user_file_exists = user_path.exists()
        user_file_enabled = True
        user_archetype_names: list[str] = []
        if user_file_exists:
            try:
                raw = json.loads(user_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    user_file_enabled = raw.get("enabled", True) is not False
                    for phase_data in raw.get("game_phases", {}).values():
                        for arch in phase_data.get("archetypes", []):
                            name = arch.get("name")
                            if name:
                                user_archetype_names.append(name)
            except (json.JSONDecodeError, OSError):
                pass

        # Archetype names from the merged (non-user) tiers for conflict detection
        refreshed_archetype_names: list[str] = []
        if status.get("source") not in ("user_builds", "empty"):
            for phase_data in build_data.get("game_phases", {}).values():
                for arch in phase_data.get("archetypes", []):
                    name = arch.get("name")
                    if name and name not in refreshed_archetype_names:
                        refreshed_archetype_names.append(name)

        provenance = {
            "source": status.get("source"),
            "filename": status.get("filename"),
            "last_updated": status.get("last_updated"),
            "user_file_exists": user_file_exists,
            "user_file_enabled": user_file_enabled,
            "user_archetype_names": user_archetype_names,
            "refreshed_archetype_names": refreshed_archetype_names,
        }
        return jsonify({"ok": True, "catalog": build_data, "provenance": provenance, "errors": []})
    except Exception as exc:
        return jsonify({"ok": False, "errors": [str(exc)], "catalog": {}}), 500


@app.route("/api/builds/user/<hero>", methods=["PUT"])
def api_user_builds_put(hero: str):
    """Upsert a single archetype into the user catalog."""
    try:
        resolved = _user_builds_hero_or_404(hero)
        if resolved is None:
            return jsonify({"ok": False, "errors": [f"unknown hero: {hero}"], "catalog": {}}), 404
        hero_slug, hero_display, user_path = resolved

        body = request.get_json(silent=True) or {}
        archetype = body.get("archetype")
        if not isinstance(archetype, dict):
            return jsonify({"ok": False, "errors": ["body must contain 'archetype' dict"], "catalog": {}}), 400

        phase = archetype.get("phase")
        if phase not in ("early_mid", "late"):
            return jsonify({
                "ok": False,
                "errors": ["'phase' must be 'early_mid' or 'late' (early phase has a different schema)"],
                "catalog": {},
            }), 400

        # Strip the routing key 'phase' — not a schema archetype field
        arch_to_store = {k: v for k, v in archetype.items() if k != "phase"}

        data = _load_user_file(user_path, hero_display)
        # Ensure the phase exists
        game_phases = data.setdefault("game_phases", {})
        phase_obj = game_phases.setdefault(phase, {"day_range": "", "description": "", "archetypes": []})
        archetypes_list = phase_obj.setdefault("archetypes", [])

        arch_name = arch_to_store.get("name")
        replaced = False
        for i, existing in enumerate(archetypes_list):
            if existing.get("name") == arch_name:
                archetypes_list[i] = arch_to_store
                replaced = True
                break
        if not replaced:
            archetypes_list.append(arch_to_store)

        ok, err = scorer.validate_builds_catalog(data)
        if not ok:
            return jsonify({"ok": False, "errors": [err], "catalog": {}}), 400

        _atomic_write_user_builds_bytes(user_path, json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8"))
        invalidate_catalog_cache(hero_slug)
        return jsonify({"ok": True, "catalog": data, "errors": []})
    except Exception as exc:
        return jsonify({"ok": False, "errors": [str(exc)], "catalog": {}}), 500


@app.route("/api/builds/user/<hero>/<path:archetype_name>", methods=["DELETE"])
def api_user_builds_delete(hero: str, archetype_name: str):
    """Remove a single archetype by name from the user catalog."""
    try:
        resolved = _user_builds_hero_or_404(hero)
        if resolved is None:
            return jsonify({"ok": False, "errors": [f"unknown hero: {hero}"], "catalog": {}}), 404
        hero_slug, hero_display, user_path = resolved

        archetype_name = unquote(archetype_name)

        if not user_path.exists():
            return jsonify({"ok": False, "errors": [f"no user catalog for {hero_slug}"], "catalog": {}}), 404

        data = _load_user_file(user_path, hero_display)
        found = False
        for phase_data in data.get("game_phases", {}).values():
            archetypes = phase_data.get("archetypes")
            if not isinstance(archetypes, list):
                continue
            for i, arch in enumerate(archetypes):
                if arch.get("name") == archetype_name:
                    archetypes.pop(i)
                    found = True
                    break
            if found:
                break

        if not found:
            return jsonify({"ok": False, "errors": [f"archetype '{archetype_name}' not found"], "catalog": {}}), 404

        _atomic_write_user_builds_bytes(user_path, json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8"))
        invalidate_catalog_cache(hero_slug)
        return jsonify({"ok": True, "catalog": data, "errors": []})
    except Exception as exc:
        return jsonify({"ok": False, "errors": [str(exc)], "catalog": {}}), 500


@app.route("/api/builds/user/<hero>/disable", methods=["POST"])
def api_user_builds_disable(hero: str):
    """Set enabled=False on the user catalog (idempotent)."""
    try:
        resolved = _user_builds_hero_or_404(hero)
        if resolved is None:
            return jsonify({"ok": False, "errors": [f"unknown hero: {hero}"], "catalog": {}}), 404
        hero_slug, hero_display, user_path = resolved

        if not user_path.exists():
            # Nothing to disable — return ok immediately
            return jsonify({"ok": True, "enabled": False})

        data = _load_user_file(user_path, hero_display)
        data["enabled"] = False
        _atomic_write_user_builds_bytes(user_path, json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8"))
        invalidate_catalog_cache(hero_slug)
        return jsonify({"ok": True, "enabled": False})
    except Exception as exc:
        return jsonify({"ok": False, "errors": [str(exc)], "catalog": {}}), 500


@app.route("/api/builds/user/<hero>/enable", methods=["POST"])
def api_user_builds_enable(hero: str):
    """Set enabled=True on the user catalog (idempotent). Creates skeleton if absent."""
    try:
        resolved = _user_builds_hero_or_404(hero)
        if resolved is None:
            return jsonify({"ok": False, "errors": [f"unknown hero: {hero}"], "catalog": {}}), 404
        hero_slug, hero_display, user_path = resolved

        data = _load_user_file(user_path, hero_display)
        data["enabled"] = True
        _atomic_write_user_builds_bytes(user_path, json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8"))
        invalidate_catalog_cache(hero_slug)
        return jsonify({"ok": True, "enabled": True})
    except Exception as exc:
        return jsonify({"ok": False, "errors": [str(exc)], "catalog": {}}), 500


# ── Routes — overlay ──────────────────────────────────────────────────────────

@app.route("/api/overlay/state")
def api_overlay_state():
    conn = _conn()
    try:
        state = build_overlay_state(
            conn,
            resolve_fn=_resolve,
            safe_json_fn=db.safe_json,
            lookup_image_by_name_fn=lookup_image_url,
        )
        if "error" in state:
            if state.get("error") == "No runs found":
                return jsonify({"state": "no_runs"})
            return jsonify(state), 404
        return jsonify(state)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


# ── Routes — runs ─────────────────────────────────────────────────────────────

@app.route("/api/runs")
def api_runs():
    conn = _conn()
    try:
        runs = conn.execute("""
            SELECT r.*, COUNT(d.id) as decision_count
            FROM runs r
            LEFT JOIN decisions d ON d.run_id = r.id
            GROUP BY r.id
            ORDER BY r.id DESC
            LIMIT 30
        """).fetchall()

        if not runs:
            return jsonify([])

        run_dicts = [dict(r) for r in runs]
        run_ids = [r["id"] for r in run_dicts]
        placeholders = ",".join("?" * len(run_ids))

        # --- Chunk 1: COMMITTED archetype scan in one pass ---
        committed_rows = conn.execute(f"""
            SELECT run_id, decision_seq, score_notes
            FROM decisions
            WHERE run_id IN ({placeholders}) AND score_notes LIKE '%COMMITTED%'
            ORDER BY run_id, decision_seq
        """, run_ids).fetchall()
        # Keep only the first committed row per run.
        first_committed: dict[int, str] = {}
        for cr in committed_rows:
            if cr["run_id"] not in first_committed:
                first_committed[cr["run_id"]] = cr["score_notes"]

        result = []
        for r in run_dicts:
            rid = r["id"]
            build_data, _relevant_items = load_builds(r.get("hero"))

            run_record = _get_run_record(conn, r)
            pvp_w = run_record["pvp_wins"]
            pvp_l = run_record["pvp_losses"]
            pve_w = run_record["pve_wins"]
            pve_l = run_record["pve_losses"]

            archetype = None
            notes = first_committed.get(rid)
            if notes:
                m = re.search(r'COMMITTED to ([\w\s\-]+?)(?:\s*\(|\.)', notes)
                if m:
                    archetype = _clean_archetype_label(m.group(1))
            if not archetype:
                inferred_name, _ = infer_archetype_from_decisions(
                    conn, rid, build_data=build_data, resolve_fn=_resolve,
                )
                archetype = _clean_archetype_label(inferred_name)

            result.append({
                "id": rid, "hero": r["hero"], "outcome": r["outcome"],
                "started_at": r["started_at"], "ended_at": r.get("ended_at"),
                "pvp_wins": pvp_w, "pvp_losses": pvp_l,
                "pve_wins": pve_w, "pve_losses": pve_l,
                "decision_count": r["decision_count"], "archetype": archetype,
            })
        return jsonify(result)
    finally:
        conn.close()


@app.route("/api/runs/<int:run_id>/decisions")
def api_decisions(run_id: int):
    conn = _conn()
    try:
        decisions = conn.execute(
            "SELECT * FROM decisions WHERE run_id=? ORDER BY decision_seq", (run_id,)
        ).fetchall()
        result = [
            format_decision_row(
                dict(d),
                resolve_fn=lambda tid: _resolve(conn, tid),
                get_tier_fn=lambda tid: _get_tier(conn, tid),
                safe_json_fn=db.safe_json,
                resolve_instance_ids_fn=lambda ids: _resolve_instance_ids_via_api_cards(conn, ids),
                is_unresolved_fn=is_unresolved,
                resolve_image_fn=lambda tid: _resolve_image(conn, tid),
            )
            for d in decisions
        ]
        return jsonify(result)
    finally:
        conn.close()


@app.route("/api/runs/<int:run_id>/combats")
def api_combats(run_id: int):
    conn = _conn()
    try:
        combats = conn.execute("""
            SELECT outcome, combat_type, duration_secs, timestamp
            FROM combat_results WHERE run_id=? ORDER BY id
        """, (run_id,)).fetchall()
        return jsonify(_rows_to_dicts(combats))
    finally:
        conn.close()


@app.route("/api/runs/<int:run_id>/summary")
def api_summary(run_id: int):
    conn = _conn()
    try:
        result = build_run_summary(conn, run_id, resolve_fn=_resolve)
        if "error" in result:
            return jsonify(result), 404
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


@app.route("/api/status")
def api_status():
    db_path = _get_db_path()
    return jsonify({
        "db_exists": db_path.exists(),
        "db_path": str(db_path),
        "db_size_mb": round(db_path.stat().st_size / 1024 / 1024, 2) if db_path.exists() else 0,
    })


@app.route("/api/content/status")
def api_content_status():
    return jsonify(card_cache.content_status())


@app.route("/api/setup/status")
def api_setup_status():
    return jsonify(first_run.setup_status())


def _update_check_stale(last_check: Optional[dict], interval_hours: object) -> bool:
    if not isinstance(last_check, dict):
        return True
    try:
        interval = float(interval_hours)
    except (TypeError, ValueError):
        interval = 24.0
    if interval <= 0:
        return False
    checked_at = last_check.get("checked_at")
    if not checked_at:
        return True
    try:
        checked = datetime.fromisoformat(str(checked_at).replace("Z", "+00:00"))
    except ValueError:
        return True
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - checked) >= timedelta(hours=interval)


def _with_update_handoff_state(payload: dict) -> dict:
    out = dict(payload or {})
    try:
        import settings

        settings.load()
        out["last_download"] = settings.get("updates.last_download")
        out["last_install"] = settings.get("updates.last_install")
    except Exception:
        out["last_download"] = None
        out["last_install"] = None
    try:
        import app_paths

        out["install_launch_available"] = app_paths.is_packaged()
    except Exception:
        out["install_launch_available"] = False
    try:
        out = update_checker.enrich_update_handoff_state(out)
    except Exception:
        out["installed_exe_path"] = None
        out["relaunch_available"] = False
        out["relaunch_after_install"] = False
    return out


def _with_last_update_download(payload: dict) -> dict:
    return _with_update_handoff_state(payload)


@app.route("/api/updates/status")
def api_updates_status():
    force = request.args.get("force") in {"1", "true", "yes"}
    try:
        import settings

        settings.load()
        last_check = settings.get("updates.last_check")
        interval_hours = settings.get("updates.check_interval_hours", 24)
    except Exception:
        last_check = None
        interval_hours = 24
    stale = _update_check_stale(last_check, interval_hours)
    if force or not last_check or stale:
        try:
            return jsonify(_with_last_update_download(update_checker.check_for_updates(persist=True)))
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)})
    return jsonify(_with_last_update_download(last_check))


@app.route("/api/updates/dismiss", methods=["POST"])
def api_updates_dismiss():
    payload = request.get_json(silent=True) or {}
    version = payload.get("version")
    if not version:
        current = update_checker.check_for_updates(persist=False)
        version = current.get("latest_version")
    if not version:
        return jsonify({"ok": False, "error": "no version to dismiss"}), 400
    return jsonify(update_checker.dismiss_update(str(version)))


@app.route("/api/updates/download", methods=["POST"])
def api_updates_download():
    payload = request.get_json(silent=True) or {}
    manifest = payload if payload.get("download_url") else None
    prefer_portable = payload.get("prefer_portable")
    if prefer_portable not in (True, False):
        prefer_portable = None
    if manifest is None:
        try:
            import settings

            settings.load()
            manifest = settings.get("updates.last_check") or {}
        except Exception:
            manifest = {}
    result = update_checker.download_update(
        manifest=manifest,
        prefer_portable=prefer_portable,
        persist=True,
    )
    status_code = 200 if result.get("ok") else 400
    return jsonify(result), status_code


@app.route("/api/updates/reveal-installer", methods=["POST"])
def api_updates_reveal_installer():
    result = update_checker.reveal_downloaded_installer()
    status_code = 200 if result.get("ok") else 400
    return jsonify(result), status_code


@app.route("/api/updates/install", methods=["POST"])
def api_updates_install():
    payload = request.get_json(silent=True) or {}
    shutdown_first = payload.get("shutdown_first", True)
    if shutdown_first not in (True, False):
        shutdown_first = bool(shutdown_first)
    if "install_silent" in payload:
        try:
            import settings

            settings.load()
            settings.set("updates.install_silent", bool(payload.get("install_silent")))
            settings.save()
        except Exception:
            pass
    try:
        import settings

        settings.load()
        manifest = settings.get("updates.last_check") or {}
    except Exception:
        manifest = {}
    blocked = update_checker.upgrade_blocked_reason(manifest)
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 400
    shutdown_cb = _shutdown_callback if shutdown_first else None
    result = update_checker.launch_downloaded_installer(
        shutdown_first=shutdown_first,
        shutdown_callback=shutdown_cb,
    )
    status_code = 200 if result.get("ok") else 400
    return jsonify(result), status_code


@app.route("/api/updates/apply-portable", methods=["POST"])
def api_updates_apply_portable():
    payload = request.get_json(silent=True) or {}
    shutdown_first = payload.get("shutdown_first", True)
    if shutdown_first not in (True, False):
        shutdown_first = bool(shutdown_first)
    try:
        import settings

        settings.load()
        manifest = settings.get("updates.last_check") or settings.get("updates.last_download") or {}
    except Exception:
        manifest = {}
    blocked = update_checker.upgrade_blocked_reason(manifest)
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 400
    shutdown_cb = _shutdown_callback if shutdown_first else None
    result = update_checker.apply_portable_update(
        shutdown_first=shutdown_first,
        shutdown_callback=shutdown_cb,
    )
    status_code = 200 if result.get("ok") else 400
    return jsonify(result), status_code


@app.route("/api/updates/preferences", methods=["POST"])
def api_updates_preferences():
    payload = request.get_json(silent=True) or {}
    try:
        import settings

        settings.load()
        if "download_on_check" in payload:
            settings.set("updates.download_on_check", bool(payload.get("download_on_check")))
        if "install_on_quit" in payload:
            settings.set("updates.install_on_quit", bool(payload.get("install_on_quit")))
        settings.save()
        return jsonify({
            "ok": True,
            "download_on_check": settings.get("updates.download_on_check", False),
            "install_on_quit": settings.get("updates.install_on_quit", False),
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/updates/relaunch", methods=["POST"])
def api_updates_relaunch():
    payload = request.get_json(silent=True) or {}
    target_version = payload.get("target_version")
    if not target_version:
        try:
            import settings

            settings.load()
            target_version = update_checker._resolve_relaunch_target_version(
                last_install=settings.get("updates.last_install"),
                last_download=settings.get("updates.last_download"),
                last_check=settings.get("updates.last_check"),
            )
        except Exception:
            target_version = None
    if not target_version:
        return jsonify({"ok": False, "error": "no target_version to relaunch"}), 400
    result = update_checker.relaunch_installed_coach(str(target_version))
    status_code = 200 if result.get("ok") else 400
    return jsonify(result), status_code


# ── Routes — control ──────────────────────────────────────────────────────────

@app.route("/api/control/shutdown", methods=["POST"])
def api_control_shutdown():
    """
    Graceful shutdown trigger endpoint.
    
    Called by the overlay's Quit button or can be called from curl/external clients.
    Dispatches the shutdown event on a daemon thread so the HTTP response can be sent
    before the server begins to shut down.
    
    Returns:
        JSON response with status.
    """
    if _shutdown_callback:
        threading.Thread(target=_shutdown_callback, daemon=True).start()
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "no shutdown handler registered"}), 500


@app.route("/api/runs/<int:run_id>/force-end", methods=["POST"])
def api_force_end_run(run_id: int):
    """
    Manually end an active run from the overlay (issue #84).

    Used when the game crashes, the player Alt-F4s mid-run, or the Mono
    capture misses the terminal EndRun event, leaving the overlay stuck on
    a live run with no terminal event ever arriving. The overlay's existing
    "Leave Run" dismiss button is gated on is_active === false, so without
    this route there is no recoverable path back to idle.

    Behavior:
      - 404 if no runs row matches run_id.
      - 200 {"ok": True, "already_ended": True} if outcome IS NOT NULL
        (idempotent; the second click after a successful close is harmless).
      - Otherwise calls the registered force-end callback (signature
        (run_id, ts_iso) -> bool) so RunState's in-memory _run_closed flag
        flips together with the DB row. If no callback is registered (e.g.
        the dashboard is launched without the watcher), falls back to a
        direct db.close_run + db.flush so the route is still useful in
        diagnostic mode. The fallback case includes "fallback": True in
        the response so callers can tell which path ran.
    """
    ts_iso = datetime.now(timezone.utc).isoformat()

    conn = _conn()
    try:
        row = conn.execute(
            "SELECT id, outcome FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return jsonify({"ok": False, "error": "run not found"}), 404
    if row["outcome"] is not None:
        return jsonify({"ok": True, "already_ended": True, "outcome": row["outcome"]})

    if _force_end_callback is not None:
        try:
            closed = bool(_force_end_callback(run_id, ts_iso))
        except Exception as exc:
            traceback.print_exc()
            return jsonify({"ok": False, "error": f"callback failed: {exc}"}), 500
        if closed:
            return jsonify({"ok": True, "outcome": "force_ended", "ts": ts_iso})
        # Callback declined (e.g. the watcher's RunState has moved on to a
        # different run since the overlay rendered). Fall through to the
        # direct-DB path so the user's intent still lands.

    db.close_run(run_id, ts_iso, "force_ended")
    db.flush()
    return jsonify({
        "ok": True,
        "outcome": "force_ended",
        "ts": ts_iso,
        "fallback": True,
    })


# ── Server lifecycle ──────────────────────────────────────────────────────────

def set_shutdown_callback(cb: Callable) -> None:
    """
    Register a callback to be invoked when /api/control/shutdown is posted.

    Args:
        cb: A callable that initiates shutdown (e.g., shutdown_event.set).
    """
    global _shutdown_callback
    _shutdown_callback = cb


def set_force_end_callback(cb: Optional[Callable[[int, str], bool]]) -> None:
    """
    Register the callback that /api/runs/<id>/force-end will invoke.

    The watcher registers a lambda that delegates to RunState.force_end so
    the in-memory run state and the DB row close in lock-step. Pass None
    to clear (used by tests for isolation).
    """
    global _force_end_callback
    _force_end_callback = cb


def _run_production_server(port: int):
    try:
        from waitress import serve
    except ImportError:
        print("[Web] waitress not installed; falling back to Flask dev server.")
        app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)
        return
    serve(app, host="127.0.0.1", port=port, threads=8, _quiet=True)


def start_web_server(port=DEFAULT_PORT, db_path=None, background=True, auto_refresh_builds=True):
    global DB_PATH
    if db_path:
        DB_PATH = Path(db_path)
    if auto_refresh_builds:
        _start_build_refresh("startup")
    if background:
        t = threading.Thread(target=lambda: _run_production_server(port), daemon=True, name="web-server")
        t.start()
        print(f"[Web] Dashboard running at http://127.0.0.1:{port}")
        return t
    else:
        print(f"[Web] Dashboard running at http://127.0.0.1:{port}")
        _run_production_server(port)


def main():
    parser = argparse.ArgumentParser(description="Bazaar Coach Web Dashboard")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--db", type=str, default=None, help="Path to bazaar_runs.db")
    parser.add_argument("--no-refresh-builds", action="store_true",
                        help="Do not refresh build catalogs when the web server starts")
    args = parser.parse_args()
    start_web_server(
        port=args.port,
        db_path=args.db,
        background=False,
        auto_refresh_builds=not args.no_refresh_builds,
    )


if __name__ == "__main__":
    main()
