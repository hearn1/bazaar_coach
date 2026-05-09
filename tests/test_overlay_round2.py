from pathlib import Path

from web.server import app


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
