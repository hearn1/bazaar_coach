# Bazaar Tracker - Roadmap

Active work tracker. Project context, architecture, completed work history, and stable operating notes live in `CLAUDE.md`.

Status labels:
- `Partial`: useful foundation exists, but the feature is not complete enough to close.
- `Open`: not yet implemented.
- `On Hold`: blocked on an external dependency or prerequisite.

Completed roadmap items are removed from this file rather than kept as checked-off entries.

Manual catalog curation validation has moved out of the roadmap. Future curation runs should distinguish safe no-op workflow validation from evidence-bearing manual catalog curation validation. Evidence-bearing validation requires post fetch evidence, normally via `--fetch-posts`, or an explicitly evidence-backed empty result after fetch attempts.

## Alpha v0.2 Punch List

Items from prod-readiness verification. P0 = release-blocking, P1 = release-eroding, P2 = post-release / `live_cron` prep.

17 items closed 2026-05-09 (closed in 4338965 and Alpha v0.2 Punch List Round 2); round 3 + round 4 closed 10 P1 items including the overlay header redesign (run pill moved to subtitle, × corner-pinned).

All v0.2 punch list items closed. See git history for change log.

## Open Feature Work

### Multi-Hero Support - On Hold

Goal: add Jules and Stelle hero catalogs while keeping existing Karnok/Mak/Dooley/Vanessa/Pygmalien behavior stable.

Status: Karnok, Mak, Dooley, Vanessa, and Pygmalien have populated catalogs. Jules and Stelle are on hold because they are not yet purchased.

Relevant files:
- `<hero>_builds.json` files
- `scorer.py`
- `web/build_helpers.py`
- `web/overlay_state.py`
- `capture_mono.py` and `msgpack_decoder.py` hero enum mappings

Implementation notes:
- Add one hero at a time as a new build JSON catalog.
- Keep build schema compatible with existing `game_phases`, `archetypes`, `scoring_weights`, and `timing_profile` fields.
- Make sure new hero names match the names emitted by Mono capture and stored on `runs.hero`.
- Use the enricher fetch + compare workflow in [bazaar-builds](https://github.com/hearn1/bazaar-builds) to populate initial archetypes before writing the catalog here.

How to test:
- Start a run on the new hero and confirm `runs.hero` is correct in SQLite.
- Verify `scorer.py` loads the new catalog instead of falling back to no-score behavior.
- Verify overlay Coach tab displays the new hero's archetypes and condition items.

### Automated Builds Refresh Pipeline - Open

Goal: a scheduled job that fetches fresh build data, regenerates `<hero>_builds.json`, and opens a PR with the diff for human review. Long-term the curator's role becomes "review the PR" instead of "run the enricher and edit JSON".

Status: implementation work lives in the [bazaar-builds](https://github.com/hearn1/bazaar-builds) repo and has been promoted to `phase: shadow_cron` with `dry_run: true`. The GitHub Actions cron schedule exists. Scheduled `shadow_cron` runs default to deterministic `no_llm_shadow`, may fetch sources, evaluate, write diff/proposal artifacts, upload review artifacts, and commit `stats/<hero>_stats.json` sidecars in bazaar-builds on `main`. They still do not mutate tracker catalogs or open tracker PRs. `live_cron` remains disabled until a later manual gate with accumulated healthy shadow history and semantic catalog-review readiness.

Historical automated-pipeline design notes have been retired from `docs/`. Keep current tracker-facing pipeline facts here, and keep bazaar-builds operator details in that repo's `README.md`, `ROADMAP.md`, and `CLAUDE.md`.

Promotion evidence:
- Python 3.12.10 temporary environment used.
- Focused pipeline tests passed: `59 passed in 0.39s`.
- Current tracked bazaar-builds unit suite: `119 passed` with `python -m pytest -q tests`; bare repo-root pytest can collect generated artifacts and fail before the suite runs.
- All supported heroes completed `local_dry_run` with `--mock-llm`, live source fetches, temp-only artifacts, and exit code 0: Dooley, Karnok, Mak, Pygmalien, and Vanessa.
- Live source fetches succeeded for three sources: bazaar-builds.net `2026-W19`, bazaardb `14.0 (Hotfix May 7)`, Mobalytics `v541`. This is source count, not three temporal windows. Markdown source-health tables are summaries; the diff JSON is the fuller source-health review artifact when per-source observations or diagnostics matter.
- Each hero produced diff JSON and proposal markdown. No real LLM/API calls occurred, and no checked-in pipeline state, catalog, stats sidecar, or tracker catalog files mutated during validation.
- Mock-mode proposals are operational validation only, not catalog-acceptance evidence. Support-only classifications, low confidence, duplicate/near-duplicate proposals, and missing evidence refs/sample counts remain normal curator review items rather than pipeline failures.

Classifier follow-up:
- Deterministic/no-LLM shadow mode is implemented and is the scheduled `shadow_cron` default.
- ChatGPT Plus/Pro subscriptions do not provide reusable OpenAI API billing for GitHub Actions. OpenAI API usage requires separate API billing or credits and should be rechecked against current official pricing/model docs at implementation time.
- Decide the semantic classifier strategy before `live_cron` or catalog-acceptance automation: existing Anthropic/Claude wiring, an alternate provider such as Gemini or another free/lower-cost provider, provider abstraction before hosted usage, or an explicit operator waiver with the risk recorded.
- Use bazaardb `CORE ITEMS` / `SUPPORTING ITEMS` section metadata only after hero/source scoping has been validated as safe.
- Keep the classifier provider pluggable: `deterministic`, existing Anthropic/Claude wiring, an alternate hosted option such as Gemini to investigate for low-volume classification, and a later OpenAI API option if separate billing is acceptable.
- If Gemini or another free/lower-cost hosted provider is evaluated, verify current free quota, rate limits, data-use terms, model names, billing rules, and structured JSON reliability at implementation time. No alternate provider is selected yet.
- Local/open-weight models remain possible, but are probably too heavy or brittle for GitHub-hosted Actions at this expected volume.
- Waiting for Anthropic credits has the least implementation churn if existing Claude wiring is otherwise healthy, but it does not unblock unpaid/local dry-run operation.

How to test:
- Local dry run: run selected heroes from a Python 3.12 virtualenv with `--mock-llm` or `--classifier-mode no_llm_shadow`; confirm artifacts are produced without catalog, tracker, or stats-sidecar mutation.
- Shadow monitoring: review scheduled/manual shadow artifacts, confirm source-health fields are clear, confirm `classification_mode: no_llm_shadow`, `semantic_classification: false`, and `llm_provider: none`, and confirm stats sidecar commits in bazaar-builds never mutate tracker catalogs or open tracker PRs.
- Before flipping to `live_cron`: confirm at least 6 healthy bazaardb patch windows and at least 60 calendar days of shadow output; review source-health/stats history; decide or explicitly waive the semantic classifier/provider strategy; confirm any required secret/API/cost readiness; confirm rolling tracker PR behavior and rollback path.
- Live readiness: require at least 6 healthy bazaardb patch windows and at least 60 calendar days of shadow output before enabling rolling tracker PRs.

### Build Archetype Images - Open

Goal: show a single representative image per build archetype in the overlay/dashboard rather than attempting per-card inline images. Drop the per-card image pipeline.

Status: current implementation still renders per-card item thumbnails in overlay/review/dashboard. Replacing that with archetype images requires catalog schema/API/UI work, not just adding image files.

Relevant files:
- `<hero>_builds.json` - add optional `image` field per archetype
- `web/build_helpers.py` - expose image field when loading archetypes
- `web/static/overlay.html` - render archetype image in Coach tab
- `web/static/index.html` - render archetype image in dashboard build section

Direction:
- Each archetype entry in `*_builds.json` gets an optional `image` field (URL or local filename).
- Images can be sourced manually (curator picks one representative card art or build screenshot) or downloaded automatically during `refresh-content` / `update-builds`.
- Downloaded images are stored in `static_cache/images/builds/` keyed by hero + archetype slug.
- Overlay Coach tab shows the archetype image or no image alongside the archetype name and checklist.
- Remove inline per-card image rendering from review/overlay once this replaces it. Existing `web/card_images.py`, manifest, and extraction scripts can be archived or deleted once this replaces them.
- BazaarDB outreach is still open. If they respond, their images could be used as the source for archetype art.

Implementation notes:
- Keep it simple: one image per archetype, not per card. No manifest, no quality diagnostics.
- Image field is optional. Archetypes without one should display cleanly with no broken-image placeholder.
- If downloading during refresh: respect rate limits, store locally, never rehost.

How to test:
- Add an `image` field to one archetype in `karnok_builds.json` and confirm overlay Coach tab renders it.
- Confirm archetypes without an image field display cleanly with no broken-image placeholder.
- If auto-download is implemented, run `refresh-content` and confirm images land in `static_cache/images/builds/`.

## Feature Backlog (post-alpha)

Candidates surfaced during the v0.2 prod-readiness design pass. These are explicitly out of scope for the initial public release. Yes items have a full description and a clear path forward; Maybe items are recorded as one-line stubs pending more demand or unblocking work. No items have been dropped.

### Yes — Active backlog

(Empty — Local Build Override Editor shipped on `feature/local-build-override-editor`.)

### Maybe — Pending demand or unblock

- **Cross-Run Analytics Dashboard** — Aggregate stats (win rate by hero/archetype, score-by-phase, gold curves, day-of-death) read-only over existing tables. ~3-5 days (M). Requires a chart-library decision (Chart.js CDN vs inline-bundle vs hand-rolled SVG); defer `runs.patch_label` and `runs.archetype` columns to v2.
- **Drill / What-If Mode** — From a completed run, pick any decision and score the alternative; show score delta. `_score_single_decision` is already cleanly parameterized; cap scope to single-decision delta against the offered set only — no sequence cascade.
- **OBS Browser Source** — Stripped HTML view at `/obs/coach` with a transparent background, sized for stream overlays. Polls existing `/api/overlay/state` — most data already available. ~1 day. Optional Discord rich presence deferred separately due to reconnect-loop fragility.
- **Catalog Pack Import** — Import community-authored multi-hero catalog packs (URL paste or file drop), validated and switchable as a unit. ~5-8 days (M). Distinct from Local Build Override (immutable bundle vs per-hero edit), but should land after Override's resolver-layer refactor to avoid duplicating the catalog-tier work.
- **Crash & Auto-Diagnostics Reporter** — Auto-package session log + last N decisions on unhandled exception or watcher silence; pre-fill a GitHub issue URL (no auth needed). ~4-6 hours. Builds on existing `export-diagnostics`. Smallest item in the backlog.
- **Opponent Build Inference** — Classify opponent boards from Mono snapshots, surface PvP meta-tracker. **Validity gate first:** schema and Frida hooks already exist (`combat_results.opponent_board` column + `_CAPTURE_OPPONENT_BOARD` flag, currently disabled by default at `capture_mono.py` ~L2385). Ship a 1-run validation spike with `--include-opponent-board` to confirm fill rate before committing to ~2-3 days of classifier + dashboard work.

**Items reviewed and dropped:** Run Export & Share (no immediate consumer), Replay Scrubber (low marginal value over existing decision detail expansion), Coaching Diff vs Reference Run (depends on dropped Export & Share, alignment heuristic make-or-break risk).
