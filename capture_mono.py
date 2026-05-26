# -*- coding: utf-8 -*-
# Debugging notes (dict layouts, mid-run pickup gaps, FAST_GAMESIM_PATH):
# see docs/mono-internals.md.
"""
capture_mono.py â€” Frida Mono hook for The Bazaar's managed GameStateHandler.

WHY THIS APPROACH
=================
All prior capture attempts hit the same wall: Unity's bundled TLS library
doesn't export SSL_read/SSL_write, the game uses IPv6 + Cloudflare, and
the localhost internal tunnel means Winsock/Schannel hooks only see
encrypted traffic on the wrong side of the pipe.

This script skips the network layer entirely. The Bazaar runs on Unity
with Mono (confirmed: mono-2.0-bdwgc.dll is loaded). We call the Mono C
API directly via NativeFunction to find the managed GameStateHandler class,
then hook the method that processes NetMessageGameStateSync. When the game
receives a server response, our hook fires with the fully deserialized
GameStateSnapshotDTO already in managed memory. We read its fields and
send structured JSON back to Python.

No proxy, no cert, no TLS decryption, no kernel driver, no admin rights.

REQUIREMENTS
============
  pip install frida frida-tools
  The game must be running (or use --wait).

USAGE
=====
  python capture_mono.py                     # attach to running game
  python capture_mono.py --wait              # wait for game to launch
  python capture_mono.py --log               # save captures to disk
  python capture_mono.py --log --db          # save + write to SQLite
"""

import argparse
import datetime
import hashlib
import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

CAPTURES_DIR = Path(__file__).parent / "captures"

# Frida Mono agent - uses native Mono C API via NativeFunction
FRIDA_MONO_AGENT = (Path(__file__).parent / "capture_mono_agent.js").read_text(encoding="utf-8")


_output_dir = None
_do_log = False
_do_db = False
_log_file = None
_snapshot_count = 0
_probe_hits = {}
_capture_calls = {}
_seen_snapshot_keys = set()
_duplicate_snapshot_count = 0
_last_merged_snapshot = None
_pending_snapshot_db_by_key: dict[str, dict] = {}
_pending_snapshot_db_keys: set[str] = set()
_coalesced_snapshot_db_updates = 0
_db_queue = None
_db_thread = None
_api_log_module = None
_CARD_LIST_KEYS = (
    "offered",
    "player_board",
    "player_stash",
    "player_skills",
    "opponent_board",
)
_PERSISTENT_CARD_KEYS = (
    "player_board",
    "player_stash",
    "player_skills",
    "opponent_board",
)
_VERBOSE_DEBUG = False
_VERBOSE_HOOKS = False
_RENDER_ALL_SNAPSHOTS = False
_DETAILED_SNAPSHOTS = False
_FULL_DELTA_CARDS = False
# Snapshot printing is compact-by-default (one line) to keep the console
# cheap during bursty GameSim deltas. Use --verbose-snapshots for the
# legacy multi-line block. _SNAPSHOT_PRINT_MIN_INTERVAL_MS rate-limits
# the verbose path so a burst of identical snapshots only emits one
# block per interval.
_COMPACT_SNAPSHOTS = True
_SNAPSHOT_PRINT_MIN_INTERVAL_MS = 500.0
_last_snapshot_print_ms: float = 0.0
_snapshot_prints_suppressed: int = 0
_DELTA_PLAYER_ATTRS = True
# Leave action-time card decoding off by default. It is useful for debugging
# inferred move/buy/sell coverage, but it adds extra GameSim work during the
# exact click paths where hitching is most noticeable (sell / event choice).
_ACTION_EVENT_CARDS = False
# Opponent board is needed for PvP retrospective scoring — capture by default.
_CAPTURE_OPPONENT_BOARD = True
# ENABLE_PROBES kept disabled (noisy, not needed)
_ENABLE_PROBES = False
# ENABLE_BROAD_HOOKS kept disabled (narrow hooks sufficient)
_ENABLE_BROAD_HOOKS = False
_rendered_snapshot_keys = set()
_mono_db_conn = None
_event_template_ids_by_instance: dict = {}
# F1: deferred card data keyed by snapshot_id â€” merged into snapshots when the deferred message arrives
_deferred_cards_by_snapshot_id: dict = {}
_deferred_template_events_by_snapshot_id: dict = {}
# Deferred Player.Attributes (mirrors deferred_cards): JS agent enumerates the
# managed Attributes dict off the game thread and ships the decoded key-value
# map here. Stored by snapshot_id so _merge_partial_snapshot can pick it up if
# it arrived before the snapshot; also merged in-place into _last_merged_snapshot
# on late arrival so the persisted row reflects the attrs.
_deferred_attrs_by_snapshot_id: dict = {}
_deferred_attrs_pickup_count = 0       # applied via _merge_partial_snapshot pickup (attrs arrived first)
_deferred_attrs_late_arrival_count = 0  # merged into _last_merged_snapshot after it was persisted
_deferred_attrs_dropped_count = 0      # dropped due to cap eviction or empty payload
_deferred_attrs_last_stat_log_ms = 0.0
_DEFERRED_ATTRS_STAT_INTERVAL_MS = 60000.0
_SLOW_HOOK_LOG_THRESHOLD_MS = 8.0


def _get_mono_conn():
    """Return a reusable SQLite connection for the mono-db-writer thread."""
    global _mono_db_conn
    if _mono_db_conn is None:
        import sqlite3
        import app_paths
        _mono_db_conn = sqlite3.connect(
            app_paths.db_path(),
            timeout=30.0,
        )
        _mono_db_conn.row_factory = sqlite3.Row
        _mono_db_conn.execute("PRAGMA journal_mode=WAL")
        _mono_db_conn.execute("PRAGMA synchronous=NORMAL")
        _mono_db_conn.execute("PRAGMA foreign_keys=ON")
        _mono_db_conn.execute("PRAGMA busy_timeout=30000")
    return _mono_db_conn


_INTERESTING_RENDER_STATES = {
    "Choice",
    "Loot",
    "LevelUp",
    "Pedestal",
    "EndRunVictory",
    "EndRunDefeat",
}

_INFO_SUPPRESS_PREFIXES = (
    "Found ",
    "Images:",
    "GameStateHandler methods",
    "GameStateHandler fields",
    "Hooking ",
    "Attached ",
    "Scanning ",
    "Global scan checked ",
)

_DEBUG_ALLOW_SUBSTRINGS = (
    "was null",
    "readObjectField",
    "readSnapshot:",
    "readPlayer:",
    "readCard:",
    "readState:",
    "readRun:",
    "HashSet _slots not found",
)


def _should_print_info(msg: str) -> bool:
    return not any(msg.startswith(prefix) for prefix in _INFO_SUPPRESS_PREFIXES)


def _should_print_debug(msg: str) -> bool:
    return _VERBOSE_DEBUG and any(token in msg for token in _DEBUG_ALLOW_SUBSTRINGS)


def _should_render_snapshot(gs: dict) -> bool:
    if _RENDER_ALL_SNAPSHOTS:
        return True
    if _snapshot_count == 0:
        return True

    state = gs.get("state", {})
    state_name = state.get("state")
    return state_name in _INTERESTING_RENDER_STATES


def _render_signature(gs: dict) -> str:
    run = gs.get("run", {})
    state = gs.get("state", {})
    player = gs.get("player", {})
    return json.dumps(
        {
            "state": state.get("state"),
            "day": run.get("day"),
            "hour": run.get("hour"),
            "gold": player.get("Gold"),
            "hp": player.get("Health"),
            "hp_max": player.get("HealthMax"),
            "prestige": player.get("Prestige"),
            "wins": run.get("victories"),
            "losses": run.get("defeats"),
            "selection_set": _normalized_selection(state.get("selection_set")),
            "offered_count": len(gs.get("offered", [])),
            "board_count": len(gs.get("player_board", [])),
            "skills_count": len(gs.get("player_skills", [])),
            "opponent_count": len(gs.get("opponent_board", [])),
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _normalized_selection(values):
    if not values:
        return []
    normalized = []
    for value in values:
        if value in (None, "", "None"):
            continue
        if isinstance(value, str):
            candidate = value.strip()
            if not candidate:
                continue
            has_ascii_identifier = any(ch.isascii() and ch.isalnum() for ch in candidate)
            has_non_ascii = any(not ch.isascii() for ch in candidate)
            if has_non_ascii and not has_ascii_identifier:
                continue
            normalized.append(candidate)
        else:
            normalized.append(value)
    return normalized


def _perf_now_ms() -> float:
    return time.perf_counter() * 1000.0


def _log_hook_perf(payload: dict):
    if payload.get("stage") != "hook":
        return
    try:
        duration_ms = float(payload.get("hook_duration"))
    except Exception:
        return
    if duration_ms < _SLOW_HOOK_LOG_THRESHOLD_MS:
        return
    fields = {
        "hook": payload.get("hook"),
        "call_count": payload.get("call_count"),
        "status": payload.get("status"),
    }
    detail_text = " ".join(
        f"{key}={value}" for key, value in fields.items() if value not in (None, "", [], {})
    )
    suffix = f" | {detail_text}" if detail_text else ""
    print(f"[MonoPerf] slow hook: {duration_ms:.1f} ms{suffix}")


def _prune_disabled_snapshot_cards(gs: dict) -> dict:
    """Drop snapshot sections that are intentionally disabled in this run."""
    if not _CAPTURE_OPPONENT_BOARD:
        gs["opponent_board"] = []
    return gs


def on_message(message, data):
    """Handle messages from the Frida Mono agent.

    IMPORTANT: This callback runs on Frida's message-pump thread.  Blocking
    here back-pressures the agent's send() calls, which in turn stalls the
    game thread inside the hooked method.  Keep this as thin as possible â€”
    heavy work (snapshot processing, action inference, DB/file I/O) is
    dispatched to _db_queue for the background worker.
    """
    if message["type"] == "send":
        payload = message["payload"]
        msg_type = payload.get("type", "")

        if msg_type == "info":
            msg = payload["msg"]
            if _should_print_info(msg):
                print(f"[Mono] {msg}")
        elif msg_type == "error":
            print(f"[Mono] ERROR: {payload['msg']}")
        elif msg_type == "debug":
            msg = payload["msg"]
            if _should_print_debug(msg):
                print(f"[Mono] DEBUG: {msg}")
        elif msg_type == "ready":
            print(f"[Mono] {payload['msg']}")
        elif msg_type == "probe":
            handle_probe(payload)
        elif msg_type == "capture_call":
            handle_capture_call(payload)
        elif msg_type == "perf":
            _log_hook_perf(payload)
        elif msg_type == "batch":
            # Batched items from a single hook invocation â€” dispatch each.
            for item in payload.get("items", []):
                _dispatch_item(item)
        elif msg_type == "game_state":
            _dispatch_item(payload)
        elif msg_type == "deferred_cards":
            # F1: deferred card data arrives after the snapshot â€” merge on background thread
            if _db_queue is not None:
                _db_queue.put(("deferred_cards", payload))
            else:
                handle_deferred_cards(payload)
        elif msg_type == "deferred_template_events":
            if _db_queue is not None:
                _db_queue.put(("deferred_template_events", payload))
            else:
                handle_deferred_template_events(payload)
        elif msg_type == "deferred_player_attrs":
            if _db_queue is not None:
                _db_queue.put(("deferred_player_attrs", payload))
            else:
                handle_deferred_player_attrs(payload)

    elif message["type"] == "error":
        print(f"[Mono] Script error: {message.get('description', message)}")


def _dispatch_item(item):
    """Route a single agent message to the background worker queue.

    For game_state, we push onto the queue so processing happens off the
    Frida message-pump thread.  Lightweight message types (probe,
    capture_call, info, etc.) are still handled inline.
    """
    msg_type = item.get("type", "")
    data = item.get("data", {}) or {}
    if msg_type == "game_state":
        if _db_queue is not None:
            _db_queue.put(("process_snapshot", data))
        else:
            # Fallback: process inline if queue not started (no --log/--db)
            handle_game_state(data)


def handle_probe(payload):
    """Track which GameStateHandler methods fire during gameplay."""
    method = payload.get("method", "?")
    _probe_hits[method] = _probe_hits.get(method, 0) + 1
    count = _probe_hits[method]
    if count <= 3:
        print(f"[Probe] GameStateHandler.{method}() fired (#{count})")
    elif count == 4:
        print(f"[Probe] GameStateHandler.{method}() fired (suppressing further...)")


def handle_capture_call(payload):
    """Track how often the hooked capture method fires and whether extraction succeeded."""
    method = payload.get("method", "?")
    count = int(payload.get("count", 0) or 0)
    status = payload.get("status", "?")
    _capture_calls[method] = count
    if _VERBOSE_HOOKS:
        print(f"[Capture] {method} call #{count} -> {status}")


def handle_deferred_cards(payload):
    """F1: Merge deferred card data (from setImmediate) into the matching snapshot.

    The JS agent defers heavy card collection decoding off the game thread via
    setImmediate. When the decoded cards arrive here, store them in
    _deferred_cards_by_snapshot_id so that _merge_partial_snapshot (or
    handle_game_state) can pick them up. We also try to merge into
    _last_merged_snapshot if the IDs match.
    """
    global _deferred_cards_by_snapshot_id, _last_merged_snapshot
    snapshot_id = payload.get("snapshot_id")
    cards = payload.get("cards") or {}
    if not snapshot_id or not cards:
        return
    _prune_disabled_snapshot_cards(cards)
    # Store for future merges
    _deferred_cards_by_snapshot_id[snapshot_id] = cards
    # Cap size to avoid unbounded growth (keep last 32 snapshots)
    if len(_deferred_cards_by_snapshot_id) > 32:
        oldest = min(_deferred_cards_by_snapshot_id.keys())
        del _deferred_cards_by_snapshot_id[oldest]
    # If the matching snapshot is already in _last_merged_snapshot, merge now
    if _last_merged_snapshot and _last_merged_snapshot.get("id") == snapshot_id:
        for key in _CARD_LIST_KEYS:
            if cards.get(key):
                _last_merged_snapshot[key] = [dict(c) for c in cards[key]]
        _apply_event_template_recovery(_last_merged_snapshot)
        # Persist updated snapshot with card data
        if _do_log or _do_db:
            persist_snapshot(_last_merged_snapshot)
        if _VERBOSE_HOOKS:
            total = sum(len(cards.get(k, [])) for k in _CARD_LIST_KEYS)
            print(f"[Mono] Deferred cards merged into snapshot #{snapshot_id}: {total} cards")


def handle_deferred_template_events(payload):
    """Merge deferred GameSim template events into the matching snapshot."""
    global _deferred_template_events_by_snapshot_id, _last_merged_snapshot
    snapshot_id = payload.get("snapshot_id")
    template_events = payload.get("card_template_events") or []
    if not snapshot_id or not template_events:
        return

    _deferred_template_events_by_snapshot_id[snapshot_id] = list(template_events)
    if len(_deferred_template_events_by_snapshot_id) > 64:
        oldest = min(_deferred_template_events_by_snapshot_id.keys())
        del _deferred_template_events_by_snapshot_id[oldest]

    if _last_merged_snapshot and _last_merged_snapshot.get("id") == snapshot_id:
        _last_merged_snapshot["card_template_events"] = list(template_events)
        _apply_event_template_recovery(_last_merged_snapshot)
        if _do_log or _do_db:
            persist_snapshot(_last_merged_snapshot)
        if _VERBOSE_HOOKS:
            print(
                f"[Mono] Deferred template events merged into snapshot #{snapshot_id}: "
                f"{len(template_events)} events"
            )


def handle_deferred_player_attrs(payload):
    """Merge deferred Player.Attributes (from setImmediate) into the matching snapshot.

    Mirrors handle_deferred_cards: store by snapshot_id so _merge_partial_snapshot
    can pick it up if it arrives before the snapshot, and also merge in-place
    into _last_merged_snapshot + re-persist on late arrival.
    """
    global _deferred_attrs_by_snapshot_id, _last_merged_snapshot
    global _deferred_attrs_late_arrival_count, _deferred_attrs_dropped_count
    snapshot_id = payload.get("snapshot_id")
    attrs = payload.get("attrs") or {}
    if not snapshot_id or not attrs:
        _deferred_attrs_dropped_count += 1
        _maybe_log_deferred_attrs_stats()
        return

    _deferred_attrs_by_snapshot_id[snapshot_id] = attrs
    if len(_deferred_attrs_by_snapshot_id) > 128:
        oldest = min(_deferred_attrs_by_snapshot_id.keys())
        del _deferred_attrs_by_snapshot_id[oldest]
        _deferred_attrs_dropped_count += 1

    if _last_merged_snapshot and _last_merged_snapshot.get("id") == snapshot_id:
        player = _last_merged_snapshot.setdefault("player", {})
        for k, v in attrs.items():
            player[k] = v
        _deferred_attrs_late_arrival_count += 1
        if _do_log or _do_db:
            persist_snapshot(_last_merged_snapshot)
        if _VERBOSE_HOOKS:
            print(
                f"[Mono] Deferred player attrs merged (late) into snapshot #{snapshot_id}: "
                f"{len(attrs)} attrs"
            )
    _maybe_log_deferred_attrs_stats()


def _maybe_log_deferred_attrs_stats():
    """Emit a periodic summary so broken deferred-attrs flow is visible in logs.

    Signals to watch:
      - pickup=0 and late=0 → feature not wiring up (messages arriving but no merges).
      - dropped growing fast → attrs piling up without matching snapshots.
      - pending dict size near cap → eviction is stealing attrs before snapshots arrive.
    """
    global _deferred_attrs_last_stat_log_ms
    now_ms = _perf_now_ms()
    if now_ms - _deferred_attrs_last_stat_log_ms < _DEFERRED_ATTRS_STAT_INTERVAL_MS:
        return
    _deferred_attrs_last_stat_log_ms = now_ms
    pending = len(_deferred_attrs_by_snapshot_id)
    total_applied = _deferred_attrs_pickup_count + _deferred_attrs_late_arrival_count
    if total_applied == 0 and _deferred_attrs_dropped_count == 0 and pending == 0:
        return
    print(
        f"[Mono] deferred_player_attrs stats: "
        f"pickup={_deferred_attrs_pickup_count} "
        f"late_arrival={_deferred_attrs_late_arrival_count} "
        f"dropped={_deferred_attrs_dropped_count} "
        f"pending={pending}"
    )


def _snapshot_dedupe_key(gs):
    """Return a stable dedupe key for a game-state message."""
    message_id = gs.get("message_id")
    if message_id:
        return f"msg:{message_id}"

    canonical = {
        "run": gs.get("run", {}),
        "state": gs.get("state", {}),
        "player": gs.get("player", {}),
        "offered": gs.get("offered", []),
        "player_board": gs.get("player_board", []),
        "player_stash": gs.get("player_stash", []),
        "player_skills": gs.get("player_skills", []),
        "opponent_board": gs.get("opponent_board", []),
    }
    digest = hashlib.sha1(
        json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    return f"sha1:{digest}"


def _snapshot_db_queue_key(gs: dict) -> str | None:
    """Return a stable key so repeated DB writes for one snapshot can coalesce."""
    if not isinstance(gs, dict):
        return None
    snap_id = gs.get("id")
    if snap_id is not None:
        return f"id:{snap_id}"
    message_id = gs.get("message_id")
    if message_id:
        return f"msg:{message_id}"
    return _snapshot_dedupe_key(gs)


def _merge_partial_snapshot(gs):
    """Overlay dynamic partial updates onto the most recent captured state."""
    global _last_merged_snapshot, _deferred_attrs_pickup_count

    merged = {
        **gs,
        "run": dict(gs.get("run", {})),
        "state": dict(gs.get("state", {})),
        "player": dict(gs.get("player", {})),
    }
    for key in _CARD_LIST_KEYS:
        merged[key] = [dict(card) for card in gs.get(key, [])]

    if "dynamic-data" in str(merged.get("hook_source", "")) and _last_merged_snapshot:
        prev = _last_merged_snapshot
        merged["run"] = {**prev.get("run", {}), **merged.get("run", {})}
        merged["state"] = {**prev.get("state", {}), **merged.get("state", {})}
        merged["player"] = {**prev.get("player", {}), **merged.get("player", {})}
        prev_hp = prev.get("player", {}).get("Health")
        prev_hp_max = prev.get("player", {}).get("HealthMax")
        curr_hp = gs.get("player", {}).get("Health")
        curr_hp_max = gs.get("player", {}).get("HealthMax")

        # Some dynamic GameSim snapshots regress HP to the hero's baseline
        # 300/300 even after health has scaled up for the run. Preserve the
        # richer prior values instead of letting the baseline overwrite them.
        if (
            prev_hp_max not in (None, 0)
            and curr_hp == 300
            and curr_hp_max == 300
            and isinstance(prev_hp_max, (int, float))
            and prev_hp_max > 300
        ):
            merged["player"]["Health"] = prev_hp
            merged["player"]["HealthMax"] = prev_hp_max
        for key in _PERSISTENT_CARD_KEYS:
            if not merged.get(key) and prev.get(key):
                merged[key] = [dict(card) for card in prev.get(key, [])]
        if not any(merged.get(key) for key in _CARD_LIST_KEYS):
            for key in _CARD_LIST_KEYS:
                merged[key] = [dict(card) for card in prev.get(key, [])]

    # Dynamic card deltas often mention only the category a card moved into.
    # If the same instance appears in the current delta under one category,
    # evict any stale copies from all other categories carried forward from
    # previous snapshots before we persist or infer actions.
    current_card_by_instance = {}
    current_category_by_instance = {}
    for key in _CARD_LIST_KEYS:
        for card in gs.get(key, []) or []:
            instance_id = (card or {}).get("instance_id")
            if instance_id:
                current_card_by_instance[instance_id] = dict(card)
                current_category_by_instance[instance_id] = key

    if current_category_by_instance:
        for key in _CARD_LIST_KEYS:
            reconciled = []
            for card in merged.get(key, []) or []:
                instance_id = (card or {}).get("instance_id")
                if not instance_id:
                    reconciled.append(card)
                    continue
                winning_category = current_category_by_instance.get(instance_id)
                if winning_category and winning_category != key:
                    winning_card = current_card_by_instance.get(instance_id, {})
                    if (
                        winning_category == "offered"
                        and key in _PERSISTENT_CARD_KEYS
                        and not winning_card.get("owner")
                        and winning_card.get("section") in (None, "", "None")
                        and winning_card.get("socket") in (None, "", "None")
                    ):
                        reconciled.append(card)
                        continue
                    continue
                reconciled.append(card)
            merged[key] = reconciled

    # Even outside the immediate delta, player/opponent ownership should win
    # over "offered" when the same instance leaks into multiple categories.
    owner_category_by_instance = {}
    for key in ("player_board", "player_stash", "player_skills", "opponent_board"):
        for card in merged.get(key, []) or []:
            instance_id = (card or {}).get("instance_id")
            if instance_id:
                owner_category_by_instance[instance_id] = key

    if owner_category_by_instance:
        merged["offered"] = [
            card
            for card in (merged.get("offered", []) or [])
            if (card or {}).get("instance_id") not in owner_category_by_instance
        ]

    # F1: pull in any deferred card data that arrived before this snapshot processed
    snap_id = gs.get("id")
    if snap_id and snap_id in _deferred_cards_by_snapshot_id:
        deferred = _deferred_cards_by_snapshot_id.pop(snap_id)
        for key in _CARD_LIST_KEYS:
            if deferred.get(key):
                merged[key] = [dict(c) for c in deferred[key]]
    if snap_id and snap_id in _deferred_template_events_by_snapshot_id:
        merged["card_template_events"] = list(
            _deferred_template_events_by_snapshot_id.pop(snap_id)
        )
    # Pull in any deferred player attrs that arrived before this snapshot processed.
    # Common case: attrs arrive AFTER the snapshot (handled by handle_deferred_player_attrs);
    # this branch handles the race where setImmediate fires before the worker dequeues.
    if snap_id and snap_id in _deferred_attrs_by_snapshot_id:
        deferred_attrs = _deferred_attrs_by_snapshot_id.pop(snap_id)
        player_dict = merged.setdefault("player", {})
        for k, v in deferred_attrs.items():
            player_dict[k] = v
        _deferred_attrs_pickup_count += 1

    _prune_disabled_snapshot_cards(merged)
    _apply_event_template_recovery(merged)

    _last_merged_snapshot = {
        **merged,
        "run": dict(merged.get("run", {})),
        "state": dict(merged.get("state", {})),
        "player": dict(merged.get("player", {})),
    }
    for key in _CARD_LIST_KEYS:
        _last_merged_snapshot[key] = [dict(card) for card in merged.get(key, [])]
    return merged


def handle_game_state(gs):
    """Process a captured game state snapshot."""
    global _snapshot_count, _duplicate_snapshot_count
    gs = _merge_partial_snapshot(gs)
    _prune_disabled_snapshot_cards(gs)
    # QW5: normalize numeric timestamp (Date.now() ms) to ISO string
    _ts = gs.get("timestamp")
    if isinstance(_ts, (int, float)):
        gs["timestamp"] = datetime.datetime.fromtimestamp(
            _ts / 1000.0, tz=datetime.timezone.utc
        ).isoformat()
    dedupe_key = _snapshot_dedupe_key(gs)
    if dedupe_key in _seen_snapshot_keys:
        _duplicate_snapshot_count += 1
        if _VERBOSE_HOOKS:
            print(
                f"[Mono] Duplicate snapshot skipped "
                f"({gs.get('hook', '?')}, {gs.get('message_id') or dedupe_key})"
            )
        return

    _seen_snapshot_keys.add(dedupe_key)
    _snapshot_count += 1

    if not _should_render_snapshot(gs):
        if _do_log or _do_db:
            persist_snapshot(gs)
        return

    snap_id = gs.get("id", _snapshot_count)
    run = gs.get("run", {})
    state = gs.get("state", {})
    player = gs.get("player", {})

    state_name = state.get("state", "?")
    hero = player.get("hero", "?")
    day = run.get("day", "?")
    hour = run.get("hour", "?")
    message_id = gs.get("message_id")
    gold = player.get("Gold", "?")
    hp = player.get("Health", "?")
    hp_max = player.get("HealthMax", "?")
    prestige = player.get("Prestige", "?")
    wins = run.get("victories", 0)
    losses = run.get("defeats", 0)

    render_sig = _render_signature(gs)
    if render_sig in _rendered_snapshot_keys:
        if _do_log or _do_db:
            persist_snapshot(gs)
        return
    _rendered_snapshot_keys.add(render_sig)

    global _last_snapshot_print_ms, _snapshot_prints_suppressed

    # Rate-limit verbose prints so bursty GameSim deltas don't flood the
    # console. The compact (default) form emits a single line per snapshot
    # with the same essential info; only the legacy multi-line block is
    # throttled.
    now_ms = time.time() * 1000.0
    if _COMPACT_SNAPSHOTS:
        msg_tag = f" msg={message_id}" if message_id else ""
        print(
            f"[Mono] [#{snap_id}]{msg_tag} {state_name} | {hero}"
            f" Day {day} Hour {hour} | Gold: {gold} HP: {hp}/{hp_max}"
            f" Prestige: {prestige} PvP: {wins}W/{losses}L"
        )
    else:
        should_emit = (
            _SNAPSHOT_PRINT_MIN_INTERVAL_MS <= 0
            or (now_ms - _last_snapshot_print_ms) >= _SNAPSHOT_PRINT_MIN_INTERVAL_MS
            or state_name in _INTERESTING_RENDER_STATES
        )
        if should_emit:
            lines = [f"\n{'=' * 60}"]
            header = f"  [#{snap_id}"
            if message_id:
                header += f" | msg={message_id}"
            header += f"] {state_name}  |  {hero}  Day {day} Hour {hour}"
            lines.append(header)
            lines.append(
                f"  Gold: {gold}  HP: {hp}/{hp_max}  Prestige: {prestige}"
                f"  PvP: {wins}W/{losses}L"
            )
            if _snapshot_prints_suppressed > 0:
                lines.append(
                    f"  (+{_snapshot_prints_suppressed} suppressed snapshots"
                    f" since last verbose block)"
                )
                _snapshot_prints_suppressed = 0
            lines.append(f"{'=' * 60}\n")
            print("\n".join(lines))
            _last_snapshot_print_ms = now_ms
        else:
            _snapshot_prints_suppressed += 1

    # Persist directly â€” we're already on the background worker thread.
    if _do_log or _do_db:
        persist_snapshot(gs)


def start_db_writer():
    """Initialize API tables once and start a background worker thread.

    The worker handles both persistence AND snapshot/command processing so
    that on_message can return immediately and unblock Frida's message pump.
    The queue is always created (even without --log/--db) so that processing
    can be offloaded regardless.
    """
    global _db_queue, _db_thread, _api_log_module, _do_db

    if _do_db:
        try:
            import api_log
            api_log.init_api_tables()
            api_log.init_api_tables = lambda: None
            _api_log_module = api_log
        except ImportError:
            print("[Mono] WARNING: api_log.py not found - skipping DB write")
            _do_db = False
        except Exception as e:
            print(f"[Mono] WARNING: DB init failed - skipping DB write ({e})")
            _do_db = False

    _db_queue = queue.Queue()

    def _describe_payload(kind, payload):
        if not isinstance(payload, dict):
            return f"type={type(payload).__name__}"

        parts = []
        if payload.get("id") is not None:
            parts.append(f"id={payload.get('id')}")
        if payload.get("snapshot_id") is not None:
            parts.append(f"snapshot_id={payload.get('snapshot_id')}")
        if payload.get("message_id") is not None:
            parts.append(f"message_id={payload.get('message_id')}")
        if payload.get("event_seq") is not None:
            parts.append(f"event_seq={payload.get('event_seq')}")
        if payload.get("event_type"):
            parts.append(f"event_type={payload.get('event_type')}")

        state = payload.get("state")
        if isinstance(state, dict) and state.get("state"):
            parts.append(f"state={state.get('state')}")
        run = payload.get("run")
        if isinstance(run, dict):
            if run.get("day") is not None:
                parts.append(f"day={run.get('day')}")
            if run.get("hour") is not None:
                parts.append(f"hour={run.get('hour')}")

        details = payload.get("details")
        if isinstance(details, dict):
            interesting = {}
            for key in ("decision_id", "offered", "rejected", "inferred_purchase", "rerolls"):
                if key in details:
                    interesting[key] = details[key]
            if interesting:
                parts.append(f"details={interesting}")

        return " ".join(parts) if parts else "dict"

    def _worker():
        while True:
            item = _db_queue.get()
            kind = "snapshot"
            payload = item
            try:
                if item is None:
                    return
                if isinstance(item, tuple):
                    kind, payload = item
                else:
                    kind, payload = "snapshot", item
                if kind == "process_snapshot":
                    # Full processing: merge, dedup, render, persist
                    handle_game_state(payload)
                elif kind == "deferred_cards":
                    # F1: merge deferred card data into matching snapshot
                    handle_deferred_cards(payload)
                elif kind == "deferred_template_events":
                    handle_deferred_template_events(payload)
                elif kind == "deferred_player_attrs":
                    handle_deferred_player_attrs(payload)
                elif kind == "snapshot":
                    persist_snapshot(payload)
                elif kind == "snapshot_db":
                    actual_payload = payload
                    if isinstance(payload, dict) and payload.get("_snapshot_db_key"):
                        queue_key = payload.get("_snapshot_db_key")
                        actual_payload = _pending_snapshot_db_by_key.pop(queue_key, None)
                        _pending_snapshot_db_keys.discard(queue_key)
                    if actual_payload:
                        payload = actual_payload
                        _store_game_state_to_db_impl(actual_payload)
            except Exception as e:
                queue_depth = _db_queue.qsize() if _db_queue is not None else 0
                print(
                    f"[Mono] Persist error: kind={kind} queue_depth={queue_depth} "
                    f"payload={_describe_payload(kind, payload)} err={e}"
                )
                if "locked" in str(e).lower():
                    print(
                        f"[Mono] Persist lock detail: kind={kind} queue_depth={queue_depth} "
                        f"payload={_describe_payload(kind, payload)}"
                    )
            finally:
                _db_queue.task_done()

    _db_thread = threading.Thread(target=_worker, name="mono-db-writer", daemon=True)
    _db_thread.start()


def stop_db_writer():
    """Flush and stop the background DB writer thread."""
    global _db_queue, _db_thread, _mono_db_conn

    if _db_queue is None:
        return

    _db_queue.put(None)
    _db_queue.join()
    if _db_thread is not None:
        _db_thread.join(timeout=2.0)
    _db_queue = None
    _db_thread = None

    if _mono_db_conn is not None:
        try:
            _mono_db_conn.commit()
            _mono_db_conn.close()
        except Exception:
            pass
        _mono_db_conn = None

    _pending_snapshot_db_by_key.clear()
    _pending_snapshot_db_keys.clear()


def persist_snapshot(gs):
    """Write snapshot artifacts on the background worker.

    Persist compact snapshot artifacts and enqueue DB writes when enabled.
    """
    run = gs.get("run", {})
    state = gs.get("state", {})
    player = gs.get("player", {})

    if _do_log and _output_dir:
        snap_id = gs.get("id", 0)
        json_path = _output_dir / f"state_{snap_id:03d}.json"
        json_path.write_text(json.dumps(gs, indent=2, default=str))

        if _log_file:
            entry = {
                "ts": gs.get("timestamp", datetime.datetime.now().isoformat()),
                "id": snap_id,
                "message_id": gs.get("message_id"),
                "state": state.get("state"),
                "hero": player.get("hero"),
                "day": run.get("day"),
                "victories": run.get("victories"),
                "defeats": run.get("defeats"),
                "gold": player.get("Gold"),
                "hp": player.get("Health"),
                "prestige": player.get("Prestige"),
            }
            _log_file.write(json.dumps(entry) + "\n")
            _log_file.flush()

    if _do_db:
        store_game_state_to_db(gs)


def store_game_state_to_db(gs):
    """Enqueue snapshot persistence work for the mono DB writer thread."""
    global _coalesced_snapshot_db_updates
    if _db_queue is None:
        _store_game_state_to_db_impl(gs)
        return
    queue_key = _snapshot_db_queue_key(gs)
    if not queue_key:
        _db_queue.put(("snapshot_db", gs))
        return

    _pending_snapshot_db_by_key[queue_key] = gs
    if queue_key in _pending_snapshot_db_keys:
        _coalesced_snapshot_db_updates += 1
        return

    _pending_snapshot_db_keys.add(queue_key)
    _db_queue.put((
        "snapshot_db",
        {
            "_snapshot_db_key": queue_key,
            "id": gs.get("id"),
            "message_id": gs.get("message_id"),
            "state": gs.get("state"),
        },
    ))


def _is_suspicious_template_id(template_id: str) -> bool:
    if not template_id:
        return False
    template_id = str(template_id).lower()
    if template_id == "00000000-0000-0000-0000-000000000000":
        return True
    return template_id.endswith("-0000-0000-0000-000000000000") or template_id.endswith("-0000-0000-000000000000")


def _update_event_template_cache(gs: dict) -> dict[str, str]:
    """Capture authoritative instance->template pairs from GameSim events."""
    event_map: dict[str, str] = {}
    for event in gs.get("card_template_events") or []:
        if not isinstance(event, dict):
            continue
        instance_id = event.get("instance_id")
        template_id = event.get("template_id")
        if not instance_id or not template_id:
            continue
        if _is_suspicious_template_id(template_id):
            continue
        event_map[instance_id] = template_id
        _event_template_ids_by_instance.pop(instance_id, None)
        _event_template_ids_by_instance[instance_id] = template_id

    while len(_event_template_ids_by_instance) > 4096:
        oldest = next(iter(_event_template_ids_by_instance))
        _event_template_ids_by_instance.pop(oldest, None)

    return event_map


def _apply_event_template_recovery(gs: dict) -> None:
    """Repair suspicious card template IDs using recent GameSim spawn/deal events."""
    event_map = _update_event_template_cache(gs)
    if not _event_template_ids_by_instance and not event_map:
        return

    template_lookup = dict(_event_template_ids_by_instance)
    if event_map:
        template_lookup.update(event_map)

    recovered = []
    for category in _CARD_LIST_KEYS:
        for card in gs.get(category, []) or []:
            if not isinstance(card, dict):
                continue
            instance_id = card.get("instance_id")
            if not instance_id:
                continue
            recovered_template = template_lookup.get(instance_id)
            if not recovered_template:
                continue
            current_template = card.get("template_id")
            if current_template == recovered_template:
                continue
            if current_template and not _is_suspicious_template_id(current_template):
                continue
            card["template_id"] = recovered_template
            card["_template_recovered_from_event"] = "gamesim_event"
            recovered.append({
                "instance_id": instance_id,
                "category": category,
                "from": current_template or "<blank>",
                "to": recovered_template,
            })

    if recovered:
        print(
            f"[Mono] Recovered template ids from GameSim events "
            f"snapshot_id={gs.get('id')} message_id={gs.get('message_id')} "
            f"count={len(recovered)} sample={json.dumps(recovered[:8], default=str)}"
        )


def _infer_synthetic_event_category(gs: dict, event: dict) -> str | None:
    """Infer an api_cards category for a template event when no card snapshot exists."""
    state_name = (gs.get("state") or {}).get("state")
    if state_name in {
        "Choice",
        "Loot",
        "LevelUp",
        "Pedestal",
        "Encounter",
        "EndRunVictory",
        "EndRunDefeat",
    }:
        return "offered"
    return None


def _build_synthetic_event_card_rows(gs_id: int, gs: dict, existing_rows: list[tuple]) -> list[tuple]:
    """Create fallback api_cards rows from GameSim template events.

    This covers runs where the dynamic Cards collection yields 0 decoded cards
    but GameSim events still provide authoritative instance/template pairs.
    """
    card_by_instance: dict[str, tuple] = {}
    for row in existing_rows:
        instance_id = row[1]
        template_id = row[2]
        if not instance_id:
            continue
        if instance_id not in card_by_instance:
            card_by_instance[instance_id] = row
            continue
        prev_template = card_by_instance[instance_id][2]
        if _is_suspicious_template_id(prev_template) and not _is_suspicious_template_id(template_id):
            card_by_instance[instance_id] = row

    synthetic_rows: list[tuple] = []
    synthetic_log: list[dict] = []
    for event in gs.get("card_template_events") or []:
        if not isinstance(event, dict):
            continue
        instance_id = event.get("instance_id")
        template_id = event.get("template_id")
        if not instance_id or not template_id or _is_suspicious_template_id(template_id):
            continue
        category = _infer_synthetic_event_category(gs, event)
        if not category:
            continue

        existing = card_by_instance.get(instance_id)
        if existing and existing[2] and not _is_suspicious_template_id(existing[2]):
            continue

        row = (
            gs_id,
            instance_id,
            template_id,
            event.get("card_type"),
            None,
            None,
            None,
            None,
            None,
            category,
        )
        card_by_instance[instance_id] = row
        synthetic_rows.append(row)
        synthetic_log.append(
            {
                "instance_id": instance_id,
                "template_id": template_id,
                "category": category,
                "event_type": event.get("event_type"),
            }
        )

    if synthetic_log:
        print(
            f"[Mono] Synthesized api_cards from GameSim events "
            f"snapshot_id={gs.get('id')} message_id={gs.get('message_id')} "
            f"state={(gs.get('state') or {}).get('state')} "
            f"count={len(synthetic_log)} sample={json.dumps(synthetic_log[:8], default=str)}"
        )

    return synthetic_rows


def _log_suspicious_snapshot_cards(gs):
    suspicious = []
    for category, cards in [
        ("offered", gs.get("offered", [])),
        ("player_board", gs.get("player_board", [])),
        ("player_stash", gs.get("player_stash", [])),
        ("player_skills", gs.get("player_skills", [])),
        ("opponent_board", gs.get("opponent_board", [])),
    ]:
        for card in cards or []:
            template_id = card.get("template_id")
            if not _is_suspicious_template_id(template_id):
                continue
            suspicious.append({
                "category": category,
                "instance_id": card.get("instance_id"),
                "template_id": template_id,
                "card_type": card.get("type"),
                "owner": card.get("owner"),
                "section": card.get("section"),
                "socket": card.get("socket"),
                "debug_source": card.get("_debug_source"),
                "probe": card.get("_debug_probe"),
            })
    if not suspicious:
        return
    run = gs.get("run", {})
    state = gs.get("state", {})
    print(
        f"[Mono] Suspicious template ids in snapshot "
        f"snapshot_id={gs.get('id')} message_id={gs.get('message_id')} "
        f"state={state.get('state')} day={run.get('day')} hour={run.get('hour')} "
        f"selection_set={state.get('selection_set')} count={len(suspicious)} "
        f"event_template_count={len(gs.get('card_template_events') or [])} "
        f"cards={json.dumps(suspicious[:8], default=str)}"
    )


def _store_game_state_to_db_impl(gs):
    """Write the captured game state to api_game_states / api_cards tables."""
    try:
        import api_log
        api_log.init_api_tables()
    except ImportError:
        print("[Mono] WARNING: api_log.py not found â€” skipping DB write")
        return

    from datetime import datetime, timezone
    import sqlite3

    attempts = 5
    for attempt in range(1, attempts + 1):
        conn = _get_mono_conn()
        now = datetime.now(timezone.utc).isoformat()

        run = gs.get("run", {})
        state = gs.get("state", {})
        player = gs.get("player", {})

        captured_at = gs.get("timestamp") or now
        _log_suspicious_snapshot_cards(gs)

        try:
            cur = conn.execute("""
                INSERT INTO api_game_states
                    (message_id, captured_at, run_state, hero, day, hour,
                     victories, defeats, gold, health, health_max, level,
                     data_version, offered_count, board_count, stash_count,
                     skills_count, opponent_count, selection_set,
                     reroll_cost, rerolls_remaining, full_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING id
            """, (
                None,
                captured_at,
                state.get("state"),
                player.get("hero"),
                run.get("day"),
                run.get("hour"),
                run.get("victories"),
                run.get("defeats"),
                player.get("Gold"),
                player.get("Health"),
                player.get("HealthMax"),
                player.get("Level"),
                run.get("data_version"),
                len(gs.get("offered", [])),
                len(gs.get("player_board", [])),
                len(gs.get("player_stash", [])),
                len(gs.get("player_skills", [])),
                len(gs.get("opponent_board", [])),
                json.dumps(state.get("selection_set")) if state.get("selection_set") else None,
                state.get("reroll_cost"),
                state.get("rerolls_remaining"),
                json.dumps(gs, default=str),
            ))
            gs_id = cur.fetchone()[0]
            card_rows = []
            for category, cards in [
                ("offered", gs.get("offered", [])),
                ("player_board", gs.get("player_board", [])),
                ("player_stash", gs.get("player_stash", [])),
                ("player_skills", gs.get("player_skills", [])),
                ("opponent_board", gs.get("opponent_board", [])),
            ]:
                for c in (cards or []):
                    card_rows.append((gs_id, c.get("instance_id"), c.get("template_id"),
                                      c.get("type"), c.get("tier"), c.get("size"),
                                      c.get("owner"), c.get("section"), c.get("socket"), category))
            synthetic_rows = _build_synthetic_event_card_rows(gs_id, gs, card_rows)
            if synthetic_rows:
                card_rows.extend(synthetic_rows)
            if card_rows:
                conn.executemany("""
                    INSERT INTO api_cards (game_state_id, instance_id, template_id, card_type,
                                           tier, size, owner, section, socket, category)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, card_rows)

            conn.commit()
            return
        except sqlite3.OperationalError as e:
            if "locked" not in str(e).lower() or attempt == attempts:
                raise
            try:
                conn.rollback()
            except Exception:
                pass
            backoff_s = 0.15 * attempt
            print(
                f"[Mono] Snapshot DB busy; retry {attempt}/{attempts - 1} "
                f"after {backoff_s:.2f}s for snapshot id={gs.get('id')} "
                f"message_id={gs.get('message_id')}"
            )
            time.sleep(backoff_s)

# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# PROCESS FINDING (reused from capture_frida.py)
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def list_game_processes(process_name="TheBazaar.exe"):
    try:
        script = (
            f"$p = Get-CimInstance Win32_Process -Filter \\\"name='{process_name}'\\\" | "
            "Select-Object ProcessId, CreationDate, CommandLine; "
            "if ($p) { $p | ConvertTo-Json -Compress }"
        )
        run_kwargs = dict(capture_output=True, text=True, timeout=8)
        if sys.platform == "win32":
            run_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            **run_kwargs,
        )
        stdout = result.stdout.strip()
        if stdout:
            data = json.loads(stdout)
            if isinstance(data, dict):
                data = [data]
            return [{"pid": int(item["ProcessId"]),
                     "created": item.get("CreationDate"),
                     "command_line": item.get("CommandLine") or ""}
                    for item in data]
    except Exception:
        pass
    return []


def find_game_pid(process_name="TheBazaar.exe"):
    processes = list_game_processes(process_name)
    if processes:
        processes.sort(key=lambda p: (p.get("created") or "", p["pid"]))
        return processes[-1]["pid"]

    try:
        run_kwargs2 = dict(capture_output=True, text=True, timeout=5)
        if sys.platform == "win32":
            run_kwargs2["creationflags"] = subprocess.CREATE_NO_WINDOW
        result = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {process_name}", "/FO", "CSV", "/NH"],
            **run_kwargs2,
        )
        for line in result.stdout.strip().split("\n"):
            if process_name.replace(".exe", "") in line:
                parts = line.split(",")
                if len(parts) >= 2:
                    return int(parts[1].strip('"'))
    except FileNotFoundError:
        pass
    return None


def wait_for_game_pid(process_name="TheBazaar.exe", poll_seconds=1.0, settle_seconds=8.0):
    print(f"[Mono] Waiting for {process_name} to start...")
    first_seen_at = None
    chosen_pid = None
    last_status_at = 0.0

    while True:
        candidate_pid = find_game_pid(process_name)

        if candidate_pid is not None:
            now = time.time()
            if chosen_pid != candidate_pid:
                chosen_pid = candidate_pid
                first_seen_at = now
                print(f"[Mono] Detected {process_name} PID {candidate_pid}; waiting for startup settle...")
            elif first_seen_at is not None and now - first_seen_at >= settle_seconds:
                print(f"[Mono] Selected PID {chosen_pid} after startup settle.")
                return chosen_pid
        else:
            first_seen_at = None
            chosen_pid = None
            now = time.time()
            if now - last_status_at >= 5:
                print(f"[Mono] Still waiting for {process_name}...")
                last_status_at = now

        time.sleep(poll_seconds)


def main():
    global _output_dir, _do_log, _do_db, _log_file
    global _VERBOSE_DEBUG, _VERBOSE_HOOKS, _RENDER_ALL_SNAPSHOTS, _DETAILED_SNAPSHOTS, _FULL_DELTA_CARDS
    global _DELTA_PLAYER_ATTRS, _ACTION_EVENT_CARDS, _CAPTURE_OPPONENT_BOARD, _ENABLE_PROBES, _ENABLE_BROAD_HOOKS
    global _COMPACT_SNAPSHOTS, _SNAPSHOT_PRINT_MIN_INTERVAL_MS

    parser = argparse.ArgumentParser(
        description="Mono-hooking Frida capture for The Bazaar â€” "
                    "reads game state directly from managed C# objects"
    )
    parser.add_argument("--pid", type=int, default=None,
                        help="PID of TheBazaar.exe (auto-detected if not specified)")
    parser.add_argument("--log", action="store_true",
                        help="Save captured game states to disk")
    parser.add_argument("--db", action="store_true",
                        help="Write captured states to SQLite (api_game_states table)")
    parser.add_argument("--process", type=str, default="TheBazaar.exe",
                        help="Process name to attach to")
    parser.add_argument("--wait", action="store_true",
                        help="Wait for game to launch before attaching")
    parser.add_argument("--verbose-hooks", action="store_true",
                        help="Print per-hook capture calls and duplicate-skip messages")
    parser.add_argument("--verbose-debug", action="store_true",
                        help="Print selected debug messages from the Frida reader")
    parser.add_argument("--all-snapshots", action="store_true",
                        help="Print every captured snapshot instead of only choice-like states")
    parser.add_argument("--detailed-snapshots", action="store_true",
                        help="Print full offered/board/skill/opponent template details for rendered snapshots")
    parser.add_argument("--full-delta-cards", action="store_true",
                        help="Fully decode card collections on every GameSim delta (slower, more complete)")
    parser.add_argument("--delta-player-attrs", action="store_true",
                        help="Decode dynamic player attributes on every GameSim delta (slower, richer Gold/HP)")
    parser.add_argument("--action-delta-cards", action="store_true",
                        help="Also decode action-time card identity on GameSim deltas (slower, may improve inferred move/buy/sell coverage)")
    parser.add_argument("--include-opponent-board", action="store_true",
                        help="Keep opponent board cards in deferred snapshots (more payload and DB work)")
    parser.add_argument("--enable-probes", action="store_true",
                        help="Attach passive probe hooks for method discovery (slower)")
    parser.add_argument("--broad-hooks", action="store_true",
                        help="Attach the older broad hook set for debugging (slower, more duplicate work)")
    parser.add_argument("--verbose-snapshots", action="store_true",
                        help="Print the legacy multi-line snapshot block instead of the default "
                             "compact one-line form (rate-limited to reduce console hitching)")
    parser.add_argument("--snapshot-print-interval-ms", type=float, default=None,
                        help="Minimum milliseconds between consecutive verbose snapshot blocks "
                             f"(default {int(_SNAPSHOT_PRINT_MIN_INTERVAL_MS)}ms; 0 disables throttling)")
    args = parser.parse_args()

    _do_log = args.log
    _do_db = args.db
    _VERBOSE_HOOKS = args.verbose_hooks
    _VERBOSE_DEBUG = args.verbose_debug
    _RENDER_ALL_SNAPSHOTS = args.all_snapshots
    _DETAILED_SNAPSHOTS = args.detailed_snapshots
    _FULL_DELTA_CARDS = args.full_delta_cards or _FULL_DELTA_CARDS
    _DELTA_PLAYER_ATTRS = args.delta_player_attrs or _DELTA_PLAYER_ATTRS
    _ACTION_EVENT_CARDS = args.action_delta_cards or _ACTION_EVENT_CARDS
    _CAPTURE_OPPONENT_BOARD = args.include_opponent_board or _CAPTURE_OPPONENT_BOARD
    _ENABLE_PROBES = args.enable_probes  # kept False by default
    _ENABLE_BROAD_HOOKS = args.broad_hooks  # kept False by default
    _COMPACT_SNAPSHOTS = not args.verbose_snapshots
    if args.snapshot_print_interval_ms is not None:
        _SNAPSHOT_PRINT_MIN_INTERVAL_MS = max(0.0, args.snapshot_print_interval_ms)

    # Always start the background worker â€” it handles snapshot processing
    # (merge, dedup, action inference, rendering) in addition to persistence.
    # This keeps on_message thin and avoids back-pressuring Frida's send().
    start_db_writer()

    try:
        import frida
    except ImportError:
        print("[Mono] ERROR: frida not installed.")
        print("[Mono] Install with: pip install frida frida-tools")
        sys.exit(1)

    # Find game process
    pid = args.pid
    if pid is None:
        pid = find_game_pid(args.process)
        if pid is None:
            if args.wait:
                pid = wait_for_game_pid(args.process)
            else:
                print(f"[Mono] ERROR: {args.process} not found running.")
                print("[Mono] Start the game first, or use --wait.")
                sys.exit(1)

    print(f"[Mono] Attaching to PID {pid} ({args.process})...")

    # Setup output directory
    if _do_log:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        _output_dir = CAPTURES_DIR / f"mono_{ts}"
        _output_dir.mkdir(parents=True, exist_ok=True)
        _log_file = open(_output_dir / "capture.jsonl", "w")
        print(f"[Mono] Logging to {_output_dir}")

    # Attach and inject
    try:
        session = frida.attach(pid)
    except Exception as e:
        print(f"[Mono] Failed to attach: {e}")
        if "access" in str(e).lower():
            print("[Mono] Try running as Administrator.")
        sys.exit(1)

    script_source = FRIDA_MONO_AGENT.replace(
        "__FULL_DELTA_CARDS__",
        "true" if _FULL_DELTA_CARDS else "false",
    )
    script_source = script_source.replace(
        "__ENABLE_PROBES__",
        "true" if _ENABLE_PROBES else "false",
    )
    script_source = script_source.replace(
        "__ENABLE_BROAD_HOOKS__",
        "true" if _ENABLE_BROAD_HOOKS else "false",
    )
    script_source = script_source.replace(
        "__DELTA_PLAYER_ATTRS__",
        "true" if _DELTA_PLAYER_ATTRS else "false",
    )
    script_source = script_source.replace(
        "__ACTION_EVENT_CARDS__",
        "true" if _ACTION_EVENT_CARDS else "false",
    )
    script_source = script_source.replace(
        "__CAPTURE_OPPONENT_BOARD__",
        "true" if _CAPTURE_OPPONENT_BOARD else "false",
    )
    script_source = script_source.replace(
        "__VERBOSE_HOOK_CALLS__",
        "true" if _VERBOSE_HOOKS else "false",
    )
    script = session.create_script(script_source)
    script.on("message", on_message)
    script.load()

    print(f"\n{'=' * 60}")
    print(f"  MONO CAPTURE ACTIVE")
    print(f"  Play the game â€” game state snapshots will appear here")
    print(f"  Press Ctrl+C to stop")
    print(f"{'=' * 60}\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        print("\n[Mono] Detaching...")
        try:
            script.unload()
            session.detach()
        except Exception:
            pass
        stop_db_writer()
        if _log_file:
            _log_file.close()

        # Print probe summary if any
        if _probe_hits:
            print("\n[Mono] Probe hit summary:")
            for method, count in sorted(_probe_hits.items(), key=lambda x: -x[1]):
                print(f"  {method}: {count} calls")

        if _capture_calls:
            print("\n[Mono] Capture hook summary:")
            for method, count in sorted(_capture_calls.items(), key=lambda x: -x[1]):
                print(f"  {method}: {count} calls")

        if _duplicate_snapshot_count:
            print(f"[Mono] Duplicate snapshots skipped: {_duplicate_snapshot_count}")
        if _coalesced_snapshot_db_updates:
            print(f"[Mono] Coalesced snapshot DB updates: {_coalesced_snapshot_db_updates}")

        print(f"[Mono] Done. {_snapshot_count} snapshots captured.")
        if _output_dir:
            print(f"[Mono] Captures saved to: {_output_dir}")


if __name__ == "__main__":
    main()
