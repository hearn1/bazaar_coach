from web.build_helpers import extract_skip_relevant_items, get_phase_notes, load_builds


# P1-C: unquoted bracket form
def test_extract_skip_unquoted_bracket():
    notes = "Skipped after 1 reroll(s) - missed: Core for Submarine: [Captain's Quarters]"
    assert extract_skip_relevant_items(notes) == ["Captain's Quarters"]


# P1-C: regression — existing quoted list form must still work
def test_extract_skip_quoted_list():
    notes = "missed: ['Foo', 'Bar']"
    assert extract_skip_relevant_items(notes) == ["Foo", "Bar"]


# P1-C: empty brackets produce nothing
def test_extract_skip_empty_bracket():
    assert extract_skip_relevant_items("missed: []") == []


# P1-C: no missed marker
def test_extract_skip_no_marker():
    assert extract_skip_relevant_items("skipped shop") == []


# P1-H: late phase unions economy_items from early phase
def test_phase_notes_late_unions_economy_items():
    build_data, _ = load_builds("Karnok")
    result = get_phase_notes(8, build_data=build_data)
    assert result["phase"] == "late"
    assert len(result["economy_items"]) > 0


# P1-H: late phase unions universal_utility_items from early phase
def test_phase_notes_late_unions_utility_items():
    build_data, _ = load_builds("Karnok")
    result = get_phase_notes(8, build_data=build_data)
    assert len(result["universal_utility_items"]) > 0


# P1-H: early phase still returns its own items (no regression)
def test_phase_notes_early_returns_economy_items():
    build_data, _ = load_builds("Karnok")
    result = get_phase_notes(2, build_data=build_data)
    assert result["phase"] == "early"
    assert "Hunter's Journal" in result["economy_items"]


# P1-H: field names unchanged
def test_phase_notes_field_names_unchanged():
    build_data, _ = load_builds("Karnok")
    result = get_phase_notes(8, build_data=build_data)
    assert "economy_items" in result
    assert "universal_utility_items" in result
    assert "phase" in result
    assert "day_range" in result
    assert "description" in result
    assert "notes" in result
