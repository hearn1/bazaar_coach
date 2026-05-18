# Plan: Promote bazaar-builds to live_cron via Deterministic Classifier

## Context

`bazaar-builds` (hearn1/bazaar-builds) runs a daily cron that proposes catalog changes for
`bazaar_coach` (hearn1/bazaar_coach). It is currently `phase: shadow_cron / dry_run: true` —
no PRs flow to bazaar_coach. Two gates block promotion:

- **Gate 2**: requires 60 calendar days of shadow output
- **Gate 3**: requires `semantic_classification: true` in a sidecar — always False today
  because `_check_classifier_readiness()` has a hardcoded `classifier_ready = False` and
  stats sidecar v1 has no field for it

Current mode is `no_llm_shadow` → items are `classification_pending`, no tier assigned.

Goal: deploy a deterministic (no-LLM) classifier, wire it into the gate checks, reduce
Gate 2 to **7 days of *classifier-produced* output** (measured from the first deterministic
run, not from the oldest shadow window), then flip to `live_cron`.

---

## Key Architecture Facts (from reading source)

- **`readiness.py`** — 4 gates: (1) ≥2 healthy bazaardb windows, (2) ≥60 shadow days,
  (3) classifier ready or waiver, (4) no malformed run in last 14 days.
  `_check_classifier_readiness()` unconditionally returns `classifier_ready = False`.
  `shadow_days` is derived from `oldest_observed_at` = oldest healthy bazaardb window EVER
  (it does **not** distinguish classified vs unclassified windows).
- **`stats.py`** — `HeroStats` schema_version=1 is strict (`from_dict` rejects version
  mismatches in **both** directions, so a version bump would break old↔new interop).
  Additive optional fields with `_optional_str` default None are safe with no bump;
  old code ignores unknown keys.
- **`diff.py`** — `ClassifierMode = Literal["llm", "mock", "no_llm_shadow"]`.
  `_classify_group()`: after the `mock`/`no_llm_shadow` branches its tail already calls
  `classifier.classify_archetype(...)` **generically** — any duck-typed classifier object is
  handled with no new branch, provided `classifier` is not None.
  `_artifact_classification_mode()` already passes any non-`no_llm_shadow` mode through
  unchanged — **no edit needed there**.
  `semantic_classification` is currently `artifact_mode == "llm"` only.
- **`pipeline.py`** — guards: `_no_llm_shadow_allowed()` only gates `no_llm_shadow`;
  `_mock_llm_allowed()`/`_resolve_classifier_mode()` only react to `mock_llm`. Nothing
  blocks "deterministic" in any phase. The only hard requirement: `run()` must construct a
  non-None classifier object for `deterministic`, or `_classify_group`'s
  `if classifier is None: raise` fires.
- **`.github/workflows/automated-builds-refresh.yml`** — the scheduled cron passes no
  inputs, so the "Run pipeline" step resolves `classifier_mode` to the hardcoded
  `no_llm_shadow` for `shadow_cron`. **Deterministic will never run on the cron until this
  YAML is changed.** Stats land on `main` only via the rolling `automated/stats-sync-<hero>`
  PR (direct push to `main` is branch-protected); `readiness` reads the merged `stats/` dir.
- **`llm.py`** — `ItemClassification(item, classification, confidence, rationale, surface)`
  (frozen dataclass, positional). `_surface_for(classification, confidence)`: invalid|low →
  "suppressed"; medium → "weaker_signal"; high → "top_line". Only "top_line" reaches the PR
  body; "weaker_signal" reaches the PR comment.
- **Candidate row fields**: `item`, `windows_seen`, `source_presence`, `classification_ceiling`
  (`carry_core_support` | `support_only` | `not_applicable`), `sample_count_latest`,
  `first_seen_window`, `threshold_result`, `evidence_refs`.
- **`classify_archetype()` parameters**: `hero`, `phase`, `archetype`, `existing_buckets`,
  `candidate_items`, `evidence_summary`, `mobalytics_description`.

---

## Part 1 — DeterministicClassifier

### New file: `automated_builds_pipeline/deterministic_classifier.py`

Implements the same `classify_archetype()` interface as `LLMClassifier`. **Surface is computed
via the shared `llm._surface_for(classification, confidence)`** rather than hand-assigned, so
the two classifiers can never drift.

**Carry detection** — extracted from the `archetype` parameter string:
1. Strip trailing "build/builds/run/deck" suffixes (regex, case-insensitive)
2. Try exact match of the stripped phrase against `known_items`
3. If no match, try just the first word
4. Match must also be in this run's `candidate_items` (so we never re-propose an already-cataloged carry)

**Core vs Support thresholds** using candidate row signals:

| Condition | Classification | Confidence |
|---|---|---|
| Archetype name match + bazaardb present + ceiling=carry_core_support | carry | high |
| bazaardb present + windows_seen ≥ 3 + ceiling=carry_core_support | core | high |
| bazaardb present + windows_seen ≥ 2 | core (→support if ceiling=support_only) | medium |
| Any source present + windows_seen ≥ 1 (multi) | support | medium |
| windows_seen == 1 + single source only | support | low |
| item not in known_items / ceiling=not_applicable | invalid | low |

Surface is then `_surface_for(classification, confidence)` — high→top_line, medium→weaker_signal,
low/invalid→suppressed. The `support_only` cap is also independently re-applied in `diff.py`
(idempotent), so the classifier's cap is belt-and-suspenders only.

```python
from automated_builds_pipeline.llm import ItemClassification, _surface_for

CARRY_SUFFIX_RE = re.compile(r'\s+(build|builds|run|deck)s?\s*$', re.IGNORECASE)
CORE_HIGH_WINDOWS = 3
CORE_MED_WINDOWS = 2
BAZAARDB = "bazaardb"

class DeterministicClassifier:
    known_items: set[str]

    def __init__(self, known_items_path: Optional[Path] = None):
        self.known_items = set()
        if known_items_path and known_items_path.exists():
            self.known_items = {
                ln.strip() for ln in known_items_path.read_text(encoding="utf-8").splitlines()
                if ln.strip()
            }

    def classify_archetype(self, hero, phase, archetype, existing_buckets,
                           candidate_items, evidence_summary, mobalytics_description):
        carry = _extract_carry_candidate(archetype or "", self.known_items, candidate_items)
        return [_classify_row(row, self.known_items, carry) for row in candidate_items]


def _make(item, classification, confidence, rationale):
    return ItemClassification(item, classification, confidence, rationale,
                              _surface_for(classification, confidence))


def _extract_carry_candidate(archetype, known_items, candidate_items):
    candidate_names = {row.get("item") for row in candidate_items}
    stripped = CARRY_SUFFIX_RE.sub("", archetype).strip()
    for phrase in [stripped, stripped.split()[0] if stripped else ""]:
        if phrase and phrase in known_items and phrase in candidate_names:
            return phrase
    return None


def _classify_row(row, known_items, carry_candidate):
    item = str(row.get("item", ""))
    if item not in known_items:
        return _make(item, "invalid", "low", "Item not in known_items")
    ceiling = row.get("classification_ceiling", "carry_core_support")
    if ceiling == "not_applicable":
        return _make(item, "invalid", "low", "All sources absent (not_applicable ceiling)")
    src = row.get("source_presence", {})
    bazaardb_present = src.get(BAZAARDB) == "present"
    source_count = sum(1 for v in src.values() if v == "present")
    windows = int(row.get("windows_seen") or 0)

    if item == carry_candidate and bazaardb_present and ceiling == "carry_core_support":
        return _make(item, "carry", "high", f"Archetype name match: {item}")
    if bazaardb_present and windows >= CORE_HIGH_WINDOWS and ceiling == "carry_core_support":
        return _make(item, "core", "high", f"bazaardb present, {windows} windows")
    if bazaardb_present and windows >= CORE_MED_WINDOWS:
        cls = "support" if ceiling == "support_only" else "core"
        return _make(item, cls, "medium", f"bazaardb present, {windows} windows")
    if source_count >= 1 and windows >= 1:
        if windows == 1 and source_count == 1:
            return _make(item, "support", "low", "Single-window single-source signal")
        return _make(item, "support", "medium", f"{source_count} sources, {windows} windows")
    return _make(item, "support", "low", "Weak signal")
```

### Changes to `automated_builds_pipeline/diff.py`

1. `ClassifierMode = Literal["llm", "mock", "no_llm_shadow", "deterministic"]`
2. **No `_classify_group()` change required** — the generic tail already invokes
   `classifier.classify_archetype(...)` for any non-None duck-typed classifier. (Do not add a
   redundant branch.)
3. **No `_artifact_classification_mode()` change required** — it already returns
   `"deterministic"` for non-`no_llm_shadow` modes.
4. `semantic_classification`: change `artifact_mode == "llm"` →
   `artifact_mode in ("llm", "deterministic")`.
5. `llm_provider`: emit `"deterministic"` for the deterministic mode (clearer than `"none"`).
6. Arg parser: add `"deterministic"` to `--classifier-mode` choices.
7. Widen `classifier` param hints from `Optional[LLMClassifier]` to a `Classifier` Protocol
   (or `Optional[object]`) so DeterministicClassifier type-checks.

### Changes to `automated_builds_pipeline/pipeline.py`

1. `ClassifierMode` literal: add `"deterministic"`.
2. `run()`: wire DeterministicClassifier when `classifier_mode == "deterministic"`:
   ```python
   elif classifier_mode == "deterministic":
       from automated_builds_pipeline.deterministic_classifier import DeterministicClassifier
       classifier = DeterministicClassifier(known_items_path=_names_file(tracker_repo))
       classifier.known_items.update(diff._all_catalog_names(catalog))
   ```
3. No phase guard needed — `deterministic` is intentionally allowed in all phases. Existing
   `no_llm_shadow`/`mock_llm` guards are unchanged.
4. Record classifier mode/start **before** the `if state.phase != "local_dry_run":`
   `save_stats(...)` block:
   ```python
   stats.last_classifier_mode = classifier_mode
   if classifier_mode in REAL_CLASSIFIER_MODES and stats.classifier_started_at is None:
       stats.classifier_started_at = _utc_now()   # set once, never overwritten
   ```
5. Arg parser: add `"deterministic"` to `--classifier-mode` choices.

---

## Part 2 — Workflow YAML (`.github/workflows/automated-builds-refresh.yml`)

**Required — without this the scheduled cron never runs deterministic.** In the "Run
pipeline" step, change the `shadow_cron` default:

```bash
if [[ -z "$classifier_mode" ]]; then
  if [[ "${{ steps.state.outputs.phase }}" == "shadow_cron" ]]; then
    classifier_mode="deterministic"     # was: no_llm_shadow
  else
    classifier_mode="llm"
  fi
fi
```

Also update the `workflow_dispatch` `classifier_mode` input description to list
`deterministic` (and `no_llm_shadow`) as accepted values. No other workflow steps change;
the readiness `--enforce` step still only runs in `live_cron`.

---

## Part 3 — Gate Updates in `stats.py` and `readiness.py`

### `automated_builds_pipeline/stats.py` — two optional fields on HeroStats

No schema version bump (additive optional fields; old sidecars load with None; old code
ignores unknown keys):

```python
@dataclass
class HeroStats:
    hero: str
    schema_version: int = SCHEMA_VERSION
    generated_at: Optional[str] = None
    last_classifier_mode: Optional[str] = None       # NEW — most recent run's mode
    classifier_started_at: Optional[str] = None       # NEW — UTC of first real-classifier run, set once
    retention_windows: ...
    source_windows: ...
    items: ...
```

- `to_dict()`: include each key only when not None.
- `from_dict()`: `last_classifier_mode=_optional_str(data, "last_classifier_mode")`,
  `classifier_started_at=_optional_str(data, "classifier_started_at")`.

### `automated_builds_pipeline/readiness.py` — Gate 3 + Gate 2

**Constants:**
```python
MIN_SHADOW_DAYS = 60                   # fallback when no real classifier has run
MIN_CLASSIFIED_DAYS = 7                # NEW — classified-output requirement
REAL_CLASSIFIER_MODES = frozenset({"llm", "deterministic"})
```
(`REAL_CLASSIFIER_MODES` defined once, imported by both readiness.py and pipeline.py.)

**Gate 3 — `_check_classifier_readiness()`** keeps the `(classifier_ready, waiver_found)`
contract:
```python
def _check_classifier_readiness(sidecars, waiver_dir):
    classifier_ready = any(
        s.last_classifier_mode in REAL_CLASSIFIER_MODES for s in sidecars
    )
    waiver_found = (
        waiver_dir is not None and waiver_dir.is_dir()
        and any(waiver_dir.glob("classifier_waiver_*.md"))
    )
    return classifier_ready, waiver_found
```

**Gate 2 — measure the *classified* span, gated on `classifier_ready` only (NOT waiver):**
```python
# oldest_observed_at: existing oldest-healthy-bazaardb-window logic (unchanged) → 60d fallback
classifier_started = _min_classifier_started_at(sidecars)  # min non-None across sidecars

if classifier_ready and classifier_started is not None:
    classified_days = (now - classifier_started).total_seconds() / 86400.0
    if classified_days < MIN_CLASSIFIED_DAYS:
        blockers.append(
            f"Classifier-produced output spans only {classified_days:.1f} days; "
            f"{MIN_CLASSIFIED_DAYS} required. First classified run at "
            f"{classifier_started.isoformat()}."
        )
else:
    # No real classifier (or a waiver only) → full 60-day shadow requirement still applies.
    if shadow_days < MIN_SHADOW_DAYS:
        blockers.append(
            f"Shadow output spans only {shadow_days:.1f} days; {MIN_SHADOW_DAYS} required "
            f"(no classifier deployed)."
        )
```

Notes:
- A **waiver no longer shortens Gate 2** — it only satisfies Gate 3. With no classifier you
  still need the full 60 days.
- `last_classifier_mode` is brittle (a later `no_llm_shadow` run flips
  `classifier_ready` False). `classifier_started_at` is set once and never overwritten, so it
  is the durable signal for the classified span. Gate 3 still keys off
  `last_classifier_mode` so a regression to no_llm_shadow correctly re-blocks promotion.
- Add `"effective_min_shadow_days"`, `"classified_days"`, `"classifier_started_at"` to the
  `summary` dict; update `_print_human()` to show the active threshold.

### New / updated tests (`tests/`)

- `test_readiness.py`: add cases — (a) sidecar with `last_classifier_mode="deterministic"` +
  `classifier_started_at` ≥7d ago + ≥2 healthy windows → ready; (b) classified <7d → blocked;
  (c) **waiver-only must still require 60 days** (regression guard for the C3 fix);
  (d) `last_classifier_mode="no_llm_shadow"` after a deterministic run → Gate 3 re-blocks.
- `test_stats.py`: round-trip with both new fields set and unset; confirm old sidecar
  (no fields) loads with None and `to_dict()` omits them.
- `test_pipeline.py` / `test_diff.py`: `build_arg_parser()` accepts
  `--classifier-mode deterministic`; pipeline wires DeterministicClassifier and sets
  `classifier_started_at` once.

---

## Part 4 — Flip to live_cron (manual, after gates pass)

**Pre-flight:**
0. `python -m automated_builds_pipeline.readiness --json` to read the **current**
   `healthy_bazaardb_windows` (Gate 1). Gate 1 (≥2 healthy bazaardb windows) is governed by
   The Bazaar's patch cadence, not by this plan — see Open Questions. It may already be
   satisfied by accumulated shadow windows; if not, it is the true critical path.
1. The rolling `automated/stats-sync-<hero>` PRs carrying `last_classifier_mode` /
   `classifier_started_at` must be **merged to `main`** before readiness can see them.
2. `python -m automated_builds_pipeline.readiness --enforce` exits 0.
3. `TRACKER_PR_TOKEN` secret exists (scoped to bazaar_coach: Contents R/W, PRs R/W).
4. bazaar_coach `main` allows PRs from the PAT owner.

**Flip `pipeline_state.json`:** `{ "dry_run": false, "phase": "live_cron", ... }`

**Update `CLAUDE.md` in `bazaar_coach`** — replace the promotion criteria block AND align
the existing "scheduled runs default to deterministic `no_llm_shadow`" line to read
`deterministic`:
```
Do not promote to `live_cron` until all of these are true:
- At least 2 healthy bazaardb patch windows have accumulated.
- Deterministic/LLM classifier-produced output spans ≥7 calendar days
  (measured from classifier_started_at); otherwise ≥60 days of shadow.
- last_classifier_mode is a real classifier in a hero sidecar, OR an explicit
  waiver file is placed (waiver does NOT shorten the day requirement).
- No malformed shadow run in the last 14 days.
- Curator manually flips phase/dry_run after reviewing shadow artifacts.
```

---

## Order of Operations

```
Step 0  [verify]            readiness --json: record current healthy_bazaardb_windows (Gate 1)
Step 1  [bazaar-builds PR]  deterministic_classifier.py (surface via _surface_for)
Step 2  [bazaar-builds PR]  diff.py: ClassifierMode + semantic_classification + provider + arg choices + type hint
Step 3  [bazaar-builds PR]  pipeline.py: wire classifier, set last_classifier_mode + classifier_started_at
Step 4  [bazaar-builds PR]  stats.py: add last_classifier_mode + classifier_started_at
Step 5  [bazaar-builds PR]  readiness.py: Gate 3 + Gate 2 (classified-span, classifier_ready-only)
Step 6  [bazaar-builds PR]  workflow YAML: shadow_cron default → deterministic + input desc
Step 7  [bazaar-builds PR]  tests: readiness/stats/pipeline/diff (incl. waiver-still-60d regression)
Step 8  [bazaar-builds PR]  ROADMAP.md: gate status
Step 9  [merge]             Merge the above; cron now runs deterministic in shadow_cron
Step 10 [wait/observe]      ≥7 days of deterministic output; review shadow artifacts
Step 11 [merge stats]       Merge rolling automated/stats-sync-<hero> PRs into main
Step 12 [verify]            readiness --enforce → exit 0 (Gate 1 may gate longer; see Step 0)
Step 13 [verify]            Confirm TRACKER_PR_TOKEN secret provisioned
Step 14 [bazaar-builds]     Flip pipeline_state.json → phase: live_cron, dry_run: false
Step 15 [bazaar_coach]      Update CLAUDE.md promotion criteria + no_llm_shadow→deterministic line
Step 16 [verify]            Next cron opens a PR in bazaar_coach for ≥1 hero
```

---

## File Summary

| Repo | File | Change |
|---|---|---|
| bazaar-builds | `automated_builds_pipeline/deterministic_classifier.py` | **NEW** |
| bazaar-builds | `automated_builds_pipeline/diff.py` | ClassifierMode + semantic_classification + provider + arg choices + type hint |
| bazaar-builds | `automated_builds_pipeline/pipeline.py` | Wire classifier; record mode + started_at |
| bazaar-builds | `automated_builds_pipeline/stats.py` | Add `last_classifier_mode`, `classifier_started_at` |
| bazaar-builds | `automated_builds_pipeline/readiness.py` | Gate 3 + Gate 2 classified-span |
| bazaar-builds | `.github/workflows/automated-builds-refresh.yml` | shadow_cron default → deterministic |
| bazaar-builds | `tests/test_{readiness,stats,pipeline,diff}.py` | New + regression tests |
| bazaar-builds | `ROADMAP.md` | Update gate status |
| bazaar-builds | `pipeline_state.json` | Flip at Step 14 (manual) |
| bazaar_coach | `CLAUDE.md` | Promotion criteria + no_llm_shadow→deterministic line |

---

## ROADMAP.md updates (bazaar-builds)

- Gate 3: mark mechanism implemented — deterministic (no-LLM) classifier; `classifier_ready`
  now reads `last_classifier_mode` from hero sidecars.
- Gate 2: documented as 7 days of classifier-produced output (from `classifier_started_at`),
  60-day shadow fallback when no real classifier; **waiver does not shorten the day count**.
- Phase status: `shadow_cron` classifier default moved from `no_llm_shadow` → `deterministic`.
- Gate 1 remains patch-cadence-bound (note as the likely critical path).

---

## Verification Checkpoints

After first deterministic cron (Step 9/10), inspect `<hero>_diff.json`:
- `classification_mode: "deterministic"` ✓
- `semantic_classification: true` ✓
- `llm_provider: "deterministic"` ✓
- Items in `candidate_core` / `candidate_support` (not `candidate_pending`) ✓

Inspect the merged `stats/<hero>_stats.json` (Step 11):
- `last_classifier_mode: "deterministic"` ✓
- `classifier_started_at` set, unchanged across subsequent runs ✓

After Step 12: `readiness --json` shows `classifier_ready: true`,
`classified_days ≥ 7`; `readiness --enforce` exits 0 (assuming Gate 1 satisfied).

After Step 16: PR `[automated-builds] <hero> proposal` opens in bazaar_coach ✓

---

## Open Questions / Tunable Thresholds

- **Gate 1 patch cadence (likely the real critical path).** ≥2 *distinct* healthy bazaardb
  window_ids are required; window_id is keyed by patch label, so runs between patches reuse
  the same window and do not add count. If The Bazaar patches ~monthly, Gate 1 — not Gate 2 —
  determines the timeline regardless of how fast the classifier work lands. Step 0 reads the
  current count; if already ≥2 (likely after ~53 days of shadow), timeline is the 7-day
  classified-observation window plus stats-PR merges. If <2, promotion waits on game patches.
- `CORE_HIGH_WINDOWS = 3` / `CORE_MED_WINDOWS = 2` are proposed defaults. If shadow artifacts
  show bazaardb typically confirms in 2 windows, lowering `CORE_HIGH_WINDOWS` to 2 promotes
  those items to top_line and reduces curator load. Single-constant post-deploy change.
