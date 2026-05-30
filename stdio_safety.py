"""Small helpers for making packaged console output non-fatal."""

from __future__ import annotations

import sys


def configure_stdio_backslashreplace() -> None:
    """Ensure unencodable log characters cannot crash live capture workers.

    PyInstaller console streams on Windows can be backed by a legacy code page
    (e.g. ``cp932`` on a Japanese locale). Normal ``print()`` then raises
    ``UnicodeEncodeError`` for emoji, arrows, or punctuation in diagnostic
    lines, killing the capture worker (see issue #152). Logging should never
    interrupt gameplay event processing.

    We force ``utf-8`` on the streams so the bytes the worker emits match what
    the parent reads (``coach.launch_capture_mono`` decodes the pipe as utf-8),
    and fall back to ``errors="backslashreplace"`` so anything still
    unencodable is escaped rather than fatal. Encoding and error handler are
    applied independently because some frozen stream wrappers accept one but
    not the other.
    """
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="backslashreplace")
            continue
        except (TypeError, ValueError, LookupError):
            # Some embedded/frozen stream wrappers reject a combined call or an
            # encoding change; fall through and at least make errors non-fatal.
            pass
        try:
            reconfigure(errors="backslashreplace")
        except (TypeError, ValueError):
            pass
