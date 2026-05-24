# Bazaar Coach — Roadmap

Open work tracker. Items live here until they ship; completed items are removed rather than kept as checked-off entries. Stable architecture notes live in `CLAUDE.md`.

Status labels:

- `Open` — not yet implemented.
- `Partial` — useful foundation exists, more work needed.
- `On Hold` — blocked by an external dependency or prerequisite.

## Bug triage queue

Prioritized fix order for open product bugs. Ordering principle: infra changes that affect everything else first, then correctness regressions ranked by blast radius, then content/catalog. Per-issue root cause + effort lives on the GitHub thread.

1. [#85](https://github.com/hearn1/bazaar_coach/issues/85) — Local vs `.exe` functionality drift. **Do first** — duplicate `karnok_builds.json` at repo root vs `builds/` means dev and packaged builds load different catalogs, which makes triage of every other bug suspect. Effort: M.
2. [#83](https://github.com/hearn1/bazaar_coach/issues/83) — PvP/PvE/day wrong on overlay header. `_get_latest_live_snapshot` in `web/overlay_state.py` historically unscoped to current run/hero; mostly addressed in #88/#96, verify any remaining edge cases. Effort: M.
3. [#84](https://github.com/hearn1/bazaar_coach/issues/84) — Leave Run button missing. Likely the same root cause as #83 (prior run never closed → new run shows stale state with `is_active=true`). Verify after #83; layer in a manual "force end" control only if still needed. Effort: S after #83.
4. [#81](https://github.com/hearn1/bazaar_coach/issues/81) — Universal utility items marked suboptimal when committed. `scorer.score_late_decision` committed-branch never checks `universal_utility_items` / `economy_items`. Surgical scorer fix. Effort: S.
5. [#77](https://github.com/hearn1/bazaar_coach/issues/77) — Missed items not showing in review tab. `_emit_shop_visit_missed_entry` + acquired-name suppression filter in `web/review_builder.py` are over-suppressing early-run misses. Land after the scorer/state fixes so fixtures are stable. Effort: M.
6. [#82](https://github.com/hearn1/bazaar_coach/issues/82) — Night Vision / Chains / Fairy Circle build display is wrong. Likely a catalog content fix that should ride on #85's consolidation. Effort: M.

## Refactor / entropy reduction

Ranked by entropy-reduction value. Independent of the bug queue — these are quality-of-code work, not user-visible fixes. Ordering for implementation lives in each issue thread.

1. [#98](https://github.com/hearn1/bazaar_coach/issues/98) — Collapse the two duplicate scoring paths in `scorer.py`. `_score_loaded_run` and `_score_single_decision` implement nearly the same logic; collapse the batch path to a loop over the single-decision path. ~150-180 net LOC.
2. [#99](https://github.com/hearn1/bazaar_coach/issues/99) — Drop the inert `action_events` side-channel in `capture_mono.py`. Computed but never persisted to DB or consumed by RunState/overlay; includes a Pipeline-B migration gap analysis for the long-term "drop Player.log" goal. ~450-550 net LOC.
3. [#100](https://github.com/hearn1/bazaar_coach/issues/100) — Collapse the overlapping PvP/PvE record helpers in `web/overlay_state.py`. `_get_pvp_record` / `_get_pve_record` are vestigial; `_get_run_record` already covers both call sites. ~40-60 net LOC.
4. [#101](https://github.com/hearn1/bazaar_coach/issues/101) — Delete `bridge.py`. Manual diagnostic with no in-app callers; superseded by `decisions.api_game_state_id`. ~518 net LOC.
5. [#102](https://github.com/hearn1/bazaar_coach/issues/102) — Consolidate `score_run` + `print_report` CLI scaffolding in `scorer.py`. Same category as #101: post-hoc analysis superseded by live scoring. Land after #98. ~140-180 net LOC.
6. [#103](https://github.com/hearn1/bazaar_coach/issues/103) — Unify the three offered-list trackers in `run_state.py` (`pending_offered`, `_pending_event_choices`, `_shop.offered`). Fragile; land last. ~30-50 net LOC.
7. [#104](https://github.com/hearn1/bazaar_coach/issues/104) — Simplify inferred-purchase reconciliation in `run_state._on_card_purchased`. Three near-duplicate branches collapse to one. ~20-30 net LOC.
8. [#105](https://github.com/hearn1/bazaar_coach/issues/105) — Unify `_load_json_list` / `_load_json_dict` / `_safe_json` helpers across `scorer.py` and `web/server.py`. Tiny, bundle with another scorer change. ~15-25 net LOC.

## Long-term goal — drop Player.log dependency

Pipeline B (`capture_mono.py`) already captures ~80% of what Player.log provides. The remaining gaps and concrete first step are documented in [#99](https://github.com/hearn1/bazaar_coach/issues/99). A staged migration would eventually retire `watcher.py`, `parser.py`, and large portions of `run_state.py`. Not actively in progress.

## Feature backlog

Not in any current release.

- **Cross-Run Analytics Dashboard** — aggregate win rate by hero/archetype, score by phase, gold curves, day-of-death.
- **Drill / What-If Mode** — pick a past decision and score an alternative.
- **OBS Browser Source** — minimal transparent view for stream overlays.
- **Catalog Pack Import** — accept community-authored catalog packs.
- **Crash & Auto-Diagnostics Reporter** — package session log + last decisions on unhandled exception or watcher silence.
- **Opponent Build Inference** — classify opponent boards after a validation spike proves capture fill rate is high enough.

Dropped:

- Run Export & Share
- Replay Scrubber
- Coaching Diff vs Reference Run
