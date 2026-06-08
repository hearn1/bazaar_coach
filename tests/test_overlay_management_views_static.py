"""
Static regression tests for issue #221: management-view state isolation.
Updated for issue #222: shared management-view helper refactor.

Verifies:
- activeView is declared for the three known management views.
- renderManagementView routes all three views; renderActivePane calls it in the idle branch.
- Each open function calls openManagementView with the correct view name.
- Opening one management view does not clear or mutate another view's state.
- Unified data-management-back handler calls closeManagementView(); closeManagementView resets all state.
- closeManagementView and closeManagementViewForLiveRun both reset my-builds form state.
- Click handlers call event.preventDefault() and return without fall-through.
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
    """activeView declaration names all three views; renderManagementView routes them;
    renderActivePane idle branch delegates to renderManagementView()."""
    src = _overlay_src()

    # Declaration must document all three known view names.
    decl_start = src.index("let activeView = null;")
    decl_line = src[decl_start:src.index("\n", decl_start)]
    for view in ("build-data", "my-builds", "history"):
        assert view in decl_line, f"activeView declaration must document '{view}'"

    # renderManagementView must route all three activeView values.
    fn = _fn_body(src, "function renderManagementView() {", "function renderHistoryView(")
    for view in ("build-data", "my-builds", "history"):
        assert f'activeView === "{view}"' in fn, (
            f'renderManagementView must route activeView === "{view}"'
        )

    # renderActivePane idle branch must delegate to renderManagementView().
    rend_fn = _fn_body(src, "function renderActivePane(state) {", "function renderItemImagesAlert(")
    idle_start = rend_fn.index("if (shouldShowIdleState(state))")
    idle_end = rend_fn.index('if (activeTab === "review")', idle_start)
    idle_block = rend_fn[idle_start:idle_end]
    assert "renderManagementView()" in idle_block, (
        "renderActivePane idle branch must call renderManagementView()"
    )


# ---------------------------------------------------------------------------
# Open-function correctness: each calls openManagementView with its own view
# ---------------------------------------------------------------------------

def test_management_view_open_functions_set_only_their_own_active_view():
    """Each open function calls openManagementView with the correct view string and no other view."""
    src = _overlay_src()

    cases = [
        ("async function openBuildDataView() {", "async function openHistoryView(", "build-data"),
        ("async function openMyBuildsView() {",  "async function loadMyBuildsData(", "my-builds"),
        ("async function openHistoryView() {",   "async function loadHistoryRuns(",  "history"),
    ]
    for fn_decl, end_decl, expected_view in cases:
        fn = _fn_body(src, fn_decl, end_decl)
        assert f'openManagementView("{expected_view}")' in fn, (
            f"{fn_decl!r} must call openManagementView(\"{expected_view}\")"
        )
        # Must not also call openManagementView for a different view.
        for other_view in ("build-data", "my-builds", "history"):
            if other_view != expected_view:
                assert f'openManagementView("{other_view}")' not in fn, (
                    f"{fn_decl!r} must not call openManagementView for unrelated view '{other_view}'"
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
# Back handler: unified data-management-back calls closeManagementView
# ---------------------------------------------------------------------------

def test_management_view_back_handler_calls_close_helper():
    """Unified data-management-back handler calls closeManagementView() and returns."""
    src = _overlay_src()
    block = _handler_block(src, "management-back")
    assert "closeManagementView()" in block, "management-back handler must call closeManagementView()"
    assert "return;" in block, "management-back handler must return after handling"


def test_close_management_view_resets_all_state():
    """closeManagementView must clear activeView, all my-builds form fields, contentScrollTop, and call render()."""
    src = _overlay_src()
    fn = _fn_body(src, "function closeManagementView() {", "function dismissCompletedRun(")
    assert "activeView = null" in fn, "closeManagementView must set activeView = null"
    assert "contentScrollTop = 0" in fn, "closeManagementView must reset contentScrollTop"
    assert "render()" in fn, "closeManagementView must call render()"
    for field in ("myBuildsFormOpen", "myBuildsFormData", "myBuildsDeleteConfirm", "myBuildsFormDraft"):
        assert field in fn, f"closeManagementView must reset {field}"


# ---------------------------------------------------------------------------
# Click-handler no-fall-through: unified handlers call preventDefault() and return
# ---------------------------------------------------------------------------

def test_management_click_handlers_do_not_fall_through():
    """Unified open/back click handlers call event.preventDefault() before return."""
    src = _overlay_src()

    # Unified management-back handler (uses ='true' selector).
    back_block = _handler_block(src, "management-back")
    assert "event.preventDefault()" in back_block, (
        "[data-management-back] handler must call event.preventDefault()"
    )
    assert "return;" in back_block, (
        "[data-management-back] handler must return after handling"
    )
    assert back_block.index("event.preventDefault()") < back_block.index("return;"), (
        "[data-management-back] handler must call event.preventDefault() before return"
    )

    # Unified open-management handler (uses value selector, not ='true').
    open_attr = "[data-open-management]"
    pos = src.index(open_attr)
    return_end = src.index("return;", pos)
    close_brace = src.index("\n      }", return_end)
    open_block = src[pos:close_brace]
    assert "event.preventDefault()" in open_block, (
        "[data-open-management] handler must call event.preventDefault()"
    )
    assert "return;" in open_block, (
        "[data-open-management] handler must return after handling"
    )
    assert open_block.index("event.preventDefault()") < open_block.index("return;"), (
        "[data-open-management] handler must call event.preventDefault() before return"
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
        "function openManagementView(",
    )
    assert "activeView = null" in fn, "closeManagementViewForLiveRun must set activeView = null"
    assert "contentScrollTop = 0" in fn, "closeManagementViewForLiveRun must reset contentScrollTop"
    for field in ("myBuildsFormOpen", "myBuildsFormData", "myBuildsDeleteConfirm", "myBuildsFormDraft", "myBuildsFormError"):
        assert field in fn, f"closeManagementViewForLiveRun must reset {field}"


# ---------------------------------------------------------------------------
# Issue #252: stale build catalog after My Builds mutations
# ---------------------------------------------------------------------------

def test_fetch_build_catalog_accepts_force_option():
    """fetchBuildCatalog signature must include a { force = false } destructured option."""
    src = _overlay_src()
    decl = "async function fetchBuildCatalog(hero, { force = false } = {}) {"
    assert decl in src, "fetchBuildCatalog must accept a { force = false } option"


def test_fetch_build_catalog_force_bypasses_cache_guard():
    """When force=true the early-return cache guard must be skipped (guard is inside !force &&)."""
    src = _overlay_src()
    fn = _fn_body(src, "async function fetchBuildCatalog(hero, { force = false } = {}) {", "async function ensureCatalogMatchesState(")
    assert "!force &&" in fn, "fetchBuildCatalog must guard the early-return with !force &&"


def test_ensure_catalog_matches_state_exists_and_calls_force_fetch():
    """ensureCatalogMatchesState must exist and call fetchBuildCatalog with force: true."""
    src = _overlay_src()
    fn = _fn_body(src, "async function ensureCatalogMatchesState(", "async function fetchBrowseHeroList(")
    assert "fetchBuildCatalog(" in fn, "ensureCatalogMatchesState must call fetchBuildCatalog"
    assert "force: true" in fn, "ensureCatalogMatchesState must pass force: true"


def test_fetch_state_calls_ensure_catalog_matches_state():
    """fetchState must call ensureCatalogMatchesState after loading the build catalog."""
    src = _overlay_src()
    fn = _fn_body(src, "async function fetchState() {", "async function startPolling(")
    assert "ensureCatalogMatchesState(" in fn, (
        "fetchState must call ensureCatalogMatchesState to detect catalog/server mismatch"
    )
    catalog_pos = fn.index("fetchBuildCatalog(")
    ensure_pos = fn.index("ensureCatalogMatchesState(")
    assert catalog_pos < ensure_pos, "fetchState must call ensureCatalogMatchesState after fetchBuildCatalog"


def test_toggle_my_builds_force_refreshes_catalog():
    """toggleMyBuildsEnabledState must call fetchBuildCatalog with force: true after loadMyBuildsData."""
    src = _overlay_src()
    fn = _fn_body(src, "async function toggleMyBuildsEnabledState(", "async function openBuildDataView(")
    assert "fetchBuildCatalog(myBuildsHero, { force: true })" in fn, (
        "toggleMyBuildsEnabledState must force-refresh the build catalog after enable/disable"
    )
    load_pos = fn.index("loadMyBuildsData(")
    force_pos = fn.index("fetchBuildCatalog(myBuildsHero, { force: true })")
    assert load_pos < force_pos, "force-refresh must happen after loadMyBuildsData"


def test_submit_my_builds_form_force_refreshes_catalog():
    """submitMyBuildsFormData must call fetchBuildCatalog with force: true on success."""
    src = _overlay_src()
    fn = _fn_body(src, "async function submitMyBuildsFormData() {", "async function deleteMyBuildsArchetypeConfirmed(")
    assert "fetchBuildCatalog(myBuildsHero, { force: true })" in fn, (
        "submitMyBuildsFormData must force-refresh the build catalog after a successful save"
    )


def test_delete_my_builds_force_refreshes_catalog():
    """deleteMyBuildsArchetypeConfirmed must call fetchBuildCatalog with force: true on success."""
    src = _overlay_src()
    fn = _fn_body(src, "async function deleteMyBuildsArchetypeConfirmed(", "async function toggleMyBuildsEnabledState(")
    assert "fetchBuildCatalog(myBuildsHero, { force: true })" in fn, (
        "deleteMyBuildsArchetypeConfirmed must force-refresh the build catalog after a successful delete"
    )


def test_get_active_coach_confidence_uses_active_arch_score():
    """getActiveCoach must derive confidence from the displayed activeArch's own score, not always arch_scores[0]."""
    src = _overlay_src()
    fn = _fn_body(src, "function getActiveCoach(state) {", "function getTier(")
    assert "findScoreForArch(" in fn, (
        "getActiveCoach must call findScoreForArch to tie confidence to the displayed activeArch"
    )
    assert "arch_scores?.[0]?.raw_score" not in fn, (
        "getActiveCoach must not blindly read arch_scores[0] for confidence"
    )


def test_find_score_for_arch_exists_and_looks_up_by_name():
    """findScoreForArch helper must exist and look up a named entry in arch_scores."""
    src = _overlay_src()
    fn = _fn_body(src, "function findScoreForArch(state, archName) {", "function getActiveCoach(")
    assert "arch_scores" in fn, "findScoreForArch must search state.arch_scores"
    assert "archName" in fn, "findScoreForArch must filter by archName"
