# Bazaar Coach — Roadmap

Open work tracker. Items live here until they ship; completed items are removed rather than kept as checked-off entries. Open bugs and refactor work live on GitHub Issues. Stable architecture notes live in `CLAUDE.md`.

## Long-term goal — drop Player.log dependency

Pipeline B (`capture_mono.py`) already captures ~80% of what Player.log provides. A staged migration would eventually retire `watcher.py`, `parser.py`, and large portions of `run_state.py`. Not actively in progress.

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
