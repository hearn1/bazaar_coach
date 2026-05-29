"""Small helpers for making packaged console output non-fatal."""

from __future__ import annotations

import sys


def configure_stdio_backslashreplace() -> None:
    """Ensure unencodable log characters cannot crash live capture workers.

    PyInstaller console streams on Windows can be backed by a legacy code page.
    Normal ``print()`` then raises ``UnicodeEncodeError`` for emoji, arrows, or
    punctuation in diagnostic lines. Logging should never interrupt gameplay
    event processing, so make stdout/stderr escape unencodable characters.
    """
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(errors="backslashreplace")
        except (TypeError, ValueError):
            # Some embedded/frozen stream wrappers do not allow reconfigure.
            pass
