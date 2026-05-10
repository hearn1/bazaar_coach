from pathlib import Path

HTML_PATH = Path(__file__).resolve().parents[1] / "web" / "static" / "index.html"


def test_my_builds_button_present():
    html = HTML_PATH.read_bytes()
    assert b'id="my-builds-btn"' in html


def test_my_builds_panel_present():
    html = HTML_PATH.read_bytes()
    assert b'id="my-builds-panel"' in html


def test_toggle_my_builds_panel_function():
    html = HTML_PATH.read_bytes()
    assert b'function toggleMyBuildsPanel' in html


def test_form_field_core_items():
    html = HTML_PATH.read_bytes()
    assert b'core_items' in html


def test_form_field_carry_items():
    html = HTML_PATH.read_bytes()
    assert b'carry_items' in html


def test_form_field_support_items():
    html = HTML_PATH.read_bytes()
    assert b'support_items' in html


def test_form_field_condition_items():
    html = HTML_PATH.read_bytes()
    assert b'condition_items' in html


def test_form_field_phase():
    html = HTML_PATH.read_bytes()
    assert b'phase' in html


def test_form_field_timing_profile():
    html = HTML_PATH.read_bytes()
    assert b'timing_profile' in html


def test_form_field_notes():
    html = HTML_PATH.read_bytes()
    assert b'notes' in html


def test_put_method_present():
    html = HTML_PATH.read_bytes()
    assert b"method: 'PUT'" in html


def test_user_builds_api_path_present():
    html = HTML_PATH.read_bytes()
    assert b'/api/builds/user/' in html


def test_delete_method_present():
    html = HTML_PATH.read_bytes()
    assert b"method: 'DELETE'" in html


def test_my_builds_conflict_element():
    html = HTML_PATH.read_bytes()
    assert b'id="my-builds-conflict"' in html


def test_my_builds_hero_select_element():
    html = HTML_PATH.read_bytes()
    assert b'id="my-builds-hero-select"' in html


def test_load_my_builds_function():
    html = HTML_PATH.read_bytes()
    assert b'function loadMyBuilds' in html


def test_check_my_builds_conflicts_function():
    html = HTML_PATH.read_bytes()
    assert b'function checkMyBuildsConflicts' in html


def test_submit_my_builds_form_function():
    html = HTML_PATH.read_bytes()
    assert b'function submitMyBuildsForm' in html


def test_delete_my_builds_archetype_function():
    html = HTML_PATH.read_bytes()
    assert b'function deleteMyBuildsArchetype' in html
