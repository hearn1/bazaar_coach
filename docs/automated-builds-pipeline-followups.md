# Automated Builds Pipeline — Post-Implementation Follow-Ups

*Session: 2026-05-06. Captures the state of the pipeline after the first end-to-end local dry run, the resulting fetcher bug fixes, and the architecture-review findings the dry run surfaced. Not a redesign; the locked decisions in `automated-builds-pipeline-design.md` stand. This document organizes the remaining work into discrete sessions and notes pending curator decisions.*

---

## 1. Status Snapshot (2026-05-06)

| Subtask / area | State |
|---|---|
| Subtasks 1–7 (design + implementation) | All merged. ROADMAP entry remains Open pending curator flip out of `phase: implementation`. |
| Issue #4 — bazaar-builds.net fetcher date health | **Closed.** PR `bazaar-builds#5`. Untracked record-creation paths bypassing the date filter were fixed; tests added; live fetch healthy. |
| Issue #3 — Mobalytics `document_version_missing` | **Closed.** PR `bazaar-builds#6`. Path walker now tolerates unrelated queries and null rows; diagnostic detail split into `document_path_missing` vs `document_version_missing`; live shape sample committed at `bazaar-builds/research/samples/mobalytics/meta-builds-preloaded-state-builds-2026-05-06.json`; live fetch healthy at `mobalytics_meta_builds:v540`. |
| Issue #2 — bazaardb `content_landmark_missing` / core-item evidence | **Closed.** A1 research confirmed the source remains viable; A2 restored the fetcher; follow-up `5367e7d` included bazaardb core-item evidence and merged via `bazaar-builds` merge commit `ddd5277`. |
| Pipeline phase | `implementation` (cron does nothing). Will not promote to `local_dry_run` / `shadow_cron` / `live_cron` until the curator manually flips. |
| Healthy sources today | bazaar-builds.net, mobalytics_meta_builds, bazaardb. (mobalytics_build_articles is `skipped` until article slugs are configured.) |

The 2026-05-06 dry run also surfaced a set of architecture-review findings beyond the three fetcher bugs. They are catalogued in §3 below and slotted into sessions in §2.

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
- Open a PR closing issue #2.

The split lets A1 end with curator review of the research note before A2 commits to a parser shape. If A1 finds the page essentially unchanged and only Cloudflare detection improved, A1 + A2 can collapse into a single session — that decision is part of A1's deliverable.

### B. Source Drift Defense — design pass (architecture-review finding [4]) — completed

The 2026-05-05 dry run filed three fetcher bugs in a single run, ~24 hours after the source-shape probe. All three sources had drifted between probe and dry run. Today's tests are fixture-based and protect against parser regressions but not against source-side drift. **Session B landed the light design in `automated-builds-pipeline-design.md` §10.5.**

- Add a separate `live-sources-smoke` workflow: weekly cron plus manual dispatch, required sources go red on `unhealthy`, every run uploads a structured source-health artifact, and `mobalytics_build_articles:skipped` remains green when no article slugs are configured.
- Reuse the production bazaardb runtime expectations in smoke: Playwright Chromium installed with deps and `xvfb-run` available for headed fallback when headless is stuck on the Cloudflare challenge.
- Add `python -m automated_builds_pipeline.research.refresh_samples`: source-selectable, writes refreshed current-shape samples under `research/samples/<source>/`, supports local no-commit review, and avoids production stats/proposals/tracker catalogs/pipeline state.
- Defer broad health-detail vocabulary cleanup; keep B' to workflow + command implementation unless implementation uncovers a narrow fetcher bug.

**Recommended B' implementation session:** doc-to-code pass in `bazaar-builds` only. Implement `live-sources-smoke`, implement the refresh-samples research command, add focused tests around command flags/summary shape where practical, and do not run the live pipeline or change `pipeline_state.json`.

### C. Noise-section UX in early-phase proposals (architecture-review finding [1])

Small implementation. The 2026-05-05 dry run produced 89 noise entries because `_initial_noise()` emits a per-item row for every catalog item flagged `not_enough_windows` when the stats sidecar is empty. Goals:

- Filter `insufficient_history` rows out of `_initial_noise()` entirely.
- For other deferred reasons, roll up to one summary line per `(threshold_reason, count)` rather than per-item.
- Update tests in `tests/test_diff.py`.
- Update `automated-builds-pipeline-design.md` §8 with a one-line noise-section note (this is an UX clarification, not a design change).

Self-contained. Can land any time after A2 or in parallel.

### D. Small cleanups (architecture-review findings [2], [5], [6])

Trivial, fold opportunistically into whichever session next touches the affected file:

- **[2] Composite `window_id` ugliness.** `diff.py:_window_id()` joins every `source_health` window_id including `<source>:unknown` placeholders, producing strings like `bazaardb:2026-W19+bazaardb:unknown+mobalytics_meta_builds:unknown`. Drop unhealthy/unknown entries from the composite; fall back to `evaluation.run_id` if only those are left.
- **[5] Reshuffle slot reserved-but-unused.** `proposed_changes.archetype_reshuffles` is in the diff schema but never populated — reshuffle signal goes to noise as `reshuffle_deferred`. Per design §8 unresolved this is acceptable for v1. Add a one-line code comment so the next reviewer doesn't hunt for the populating code.
- **[6] Duplicate catalog walkers.** `_catalog_index()` in `diff.py` and `iter_catalog_items()` in `evaluator.py` independently re-walk the catalog with the same schema-awareness logic. Not a bug; collapse into a shared helper only if a third walker appears.

### E. Deferred — subtask 7 broader review tooling

Subtask 7 shipped a minimal PR-comment template. The broader review-tooling design pass (dashboard or richer surface for per-proposal stats) is deferred per design `§11` last bullet. Defer until shadow_cron has been running long enough for the curator to know what gaps the minimal template leaves.

---

## 3. Architecture-Review Findings — 2026-05-06 (Reference)

Findings surfaced during the 2026-05-06 review of the dry run. Numbering matches the original review for cross-reference. Findings already actioned are noted.

| # | Finding | Status |
|---|---|---|
| 1 | Noise overflow in early-phase proposals (89 entries from `not_enough_windows`). | Open — Session C above. |
| 2 | `bazaardb:unknown` leaks into composite `window_id`. | Open — Session D above. |
| 3 | `document_version_missing` overloaded both "schema path moved" and "version field missing". | **Resolved** in PR `bazaar-builds#6`. Detail split into `document_path_missing` vs `document_version_missing`. |
| 4 | Research-time vs production-time shape drift is the dominant fragility. | Design complete — implement Session B' above. |
| 5 | Reshuffle slot is reserved-but-unused; signal goes to noise. | Open — Session D above. Acceptable for v1 per design §8 unresolved. |
| 6 | Catalog-walker logic duplicated across `diff.py` and `evaluator.py`. | Open — Session D above. Low-priority cleanup. |
| 7 | No locked-design contradictions found between implementation and design doc / subtask 1 spec. | Confirmed — no action. |

Full review notes are preserved in the 2026-05-06 chat transcript that produced this document; the actionable subset is the table above plus §2.

---

## 4. Pending Curator Decisions

These need a curator answer before the next implementation pass:

- **Session B' timing**: launch the implementation pass for `live-sources-smoke` + `refresh_samples` before or alongside Session C. Recommendation: B' next, because it protects the now-fixed fetchers before more cron-adjacent work accumulates.
- **Issue creation for smoke failures**: red workflow + uploaded artifact is the pinned first implementation. Curator decision needed only if B' should also open/update a GitHub Issue for required-source failures.
- **Session C timing**: land standalone. Recommendation: keep proposal-noise UX cleanup separate from B' source-drift implementation.
- **Subtask 7 (review tooling)**: defer indefinitely, or schedule a design pass once shadow_cron has run for ~4 windows and the curator can describe what the PR-comment template is missing? Recommendation: defer until shadow_cron starts producing real data.

---

## 5. Operational Caveats

- **`anthropic` package missing in local venv**: end-to-end local dry runs that exercise the LLM step will halt at diff generation. The GitHub Actions runner installs deps fresh, so cron is unaffected. `pip install anthropic` in the local venv to enable full local coverage.
- **Pipeline phase remains `implementation`**: the workflow exits without action on its weekly cron. Even with issue #2 closed, the curator must manually flip to `local_dry_run` for ad-hoc verification, then to `shadow_cron`. Promotion to `live_cron` requires ≥6 healthy bazaardb patch windows AND ≥60 calendar days of shadow output (per subtask 1 §8).
