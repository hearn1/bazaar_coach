# Rename: bazaar_tracker -> bazaar_coach

Plan for renaming the project from "Bazaar Tracker" to "Bazaar Coach". Self-contained so it can be picked up in a fresh Claude session.

**Context that shapes this plan:** the project has never been publicly released. Only local use by the maintainer. That removes every backwards-compatibility concern (no installs to upgrade, no scripts pinned to old env vars, no clones in the wild relying on the old GitHub URL).

GitHub does support repo rename — old URLs auto-redirect indefinitely, all issues/PRs/releases/tags survive, and existing clones keep working until people update remotes. The repo flip itself is the cheap part. The work is in the code, filesystem paths, installer identity, and cross-repo references that name the project.

---

## What was overkill and explicitly dropped

These were considered and rejected because there is no released audience to protect:

| Concern | Why dropped |
|---|---|
| Keep `APP_NAME = "BazaarTracker"` in [app_paths.py:22](../app_paths.py) for data-dir compat | No one but the maintainer has data under that path |
| Preserve Inno Setup `AppId` GUID `{B2CE81B8-0D78-4424-9D88-2B290B215991}` | No installs in the wild to upgrade |
| Add `BAZAAR_COACH_*` env vars as aliases alongside `BAZAAR_TRACKER_*` | No scripts pinned to old names |
| `tracker.py` -> `coach.py` shim that prints deprecation + execs new entrypoint | No users typing `python tracker.py` from a script |
| Rely on GitHub URL redirects after rename | Redirects exist, but no one is hitting old URLs — hard-code new URLs from day one |
| Phase 1 UI-only PR as a "does the new name look right" gate | A 30-second string change + reload achieves the same review |
| 4 separate PRs over a focused day | Splitting buys nothing without a production audience to bisect against |

---

## What still actually matters

1. **CLAUDE.md.** Future Claude sessions read this first. Sloppy rebrand here = months of confused outputs. The one file worth proofreading slowly.
2. **bazaar-builds cross-repo references.** The workflow checks out `hearn1/bazaar_tracker` — after the rename it relies on GitHub's redirect, which works for `actions/checkout` but is silently fragile. Update [.github/workflows/automated-builds-refresh.yml](https://github.com/hearn1/bazaar-builds/blob/main/.github/workflows/automated-builds-refresh.yml) and the bazaar-builds docs in the same window.
3. **Inno Setup output filename + Start menu name.** Not for compat — purely so the installer built for QA doesn't say "Bazaar Tracker" while testing.
4. **Maintainer's local `bazaar_runs.db` and `settings.json`.** Worth preserving the local run history. One-line move before first launch with the new code.
5. **Local clone remote URL.** `git remote set-url origin https://github.com/hearn1/bazaar_coach.git` after the rename. Clones keep working but emit a warning until re-set.
6. **Repo folder on disk.** `C:\Users\Matt\Desktop\bazaar_tracker\` still says the old name. Optional cosmetic rename — `git` doesn't care, but worktrees, shell history, IDE workspaces, and any path-based scripts (including Claude's `.claude/projects/` memory dir) do. Probably easier to leave the folder name alone or rename it after everything else settles.

---

## Sequence

### Step 0 — Prereq

Confirm the in-flight bazaar-builds stats-PR work is fully merged and the rolling per-hero stats PR pattern has been verified end-to-end (manual `hero=Mak` dispatch produced an `automated/stats-sync-mak` PR successfully). Do not pile rename work on an unverified workflow fix.

### Step 1 — Single rename PR on the tracker repo (~3-4 hours)

One commit per logical group so review is easy:

1. **Entrypoint rename.** `git mv tracker.py coach.py`. Update all imports and any `python tracker.py` references in docs/scripts.
2. **Mechanical rewrite** across the ~30 files flagged by `git grep "bazaar_tracker\|Bazaar Tracker\|BazaarTracker\|BAZAAR_TRACKER_"`:
   - `bazaar_tracker` -> `bazaar_coach`
   - `Bazaar Tracker` -> `Bazaar Coach`
   - `BazaarTracker` -> `BazaarCoach`
   - `BAZAAR_TRACKER_` -> `BAZAAR_COACH_`
   - `bazaar-tracker` -> `bazaar-coach` (if any kebab-case variants exist)
3. **Data dir constant.** Change `APP_NAME = "BazaarTracker"` -> `APP_NAME = "BazaarCoach"` in [app_paths.py](../app_paths.py). Maintainer copies their local data dir manually:
   ```powershell
   Move-Item "$env:LOCALAPPDATA\BazaarTracker" "$env:LOCALAPPDATA\BazaarCoach"
   Move-Item "$env:APPDATA\BazaarTracker" "$env:APPDATA\BazaarCoach"
   ```
4. **Packaging rename.**
   - `git mv packaging/pyinstaller/BazaarTracker.spec packaging/pyinstaller/BazaarCoach.spec` and update internal `name=` references.
   - `git mv packaging/installer/BazaarTracker.iss packaging/installer/BazaarCoach.iss`. Mint a fresh `AppId` GUID, update `AppName`, `AppPublisher`, `OutputBaseFilename`, `UninstallDisplayIcon`, all `BazaarTracker.exe` references.
   - Update `packaging/installer/build_installer.ps1` and `packaging/pyinstaller/build_portable.ps1` paths and binary names.
   - Update `tests/test_packaging.py` assertions on file names.
5. **Self-update / build-refresh URLs.** Point `update_checker.py` and `refresh_builds.py` directly at `hearn1/bazaar_coach` from day one. No fallback to old URL.
6. **CLAUDE.md proofread.** Read the full file end-to-end, not just regex-sub it. Watch for awkward sentences where "tracker" was acting as a common noun ("the tracker watches the log") that should now read naturally with "coach" or be reworded.

**Validation:**
- `git grep -i "bazaar.tracker\|BazaarTracker"` returns nothing except intentional history references (there shouldn't be any — nothing's released).
- `python -m pytest -q` clean (full suite).
- `python -B -m py_compile coach.py first_run.py update_checker.py doctor.py refresh_builds.py refresh_images.py settings.py card_cache.py content_manifest.py web/server.py` clean.
- Build a portable: `packaging/pyinstaller/build_portable.ps1` produces `BazaarCoach.exe`.
- Build an installer: produces `BazaarCoachSetup.exe` (or whatever `OutputBaseFilename` is set to).
- Install it locally, run a real game session end-to-end, confirm overlay + dashboard + Mono capture all still work.

Schedule an extra hour for installer debugging — Inno Setup paths always take longer than expected on Windows, especially the first build after a long gap.

Suggested commit message: `refactor: rename codebase from bazaar_tracker to bazaar_coach`.

### Step 2 — GitHub repo rename (~2 minutes)

GitHub UI on `hearn1/bazaar_tracker` -> Settings -> Repository name -> `bazaar_coach`. URL becomes `https://github.com/hearn1/bazaar_coach`. Old URL 301-redirects.

Then update the local clone:
```powershell
git remote set-url origin https://github.com/hearn1/bazaar_coach.git
```

Verify with `git remote -v`.

### Step 3 — bazaar-builds cleanup PR (~15 min)

In the bazaar-builds repo:
- Update `.github/workflows/automated-builds-refresh.yml` checkout step: `repository: hearn1/bazaar_tracker` -> `repository: hearn1/bazaar_coach`.
- Update bazaar-builds `CLAUDE.md`, `README.md`, `ROADMAP.md`, and any other doc strings mentioning the tracker by old name.
- Verify `TRACKER_PR_TOKEN` secret still has access to the renamed repo. (GitHub PATs follow the repo's new name automatically since they're scoped by repo ID, not name, but worth confirming with a manual workflow dispatch after the rename.) Optional: rename the secret to `COACH_PR_TOKEN` for consistency, but not required.

Don't rely on redirects long-term — be explicit.

### Step 4 — Optional: rename the local folder

Rename `C:\Users\Matt\Desktop\bazaar_tracker\` -> `C:\Users\Matt\Desktop\bazaar_coach\`. Cosmetic only, but affects:
- Worktrees under `.claude/worktrees/` (paths get baked into git config).
- Claude's per-project memory dir at `C:\Users\Matt\.claude\projects\C--Users-Matt-Desktop-bazaar-tracker\` -> would need to be renamed or symlinked. Memory in `MEMORY.md` would persist but the project key changes.
- IDE workspace files / VS Code recent folders.
- Any PowerShell aliases or shortcuts.
- `gitStatus` snapshots and other harness state pinned to the old path.

**Recommendation:** skip unless the folder name visibly bothers you. The cost (rebuilding harness state, fixing every tool that cached the old path) > the win (a folder name no one but you ever sees). If you do it, do it last and in its own session.

---

## Sequencing risk

Between Step 2 (repo rename) and Step 3 (bazaar-builds update), the bazaar-builds cron will check out via redirect. That's fine for `actions/checkout`, but if the cron fires *during* Step 2 you might see one transient failure. Either:
- Schedule the rename outside the 06:00 UTC daily window, or
- Temporarily disable the bazaar-builds workflow schedule (comment out the `schedule:` block) until Step 3 lands.

---

## What NOT to do

- Don't change the on-disk data dir name without doing the manual `Move-Item` first, or you'll silently abandon your run history.
- Don't bundle the rename PR with any unrelated feature work — keep the diff mechanical so review is "did the regex catch everything" not "what is this also doing".
- Don't rename module files in the same commit as their import updates — do `git mv` + import update together so `git log --follow` works on the renamed file.
- Don't keep the old `BAZAAR_TRACKER_*` env vars as aliases. Nothing depends on them.
- Don't keep the Inno Setup AppId GUID for "compat" — there's nothing to be compatible with. Mint a new one.

---

## Effort estimate

- Step 1 (rename PR): 3-4 hours including installer rebuild + smoke test.
- Step 2 (GitHub rename): 2 minutes.
- Step 3 (bazaar-builds cleanup): 15 minutes.
- Step 4 (local folder rename): 30-60 minutes if you choose to do it; otherwise 0.

Total: **~4 hours, 2 PRs** (one in tracker/coach, one in bazaar-builds).
