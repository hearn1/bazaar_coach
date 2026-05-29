"""
mono_event_adapter.py — Translates Mono snapshot deltas into the same event-dict
format that parser.py produces, then feeds them into RunState.process(event).

Entry point: MonoEventAdapter(run_state).process_snapshot(snapshot_dict)

Call this from capture_mono's background worker right after handle_game_state()
persists the snapshot (so api_game_state_id is already assigned).

Synthetic session_id / account_id (--event-source mono only)
-------------------------------------------------------------
session_id   — UUID generated per detected run boundary.
account_id   — SHA-1 of hostname + data_dir, giving a stable per-machine id.
These are only used internally for run-boundary detection and overlay headers.
When --event-source=both the log-derived values arrive first and win (dedup).
"""

import hashlib
import socket
import time
import uuid
from typing import Optional

import app_paths
from stdio_safety import configure_stdio_backslashreplace

configure_stdio_backslashreplace()

# ---------------------------------------------------------------------------
# Stable synthetic ids (one per coach-process launch)
# ---------------------------------------------------------------------------

_SYNTHETIC_SESSION_ID: str = str(uuid.uuid4())


def _new_synthetic_session_id() -> str:
    return str(uuid.uuid4())


def _make_synthetic_account_id() -> str:
    try:
        raw = socket.gethostname() + str(app_paths.data_dir())
    except Exception:
        raw = "bazaar_coach_default"
    return "mono-" + hashlib.sha1(raw.encode("utf-8", errors="replace")).hexdigest()[:16]


_SYNTHETIC_ACCOUNT_ID: str = _make_synthetic_account_id()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts_from_snapshot(snap: dict) -> str:
    """Extract an ISO timestamp string from the snapshot dict."""
    ts = snap.get("timestamp", "")
    if isinstance(ts, (int, float)):
        import datetime
        return datetime.datetime.fromtimestamp(
            ts / 1000.0, tz=datetime.timezone.utc
        ).isoformat()
    return ts or ""


_STATE_NAME_ALIASES = {
    "Choice": "ChoiceState",
    "Encounter": "EncounterState",
    "Combat": "CombatState",
    "PVPCombat": "PVPCombatState",
    "Replay": "ReplayState",
    "Loot": "LootState",
    "LevelUp": "LevelUpState",
    "Pedestal": "PedestalState",
    "EndRunDefeat": "EndRunDefeatState",
    "EndRunVictory": "EndRunVictoryState",
}


def _state_name(snap: dict) -> str:
    raw = (snap.get("state") or {}).get("state", "") or ""
    return _STATE_NAME_ALIASES.get(raw, raw)


def _hero_name(snap: dict) -> str:
    return (snap.get("player") or {}).get("hero", "") or ""


def _gold(snap: dict) -> Optional[float]:
    return (snap.get("player") or {}).get("Gold")


def _offered_instance_ids(snap: dict) -> frozenset:
    cards = snap.get("offered") or []
    return frozenset(
        c.get("instance_id") for c in cards if isinstance(c, dict) and c.get("instance_id")
    )


def _board_instance_ids(snap: dict) -> frozenset:
    ids: set[str] = set()
    for key in ("player_board", "player_stash", "player_skills"):
        for c in (snap.get(key) or []):
            if isinstance(c, dict) and c.get("instance_id"):
                ids.add(c["instance_id"])
    return frozenset(ids)


def _template_for_instance(snap: dict, instance_id: str) -> str:
    """Retrieve template_id for an instance_id from any card list in the snapshot."""
    for key in ("player_board", "player_stash", "player_skills", "offered",
                "opponent_board"):
        for c in (snap.get(key) or []):
            if isinstance(c, dict) and c.get("instance_id") == instance_id:
                return c.get("template_id", "")
    return ""


def _rerolls_remaining(snap: dict) -> Optional[int]:
    val = (snap.get("state") or {}).get("rerolls_remaining")
    if val is None:
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _shop_window_id_from_snap(snap: dict) -> Optional[int]:
    """Try to extract a shop_window_id from the snapshot state block."""
    val = (snap.get("state") or {}).get("shop_window_id")
    if val is None:
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# MonoEventAdapter
# ---------------------------------------------------------------------------

class MonoEventAdapter:
    """
    Stateful per-run adapter: receives merged snapshot dicts from
    capture_mono's background worker and emits event dicts to RunState.

    Lifecycle:
      - Instantiate once per RunState.
      - Call process_snapshot() for every snapshot that arrives.
      - The adapter resets itself when it sees a NewRun / EndRun state transition.
    """

    def __init__(self, run_state, event_source: str = "both"):
        """
        run_state   — a RunState instance (or any object with .process(event)).
        event_source — "log" | "mono" | "both".  Only "mono" and "both" actually
                       push events; "log" is a no-op (adapter inactive).
        """
        self._run_state = run_state
        self._event_source = event_source
        self._session_id = _SYNTHETIC_SESSION_ID

        # Previous snapshot state — reset on NewRun
        self._prev_snap: Optional[dict] = None
        self._prev_snap_id = None
        self._prev_state_name: str = ""
        self._prev_offered: frozenset = frozenset()
        self._prev_board: frozenset = frozenset()
        self._prev_rerolls: Optional[int] = None
        self._prev_shop_window_id: Optional[int] = None

        # instance_id -> template_id, learned from GameSim card_dealt/card_spawned
        # events so a later card_purchased (which carries no template) can be
        # resolved. Reset per run.
        self._instance_templates: dict = {}
        # instance_ids already emitted as card_purchased this run — dedup so the
        # same GameSim CardPurchased event (it can recur across the snapshot and
        # its late deferred-template enrichment) is only turned into one decision.
        self._emitted_card_purchases: set = set()

        # One-shot flags per run
        self._emitted_run_start: bool = False
        self._emitted_hero: bool = False
        self._emitted_session_id: bool = False
        self._emitted_account_id: bool = False
        self._emitted_run_init_complete: bool = False

        # Track last combat state for combat_complete inference
        self._in_combat: bool = False

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def process_snapshot(self, snap: dict) -> None:
        """Translate a single merged snapshot into zero or more RunState events."""
        if self._event_source == "log":
            return  # adapter inactive — log pipeline is sole source

        ts = _ts_from_snapshot(snap)
        curr_state = _state_name(snap)

        # Partial snapshots (rare; usually a deferred-cards payload that arrives
        # before its parent state has been merged) carry no state name. Pre-#148
        # these were filtered out by `_should_render_snapshot` before reaching
        # the adapter; now that filter is gone, guard explicitly so diff logic
        # never compares against a missing state.
        if not curr_state:
            return

        # ── Run boundary detection ──────────────────────────────────────
        if curr_state in ("NewRun", "RunInitialized"):
            self._handle_new_run(snap, ts)
            return

        if curr_state in ("EndRunDefeatState", "EndRunVictoryState", "EndRunDefeat", "EndRunVictory"):
            self._handle_terminal_state(snap, ts, curr_state)
            return

        # ── Sequence of emits for an ordinary snapshot ──────────────────
        events = self._diff(snap, ts, curr_state)
        for evt in events:
            self._emit(evt)

        # Advance prev state
        self._prev_snap = snap
        self._prev_snap_id = snap.get("id")
        self._prev_state_name = curr_state
        self._prev_offered = _offered_instance_ids(snap)
        self._prev_board = _board_instance_ids(snap)
        self._prev_rerolls = _rerolls_remaining(snap)
        self._prev_shop_window_id = _shop_window_id_from_snap(snap)

    def note_enriched_snapshot(self, snap: dict) -> None:
        """Refresh prev-snapshot state when late deferred data enriches it.

        ``handle_game_state`` dispatches a snapshot to the adapter as soon as
        it is merged, but the JS agent decodes heavy card/offer collections off
        the game thread and delivers them in later ``deferred_*`` messages. When
        that data lands for the snapshot we *just* processed, the adapter's
        ``_prev_*`` view is stale (often an empty offered set), so the next
        snapshot's diff can't see the offered→board purchase. Re-point the prev
        state at the enriched snapshot — without re-persisting or re-running the
        full differ — so the *next* real snapshot diffs correctly.

        It DOES emit authoritative ``card_purchased`` events the enrichment just
        delivered: GameSim ``CardPurchased`` events usually arrive in a late
        ``deferred_template_events`` message (after the snapshot was already
        dispatched to the differ), so without this they'd never be recorded.
        This runs on the capture worker thread — the same thread as normal
        snapshot processing — so the resulting ``insert_decision`` is the
        ordinary (non-reentrant) write path, not the reverted redispatch machinery.
        """
        if self._event_source == "log":
            return
        snap_id = snap.get("id")
        if snap_id is None or snap_id != self._prev_snap_id:
            return
        ts = _ts_from_snapshot(snap)
        for evt in self._gamesim_selection_events(snap, ts):
            self._emit(evt)
        self._prev_snap = snap
        self._prev_offered = _offered_instance_ids(snap)
        self._prev_board = _board_instance_ids(snap)
        self._prev_rerolls = _rerolls_remaining(snap)
        self._prev_shop_window_id = _shop_window_id_from_snap(snap)

    # ------------------------------------------------------------------
    # Core differ
    # ------------------------------------------------------------------

    def _diff(self, snap: dict, ts: str, curr_state: str) -> list[dict]:
        events: list[dict] = []

        # ── Hero / session bootstrap ────────────────────────────────────
        events.extend(self._bootstrap_events(snap, ts))

        # ── State change ────────────────────────────────────────────────
        if self._prev_state_name and curr_state != self._prev_state_name:
            events.append({
                "event": "state_change",
                "ts": ts,
                "from_state": self._prev_state_name,
                "to_state": curr_state,
                "source": "mono",
            })

        # ── Combat start / complete ─────────────────────────────────────
        if curr_state in ("CombatState", "PVPCombatState") and not self._in_combat:
            self._in_combat = True
            events.append({"event": "combat_start", "ts": ts, "source": "mono"})
        elif self._in_combat and curr_state not in ("CombatState", "PVPCombatState", "ReplayState"):
            self._in_combat = False
            events.append({
                "event": "combat_complete",
                "ts": ts,
                "duration_secs": 0.0,
                "source": "mono",
            })

        # ── Skip card diffs if still in combat / replay ─────────────────
        if curr_state in ("CombatState", "PVPCombatState", "ReplayState"):
            return events

        curr_offered = _offered_instance_ids(snap)
        curr_board = _board_instance_ids(snap)

        # ── cards_dealt: offered set gained new cards (not a subset of prev) ──
        new_in_offered = curr_offered - self._prev_offered
        if new_in_offered and curr_state == "EncounterState":
            events.append({
                "event": "cards_dealt",
                "ts": ts,
                "instance_ids": sorted(new_in_offered),
                "source": "mono",
            })

        # ── Reroll detection (from #128) ────────────────────────────────
        reroll_evt = self._detect_reroll(snap, ts, curr_state, curr_offered)
        if reroll_evt:
            events.append(reroll_evt)

        # ── Sell detection (from #128) ──────────────────────────────────
        sell_evt = self._detect_sell(snap, ts, curr_board)
        if sell_evt:
            events.append(sell_evt)

        # ── card_purchased: card moved from offered → board ─────────────
        if self._prev_snap is not None:
            moved_to_board = (self._prev_offered & curr_board) - self._prev_board
            for iid in sorted(moved_to_board):
                template_id = _template_for_instance(snap, iid)
                # Determine section / target_socket from snap card record
                section, target_socket = self._locate_card_in_snap(snap, iid)
                events.append({
                    "event": "card_purchased",
                    "ts": ts,
                    "instance_id": iid,
                    "template_id": template_id,
                    "section": section,
                    "target_socket": target_socket,
                    "source": "mono",
                })

            inferred_purchase = self._detect_offer_purchase_without_board(
                snap, ts, curr_offered, curr_board, moved_to_board
            )
            if inferred_purchase:
                events.append(inferred_purchase)

        # ── Authoritative purchases from GameSim CardPurchased events ───
        # The owned-card board never populates during shops in the Mono-only
        # flow (GameStateSync is too rare; GameSim deltas carry no board), so
        # the offered→board diff above can't see a buy. The game instead emits
        # a discrete GameSimEventCardPurchased on the chosen instance — the
        # authoritative buy/selection signal (the Player.log replacement).
        events.extend(self._gamesim_selection_events(snap, ts))

        return events

    def _gamesim_selection_events(self, snap: dict, ts: str) -> list[dict]:
        """Build card_purchased decisions from a snapshot's inline template events."""
        return self._purchase_events_from_template_events(
            snap.get("card_template_events") or [], ts, snap=snap,
        )

    def ingest_card_events(self, template_events: list, ts: str) -> None:
        """Emit card_purchased decisions from a deferred template-events payload.

        GameSim ``CardPurchased`` events almost always arrive in a late
        ``deferred_template_events`` message, after the parent snapshot was
        dispatched to the differ — and in a state burst the snapshot-id match in
        ``note_enriched_snapshot`` may already have moved on. Feeding the raw
        payload straight here makes purchase capture independent of that race.
        Dedup by instance means re-processing the same event is a no-op.
        """
        if self._event_source == "log":
            return
        for evt in self._purchase_events_from_template_events(template_events or [], ts):
            self._emit(evt)

    def _purchase_events_from_template_events(
        self, template_events: list, ts: str, snap: Optional[dict] = None,
    ) -> list[dict]:
        """Turn GameSim CardPurchased events into card_purchased decisions.

        ``card_dealt``/``card_spawned`` give us instance→template (the buy event
        itself has no template). A ``card_purchased`` on the chosen instance is
        the authoritative pick — a shop item, a map/encounter node, etc. Skills
        are left to the dedicated ``skill_selected`` path. Dedup by instance so
        the same event (seen inline and again via deferred enrichment) yields one
        decision.
        """
        if not template_events:
            return []

        for e in template_events:
            if not isinstance(e, dict):
                continue
            et = e.get("event_type")
            iid = e.get("instance_id")
            tmpl = e.get("template_id")
            if et in ("card_dealt", "card_spawned") and iid and tmpl:
                self._instance_templates[iid] = tmpl

        events: list[dict] = []
        for e in template_events:
            if not isinstance(e, dict) or e.get("event_type") != "card_purchased":
                continue
            iid = e.get("instance_id")
            if not iid or iid in self._emitted_card_purchases:
                continue
            prefix = iid.split("_", 1)[0] if "_" in iid else ""
            if prefix == "skl":
                # Skill picks are recorded via the skill_selected path.
                continue
            self._emitted_card_purchases.add(iid)
            template_id = (
                self._instance_templates.get(iid)
                or e.get("template_id")
                or (_template_for_instance(snap, iid) if snap else "")
                or None
            )
            # enc/ste/com/ped are map/encounter node picks (Opponent side, the
            # ChoiceState branch in RunState._on_card_purchased); everything else
            # (notably itm_) is a player-side acquisition.
            section = "Opponent" if prefix in ("enc", "ste", "com", "ped") else "Player"
            events.append({
                "event": "card_purchased",
                "ts": ts,
                "instance_id": iid,
                "template_id": template_id,
                "section": section,
                "target_socket": None,
                "source": "mono",
            })
        return events

    # ------------------------------------------------------------------
    # Reroll / sell detectors (issue #128)
    # ------------------------------------------------------------------

    def _detect_reroll(self, snap: dict, ts: str, curr_state: str,
                       curr_offered: frozenset) -> Optional[dict]:
        """
        Reroll: rerolls_remaining decremented in the same EncounterState window
        and the offered set is largely replaced (>50% new cards, or prev offered
        is non-empty and none of the original offers survive).
        """
        if curr_state != "EncounterState":
            return None
        if self._prev_state_name != "EncounterState":
            return None

        curr_rerolls = _rerolls_remaining(snap)
        if curr_rerolls is None or self._prev_rerolls is None:
            return None
        if curr_rerolls >= self._prev_rerolls:
            return None

        # Confirm offered set changed meaningfully
        if self._prev_offered and not (curr_offered & self._prev_offered):
            # All previous offers gone → clear reroll
            pass
        elif self._prev_offered:
            new_fraction = len(curr_offered - self._prev_offered) / max(len(self._prev_offered), 1)
            if new_fraction < 0.5:
                # Too few new offers — might just be a card leaving
                return None

        shop_window_id = _shop_window_id_from_snap(snap)
        return {
            "event": "reroll",
            "ts": ts,
            "source": "mono",
            "shop_window_id": shop_window_id,
        }

    def _detect_sell(self, snap: dict, ts: str, curr_board: frozenset) -> Optional[dict]:
        """
        Sell: a card disappeared from player_board between snapshots,
        gold increased, and there's no matching cards_disposed this tick.

        Note: cards_disposed is a log-side event — we can't read it here,
        so we use the gold-delta heuristic as the sell signal.
        """
        if self._prev_snap is None:
            return None

        gone = self._prev_board - curr_board
        if not gone:
            return None

        prev_gold = _gold(self._prev_snap)
        curr_gold = _gold(snap)
        if prev_gold is None or curr_gold is None:
            return None

        gold_delta = curr_gold - prev_gold
        if gold_delta <= 0:
            return None

        # Pick one instance (multi-sell in one tick is unusual — handle first only)
        instance_id = sorted(gone)[0]
        template_id = _template_for_instance(self._prev_snap, instance_id)
        return {
            "event": "card_sold",
            "ts": ts,
            "instance_id": instance_id,
            "template_id": template_id,
            "gold": int(gold_delta),
            "source": "mono",
        }

    def _detect_offer_purchase_without_board(
        self,
        snap: dict,
        ts: str,
        curr_offered: frozenset,
        curr_board: frozenset,
        moved_to_board: frozenset,
    ) -> Optional[dict]:
        """Infer a purchase when live card ownership collections decode empty.

        Some packaged live captures still include authoritative GameSim deal
        events while dynamic card collections decode as empty. In that shape
        a shop purchase appears as exactly one previous offer disappearing
        while gold drops, with no matching board move to diff against.
        """
        if moved_to_board:
            return None
        if self._prev_snap is None or self._prev_state_name != "EncounterState":
            return None
        if _state_name(snap) != "EncounterState":
            return None
        if curr_board:
            return None

        removed_from_offers = self._prev_offered - curr_offered
        if len(removed_from_offers) != 1:
            return None

        prev_gold = _gold(self._prev_snap)
        curr_gold = _gold(snap)
        if prev_gold is None or curr_gold is None or curr_gold >= prev_gold:
            return None

        instance_id = next(iter(removed_from_offers))
        template_id = _template_for_instance(self._prev_snap, instance_id)
        return {
            "event": "card_purchased",
            "ts": ts,
            "instance_id": instance_id,
            "template_id": template_id,
            "section": "Player",
            "target_socket": "",
            "source": "mono",
        }

    # ------------------------------------------------------------------
    # Bootstrap events (hero / session / run_start)
    # ------------------------------------------------------------------

    def _bootstrap_events(self, snap: dict, ts: str) -> list[dict]:
        events: list[dict] = []

        if not self._emitted_run_start:
            self._emitted_run_start = True
            events.append({"event": "run_start", "ts": ts, "source": "mono"})

        hero = _hero_name(snap)
        if hero and not self._emitted_hero:
            self._emitted_hero = True
            events.append({"event": "hero", "ts": ts, "hero": hero, "source": "mono"})

        if not self._emitted_session_id:
            self._emitted_session_id = True
            events.append({
                "event": "session_id",
                "ts": ts,
                "session_id": self._session_id,
                "source": "mono",
            })

        if not self._emitted_account_id:
            self._emitted_account_id = True
            events.append({
                "event": "account_id",
                "ts": ts,
                "account_id": _SYNTHETIC_ACCOUNT_ID,
                "source": "mono",
            })

        if not self._emitted_run_init_complete:
            self._emitted_run_init_complete = True
            events.append({"event": "run_init_complete", "ts": ts, "source": "mono"})

        return events

    # ------------------------------------------------------------------
    # Terminal state helpers
    # ------------------------------------------------------------------

    def _handle_new_run(self, snap: dict, ts: str) -> None:
        """Reset adapter state on a new run boundary."""
        self._reset()
        # Emit run_start immediately so RunState can open a new run record
        self._emitted_run_start = True
        self._emit({"event": "run_start", "ts": ts, "source": "mono"})
        # Advance prev state
        self._prev_snap = snap
        self._prev_snap_id = snap.get("id")
        self._prev_state_name = _state_name(snap)

    def _handle_terminal_state(self, snap: dict, ts: str, curr_state: str) -> None:
        if curr_state in ("EndRunDefeatState", "EndRunDefeat"):
            self._emit({"event": "run_defeat", "ts": ts, "source": "mono"})
        elif curr_state in ("EndRunVictoryState", "EndRunVictory"):
            self._emit({"event": "run_victory", "ts": ts, "source": "mono"})
        self._reset()

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def _reset(self) -> None:
        """Reset all per-run state."""
        self._session_id = _new_synthetic_session_id()
        self._prev_snap = None
        self._prev_snap_id = None
        self._prev_state_name = ""
        self._prev_offered = frozenset()
        self._prev_board = frozenset()
        self._prev_rerolls = None
        self._prev_shop_window_id = None
        self._instance_templates = {}
        self._emitted_card_purchases = set()
        self._emitted_run_start = False
        self._emitted_hero = False
        self._emitted_session_id = False
        self._emitted_account_id = False
        self._emitted_run_init_complete = False
        self._in_combat = False

    def _locate_card_in_snap(self, snap: dict, instance_id: str) -> tuple[str, str]:
        """Find the section and socket for an instance_id in the snapshot's board lists."""
        section_map = {
            "player_board": "Player",
            "player_stash": "Storage",
            "player_skills": "Player",
        }
        for key, section in section_map.items():
            for c in (snap.get(key) or []):
                if isinstance(c, dict) and c.get("instance_id") == instance_id:
                    socket_val = c.get("socket") or ""
                    return section, socket_val
        return "Player", ""

    def _emit(self, event: dict) -> None:
        """Forward a single event dict to RunState.process()."""
        try:
            self._run_state.process(event)
        except Exception as exc:
            print(f"[MonoAdapter] process() raised for event={event.get('event')}: {exc}")
