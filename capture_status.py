"""In-memory capture (Frida/Mono) health status for the overlay.

The capture worker runs as a subprocess (``capture_mono.py`` in dev,
``BazaarCoachCLI.exe --capture-mono-worker`` when packaged). coach.py's stdout
pump (``_pump_process_output``) parses the worker's ``[Mono]`` status lines and
updates this module; the Flask app — running in the *same* main process — reads
it via :func:`get_status` to surface a capture-health indicator in the overlay.

No DB round-trip: status lives here in the main process. Crash detection uses
``proc.poll()`` (registered via :func:`attach_process`) plus an explicit
:func:`note_exit` from the pump's ``finally``, so a worker that dies after a
healthy "attached" line is reported as not-running rather than staying green.

States (the ``state`` field of :func:`get_status`):
  - ``disabled``         : capture intentionally not launched (``--no-mono``)
  - ``starting``         : worker launched, not yet attached
  - ``waiting_for_game`` : worker running, game process not detected yet
  - ``attaching``        : attached, Frida agent loaded, hooks not resolved yet
  - ``hooks_active``     : hooks resolved — capture healthy (quiet/green)
  - ``hooks_unresolved`` : attached but no capture hooks resolved (#158 mode)
  - ``not_running``      : worker exited / crashed

``level`` (``ok`` | ``info`` | ``warn`` | ``error`` | ``off``) drives overlay
styling; healthy (``ok``) is deliberately quiet.
"""
from __future__ import annotations

import re
import threading
import time

_lock = threading.Lock()
_state = "starting"
_detail = ""
_hooked_count = 0
_updated_at = time.time()
_last_line_at = time.time()
_proc = None  # subprocess.Popen | None

_LEVELS = {
    "disabled": "off",
    "starting": "info",
    "waiting_for_game": "info",
    "attaching": "info",
    "hooks_active": "ok",
    "hooks_unresolved": "warn",
    "not_running": "error",
}

_LABELS = {
    "disabled": "Capture off",
    "starting": "Starting capture…",
    "waiting_for_game": "Waiting for game",
    "attaching": "Attaching…",
    "hooks_active": "Capture active",
    "hooks_unresolved": "Hooks unresolved",
    "not_running": "Capture not running",
}

_HOOK_COUNT_RE = re.compile(r"(\d+)\s+capture method")


def reset(state: str = "starting", detail: str = "") -> None:
    """Reset to a fresh launch state and clear any registered process."""
    global _state, _detail, _hooked_count, _updated_at, _last_line_at, _proc
    with _lock:
        _state = state
        _detail = detail
        _hooked_count = 0
        _updated_at = time.time()
        _last_line_at = time.time()
        _proc = None


def attach_process(proc) -> None:
    """Register the capture subprocess so a crash is detectable via poll()."""
    global _proc
    with _lock:
        _proc = proc


def set_state(state: str, detail: str = "", hooked_count: int | None = None) -> None:
    global _state, _detail, _hooked_count, _updated_at
    with _lock:
        _state = state
        _detail = detail
        if hooked_count is not None:
            _hooked_count = hooked_count
        _updated_at = time.time()


def set_disabled() -> None:
    set_state("disabled", "Mono capture disabled (--no-mono)")


def note_exit(code) -> None:
    detail = f"exited with code {code}" if code is not None else "exited"
    set_state("not_running", detail)


def observe_line(line: str) -> None:
    """Parse one worker stdout line; update state on a recognized marker.

    Called from coach.py's pump for every line — cheap, bumps the heartbeat,
    and only mutates state on a recognized transition (first match wins).
    """
    global _last_line_at
    with _lock:
        _last_line_at = time.time()
    if not line:
        return
    # Hooks resolved — healthy. e.g. "Mono hooks active. 3 capture method(s) hooked."
    if "Mono hooks active" in line:
        m = _HOOK_COUNT_RE.search(line)
        set_state("hooks_active", "", hooked_count=int(m.group(1)) if m else None)
        return
    # #158 early-attach: agent retrying as game assemblies load.
    if "No capture hooks resolved at attach" in line:
        set_state("hooks_unresolved", "retrying as game assemblies load")
        return
    # Agent gave up after the retry window.
    if "No preferred capture hooks resolved" in line:
        set_state("hooks_unresolved", "no capture hooks resolved")
        return
    # Attached; Frida agent loaded, awaiting first state / hook resolution.
    if "Frida Mono agent loaded" in line:
        set_state("attaching", "agent loaded")
        return
    if "Failed to attach" in line:
        set_state("not_running", "failed to attach")
        return
    # Waiting for the game process to launch (--wait path).
    if ("Waiting for" in line and "to start" in line) or "Still waiting for" in line:
        set_state("waiting_for_game", "")
        return
    if "Start the game first" in line:
        set_state("waiting_for_game", "game not running")
        return


def get_status() -> dict:
    """Return the current capture status as a JSON-serializable dict.

    poll() backstop: if the worker process has exited, report ``not_running``
    even when the last parsed line said otherwise (crash detection).
    """
    with _lock:
        state = _state
        detail = _detail
        hooked = _hooked_count
        updated_at = _updated_at
        last_line_at = _last_line_at
        proc = _proc

    if state != "disabled" and proc is not None:
        code = proc.poll()
        if code is not None:
            state = "not_running"
            detail = f"exited with code {code}"

    return {
        "state": state,
        "level": _LEVELS.get(state, "info"),
        "label": _LABELS.get(state, state),
        "detail": detail,
        "hooked_count": hooked,
        "updated_at": updated_at,
        "age_seconds": max(0.0, time.time() - last_line_at),
    }
