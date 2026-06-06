"""
Static regression tests for issue #223: focus preservation during overlay polling renders.

Verifies that:
- captureFocusedInputState and restoreFocusedInputState exist in overlay.html.
- render() captures focus before root.innerHTML replacement.
- render() restores focus after root.innerHTML replacement.
- Report issue and My Builds selectors are in the whitelist.
"""
import re
from pathlib import Path

import app_paths


def _overlay_src():
    return (app_paths.repo_dir() / "web" / "static" / "overlay.html").read_text(encoding="utf-8")


def test_capture_helper_exists():
    assert "function captureFocusedInputState(" in _overlay_src()


def test_restore_helper_exists():
    assert "function restoreFocusedInputState(" in _overlay_src()


def test_focus_captured_before_root_innerHTML_in_render():
    src = _overlay_src()
    capture_pos = src.find("captureFocusedInputState()")
    inner_pos = src.find("root.innerHTML", capture_pos)
    assert capture_pos != -1, "captureFocusedInputState() call not found"
    assert inner_pos != -1, "root.innerHTML not found after captureFocusedInputState() call"


def test_focus_restored_after_root_innerHTML_in_render():
    src = _overlay_src()
    # Find the render() function body region.
    render_start = src.find("function render()")
    assert render_start != -1
    render_body = src[render_start:]
    inner_pos = render_body.find("root.innerHTML")
    restore_pos = render_body.find("restoreFocusedInputState(focusedInput)")
    assert inner_pos != -1, "root.innerHTML not found in render()"
    assert restore_pos != -1, "restoreFocusedInputState(focusedInput) call not found in render()"
    assert restore_pos > inner_pos, "restore must come after root.innerHTML replacement"


def test_report_issue_selectors_in_whitelist():
    src = _overlay_src()
    assert '"data-report-issue-title"' in src
    assert '"data-report-issue-desc"' in src


def test_my_builds_selectors_in_whitelist():
    src = _overlay_src()
    for attr in [
        '"data-mbf-name"',
        '"data-mbf-phase"',
        '"data-mbf-core_items"',
        '"data-mbf-carry_items"',
        '"data-mbf-support_items"',
        '"data-mbf-condition_items"',
        '"data-mbf-timing_profile"',
        '"data-mbf-notes"',
    ]:
        assert attr in src, f"My Builds whitelist attribute missing: {attr}"
