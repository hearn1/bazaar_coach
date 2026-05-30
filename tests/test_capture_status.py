"""Tests for capture_status: stdout-line parsing + poll() crash backstop."""
import capture_status


class _FakeProc:
    """Minimal subprocess.Popen stand-in for poll() crash detection."""

    def __init__(self, returncode=None):
        self._returncode = returncode

    def poll(self):
        return self._returncode


def setup_function(_fn):
    # Reset module state before each test (globals persist across cases).
    capture_status.reset("starting")


def test_starting_default():
    status = capture_status.get_status()
    assert status["state"] == "starting"
    assert status["level"] == "info"


def test_waiting_for_game():
    capture_status.observe_line("[Mono] Waiting for TheBazaar.exe to start...")
    status = capture_status.get_status()
    assert status["state"] == "waiting_for_game"
    assert status["level"] == "info"


def test_hooks_active_parses_count():
    capture_status.observe_line(
        "[Mono] Mono hooks active. 3 capture method(s) hooked."
    )
    status = capture_status.get_status()
    assert status["state"] == "hooks_active"
    assert status["level"] == "ok"
    assert status["hooked_count"] == 3


def test_hooks_unresolved_retry():
    capture_status.observe_line(
        "[Mono] No capture hooks resolved at attach; retrying as game "
        "assemblies load. Present: Assembly-CSharp"
    )
    status = capture_status.get_status()
    assert status["state"] == "hooks_unresolved"
    assert status["level"] == "warn"


def test_hooks_unresolved_gave_up():
    capture_status.observe_line(
        "[Mono] ERROR: No preferred capture hooks resolved. Assemblies: foo"
    )
    assert capture_status.get_status()["state"] == "hooks_unresolved"


def test_attaching():
    capture_status.observe_line("[Mono] Frida Mono agent loaded. Waiting for game state...")
    assert capture_status.get_status()["state"] == "attaching"


def test_poll_backstop_reports_crash():
    # Worker reported healthy, then the process died (nonzero exit).
    capture_status.observe_line("[Mono] Mono hooks active. 3 capture method(s) hooked.")
    capture_status.attach_process(_FakeProc(returncode=1))
    status = capture_status.get_status()
    assert status["state"] == "not_running"
    assert status["level"] == "error"
    assert "1" in status["detail"]


def test_live_process_keeps_state():
    capture_status.observe_line("[Mono] Mono hooks active. 2 capture method(s) hooked.")
    capture_status.attach_process(_FakeProc(returncode=None))  # still running
    assert capture_status.get_status()["state"] == "hooks_active"


def test_note_exit():
    capture_status.note_exit(0)
    status = capture_status.get_status()
    assert status["state"] == "not_running"


def test_disabled_ignores_poll():
    capture_status.set_disabled()
    capture_status.attach_process(_FakeProc(returncode=1))
    status = capture_status.get_status()
    assert status["state"] == "disabled"
    assert status["level"] == "off"
