# Automated Builds Pipeline — Post-Implementation Follow-Ups

*Session: 2026-05-06. Captures the state of the pipeline after the first end-to-end local dry run, the resulting fetcher bug fixes, and the architecture-review findings the dry run surfaced. Not a redesign; the locked decisions in `automated-builds-pipeline-design.md` stand. This document organizes the remaining work into discrete sessions and notes pending curator decisions.*

---

## 1. Status Snapshot (2026-05-06)

| Subtask / area | State |
|---|---|
| Subtasks 1–7 (design + implementation) | All merged. ROADMAP entry remains Open while `local_dry_run` remains artifact-only and manual-review. |
| Issue #4 — bazaar-builds.net fetcher date health | **Closed.** PR `bazaar-builds#5`. Untracked record-creation paths bypassing the date filter were fixed; tests added; live fetch healthy. |
| Issue #3 — Mobalytics `document_version_missing` | **Closed.** PR `bazaar-builds#6`. Path walker now tolerates unrelated queries and null rows; diagnostic detail split into `document_path_missing` vs `document_version_missing`; live shape sample committed at `bazaar-builds/research/samples/mobalytics/meta-builds-preloaded-state-builds-2026-05-06.json`; live fetch healthy at `mobalytics_meta_builds:v540`. |
| Issue #2 — bazaardb `content_landmark_missing` / core-item evidence | **Open.** A1 research confirmed the source remains viable; A2 restored the fetcher; follow-up `5367e7d` included bazaardb core-item evidence and merged via `bazaar-builds` merge commit `ddd5277`, but the GitHub issue remains open. |
| Pipeline phase | `bazaar-builds` remains at `phase: local_dry_run` with `dry_run: true`. The Actions cron schedule already exists, so scheduled/manual dry runs can fetch, evaluate, write, and upload artifacts, but they do not save/commit stats sidecars or mutate tracker catalogs. `shadow_cron` and `live_cron` remain future gates. |
| Healthy sources in all-hero validation | Three healthy sources were observed: bazaar-builds.net `2026-W19`, mobalytics_meta_builds `v541`, and bazaardb `14.0 (Hotfix May 7)`. These are source-health results from one validation set, not three temporal windows. Markdown source-health tables are summaries; diff JSON is the more complete source-health review artifact when diagnostics matter. |

The 2026-05-06 dry run also surfaced a set of architecture-review findings beyond the three fetcher bugs. They are catalogued in §3 below and slotted into sessions in §2.

Follow-up validation covered Dooley, Karnok, Mak, Pygmalien, and Vanessa in `local_dry_run` with `--mock-llm`, live source fetches, temp-only artifacts, and exit code 0 for each hero. Focused tests passed (`59 passed in 0.39s`). Each hero produced diff JSON and proposal markdown, no real LLM/API calls occurred, and no checked-in state, catalog, stats sidecar, or tracker catalog files mutated. Artifact review treats these mock-mode outputs as operational evidence only; support-only/low-confidence classifications, duplicate or near-duplicate proposals, and missing evidence refs/sample counts are expected curator review items, not catalog-acceptance evidence. Because scheduled workflow runs default to the real classifier unless manually dispatched with mock mode or changed in workflow/code, real scheduled LLM behavior remains a precondition to test or explicitly waive before flipping to `shadow_cron`.

---

## 2. Remaining Work, Organized into Sessions

Sessions are listed in recommended order. Each is self-contained for a fresh Sonnet/Opus session.

### A. bazaardb fetcher restoration — completed

Completed and merged. A1 captured the live DOM/current-shape research; A2 restored the fetcher; the A2 follow-up fixed core-item evidence. The historical split below is preserved for provenance:

**A1 — Research session (browser-driven, no code).** Goals:

- Run Playwright with `headless=False` against `bazaardb.gg/run/meta` and capture the rendered DOM after Cloudflare clears.
- Confirm whether the page redesigned (likely) or whether headless detection is letting the challenge pass but blocking content (less likely).
- If redesigned: identify the new section-header text or DOM landmarks, the new path from item-card image to its archetype context, and the new "N runs · X%" frequency anchor (or its replacement).
- Confirm the patch label is still present and where (link text in the top nav today).
- Check whether the unfiltered-page-then-post-hoc-hero-filter approach is still viable, or whether the hero filter is now URL-based or unavailable.
- Update `bazaar_tracker/docs/automated-builds-pipeline-research.md` with a §1.1-style 2026-05-06 follow-up section, dated, additive (do not rewrite the original 2026-05-04 findings).
- Refresh `bazaar-builds/research/samples/bazaardb/` with a current-shape sample.
- **Decision output**: is bazaardb still a viable canonical statistical source? If the new shape requires fundamentally different extraction (e.g., no per-item frequencies, no archetype groupings), flag that to the curator before spending implementation budget. The locked design (§1) priorities bazaardb as the primary source — if that priority becomes infeasible, the curator decides whether to demote it or invest more.

**A2 — Implementation session (code-driven, downstream of A1).** Goals:

- Rewrite the relevant parts of `automated_builds_pipeline/sources/bazaardb.py` to match the research findings.
- Update `tests/test_sources_bazaardb.py`. Drive the tests off the new sample committed in A1.
- Verify locally with the dry-run command from `bazaar-builds/docs/pipeline-operations.md`.
- Open a PR for the issue #2 follow-up work.

The split lets A1 end with curator review of the research note before A2 commits to a parser shape. If A1 finds the page essentially unchanged and only Cloudflare detection improved, A1 + A2 can collapse into a single session — that decision is part of A1's deliverable.

### B. Source Drift Defense — completed

The 2026-05-05 dry run filed three fetcher bugs in a single run, ~24 hours after the source-shape probe. All three sources had drifted between probe and dry run. Today's tests are fixture-based and protect against parser regressions but not against source-side drift. **Session B landed the light design in `automated-builds-pipeline-design.md` §10.5.**

- Add a separate `live-sources-smoke` workflow: weekly cron plus manual dispatch, required sources go red on `unhealthy`, every run uploads a structured source-health artifact, and `mobalytics_build_articles:skipped` remains green when no article slugs are configured.
- Reuse the production bazaardb runtime expectations in smoke: Playwright Chromium installed with deps and `xvfb-run` available for headed fallback when headless is stuck on the Cloudflare challenge.
- Add `python -m automated_builds_pipeline.research.refresh_samples`: source-selectable, writes refreshed current-shape samples under `research/samples/<source>/`, supports local no-commit review, and avoids production stats/proposals/tracker catalogs/pipeline state.
- Defer broad health-detail vocabulary cleanup; keep B' to workflow + command implementation unless implementation uncovers a narrow fetcher bug.

**B' implementation completed and merged in `bazaar-builds`.** Implementation commit `fdb46d9` landed via merge commit `3734647`. It added the source-drift defense workflow/command surface without running the live pipeline or changing `pipeline_state.json`.

### C. Noise-section UX in early-phase proposals (architecture-review finding [1]) — completed

Completed and merged in `bazaar-builds`. The 2026-05-05 dry run produced 89 noise entries because `_initial_noise()` emitted a per-item row for every catalog item flagged `not_enough_windows` when the stats sidecar was empty.

- `insufficient_history` rows are filtered out of `_initial_noise()` entirely.
- Other deferred reasons roll up to one summary line per `(threshold_reason, count)` rather than per-item.
- Tests were updated in `tests/test_diff.py`.
- `automated-builds-pipeline-design.md` §8 was updated with a one-line noise-section note (an UX clarification, not a design change).

Implementation commit `f5ee53d` landed via merge commit `39b1eeb`; the tracker design-doc update landed at `a2fac53`.

### D. Small cleanups (architecture-review findings [2], [5], [6]) — completed

Completed and merged in `bazaar-builds`. Implementation commit `7024eae` landed via merge commit `c1f0552`.

- **[2] Composite `window_id` cleanup landed.** `diff.py:_window_id()` now drops unhealthy/unknown entries from the composite and falls back to `evaluation.run_id` if only those are left.
- **[5] Reshuffle reserved-slot comment landed.** `proposed_changes.archetype_reshuffles` remains reserved-but-unused for v1; reshuffle signal still goes to noise as `reshuffle_deferred` per design §8.
- **[6] Duplicate catalog walkers intentionally deferred.** `_catalog_index()` in `diff.py` and `iter_catalog_items()` in `evaluator.py` still independently re-walk the catalog with the same schema-awareness logic. This remains acceptable unless a third walker appears.

### E. Deferred — subtask 7 broader review tooling

Subtask 7 shipped a minimal PR-comment template. The broader review-tooling design pass (dashboard or richer surface for per-proposal stats) is deferred per design `§11` last bullet. Defer until shadow_cron has been running long enough for the curator to know what gaps the minimal template leaves.

---

## 3. Architecture-Review Findings — 2026-05-06 (Reference)

Findings surfaced during the 2026-05-06 review of the dry run. Numbering matches the original review for cross-reference. Findings already actioned are noted.

| # | Finding | Status |
|---|---|---|
| 1 | Noise overflow in early-phase proposals (89 entries from `not_enough_windows`). | **Resolved** in `bazaar-builds` merge commit `39b1eeb` (implementation `f5ee53d`) and tracker design-doc commit `a2fac53`. |
| 2 | `bazaardb:unknown` leaks into composite `window_id`. | **Resolved** in `bazaar-builds` merge commit `c1f0552` (implementation `7024eae`). |
| 3 | `document_version_missing` overloaded both "schema path moved" and "version field missing". | **Resolved** in PR `bazaar-builds#6`. Detail split into `document_path_missing` vs `document_version_missing`. |
| 4 | Research-time vs production-time shape drift is the dominant fragility. | **Resolved** in `bazaar-builds` merge commit `3734647` (implementation `fdb46d9`). |
| 5 | Reshuffle slot is reserved-but-unused; signal goes to noise. | **Resolved** in `bazaar-builds` merge commit `c1f0552` (implementation `7024eae`) with an explanatory code comment; reserved slot remains acceptable for v1 per design §8 unresolved. |
| 6 | Catalog-walker logic duplicated across `diff.py` and `evaluator.py`. | Deferred intentionally; collapse into a shared helper only if a third walker appears. |
| 7 | No locked-design contradictions found between implementation and design doc / subtask 1 spec. | Confirmed — no action. |

Full review notes are preserved in the 2026-05-06 chat transcript that produced this document; the actionable subset is the table above plus §2.

---

## 4. Pending Curator Decisions

These need a curator answer before the next implementation pass:

- **Subtask 7 (review tooling)**: defer indefinitely, or schedule a design pass once shadow_cron has run for ~4 windows and the curator can describe what the PR-comment template is missing? Recommendation: defer until shadow_cron starts producing real data.

---

## 5. Operational Caveats

- **`anthropic` package missing in local venv**: end-to-end local dry runs that exercise the LLM step will halt at diff generation. The GitHub Actions runner installs deps fresh, so cron is unaffected. `pip install anthropic` in the local venv to enable full local coverage.
- **Pipeline phase is `local_dry_run`**: `bazaar-builds` has been promoted with `dry_run: true`, which keeps output artifact-only and under manual review even though the weekly Actions schedule already exists. `implementation` is the only phase that makes a scheduled run exit before fetch/artifact work. `local_dry_run` can produce scheduled/manual artifacts but does not save or commit stats. `shadow_cron` adds persistent stats sidecar writes/commits in bazaar-builds while still avoiding tracker PR/catalog mutation. Before flipping, confirm the Actions schedule on `main` is intended, Claude secret/API/cost readiness is accepted, stats sidecar commits are accepted, real-LLM validation is performed or explicitly waived, and rollback to `local_dry_run` or `implementation` is clear. `live_cron` remains a later gate requiring at least 6 healthy bazaardb patch windows and at least 60 calendar days of shadow output (per subtask 1 §8).
