"""
Static regression tests for issue #221: management-view state isolation.

Verifies:
- activeView is declared for the three known management views.
- renderActivePane only routes to management views within the idle branch.
- Each open function sets the correct activeView and resets only its own state.
- Opening one management view does not clear or mutate another view's state.
- Back handlers reset activeView, own state, contentScrollTop; call render(); return.
- Click handlers call event.preventDefault() and return without fall-through.
- closeManagementViewForLiveRun() resets all my-builds form state.
"""
from pathlib import Path

import app_paths


def _overlay_src():
    return (app_paths.repo_dir() / "web" / "static" / "overlay.html").read_text(encoding="utf-8")


def _fn_body(src: str, fn_decl: str, end_decl: str) -> str:
    """Extract function body from fn_decl up to (not including) end_decl."""
    start = src.index(fn_decl)
    end = src.index(end_decl, start)
    return src[start:end]


def _handler_block(src: str, data_attr: str) -> str:
    """Return the content of the click-handler if-block for a given data attribute.

    Extracts from the data attribute reference through (and including) the
    first ``return;`` that terminates the handler, stopping before the closing
    ``}``.  This is the authoritative region for asserting that a handler calls
    ``event.preventDefault()`` and returns without fall-through.
    """
    attr_str = f"[data-{data_attr}='true']"
    pos = src.index(attr_str)
    return_end = src.index("return;", pos)
    close_brace = src.index("\n      }", return_end)
    return src[pos:close_brace]


# ---------------------------------------------------------------------------
# Registry and idle-only routing
# ---------------------------------------------------------------------------

def test_management_views_are_registered_and_idle_only():
    """activeView declaration names all three views; renderActivePane routes to them only inside idle."""
    src = _overlay_src()

    # Declaration must document all three known view names.
    decl_start = src.index("let activeView = null;")
    decl_line = src[decl_start:src.index("\n", decl_start)]
    for view in ("build-data", "my-builds", "history"):
        assert view in decl_line, f"activeView declaration must document '{view}'"

    # renderActivePane must check all three activeView values inside the idle block.
    fn = _fn_body(src, "function renderActivePane(state) {", "function renderItemImagesAlert(")
    idle_start = fn.index("if (shouldShowIdleState(state))")
    # The non-idle path begins at the next tab-conditional branch after the idle block.
    idle_end = fn.index('if (activeTab === "review")', idle_start)
    idle_block = fn[idle_start:idle_end]
    for view in ("build-data", "my-builds", "history"):
        assert f'activeView === "{view}"' in idle_block, (
            f'renderActivePane idle branch must route activeView === "{view}"'
        )


# ---------------------------------------------------------------------------
# Open-function correctness: each sets only its own activeView
# ---------------------------------------------------------------------------

def test_management_view_open_functions_set_only_their_own_active_view():
    """Each open function writes the correct activeView string and no other view's activeView."""
    src = _overlay_src()

    cases = [
        ("async function openBuildDataView() {", "async function openHistoryView(", "build-data"),
        ("async function openMyBuildsView() {",  "async function loadMyBuildsData(", "my-builds"),
        ("async function openHistoryView() {",   "async function loadHistoryRuns(",  "history"),
    ]
    for fn_decl, end_decl, expected_view in cases:
        fn = _fn_body(src, fn_decl, end_decl)
        assert f'activeView = "{expected_view}"' in fn, (
            f"{fn_decl!r} must set activeView = \"{expected_view}\""
        )
        # Must not also set a different management view's activeView.
        for other_view in ("build-data", "my-builds", "history"):
            if other_view != expected_view:
                assert f'activeView = "{other_view}"' not in fn, (
                    f"{fn_decl!r} must not set activeView to unrelated view '{other_view}'"
                )


# ---------------------------------------------------------------------------
# State isolation: opening one view must not mutate another's state
# ---------------------------------------------------------------------------

def test_management_view_state_objects_remain_separate():
    """Opening one management view must not reset or write another view's state variables."""
    src = _overlay_src()

    # openHistoryView must not touch my-builds form state.
    history_fn = _fn_body(src, "async function openHistoryView() {", "async function loadHistoryRuns(")
    my_builds_fields = ("myBuildsFormOpen", "myBuildsFormData", "myBuildsDeleteConfirm", "myBuildsFormDraft")
    for field in my_builds_fields:
        assert field not in history_fn, (
            f"openHistoryView must not touch {field} — state isolation between views"
        )

    # openMyBuildsView must not touch historyState.
    my_builds_fn = _fn_body(src, "async function openMyBuildsView() {", "async function loadMyBuildsData(")
    assert "historyState" not in my_builds_fn, (
        "openMyBuildsView must not touch historyState — state isolation between views"
    )

    # openBuildDataView must not touch my-builds form state or historyState.
    build_data_fn = _fn_body(src, "async function openBuildDataView() {", "async function openHistoryView(")
    for field in my_builds_fields + ("historyState",):
        assert field not in build_data_fn, (
            f"openBuildDataView must not touch {field} — state isolation between views"
        )


# ---------------------------------------------------------------------------
# Back handlers: clean exit from management mode
# ---------------------------------------------------------------------------

def test_management_view_back_handlers_clear_active_view_and_return():
    """All back handlers set activeView = null, reset contentScrollTop, call render(), and return."""
    src = _overlay_src()
    for attr in ("my-builds-back", "build-data-back", "history-back"):
        block = _handler_block(src, attr)
        assert "activeView = null" in block, f"{attr} must set activeView = null"
        assert "contentScrollTop = 0" in block, f"{attr} must reset contentScrollTop = 0"
        assert "render()" in block, f"{attr} must call render()"
        assert "return;" in block, f"{attr} must return after handling"


def test_my_builds_back_handler_also_resets_form_state():
    """my-builds-back must clear all my-builds form fields, not just activeView."""
    src = _overlay_src()
    block = _handler_block(src, "my-builds-back")
    for field in ("myBuildsFormOpen", "myBuildsFormData", "myBuildsDeleteConfirm", "myBuildsFormDraft"):
        assert field in block, f"my-builds-back must reset {field}"


# ---------------------------------------------------------------------------
# Click-handler no-fall-through: each handler calls preventDefault() and returns
# ---------------------------------------------------------------------------

def test_management_click_handlers_do_not_fall_through():
    """Each open/back click handler calls event.preventDefault() before return."""
    src = _overlay_src()
    handlers = (
        "open-my-builds",
        "my-builds-back",
        "open-build-data",
        "build-data-back",
        "open-history",
        "history-back",
    )
    for attr in handlers:
        block = _handler_block(src, attr)
        assert "event.preventDefault()" in block, (
            f"[data-{attr}] handler must call event.preventDefault()"
        )
        assert "return;" in block, (
            f"[data-{attr}] handler must return after handling to prevent fall-through"
        )
        prevent_pos = block.index("event.preventDefault()")
        return_pos = block.index("return;")
        assert prevent_pos < return_pos, (
            f"[data-{attr}] handler must call event.preventDefault() before return"
        )


# ---------------------------------------------------------------------------
# closeManagementViewForLiveRun: resets all my-builds form state
# ---------------------------------------------------------------------------

def test_close_management_view_for_live_run_resets_all_form_state():
    """closeManagementViewForLiveRun must clear activeView and all my-builds form fields."""
    src = _overlay_src()
    fn = _fn_body(
        src,
        "function closeManagementViewForLiveRun() {",
        "function dismissCompletedRun(",
    )
    assert "activeView = null" in fn, "closeManagementViewForLiveRun must set activeView = null"
    assert "contentScrollTop = 0" in fn, "closeManagementViewForLiveRun must reset contentScrollTop"
    for field in ("myBuildsFormOpen", "myBuildsFormData", "myBuildsDeleteConfirm", "myBuildsFormDraft", "myBuildsFormError"):
        assert field in fn, f"closeManagementViewForLiveRun must reset {field}"
