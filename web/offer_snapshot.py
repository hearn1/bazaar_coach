"""
web/offer_snapshot.py — Pre-decision offer snapshot lookup.

find_offer_snapshot() returns the api_game_states.id of the most recent
snapshot (at or before the decision timestamp) whose api_cards
(category='offered') is a superset of the requested offered_instance_ids.

Used by RunState._build_live_decision_context to populate
decisions.api_game_state_id_at_offer — a stable pointer to the offer that
was on the table before the player acted.

captured_at format note: api_game_states.captured_at is mixed — some rows
are ISO 8601 strings, some are Unix-epoch-milliseconds integers stored as
TEXT.  Both are handled by _parse_captured_at.
"""

from __future__ import annotations

import sqlite3
from typing import Optional

import card_cache as _card_cache


def _parse_captured_at(raw: str | int | None) -> float:
    """Normalise captured_at to a float (Unix seconds) for comparison.

    Accepts:
      - None → -inf (sorts before everything)
      - int or digit string > 1e10 → Unix milliseconds → convert to seconds
      - ISO 8601 string → parse to epoch seconds
    """
    if raw is None:
        return float("-inf")
    try:
        val = float(raw)
        if val > 1e10:
            return val / 1000.0
        return val
    except (TypeError, ValueError):
        pass
    # ISO 8601 — strip timezone suffix and parse
    s = str(raw).rstrip("Z")
    # Remove +HH:MM or -HH:MM offset if present
    for sep in ("+", "-"):
        if "T" in s and sep in s.split("T", 1)[1]:
            s = s[: s.rfind(sep)]
            break
    from datetime import datetime
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).timestamp()
        except ValueError:
            continue
    return float("-inf")


def resolve_rejected_templates(
    conn: sqlite3.Connection,
    offer_snapshot_id: Optional[int],
    rejected_instance_ids: list[str],
) -> list[str]:
    """Return list[str] of template_ids aligned to rejected_instance_ids.

    Each entry is the template_id from api_cards where
    game_state_id = offer_snapshot_id AND instance_id IN (...) AND
    category = 'offered'.  Missing entries become ''.

    Args:
        conn: A SQLite connection (any thread-owned connection).
        offer_snapshot_id: The api_game_states.id of the offer snapshot.
            If None, returns a list of empty strings (same length as input).
        rejected_instance_ids: The instance IDs to resolve.

    Returns:
        A list of template_id strings, one per element of rejected_instance_ids.
        Missing entries are represented as ''.
    """
    if not rejected_instance_ids:
        return []

    if offer_snapshot_id is None:
        return [""] * len(rejected_instance_ids)

    placeholders = ",".join("?" for _ in rejected_instance_ids)
    rows = conn.execute(
        f"""
        SELECT instance_id, template_id
        FROM api_cards
        WHERE game_state_id = ?
          AND category = 'offered'
          AND instance_id IN ({placeholders})
          AND template_id IS NOT NULL
          AND template_id != ''
        """,
        (offer_snapshot_id, *rejected_instance_ids),
    ).fetchall()

    template_map: dict[str, str] = {}
    for row in rows:
        iid = row[0]
        tid = row[1]
        if iid and tid and not _card_cache.is_suspicious_template_id(tid):
            if iid not in template_map:
                template_map[iid] = tid

    return [template_map.get(iid, "") for iid in rejected_instance_ids]


def find_offer_snapshot(
    conn: sqlite3.Connection,
    baseline_id: int,
    decision_timestamp: str,
    offered_instance_ids: list[str],
) -> Optional[int]:
    """Return the api_game_states.id of the best pre-decision offer snapshot.

    The best snapshot is the most recent one (by id, descending) whose
    api_cards with category='offered' is a superset of offered_instance_ids,
    bounded by baseline_id (exclusive) to avoid prior-run rows.

    Args:
        conn: A read-only SQLite connection (any thread-owned connection).
        baseline_id: Only consider snapshots with id > baseline_id.
        decision_timestamp: ISO 8601 or Unix-ms string for the decision.
            Snapshots captured AFTER this timestamp are excluded (the offer
            must pre-date the decision).
        offered_instance_ids: Instance IDs that must all be present in the
            snapshot's offered cards.  If empty, returns None.

    Returns:
        The id of the matching api_game_states row, or None if no match.
    """
    if not offered_instance_ids:
        return None

    decision_epoch = _parse_captured_at(decision_timestamp)
    offered_set = set(offered_instance_ids)

    # Fetch candidate snapshots above the baseline, ordered most-recent first.
    # We pull enough to cover a few rerolls; 50 is ample for any shop window.
    rows = conn.execute(
        """
        SELECT id, captured_at
        FROM api_game_states
        WHERE id > ?
          AND (run_state IS NULL OR run_state NOT IN ('EndRunDefeat', 'EndRunVictory'))
        ORDER BY id DESC
        LIMIT 50
        """,
        (baseline_id,),
    ).fetchall()

    for row in rows:
        gs_id = row[0]
        cap_epoch = _parse_captured_at(row[1])

        # Skip snapshots that were captured after the decision was recorded.
        # Allow a small tolerance (5 s) to absorb clock skew between the
        # Player.log timestamp and the Mono capture timestamp.
        if cap_epoch > decision_epoch + 5.0:
            continue

        # Check that every requested instance_id appears in this snapshot's
        # offered cards.
        if not offered_set:
            continue
        placeholders = ",".join("?" for _ in offered_set)
        matched = conn.execute(
            f"""
            SELECT COUNT(DISTINCT instance_id)
            FROM api_cards
            WHERE game_state_id = ?
              AND category = 'offered'
              AND instance_id IN ({placeholders})
            """,
            (gs_id, *list(offered_set)),
        ).fetchone()[0]

        if matched >= len(offered_set):
            return gs_id

    return None
