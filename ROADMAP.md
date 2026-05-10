# Bazaar Coach - Roadmap

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

## Discord Alpha — Open Issues (v0.2.0-alpha.1)

Findings from manual installer/runtime verification on 2026-05-10 against packaged version `0.2.0-alpha.1`. Items are ordered P1 → P2 within severity.

**Verification passed:** pytest (192 tests), py_compile, portable build + smoke test, installer build, clean install launch, main "Bazaar Coach" Start Menu shortcut, Doctor shortcut target (`cmd /K "...BazaarCoach.exe" doctor`), overlay + dashboard launch, real game capture (first few actions), SQLite rows + session logs created, diagnostics ZIP created, refresh-builds.

**Open:**

### P1-1 — Terminal windows flash repeatedly while waiting for The Bazaar

**Observed:** Launched installed "Bazaar Coach" shortcut before The Bazaar was running. Multiple PowerShell/cmd-like windows repeatedly opened and closed until the game process appeared, then stopped.

**Impact:** Looks broken/scary to Discord testers. Should wait quietly for the game.

**Expected:** No visible terminal windows during the wait-for-game phase. Overlay/dashboard may show "waiting for game." Once The Bazaar is detected, `capture_mono` attaches normally.

**Likely fix area:**
- `coach.py` subprocess launch of `capture_mono` in packaged mode
- Retry loop that re-spawns `capture_mono` while game process is absent
- Windows subprocess creation flags: `CREATE_NO_WINDOW`, `STARTUPINFO` / `STARTF_USESHOWWINDOW`
- PyInstaller packaged-binary subprocess behavior (visible console window)

**How to test:**
1. Install packaged app.
2. Launch "Bazaar Coach" shortcut before The Bazaar is running.
3. Confirm no terminal windows flash — overlay/dashboard should wait quietly.
4. Open The Bazaar and confirm `capture_mono` attaches and decisions flow.

---

### P1-2 — Uninstaller "Yes, remove user data" does not remove user data

**Observed:** After uninstalling and choosing "Yes" to remove user data:
- `Test-Path "$env:LOCALAPPDATA\BazaarCoach"` → `True`
- `Test-Path "$env:APPDATA\BazaarCoach"` → `True`

Leftovers: `%LOCALAPPDATA%\BazaarCoach` contained `bazaar_runs.db`, `logs/`, diagnostics ZIP, `builds/`, `static_cache/`. `%APPDATA%\BazaarCoach` contained `settings.json`.

**Impact:** Uninstaller prompt claims data will be removed but does not remove it. Leaves private gameplay/log data behind when user explicitly chooses deletion.

**Expected:**
- Yes → removes `%LOCALAPPDATA%\BazaarCoach` and `%APPDATA%\BazaarCoach`.
- No → preserves both directories.

**Likely fix area:** `packaging/installer/BazaarCoach.iss` — Pascal script / uninstall prompt logic, `{localappdata}` and `{userappdata}` path constants, per-user install privilege behavior.

**How to test:**
1. Install app and generate some data (run the app briefly).
2. Uninstall → choose **No** → confirm app files removed, user data preserved.
3. Reinstall → generate data.
4. Uninstall → choose **Yes** → confirm `%LOCALAPPDATA%\BazaarCoach` and `%APPDATA%\BazaarCoach` are both gone.

---

### P1-3 — Runtime DB leaked into installed PyInstaller `_internal` folder

**Observed:** After uninstalling with "No" (preserve user data), the install folder was not removed because it contained:
```
%LOCALAPPDATA%\Programs\Bazaar Coach\0.2.0-alpha.1\_internal\bazaar_runs.db
%LOCALAPPDATA%\Programs\Bazaar Coach\0.2.0-alpha.1\_internal\bazaar_runs.db-shm
%LOCALAPPDATA%\Programs\Bazaar Coach\0.2.0-alpha.1\_internal\bazaar_runs.db-wal
```

**Impact:** Runtime data written into the install directory. Causes uninstall leftovers, risks split DB state (data spread across two locations), and confuses support/diagnostics.

**Expected:**
- Runtime DB lives only at `%LOCALAPPDATA%\BazaarCoach\bazaar_runs.db`.
- Settings live only at `%APPDATA%\BazaarCoach\settings.json`.
- No `bazaar_runs.db*` under `%LOCALAPPDATA%\Programs\Bazaar Coach\`.

**Likely fix area:**
- `app_paths.py` packaged-mode path detection
- `capture_mono.py` or another subprocess calling `sqlite3.connect("bazaar_runs.db")` with a relative path
- PyInstaller subprocess `cwd` defaulting to `_internal`
- Any fallback path that resolves to CWD when the app-paths detection fails

**How to test:**
1. Install packaged app.
2. Launch app and capture at least a few decisions.
3. Run: `Get-ChildItem "$env:LOCALAPPDATA\Programs\Bazaar Coach" -Recurse -Force -Filter "bazaar_runs.db*" -ErrorAction SilentlyContinue`
4. Expected: no results.
5. Confirm DB exists at `%LOCALAPPDATA%\BazaarCoach\bazaar_runs.db`.

---

### P2/P1-4 — Installed GUI exe support commands silent in terminal

**Observed:** From installed app directory:
```
.\BazaarCoach.exe doctor
.\BazaarCoach.exe refresh-builds
.\BazaarCoach.exe export-diagnostics
```
Printed no output in PowerShell. Commands did execute (`export-diagnostics` created a ZIP; `refresh-builds` appeared to work), but there was no visible confirmation.

**Note:** Start Menu "Doctor" shortcut correctly opens `cmd /K "...BazaarCoach.exe" doctor` and does show output — so there is a working support path for testers.

**Impact:** Direct terminal invocation appears broken even when commands succeed. Testers may not know the supported path is the Start Menu shortcut.

**Expected / options:**
- Provide a console-mode binary (`BazaarCoachCLI.exe`) for `doctor`/`refresh-builds`/`export-diagnostics`.
- Provide `.cmd` wrappers that open a visible window with output and log location hints.
- Or keep GUI exe as-is and update release notes / Discord alpha guide to steer testers to the Start Menu Doctor shortcut and the session log file.

**How to test:**
- Run installed support commands via the intended path (Start Menu shortcut or wrapper).
- Confirm testers can read `doctor` output and locate diagnostic ZIPs.

---

### P2-5 — Shutdown logs SQLite thread-close warning

**Observed log line during normal shutdown:**
```
[DB] close_shared_conn failed: SQLite objects created in a thread can only be used in that same thread. The object was created in thread id 16296 and this is thread id 7256.
```

**Impact:** Likely harmless (data written, shutdown completed cleanly), but looks alarming in diagnostics bundles shared by testers.

**Likely fix area:** `db.py` shared connection lifecycle / writer thread shutdown path — connection being closed from a different thread than it was opened on.

**How to test:**
1. Install app, capture at least one decision, shut down normally.
2. Confirm latest `logs/coach_*.log` has no SQLite thread-close warning on exit.

---

### P2-6 — Some early run item names unresolved

**Observed during real run capture:**
- `ShopPage Bought: ['Flying Squirrel', 'Worry Wart'] | Passed on: ['itm_ikyo_pq']`
- `Decision #3 🛒 Unknown | Inferred from shop select command`

**Impact:** Acceptable alpha caveat if infrequent; worth tracking as name-resolution polish.

**Likely fix area:** `name_resolver.py`, `run_state.py` shop decision paths, Mono template notification timing relative to first shop decisions.

**How to test:**
- Start a new run and capture the first shop decisions.
- Confirm bought/passed/review rows prefer human-readable names and do not regress previously resolved paths.

---

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

Status: implementation work lives in the [bazaar-builds](https://github.com/hearn1/bazaar-builds) repo and has been promoted to `phase: shadow_cron` with `dry_run: true`. The GitHub Actions cron schedule exists. Scheduled `shadow_cron` runs default to deterministic `no_llm_shadow`, may fetch sources, evaluate, write diff/proposal artifacts, upload review artifacts, and open/update a rolling `automated/stats-sync-<hero>` PR per hero against bazaar-builds `main` (direct push is blocked by branch protection — the per-hero branch is force-pushed each run and the PR auto-updates). They still do not mutate tracker catalogs or open tracker PRs. `live_cron` remains disabled until a later manual gate with accumulated healthy shadow history and semantic catalog-review readiness.

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
- Shadow monitoring: review scheduled/manual shadow artifacts, confirm source-health fields are clear, confirm `classification_mode: no_llm_shadow`, `semantic_classification: false`, and `llm_provider: none`, and confirm rolling stats-sync PRs in bazaar-builds never mutate tracker catalogs or open tracker PRs.
- Before flipping to `live_cron`: confirm at least 2 healthy bazaardb patch windows and at least 60 calendar days of shadow output; review source-health/stats history; decide or explicitly waive the semantic classifier/provider strategy; confirm any required secret/API/cost readiness; confirm rolling tracker PR behavior and rollback path.
- Live readiness: require at least 2 healthy bazaardb patch windows and at least 60 calendar days of shadow output before enabling rolling tracker PRs.

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
