# Bazaar Coach - Roadmap

Active work tracker. This file should contain only work that is still actionable. Completed items are removed rather than kept as checked-off entries. Stable project context and architecture notes live in `CLAUDE.md`.

Status labels:

- `Open`: not yet implemented.
- `Partial`: useful foundation exists, but more work is needed.
- `On Hold`: blocked by an external dependency or prerequisite.

## Release Todo

### P1 — Keep support-command windows open

Status: Open

Observed during installed-app testing on 2026-05-12:

- The installer creates Start Menu shortcuts for Doctor, Refresh Builds, and Export Diagnostics.
- Those shortcuts target `BazaarCoachCLI.exe` directly.
- When launched from the Start Menu or the post-install "Run Doctor" option, the terminal opens and closes immediately after the command finishes, so testers cannot read the output.

Expected:

- Main **Bazaar Coach** shortcut continues to launch `BazaarCoach.exe`.
- Support shortcuts open a visible terminal and keep it open after command completion.
- Post-install **Run Doctor** uses the same stay-open support path.
- Paths are quoted correctly because the install directory contains a space.
- Working directory remains the installed app directory.

Likely fix area:

- `packaging/installer/BazaarCoach.iss`

Implementation direction:

- Use `cmd.exe /K` for support shortcuts and post-install Doctor, or add wrapper `.cmd` files that stay open.
- Keep `BazaarCoachCLI.exe` available for users who run commands from an already-open terminal.

How to test:

1. Build the installer.
2. Install normally.
3. Launch **Bazaar Coach - Doctor** from the Start Menu.
4. Confirm a terminal opens, prints doctor output, and remains open.
5. Repeat for **Refresh Builds** and **Export Diagnostics**.
6. Install again and use the post-install **Run Doctor** option; confirm the output remains readable.

### P2 — Align release version metadata before publishing

Status: Open

Observed during installed-app testing on 2026-05-12:

- The newly built installer was named `BazaarCoachSetup-0.1.0-dev.exe`.
- `doctor` reported `Bazaar Coach 0.1.0-dev`.
- The release being tested is intended for the v0.2 alpha line.

Expected:

- App version, installer filename, install directory, and diagnostic output all match the intended release version before publishing.

Likely fix area:

- Version source used by the app.
- PyInstaller/Inno packaging version inputs.
- Release build process.

How to test:

1. Build portable and installer artifacts.
2. Confirm the installer filename contains the intended version.
3. Install the app.
4. Run `BazaarCoachCLI.exe doctor`.
5. Confirm the reported app version matches the installer version.

## Open Feature Work

### Multi-Hero Support - On Hold

Goal: add Jules and Stelle hero catalogs while keeping existing Karnok, Mak, Dooley, Vanessa, and Pygmalien behavior stable.

Status: On Hold. Jules and Stelle are not yet purchased.

Relevant files:

- `<hero>_builds.json`
- `scorer.py`
- `web/build_helpers.py`
- `web/overlay_state.py`
- `capture_mono.py`
- `msgpack_decoder.py`

Implementation notes:

- Add one hero at a time.
- Keep build schema compatible with existing `game_phases`, `archetypes`, `scoring_weights`, and `timing_profile` fields.
- Make sure new hero names match the names emitted by Mono capture and stored on `runs.hero`.
- Use the enricher fetch + compare workflow in `bazaar-builds` to populate initial archetypes before writing the catalog here.

How to test:

1. Start a run on the new hero and confirm `runs.hero` is correct in SQLite.
2. Verify `scorer.py` loads the new catalog instead of falling back to no-score behavior.
3. Verify the overlay Coach tab displays the new hero's archetypes and condition items.

### Automated Builds Refresh Pipeline - On Hold

Goal: eventually let the `bazaar-builds` repo produce reviewed catalog update PRs for this repo.

Status: On Hold. `bazaar-builds` remains in shadow/dry-run mode and must not mutate tracker catalogs or open tracker PRs until manually promoted.

Current constraints:

- Scheduled shadow runs in `bazaar-builds` are allowed to produce review artifacts there.
- Coach catalogs in this repo must not be automatically mutated yet.
- Do not promote to live catalog updates until there is enough healthy shadow history and a classifier/provider strategy has been accepted or explicitly waived.

Before live promotion:

- Require at least two healthy BazaarDB patch windows.
- Require at least 60 calendar days of healthy shadow output.
- Decide or waive the semantic classifier/provider strategy.
- Confirm required secrets, API billing/cost, rollback path, and tracker PR behavior.

How to test when this becomes active:

1. Run selected heroes in `bazaar-builds` with mock/no-LLM mode.
2. Confirm artifacts are produced without tracker catalog mutation.
3. Review shadow artifacts and source-health output.
4. Only after promotion, verify generated tracker PRs are reviewable and reversible.

## Feature Backlog

These are not part of the current release.

- **Cross-Run Analytics Dashboard** — Aggregate win rate by hero/archetype, score by phase, gold curves, and day-of-death.
- **Drill / What-If Mode** — From a completed run, pick a decision and score an alternative.
- **OBS Browser Source** — Minimal transparent view for stream overlays.
- **Catalog Pack Import** — Import community-authored catalog packs.
- **Crash & Auto-Diagnostics Reporter** — Package session log and last decisions on unhandled exception or watcher silence.
- **Opponent Build Inference** — Classify opponent boards after a validation spike proves capture fill rate is high enough.

Dropped:

- Run Export & Share
- Replay Scrubber
- Coaching Diff vs Reference Run
