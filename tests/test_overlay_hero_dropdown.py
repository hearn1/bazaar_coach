"""
Static regression tests for issue #246: hero picker dropdown in the overlay.

Part A — the open native <select> popup must not be destroyed by the 2s poll's
full re-render. fetchState defers render() while a SELECT inside the overlay is
focused, and flushes the deferred render on the select's change/blur.

Part B — the open <option> list must not blend into the transparent frameless
window: each <option> carries an opaque inline background, and the select itself
uses an opaque panel background instead of the near-transparent --panel-2.
"""
from pathlib import Path

import app_paths


def _overlay_src():
    return (app_paths.repo_dir() / "web" / "static" / "overlay.html").read_text(encoding="utf-8")


# --- Part A: defer/flush re-render while a select is open ---

def test_pending_state_render_flag_declared():
    assert "let pendingStateRender = false;" in _overlay_src()


def test_fetchstate_defers_render_for_open_select():
    src = _overlay_src()
    fetch_start = src.find("async function fetchState(")
    assert fetch_start != -1
    body = src[fetch_start:src.find("async function startPolling(")]
    # Guard checks the active element is a SELECT inside the overlay root.
    assert 'ae.tagName === "SELECT"' in body
    assert "root.contains(ae)" in body
    assert "pendingStateRender = true;" in body
    # The guard must short-circuit before the trailing render().
    guard_pos = body.find("pendingStateRender = true;")
    return_pos = body.find("return;", guard_pos)
    render_pos = body.rfind("render();")
    assert return_pos != -1 and return_pos < render_pos


def test_hero_selects_flush_pending_render():
    src = _overlay_src()
    assert "const flushPendingStateRender = ()" in src
    # Both selects flush on change and on blur.
    for attr in ("[data-browse-hero-select]", "[data-my-builds-hero-select]"):
        sel_pos = src.find(attr)
        assert sel_pos != -1, f"select wiring missing: {attr}"
        block = src[sel_pos:sel_pos + 400]
        assert 'addEventListener("blur", flushPendingStateRender)' in block
        assert "flushPendingStateRender();" in block


# --- Part B: opaque dropdown backgrounds ---

def test_options_have_opaque_background():
    src = _overlay_src()
    # Both hero option maps render an opaque per-option background.
    assert src.count('style="background:#1e1e2e;color:var(--text);"') >= 4


def test_selects_use_opaque_panel_background():
    src = _overlay_src()
    for attr in ("data-browse-hero-select", "data-my-builds-hero-select"):
        pos = src.find(attr)
        assert pos != -1
        block = src[pos:pos + 200]
        assert "background:var(--panel-3,#1e1e2e)" in block
        assert "background:var(--panel-2)" not in block
