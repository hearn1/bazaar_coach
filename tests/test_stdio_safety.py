"""Tests for stdio_safety helpers.

Coverage for configure_line_buffering (issue #182): the capture worker must
force line-buffered stdout/stderr in packaged builds so status-bearing prints
reach the parent process's stdout pump immediately.
"""
import sys

import pytest

from stdio_safety import configure_line_buffering


def test_configure_line_buffering_calls_reconfigure(monkeypatch):
    """Calls reconfigure(line_buffering=True) on streams that support it."""
    calls = []

    class _FakeStream:
        def reconfigure(self, **kw):
            calls.append(kw)

    monkeypatch.setattr(sys, "stdout", _FakeStream())
    monkeypatch.setattr(sys, "stderr", _FakeStream())
    configure_line_buffering()
    assert calls == [{"line_buffering": True}, {"line_buffering": True}]


def test_configure_line_buffering_ignores_missing_reconfigure(monkeypatch):
    """Does not raise when the stream has no reconfigure (e.g. StringIO)."""
    import io

    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    configure_line_buffering()  # must not raise


def test_configure_line_buffering_tolerates_reconfigure_error(monkeypatch):
    """Does not raise when reconfigure() itself fails."""

    class _BrokenStream:
        def reconfigure(self, **kw):
            raise ValueError("not supported in this context")

    monkeypatch.setattr(sys, "stdout", _BrokenStream())
    monkeypatch.setattr(sys, "stderr", _BrokenStream())
    configure_line_buffering()  # must not raise
