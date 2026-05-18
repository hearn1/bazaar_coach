# Opus Review Prompt: Deterministic Classifier Plan

Use this prompt with Claude Opus (claude-opus-4-7) to get a critical review of the implementation plan before coding begins.

---

## Prompt

You are reviewing an implementation plan for the `bazaar-builds` automated pipeline
(GitHub: hearn1/bazaar-builds). The pipeline proposes build catalog changes for a
game coaching tool (`bazaar_coach`, hearn1/bazaar_coach) by fetching data from
bazaardb.gg, Mobalytics, and bazaar-builds.net, then running them through a classifier
to assign carry/core/support tiers to items.

The pipeline is currently in `shadow_cron / dry_run: true` — no proposals reach the
coach repo. The goal is to reach `live_cron` as fast as possible without compromising
catalog integrity.

**Read the full implementation plan here:**
`docs/deterministic_classifier_plan.md` (in hearn1/bazaar_coach repo, current branch)

**Also read these source files from hearn1/bazaar-builds (main branch) for ground truth:**
- `automated_builds_pipeline/readiness.py` — the 4-gate promotion logic
- `automated_builds_pipeline/diff.py` — how ClassifierMode flows, how `_classify_group()` dispatches
- `automated_builds_pipeline/pipeline.py` — how classifiers are wired and guarded
- `automated_builds_pipeline/stats.py` — HeroStats schema and `append_window()`
- `automated_builds_pipeline/llm.py` — ItemClassification dataclass and interface contract

Use `gh api repos/hearn1/bazaar-builds/contents/<path>` with PowerShell base64 decoding to read those files.

**Review the plan and answer the following questions:**

### 1. Correctness
- Is the `ItemClassification` interface correctly replicated? Check the exact constructor signature in `llm.py` — does the plan's `DeterministicClassifier` call it correctly (positional vs keyword args, valid `surface` values)?
- Does `_classify_group()` in `diff.py` need any changes beyond adding a "deterministic" branch, or does the "llm" branch already handle the generic classifier call in a way that could be reused?
- Will `stats.last_classifier_mode = classifier_mode` in `pipeline.run()` persist correctly? Check where `save_stats()` is called relative to where the plan sets this field.
- Is the `_check_classifier_readiness()` signature change safe? The current function returns `(classifier_ready, waiver_found)` — does the plan's new version maintain that contract?

### 2. Gate 2 logic correctness
- The plan proposes: `effective_min_days = MIN_SHADOW_DAYS_WITH_CLASSIFIER if classifier_ready else MIN_SHADOW_DAYS`. But `classifier_ready` is determined by `_check_classifier_readiness()` which itself reads from sidecars. On the very first deterministic run, will the sidecar have been updated before `evaluate_readiness()` reads it — or is there a sequencing issue?
- Should the 7-day clock start from the *first* deterministic classifier run (oldest deterministic window), rather than from the oldest *any* window? If so, does the plan address this distinction in how `oldest_observed_at` is computed?

### 3. Carry detection risks
- The `_extract_carry_candidate()` logic strips "Builds/Build/Run/Deck" and matches the remaining phrase against `known_items`. What happens with archetypes like "Freeze Build" or "Economy Support" where the carry is not a single item name but a mechanic? Does the fallback to `None` handle this gracefully (i.e., no carry classification, items fall through to core/support logic)?
- The plan checks `phrase in candidate_items` (by item name) — is this safe if the carry item is already in the existing catalog (and therefore not in `candidate_items` for this run)?

### 4. Schema safety
- `HeroStats.from_dict()` strictly checks `schema_version == SCHEMA_VERSION`. The plan adds `last_classifier_mode` without bumping the version. Confirm this is safe: will existing sidecars (missing the field) load correctly, and will sidecars written with the new field be readable by old code?

### 5. Phase guard correctness
- The plan says "deterministic mode is allowed in all phases — do NOT pass it through `_no_llm_shadow_allowed()`". But `_no_llm_shadow_allowed()` only guards `no_llm_shadow` mode. Confirm: is there any other guard in `pipeline.run()` that would block "deterministic" in live_cron phase?
- The `_mock_llm_allowed()` guard in pipeline.py — is "deterministic" accidentally gated by mock_llm logic anywhere?

### 6. Missing pieces
- The plan does not mention updating the GitHub Actions workflow YAML (`.github/workflows/*.yml`) to pass `--classifier-mode deterministic` to the cron job. Is this needed, or does the workflow use the default from `pipeline_state.json` / a default arg? If needed, which file needs changing?
- Are there existing tests in `tests/` that test `_check_classifier_readiness()` or `_classify_group()` that will break and need updating?
- The `ROADMAP.md` update is listed but not detailed. What specifically should change there?

### 7. Timeline feasibility
- Gate 1 (≥2 healthy bazaardb windows) — how often do bazaardb patch windows occur? If the game patches monthly, this could be the actual bottleneck even after Gate 2/3 are resolved. Does the plan account for this?

### Output format
Provide your review as:
1. **Verdict**: Ready to implement / Needs changes / Blocked
2. **Critical issues** (must fix before coding): numbered list
3. **Minor issues** (fix during implementation): numbered list
4. **Confirmations** (things that check out): brief list
5. **Suggested additions to the plan**: any gaps worth closing

Be direct and specific. Reference exact function names, line-level concerns, and file paths.
