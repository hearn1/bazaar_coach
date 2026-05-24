"""
web/overlay_state.py — Assembles the /api/overlay/state API response.

This module owns the full construction of the overlay state payload. It reads
from the DB, delegates inventory projection to board_state helpers, delegates
archetype scoring to build_helpers, and delegates review row assembly to
review_builder.

No Flask imports — the route in server.py calls ``build_overlay_state(conn)``
and jsonifies the result.
"""

from typing import Optional

from board_state import BoardState
from web.build_helpers import (
    load_builds,
    score_archetypes,
    get_phase_notes,
    classify_run_tier,
)
from name_resolver import is_unresolved
from web.review_builder import build_overlay_review_rows, summarize_overlay_review_rows


# ── DB helpers (kept local to avoid circular imports with server.py) ──────────

_PVE_WIN_STATES = {"Loot", "LevelUp", "EndRunVictory"}
_PVE_LOSS_STATES = {"Choice", "Encounter", "Pedestal", "EndRunDefeat"}


def _has_column(conn, table: str, column: str) -> bool:
    try:
        return any(row["name"] == column for row in conn.execute(f"PRAGMA table_info({table})"))
    except Exception:
        return False


def _prestige_select_expr(conn) -> str:
    if _has_column(conn, "api_game_states", "full_json"):
        return "json_extract(full_json, '$.player.Prestige') AS prestige"
    return "NULL AS prestige"


def _get_pve_record(conn, run_id: int, run: Optional[dict] = None) -> tuple[int, int]:
    if run is not None:
        mono = _get_mono_record(conn, run)
        if mono and mono["has_pve_signal"]:
            return mono["pve_wins"], mono["pve_losses"]

    pve = conn.execute("""
        SELECT
            SUM(CASE WHEN outcome='opponent_died' AND (combat_type='pve' OR combat_type IS NULL) THEN 1 ELSE 0 END) as w,
            SUM(CASE WHEN outcome='player_died' AND (combat_type='pve' OR combat_type IS NULL) THEN 1 ELSE 0 END) as l
        FROM combat_results WHERE run_id=?
    """, (run_id,)).fetchone()
    if not pve:
        return 0, 0
    return (pve["w"] or 0), (pve["l"] or 0)


def _get_pvp_record(conn, run_id: int, run: dict) -> tuple[int, int]:
    mono = _get_mono_record(conn, run)
    if mono and mono["has_pvp_total"]:
        return mono["pvp_wins"], mono["pvp_losses"]

    pvp_w, pvp_l = 0, 0
    combats = conn.execute(
        "SELECT outcome, combat_type FROM combat_results WHERE run_id=?", (run_id,)
    ).fetchall()
    for c in combats:
        if (c["combat_type"] or "pve") == "pvp":
            if c["outcome"] == "opponent_died":
                pvp_w += 1
            elif c["outcome"] == "player_died":
                pvp_l += 1
    terminal = _get_run_end_snapshot(conn, run)
    if terminal and terminal.get("victories") is not None:
        return terminal["victories"], terminal.get("defeats") or 0
    return pvp_w, pvp_l


def _get_run_record(conn, run: dict) -> dict:
    """Return PvP/PvE counters, preferring run-anchored Mono snapshots."""
    mono = _get_mono_record(conn, run)
    combat = _get_combat_result_record(conn, run["id"])
    if mono and mono["has_pve_signal"]:
        pve_w, pve_l = mono["pve_wins"], mono["pve_losses"]
    else:
        pve_w, pve_l = combat["pve_wins"], combat["pve_losses"]

    if mono and mono["has_pvp_total"]:
        pvp_w, pvp_l = mono["pvp_wins"], mono["pvp_losses"]
    else:
        pvp_w, pvp_l = combat["pvp_wins"], combat["pvp_losses"]
        terminal = _get_run_end_snapshot(conn, run)
        if terminal and terminal.get("victories") is not None:
            pvp_w = terminal["victories"]
            pvp_l = terminal.get("defeats") or 0

    return {
        "pvp_wins": pvp_w,
        "pvp_losses": pvp_l,
        "pve_wins": pve_w,
        "pve_losses": pve_l,
        "record_source": "mono" if mono and mono["has_combat_signal"] else "combat_results",
    }


def _get_combat_result_record(conn, run_id: int) -> dict:
    row = conn.execute("""
        SELECT
            SUM(CASE WHEN combat_type='pvp' AND outcome='opponent_died' THEN 1 ELSE 0 END) AS pvp_w,
            SUM(CASE WHEN combat_type='pvp' AND outcome='player_died'   THEN 1 ELSE 0 END) AS pvp_l,
            SUM(CASE WHEN outcome='opponent_died' AND (combat_type='pve' OR combat_type IS NULL) THEN 1 ELSE 0 END) AS pve_w,
            SUM(CASE WHEN outcome='player_died'   AND (combat_type='pve' OR combat_type IS NULL) THEN 1 ELSE 0 END) AS pve_l
        FROM combat_results
        WHERE run_id=?
    """, (run_id,)).fetchone()
    if not row:
        return {"pvp_wins": 0, "pvp_losses": 0, "pve_wins": 0, "pve_losses": 0}
    return {
        "pvp_wins": row["pvp_w"] or 0,
        "pvp_losses": row["pvp_l"] or 0,
        "pve_wins": row["pve_w"] or 0,
        "pve_losses": row["pve_l"] or 0,
    }


def _get_mono_record(conn, run: dict) -> Optional[dict]:
    rows = _get_run_mono_state_rows(conn, run)
    if not rows:
        return None

    pvp_w = pvp_l = 0
    has_pvp_total = False
    pve_w = pve_l = 0
    has_combat_signal = False
    has_pve_signal = False
    active_combat: Optional[str] = None
    last_state = None

    for row in rows:
        state = row["run_state"]
        if state == last_state:
            continue
        last_state = state

        if row["victories"] is not None:
            pvp_w = row["victories"]
            pvp_l = row["defeats"] or 0
            has_pvp_total = True

        if state == "Combat":
            active_combat = "pve"
            has_combat_signal = True
            has_pve_signal = True
            continue
        if state == "PVPCombat":
            active_combat = "pvp"
            has_combat_signal = True
            continue

        if active_combat == "pve":
            if state in _PVE_WIN_STATES:
                pve_w += 1
                active_combat = None
            elif state in _PVE_LOSS_STATES:
                pve_l += 1
                active_combat = None
        elif active_combat == "pvp" and state not in ("Combat", "PVPCombat"):
            active_combat = None

    return {
        "pvp_wins": pvp_w,
        "pvp_losses": pvp_l,
        "pve_wins": pve_w,
        "pve_losses": pve_l,
        "has_pvp_total": has_pvp_total,
        "has_combat_signal": has_combat_signal,
        "has_pve_signal": has_pve_signal,
    }


def _get_run_mono_state_rows(conn, run: dict):
    bounds = conn.execute(
        """
        SELECT MIN(api_game_state_id) AS first_id,
               MAX(api_game_state_id) AS last_id
        FROM decisions
        WHERE run_id = ? AND api_game_state_id IS NOT NULL
        """,
        (run["id"],),
    ).fetchone()
    if not bounds or bounds["first_id"] is None:
        return []

    start_id = bounds["first_id"]
    end_snap = _get_run_end_snapshot(conn, run)
    end_id = end_snap.get("id") if end_snap else None
    next_run_first_id = _get_next_run_first_api_state_id(conn, run, start_id)
    if end_id is None:
        latest = conn.execute(
            """
            SELECT MAX(id) AS latest_id
            FROM api_game_states
            WHERE id >= ?
              AND (? IS NULL OR id < ?)
              AND (? IS NULL OR hero = ? OR hero IS NULL OR hero = '' OR hero = 'Unknown')
            """,
            (start_id, next_run_first_id, next_run_first_id, run.get("hero"), run.get("hero")),
        ).fetchone()
        end_id = latest["latest_id"] if latest and latest["latest_id"] is not None else bounds["last_id"]

    return conn.execute(
        """
        SELECT id, run_state, day, hour, victories, defeats
        FROM api_game_states
        WHERE id >= ? AND id <= ?
          AND run_state IS NOT NULL
          AND (? IS NULL OR hero = ? OR hero IS NULL OR hero = '' OR hero = 'Unknown')
        ORDER BY id
        """,
        (start_id, end_id, run.get("hero"), run.get("hero")),
    ).fetchall()


def _get_next_run_first_api_state_id(conn, run: dict, after_id: int) -> Optional[int]:
    row = conn.execute(
        """
        SELECT MIN(d.api_game_state_id) AS next_id
        FROM decisions d
        WHERE d.run_id > ?
          AND d.api_game_state_id IS NOT NULL
          AND d.api_game_state_id > ?
        """,
        (run["id"], after_id),
    ).fetchone()
    return row["next_id"] if row and row["next_id"] is not None else None


def _get_latest_live_snapshot(conn, run: dict) -> Optional[dict]:
    """Return the most recent in-progress Mono snapshot row for ``run``.

    Scoped via ``decisions.api_game_state_id`` bounds (same pattern as
    ``_get_run_mono_state_rows``) so a prior run's stale snapshots never
    bleed into the current run's overlay header.
    """
    bounds = conn.execute(
        """
        SELECT MIN(api_game_state_id) AS first_id
        FROM decisions
        WHERE run_id = ? AND api_game_state_id IS NOT NULL
        """,
        (run["id"],),
    ).fetchone()
    if not bounds or bounds["first_id"] is None:
        return None
    start_id = bounds["first_id"]
    next_run_first_id = _get_next_run_first_api_state_id(conn, run, start_id)

    prestige_expr = _prestige_select_expr(conn)
    row = conn.execute(
        f"""
        SELECT id, day, hour, gold, health, health_max,
               victories, defeats, run_state, captured_at,
               {prestige_expr}
        FROM api_game_states
        WHERE id >= ?
          AND (? IS NULL OR id < ?)
          AND (? IS NULL OR hero = ? OR hero IS NULL OR hero = '' OR hero = 'Unknown')
          AND run_state IS NOT NULL
          AND run_state NOT IN ('EndRunDefeat', 'EndRunVictory')
        ORDER BY id DESC
        LIMIT 1
        """,
        (start_id, next_run_first_id, next_run_first_id, run.get("hero"), run.get("hero")),
    ).fetchone()
    return dict(row) if row else None


def _get_run_end_snapshot(conn, run: dict) -> Optional[dict]:
    """Return a completed-run snapshot linked from decision live context."""
    prestige_expr = _prestige_select_expr(conn)
    latest = conn.execute(
        """
        SELECT api_game_state_id
        FROM decisions
        WHERE run_id = ? AND api_game_state_id IS NOT NULL
        ORDER BY decision_seq DESC, id DESC
        LIMIT 1
        """,
        (run["id"],),
    ).fetchone()
    if not latest:
        return None
    next_run_first_id = _get_next_run_first_api_state_id(conn, run, latest["api_game_state_id"])
    row = conn.execute(
        f"""
        SELECT id, day, hour, gold, health, health_max,
               victories, defeats, run_state, captured_at,
               {prestige_expr}
        FROM api_game_states
        WHERE id >= ?
          AND (? IS NULL OR id < ?)
          AND (? IS NULL OR hero = ? OR hero IS NULL OR hero = '' OR hero = 'Unknown')
          AND run_state IN ('EndRunDefeat', 'EndRunVictory')
        ORDER BY id ASC
        LIMIT 1
        """,
        (
            latest["api_game_state_id"],
            next_run_first_id,
            next_run_first_id,
            run.get("hero"),
            run.get("hero"),
        ),
    ).fetchone()
    if row:
        return dict(row)
    row = conn.execute(
        f"""
        SELECT id, day, hour, gold, health, health_max,
               victories, defeats, run_state, captured_at,
               {prestige_expr}
        FROM api_game_states
        WHERE id >= ?
          AND (? IS NULL OR id < ?)
          AND (? IS NULL OR hero = ? OR hero IS NULL OR hero = '' OR hero = 'Unknown')
        ORDER BY id ASC
        LIMIT 1
        """,
        (
            latest["api_game_state_id"],
            next_run_first_id,
            next_run_first_id,
            run.get("hero"),
            run.get("hero"),
        ),
    ).fetchone()
    if row:
        return dict(row)
    return None


def _get_in_run_prestige_fallback(conn, run: dict) -> Optional[int]:
    """Pull prestige from the latest in-run api_game_states row, terminal-OK.

    The live-snapshot path excludes EndRun rows, and decisions have no
    prestige column. When we land in ``decision_fallback`` but at least one
    in-run snapshot exists (e.g. capture went down mid-run), we can still
    surface the most recent prestige value without leaking prior-run data.
    Bounds-scoped via ``decisions.api_game_state_id`` plus next-run cutoff,
    mirroring the pattern in :func:`_get_run_mono_state_rows`.
    """
    if not _has_column(conn, "api_game_states", "full_json"):
        return None
    bounds = conn.execute(
        """
        SELECT MIN(api_game_state_id) AS first_id,
               MAX(api_game_state_id) AS last_id
        FROM decisions
        WHERE run_id = ? AND api_game_state_id IS NOT NULL
        """,
        (run["id"],),
    ).fetchone()
    if not bounds or bounds["first_id"] is None:
        return None
    start_id = bounds["first_id"]
    end_id = bounds["last_id"]
    next_run_first_id = _get_next_run_first_api_state_id(conn, run, start_id)
    row = conn.execute(
        """
        SELECT json_extract(full_json, '$.player.Prestige') AS prestige
        FROM api_game_states
        WHERE id >= ?
          AND id <= ?
          AND (? IS NULL OR id < ?)
          AND (? IS NULL OR hero = ? OR hero IS NULL OR hero = '' OR hero = 'Unknown')
          AND json_extract(full_json, '$.player.Prestige') IS NOT NULL
        ORDER BY id DESC
        LIMIT 1
        """,
        (
            start_id,
            end_id,
            next_run_first_id,
            next_run_first_id,
            run.get("hero"),
            run.get("hero"),
        ),
    ).fetchone()
    if row and row["prestige"] is not None:
        return row["prestige"]
    return None


def _build_owned_inventory_projection(conn, run_id: int) -> dict:
    """Read current owned inventory from board_snapshot_json on the latest decision.

    Falls back to decision-based name accumulation for older runs that predate
    board_snapshot_json.
    """
    row = conn.execute("""
        SELECT board_snapshot_json
        FROM decisions
        WHERE run_id = ?
        ORDER BY decision_seq DESC
        LIMIT 1
    """, (run_id,)).fetchone()

    snapshot_json = row["board_snapshot_json"] if row else None

    if snapshot_json:
        owned_names = BoardState.owned_names_from_snapshot_json(snapshot_json)
        cards_by_category = BoardState.cards_by_category_from_snapshot_json(snapshot_json)
        return {
            "source": "board_snapshot",
            "owned_names": owned_names,
            "cards_by_category": cards_by_category,
        }

    # Fallback for older runs without board_snapshot_json
    chosen_rows = conn.execute("""
        SELECT chosen_template
        FROM decisions
        WHERE run_id=? AND decision_type IN ('item', 'companion', 'free_reward')
          AND chosen_template IS NOT NULL AND chosen_template != ''
    """, (run_id,)).fetchall()

    import card_cache
    chosen_names = sorted({
        card_cache.resolve_template_id(r["chosen_template"])
        for r in chosen_rows
        if r["chosen_template"]
    } - {None, "", "Unknown"})

    return {
        "source": "decisions_fallback",
        "owned_names": chosen_names,
        "cards_by_category": {
            "player_board": [],
            "player_stash": [],
            "player_skills": [],
        },
    }


# ── Main assembler ────────────────────────────────────────────────────────────

def build_overlay_state(conn, *, resolve_fn=None, safe_json_fn=None, lookup_image_by_name_fn=None) -> dict:
    """Return the full overlay state payload dict (not yet jsonified).

    ``resolve_fn`` and ``safe_json_fn`` are passed through to review_builder
    to avoid circular imports with server.py.
    """
    run = conn.execute(
        "SELECT * FROM runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not run:
        return {"error": "No runs found"}

    run = dict(run)
    build_data, _relevant_items = load_builds(run.get("hero"))
    is_active = run["outcome"] is None

    raw_decisions = conn.execute("""
        SELECT id, decision_seq, decision_type, game_state, board_section,
               chosen_id, chosen_template, offered, offered_names, rejected,
               score_label, score_notes, day, hour, gold, health
        FROM decisions
        WHERE run_id=?
        ORDER BY decision_seq
    """, (run["id"],)).fetchall()
    latest_decision = dict(raw_decisions[-1]) if raw_decisions else None

    inventory_projection = _build_owned_inventory_projection(conn, run["id"])
    owned_set = set(inventory_projection["owned_names"])

    arch_scores = score_archetypes(owned_set, build_data=build_data)

    decision_rows = build_overlay_review_rows(
        conn,
        run["id"],
        raw_decisions,
        build_data=build_data,
        prefer_scored_fallback=True,
        resolve_fn=resolve_fn,
        safe_json_fn=safe_json_fn,
        lookup_image_by_name_fn=lookup_image_by_name_fn,
    )

    # ── Live header stats ────────────────────────────────────────────────────
    run_record = _get_run_record(conn, run)
    pve_w, pve_l = run_record["pve_wins"], run_record["pve_losses"]
    current_day = current_hour = current_gold = current_health = current_health_max = None
    current_prestige = None
    pvp_w = pvp_l = 0
    snapshot_source = "none"

    if is_active:
        live_snap = _get_latest_live_snapshot(conn, run)
        if live_snap:
            current_day = live_snap.get("day")
            current_hour = live_snap.get("hour")
            current_gold = live_snap.get("gold")
            current_health = live_snap.get("health")
            current_health_max = live_snap.get("health_max")
            current_prestige = live_snap.get("prestige")
            pvp_w = live_snap.get("victories") or 0
            pvp_l = live_snap.get("defeats") or 0
            snapshot_source = "live_mono"
        else:
            if latest_decision:
                current_day = latest_decision.get("day")
                current_hour = latest_decision.get("hour")
                current_gold = latest_decision.get("gold")
                current_health = latest_decision.get("health")
            current_prestige = _get_in_run_prestige_fallback(conn, run)
            pvp_w, pvp_l = run_record["pvp_wins"], run_record["pvp_losses"]
            snapshot_source = "decision_fallback"
    else:
        end_snap = _get_run_end_snapshot(conn, run)
        if end_snap:
            current_day = end_snap.get("day")
            current_hour = end_snap.get("hour")
            current_gold = end_snap.get("gold")
            current_health = end_snap.get("health")
            current_health_max = end_snap.get("health_max")
            current_prestige = end_snap.get("prestige")
            pvp_w = end_snap.get("victories") or 0
            pvp_l = end_snap.get("defeats") or 0
            snapshot_source = "end_run_snapshot"
        else:
            if latest_decision:
                current_day = latest_decision.get("day")
                current_hour = latest_decision.get("hour")
                current_gold = latest_decision.get("gold")
                current_health = latest_decision.get("health")
            current_prestige = _get_in_run_prestige_fallback(conn, run)
            pvp_w, pvp_l = run_record["pvp_wins"], run_record["pvp_losses"]
            snapshot_source = "decision_fallback"

    score_summary = summarize_overlay_review_rows(decision_rows)
    run_tier = classify_run_tier(pvp_w, pvp_l, current_health, current_health_max)

    return {
        "run_id": run["id"],
        "hero": run["hero"],
        "outcome": run["outcome"],
        "is_active": is_active,
        "day": current_day,
        "hour": current_hour,
        "gold": current_gold,
        "health": current_health,
        "health_max": current_health_max,
        "snapshot_source": snapshot_source,
        "decision_count": len(raw_decisions),
        "chosen_names": inventory_projection["owned_names"],
        "owned_names": inventory_projection["owned_names"],
        "owned_by_category": {
            category: [
                card["name"]
                for card in cards
                if card.get("name") and not is_unresolved(card["name"])
            ]
            for category, cards in inventory_projection["cards_by_category"].items()
        },
        "inventory_source": inventory_projection["source"],
        "prestige": current_prestige,
        "pvp_wins": pvp_w,
        "pvp_losses": pvp_l,
        "pve_wins": pve_w,
        "pve_losses": pve_l,
        "arch_scores": arch_scores,
        "decisions": decision_rows,
        "score_summary": score_summary,
        "phase_notes": get_phase_notes(current_day, build_data=build_data),
        "run_tier": run_tier,
    }
