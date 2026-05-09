import io
import sys
from pathlib import Path

import tracker


def test_tee_stream_tolerates_none_stdout_and_shutdown_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.setattr(tracker, "LOGS_DIR", tmp_path)
    monkeypatch.setattr(tracker.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(tracker.db, "close_shared_conn", lambda: None)
    monkeypatch.setattr(tracker.settings, "save", lambda: None)

    log_handle, original_stdout, original_stderr = tracker.start_session_logging()
    log_path = Path(log_handle.name)

    tracker._shutdown(None, log_handle, original_stdout, original_stderr)

    assert sys.stdout is None
    assert sys.stderr is None
    log_text = log_path.read_text(encoding="utf-8")
    assert "[Tracker] Session log:" in log_text
    assert "[Tracker] Shutdown complete." in log_text


def test_tee_stream_skips_missing_methods_and_uses_available_encoding():
    class PartialStream:
        encoding = "cp1252"

    backing = io.StringIO()
    tee = tracker.TeeStream(None, PartialStream(), backing)

    assert tee.encoding == "cp1252"
    assert tee.write("hello") == 5
    tee.flush()
    assert not tee.isatty()
    assert backing.getvalue() == "hello"


def test_tracker_cli_no_refresh_builds_passes_false(monkeypatch):
    calls = []

    class FakeEvent:
        def set(self):
            pass

        def wait(self):
            pass

    class FakeThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setattr(sys, "argv", [
        "tracker.py",
        "--no-overlay",
        "--no-mono",
        "--no-refresh-builds",
    ])
    monkeypatch.setattr(tracker, "shutdown_event", FakeEvent())
    monkeypatch.setattr(tracker.threading, "Thread", FakeThread)
    monkeypatch.setattr(tracker, "_install_signal_handlers", lambda: None)
    monkeypatch.setattr(tracker, "start_session_logging", lambda: (
        io.StringIO(),
        sys.stdout,
        sys.stderr,
    ))
    monkeypatch.setattr(tracker, "print_startup_versions", lambda: None)
    monkeypatch.setattr(tracker.settings, "load", lambda: None)
    monkeypatch.setattr(tracker.settings, "save", lambda: None)
    monkeypatch.setattr(tracker.db, "init_db", lambda: None)
    monkeypatch.setattr(tracker.db, "close_shared_conn", lambda: None)
    monkeypatch.setattr(tracker.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(tracker.atexit, "register", lambda _func: None)

    import first_run
    import web.server

    monkeypatch.setattr(first_run, "run_setup", lambda **kwargs: {"steps": []})
    monkeypatch.setattr(web.server, "set_shutdown_callback", lambda _callback: None)

    def fake_start_web_server(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(web.server, "start_web_server", fake_start_web_server)

    tracker.main()

    assert calls[0]["auto_refresh_builds"] is False
