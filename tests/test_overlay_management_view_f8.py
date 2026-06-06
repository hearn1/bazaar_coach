"""
Static regression tests for issue #220: management-view auto-close and F8 hardening.

Verifies:
- Management views only render inside the idle branch of renderActivePane().
- fetchState() calls closeManagementViewForLiveRun() on idle-to-live transition.
- F8 handler calls stopPropagation() and preventDefault().
- F8 handler guards toggleCollapse() with !event.repeat.
"""
from pathlib import Path

import app_paths


def _overlay_src():
    return (app_paths.repo_dir() / "web" / "static" / "overlay.html").read_text(encoding="utf-8")


def test_overlay_management_views_only_render_during_idle_state():
    src = _overlay_src()
    fn_start = src.index("function renderActivePane(")
    fn_end = src.index("function renderError(", fn_start)
    fn_body = src[fn_start:fn_end]

    idle_block_start = fn_body.index("if (shouldShowIdleState(state))")
    # Slice everything AFTER the idle block (past the closing brace of the if-block)
    idle_block_end = fn_body.index("\n      if (activeTab", idle_block_start)
    outside_idle = fn_body[idle_block_end:]

    for view_fn in ("renderBuildDataView()", "renderMyBuildsView()", "renderHistoryView()"):
        assert view_fn not in outside_idle, (
            f"{view_fn} must only be called inside the shouldShowIdleState branch"
        )


def test_overlay_idle_to_live_transition_closes_management_view():
    src = _overlay_src()
    fetch_start = src.index("async function fetchState()")
    fetch_end = src.index("async function startPolling()", fetch_start)
    fetch_body = src[fetch_start:fetch_end]

    transition_start = fetch_body.index("if (previousWasIdle && !nextIsIdle)")
    transition_end = fetch_body.index("}", transition_start)
    transition_block = fetch_body[transition_start:transition_end]

    assert "closeManagementViewForLiveRun()" in transition_block, (
        "idle-to-live transition must call closeManagementViewForLiveRun()"
    )


def test_overlay_f8_handler_suppresses_default_and_propagation():
    src = _overlay_src()
    handler_start = src.index('if (event.key === "F8")')
    handler_end = src.index("}", handler_start + 1)
    handler_body = src[handler_start:handler_end]

    assert "event.preventDefault()" in handler_body
    assert "event.stopPropagation()" in handler_body


def test_overlay_f8_handler_ignores_key_repeat():
    src = _overlay_src()
    handler_start = src.index('if (event.key === "F8")')
    handler_end = src.index("}", handler_start + 1)
    handler_body = src[handler_start:handler_end]

    assert "event.repeat" in handler_body, (
        "F8 handler must guard toggleCollapse() with !event.repeat"
    )
    assert "toggleCollapse()" in handler_body
