# Bazaar Tracker - Roadmap

Active work tracker. Project context, architecture, completed work history, and stable design notes live in `CLAUDE.md` and `docs/`.

Status labels:
- `Partial`: useful foundation exists, but the feature is not complete enough to close.
- `Open`: not yet implemented.
- `On Hold`: blocked on an external dependency or prerequisite.

Completed roadmap items are removed from this file rather than kept as checked-off entries.

Manual catalog curation validation has moved out of the roadmap. Future curation runs should distinguish safe no-op workflow validation from evidence-bearing manual catalog curation validation. Evidence-bearing validation requires post fetch evidence, normally via `--fetch-posts`, or an explicitly evidence-backed empty result after fetch attempts.

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

### Refresh-Builds Player Surface - Open

Goal: surface `refresh-builds` in player-facing docs/UI as the lightweight catalog-pull mechanism for players.

Status: CLI support is wired. There is no automatic startup refresh and no UI button yet.

Relevant files:
- `refresh_builds.py`
- `tracker.py`
- `web/static/index.html`
- player-facing release/docs surfaces

Implementation notes:
- Keep refresh optional and non-blocking.
- Preserve the existing validation behavior: incompatible or malformed refreshed catalogs are ignored in favor of the bundled copy.
- Avoid making normal app startup depend on GitHub availability.

How to test:
- A tracker install with no network access still loads the bundled builds for every supported hero.
- `refresh-builds` with GitHub unreachable exits non-zero and logs a warning without touching the writable copy.
- Schema validation failure on a fetched file discards the fetch and leaves the existing writable copy in place.

### Automated Builds Refresh Pipeline - Open

Goal: a scheduled job that fetches fresh build data, regenerates `<hero>_builds.json`, and opens a PR with the diff for human review. Long-term the curator's role becomes "review the PR" instead of "run the enricher and edit JSON".

Status: design and early infrastructure exist in the [bazaar-builds](https://github.com/hearn1/bazaar-builds) repo, but the production workflow is not live. The pipeline remains in `phase: implementation`, so cron exits without action until the curator promotes it.

Open implementation subtasks:
1. **Source fetchers** - bazaardb (Playwright), Mobalytics (PRELOADED_STATE), bazaar-builds.net (existing-enricher wrapper). Each emits a `WindowObservation` plus per-source health.
2. **Threshold evaluator** - engine that consumes fetcher output + sidecar history + `pipeline_state.json` + current catalog, applies the threshold rules, and emits the per-row schema from `docs/automated-builds-pipeline-subtask1-signal-spec.md`.
3. **Diff generator + LLM** - given threshold rows + catalog + LLM classification, emit a structured proposed-change set (adds, removes, archetype reshuffles) richer than today's `*_build_update_proposal.md`.
4. **GitHub Actions workflow** - cron, runs the fetchers + evaluator + diff generator, opens or updates a single rolling PR per hero with the changes plus a human-readable summary.
5. **Review tooling** - small dashboard or PR-comment template that surfaces the supporting stats for each proposed add/remove so the reviewer does not have to dig.

LLM classifier follow-up:
- Handle this in a separate session after the current GitHub Actions validation path is stable.
- ChatGPT Plus/Pro subscriptions do not provide reusable OpenAI API billing for GitHub Actions. OpenAI API usage requires separate API billing or credits and should be rechecked against current official pricing/model docs at implementation time.
- Short-term recommendation: add a deterministic/no-LLM classification mode so dry runs and CI are not blocked by provider billing or secrets. Preserve existing catalog buckets, classify new secondary-only items as `support` or `classification_pending`, and surface uncertain role decisions for curator review.
- Use bazaardb `CORE ITEMS` / `SUPPORTING ITEMS` section metadata only after hero/source scoping has been validated as safe.
- Keep the classifier provider pluggable: `deterministic`, existing Anthropic/Claude wiring, a Gemini API option to investigate for low-volume hosted classification, and a later OpenAI API option if separate billing is acceptable.
- Gemini API free tier is the first hosted fallback to evaluate for roughly five small classification calls per week. Verify current free quota, rate limits, data-use terms, model names, billing rules, and structured JSON reliability at implementation time.
- Local/open-weight models remain possible, but are probably too heavy or brittle for GitHub-hosted Actions at this expected volume.
- Waiting for Anthropic credits has the least implementation churn if existing Claude wiring is otherwise healthy, but it does not unblock unpaid/local dry-run operation.

How to test:
- Signal logic: dry-run against historical artifacts and confirm the proposed deltas match what the curator would have done by hand.
- Workflow: trigger the action manually on a fork; confirm a PR appears, contains a sane diff, and CI passes.
- End-to-end: skip a day of runs, then trigger the job; confirm the resulting PR is empty or near-empty with no spurious churn from low sample size.

### Build Archetype Images - Open

Goal: show a single representative image per build archetype in the overlay/dashboard rather than attempting per-card inline images. Drop the per-card image pipeline.

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
