from pathlib import Path
from unittest.mock import MagicMock

from web.server import app
from web.review_builder import build_overlay_review_rows, _fallback_review_title


def _overlay_text():
    return (Path(app.static_folder) / "overlay.html").read_text(encoding="utf-8")


def test_overlay_recognizes_no_runs_state_contract():
    overlay = _overlay_text()

    assert 'state.state === "no_runs"' in overlay
    assert 'state.error === "No runs found"' in overlay


def test_overlay_handles_build_items_error_envelope():
    overlay = _overlay_text()

    assert "let itemImagesError = \"\";" in overlay
    assert "function renderItemImagesAlert()" in overlay
    assert "imagesPayload?.error" in overlay
    assert "imagesPayload?.items" in overlay
    assert "Build item images could not be loaded" in overlay


# R4-D: auto-reset button in renderActiveBuildHero (not dead code)
def test_overlay_clear_manual_button_rendered():
    overlay = _overlay_text()
    assert 'data-clear-manual="true"' in overlay
    assert "× Auto" in overlay


def test_overlay_clear_manual_handler_wired():
    overlay = _overlay_text()
    assert "data-clear-manual='true'" in overlay or 'data-clear-manual="true"' in overlay
    assert "clearManualArch()" in overlay


def test_overlay_clear_manual_button_in_render_active_build_hero():
    overlay = _overlay_text()
    # data-clear-manual must appear inside renderActiveBuildHero, not just in orphaned renderOverview
    fn_start = overlay.index("function renderActiveBuildHero(")
    fn_end = overlay.index("function renderSubTabsStrip", fn_start)
    fn_body = overlay[fn_start:fn_end]
    assert 'data-clear-manual="true"' in fn_body
    assert "× Auto" in fn_body


# R4-E: header layout — × button positioned absolute
def test_overlay_header_quit_has_absolute_position():
    overlay = _overlay_text()
    # position:absolute must appear in header-quit rule (R4-E fix)
    quit_rule_start = overlay.index(".header-quit {")
    quit_rule_end = overlay.index("}", quit_rule_start)
    rule_text = overlay[quit_rule_start:quit_rule_end]
    assert "position: absolute" in rule_text or "position:absolute" in rule_text


# Option B: run-pill moved to subtitle line; header-actions row layout
def test_overlay_run_pill_not_in_header_actions():
    import re
    overlay = _overlay_text()
    # Slice the header-actions block from renderHeader and assert pill is absent
    render_start = overlay.index("function renderHeader(")
    render_end = overlay.index("function renderTabs(", render_start)
    render_body = overlay[render_start:render_end]
    # Find the header-actions div (may be absent when canLeaveCompletedRun is false)
    match = re.search(r'class="header-actions".*?</div>', render_body, re.DOTALL)
    if match:
        assert 'class="run-pill' not in match.group(0), (
            "run-pill must not appear inside .header-actions — it belongs in .subtitle"
        )
    # Also confirm pill IS present in the subtitle section
    assert 'class="run-pill' in render_body


def test_overlay_header_actions_css_is_row():
    overlay = _overlay_text()
    actions_rule_start = overlay.index(".header-actions {")
    actions_rule_end = overlay.index("}", actions_rule_start)
    rule_text = overlay[actions_rule_start:actions_rule_end]
    assert "flex-direction: row" in rule_text
    assert "flex-direction: column" not in rule_text


def test_overlay_header_actions_css_no_padding_right():
    overlay = _overlay_text()
    actions_rule_start = overlay.index(".header-actions {")
    actions_rule_end = overlay.index("}", actions_rule_start)
    rule_text = overlay[actions_rule_start:actions_rule_end]
    assert "padding-right" not in rule_text


def test_overlay_subtitle_css_has_flex_wrap():
    overlay = _overlay_text()
    subtitle_rule_start = overlay.index(".subtitle {")
    subtitle_rule_end = overlay.index("}", subtitle_rule_start)
    rule_text = overlay[subtitle_rule_start:subtitle_rule_end]
    assert "flex-wrap: wrap" in rule_text


def test_overlay_header_quit_is_sibling_of_header_actions():
    import re
    overlay = _overlay_text()
    render_start = overlay.index("function renderHeader(")
    render_end = overlay.index("function renderTabs(", render_start)
    render_body = overlay[render_start:render_end]
    # header-quit must appear OUTSIDE any header-actions block
    # Extract header-actions block (greedy inside renderHeader)
    actions_match = re.search(r'<div class="header-actions">.*?</div>', render_body, re.DOTALL)
    if actions_match:
        assert 'header-quit' not in actions_match.group(0), (
            "header-quit must not be nested inside .header-actions"
        )


# P1-F: carry checklist x/1
def test_overlay_carry_effective_total():
    overlay = _overlay_text()
    assert "effectiveOwned" in overlay
    assert "effectiveTotal" in overlay
    assert 'tone === "carry"' in overlay


# P1-H: relevant pickups label
def test_overlay_relevant_pickups_kicker():
    overlay = _overlay_text()
    assert "Relevant pickups</div>" in overlay


# R4-I: idle vs active-zero logic — tab indicator follows state, not content override
def test_overlay_is_active_zero_decision_helper():
    overlay = _overlay_text()
    assert "function isActiveZeroDecision" in overlay
    assert "state?.is_active" in overlay


def test_overlay_active_zero_subtitle_copy():
    overlay = _overlay_text()
    assert "Waiting for first decision" in overlay


def test_overlay_prev_zero_transition_sets_coach_tab():
    overlay = _overlay_text()
    assert "prevWasZero" in overlay
    assert "nextIsZero" in overlay


def test_overlay_userpickedtab_declared():
    overlay = _overlay_text()
    assert "let userPickedTab = false" in overlay


def test_overlay_userpickedtab_set_in_setactivetab():
    overlay = _overlay_text()
    # userPickedTab = true must appear inside setActiveTab
    fn_start = overlay.index("function setActiveTab(")
    fn_end = overlay.index("}", fn_start)
    fn_body = overlay[fn_start:fn_end]
    assert "userPickedTab = true" in fn_body


def test_overlay_entry_transition_zero_to_run_tab():
    overlay = _overlay_text()
    assert "!prevWasZero && nextIsZero && !userPickedTab" in overlay


def test_overlay_active_zero_content_override_removed_from_render_active_pane():
    overlay = _overlay_text()
    # The content-side override must not appear in renderActivePane.
    # It was: if (isActiveZeroDecision(state)) { return ... renderRun(state) }
    # Find renderActivePane body and assert the override is gone.
    fn_start = overlay.index("function renderActivePane(")
    fn_end = overlay.index("function renderItemImagesAlert", fn_start)
    fn_body = overlay[fn_start:fn_end]
    assert "isActiveZeroDecision(state)" not in fn_body


# P1-C residual: overlay review row must use missed item (not first offered) for skip decisions
_SKIP_SCORE_NOTES = (
    "Skipped after 1 reroll(s) - missed: "
    "Universal utility: ['Fairies']; "
    "Early carry: ['Fairies']; "
    "Core for Sustain: [Fairies]"
)

_SKIP_DECISION_RUN67_DEC518 = {
    "id": 518,
    "decision_seq": 518,
    "decision_type": "skip",
    "game_state": "Shop",
    "board_section": "Player",
    "chosen_id": "",
    "chosen_template": "",
    "chosen_name": None,
    "offered": "[]",
    "offered_names": '["Hunter\'s Boots", "Fairies", "Campfire"]',
    "offered_raw": [],
    "rejected": "[]",
    "score_label": "missed",
    "score_notes": _SKIP_SCORE_NOTES,
    "resolved_offered": ["Hunter's Boots", "Fairies", "Campfire"],
    "resolved_rejected": [],
    "rejected_names": [],
}


def test_fallback_review_title_skip_uses_missed_not_first_offered():
    """_fallback_review_title must return the scorer-identified missed item for skip
    decisions, not the first entry in resolved_offered (which can be irrelevant)."""
    title = _fallback_review_title(_SKIP_DECISION_RUN67_DEC518)
    assert title == "Fairies", (
        f"Expected 'Fairies' (missed item from score_notes), got {title!r}. "
        "Hunter's Boots is the first resolved_offered entry but is NOT the missed item."
    )


def test_build_overlay_review_rows_skip_primary_text_is_missed_item():
    """build_overlay_review_rows must produce a review row whose review_title is
    the missed item ('Fairies'), not the first offered item ('Hunter's Boots'),
    for a skip decision mirroring run 67 dec 518."""
    import scorer as _scorer

    build_data = _scorer.load_builds("Karnok")

    # Minimal conn mock — decisions are passed directly, board snapshot returns empty.
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = []  # board snapshots query

    rows = build_overlay_review_rows(
        conn,
        run_id=67,
        decisions=[_SKIP_DECISION_RUN67_DEC518],
        build_data=build_data,
        hero="Karnok",
        prefer_scored_fallback=True,
        resolve_fn=lambda _conn, tid: tid,
        safe_json_fn=lambda v: (
            __import__("json").loads(v) if isinstance(v, str) and v.strip().startswith(("[", "{")) else (v if isinstance(v, list) else [])
        ),
        lookup_image_by_name_fn=None,
    )

    assert rows, "Expected at least one review row for the skip decision"
    titles = [r.get("review_title") for r in rows]
    assert any("Fairies" in (t or "") for t in titles), (
        f"Expected a row with review_title containing 'Fairies', got titles: {titles}"
    )
    assert not any("Hunter's Boots" == t for t in titles), (
        f"'Hunter's Boots' must not appear as a review_title; got titles: {titles}"
    )
