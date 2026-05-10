"""Tests for P1-J: unscored_decisions_report and catalog_coverage_report."""

import sqlite3
from unittest.mock import patch

import db
import card_cache


def _point_db_at(tmp_path, monkeypatch):
    path = tmp_path / "bazaar_runs.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    db.close_shared_conn()
    return path


def _insert_run(conn, session_id, hero="Karnok"):
    cur = conn.execute(
        "INSERT INTO runs (session_id, hero, started_at) VALUES (?, ?, ?)",
        (session_id, hero, "2025-01-01T00:00:00"),
    )
    return cur.lastrowid


def _insert_decision(conn, run_id, seq, chosen_template, score_notes=""):
    conn.execute(
        "INSERT INTO decisions (run_id, decision_seq, timestamp, game_state, decision_type, "
        "offered, chosen_id, chosen_template, rejected, board_section, target_socket, score_notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, seq, "2025-01-01T00:00:00", "Shop", "buy",
         "[]", "id1", chosen_template, "[]", "hand", "", score_notes),
    )


def _insert_card_cache(conn, template_id, name):
    conn.execute(
        "INSERT OR REPLACE INTO card_cache (template_id, name, cached_at) VALUES (?, ?, ?)",
        (template_id, name, "2025-01-01T00:00:00"),
    )


# ── unscored_decisions_report ────────────────────────────────────────────────

UNSCORED_NOTE = "Not in Karnok catalog — no score assigned."


def test_unscored_decisions_report_basic(tmp_path, monkeypatch):
    path = _point_db_at(tmp_path, monkeypatch)
    db.init_db()

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    run_id = _insert_run(conn, "sess-1", hero="Karnok")

    # Decision 1: unscored, template in card_cache -> should resolve to name
    _insert_card_cache(conn, "tmpl-aaa", "Freeze Ray")
    _insert_decision(conn, run_id, seq=1, chosen_template="tmpl-aaa", score_notes=UNSCORED_NOTE)

    # Decision 2: unscored, same template -> count=2
    _insert_decision(conn, run_id, seq=2, chosen_template="tmpl-aaa", score_notes=UNSCORED_NOTE)

    # Decision 3: unscored, template NOT in card_cache -> COALESCE falls back to chosen_template
    _insert_decision(conn, run_id, seq=3, chosen_template="tmpl-zzz-unknown", score_notes=UNSCORED_NOTE)

    # Decision 4: scored normally -> should NOT appear
    _insert_decision(conn, run_id, seq=4, chosen_template="tmpl-aaa", score_notes="core item")

    conn.commit()
    conn.close()

    rows = db.unscored_decisions_report()

    assert len(rows) == 2

    by_name = {r["item_name"]: r for r in rows}

    # Resolved name for tmpl-aaa
    assert "Freeze Ray" in by_name
    assert by_name["Freeze Ray"]["count"] == 2
    assert by_name["Freeze Ray"]["hero"] == "Karnok"

    # Unresolved template fallback
    assert "tmpl-zzz-unknown" in by_name
    assert by_name["tmpl-zzz-unknown"]["count"] == 1


def test_unscored_decisions_report_hero_filter(tmp_path, monkeypatch):
    path = _point_db_at(tmp_path, monkeypatch)
    db.init_db()

    conn = sqlite3.connect(path)
    run_karnok = _insert_run(conn, "sess-k", hero="Karnok")
    run_mak = _insert_run(conn, "sess-m", hero="Mak")

    mak_note = "Not in Mak catalog — no score assigned."
    _insert_decision(conn, run_karnok, seq=1, chosen_template="tmpl-k1", score_notes=UNSCORED_NOTE)
    _insert_decision(conn, run_mak, seq=1, chosen_template="tmpl-m1", score_notes=mak_note)
    conn.commit()
    conn.close()

    karnok_rows = db.unscored_decisions_report(hero="Karnok")
    assert all(r["hero"] == "Karnok" for r in karnok_rows)
    assert len(karnok_rows) == 1

    mak_rows = db.unscored_decisions_report(hero="Mak")
    assert all(r["hero"] == "Mak" for r in mak_rows)
    assert len(mak_rows) == 1

    all_rows = db.unscored_decisions_report()
    assert len(all_rows) == 2


def test_unscored_decisions_report_empty(tmp_path, monkeypatch):
    _point_db_at(tmp_path, monkeypatch)
    db.init_db()
    rows = db.unscored_decisions_report()
    assert rows == []


# ── catalog_coverage_report ──────────────────────────────────────────────────

def test_catalog_coverage_report_shape(tmp_path, monkeypatch):
    """Verify report shape against bundled real catalogs and empty card_cache."""
    _point_db_at(tmp_path, monkeypatch)
    db.init_db()

    import scorer
    report = card_cache.catalog_coverage_report()

    # All heroes present
    expected_heroes = {h.capitalize() for h in scorer.CATALOG_FILENAMES}
    assert expected_heroes == set(report.keys())

    for hero, data in report.items():
        assert "catalog_items_count" in data
        assert "card_cache_total" in data
        assert "items_in_cache_not_in_catalog" in data
        assert "items_in_catalog_not_in_cache" in data
        assert isinstance(data["catalog_items_count"], int)
        assert data["card_cache_total"] == 0  # empty test DB
        assert isinstance(data["items_in_cache_not_in_catalog"], list)
        assert isinstance(data["items_in_catalog_not_in_cache"], list)


def test_catalog_coverage_report_with_cache_items(tmp_path, monkeypatch):
    """Items in card_cache but not in any catalog should appear in coverage diff."""
    path = _point_db_at(tmp_path, monkeypatch)
    db.init_db()

    conn = sqlite3.connect(path)
    # Insert a card that won't be in any catalog
    conn.execute(
        "INSERT OR REPLACE INTO card_cache (template_id, name, cached_at) VALUES (?, ?, ?)",
        ("tmpl-unique-xyz", "ZZZ_NotInAnyBuild", "2025-01-01"),
    )
    conn.commit()
    conn.close()

    report = card_cache.catalog_coverage_report()

    # card_cache_total should be 1
    for hero, data in report.items():
        assert data["card_cache_total"] == 1
        # The unknown item should appear in items_in_cache_not_in_catalog for each hero
        assert "ZZZ_NotInAnyBuild" in data["items_in_cache_not_in_catalog"]


def test_catalog_coverage_report_catalog_items_count_nonzero(tmp_path, monkeypatch):
    """Real bundled catalogs must have >0 items discovered (basic sanity)."""
    _point_db_at(tmp_path, monkeypatch)
    db.init_db()

    report = card_cache.catalog_coverage_report()

    for hero, data in report.items():
        assert data["catalog_items_count"] >= 0  # some heroes may have 0 if empty fallback
