# Bazaar Coach — Release scripts

Two PowerShell scripts that wrap the existing portable + installer build flow into single-command operations. They orchestrate the scripts under `packaging/pyinstaller/` and `packaging/installer/` — they don't replace them.

## `build_test.ps1` — pre-release test artifact

Build a full artifact set (portable zip + installer) using the current `APP_VERSION` in `version.py`. No git or GitHub side effects.

```powershell
# Default: pytest + portable build + smoke test + zip + installer.
.\packaging\release\build_test.ps1

# Faster iteration (skip pytest and smoke test).
.\packaging\release\build_test.ps1 -SkipTests -SkipSmoke

# Build and silently install locally (per-user, no UAC prompt).
.\packaging\release\build_test.ps1 -Install
```

Output:

- `dist/BazaarCoach/` — portable onedir build
- `dist/BazaarCoach-Portable-<version>.zip`
- `dist/installer/BazaarCoachSetup-<version>.exe`

When `-Install` is omitted, the script prints the silent-install command for you to copy-paste — handy when you want to eyeball the installer first.

## `cut_release.ps1` — full release cut

Bump version, build artifacts, tag, push, and create a draft GitHub Release with both assets attached.

```powershell
# Auto-increment the trailing -alpha.N / -beta.N / -rc.N suffix.
.\packaging\release\cut_release.ps1

# Explicit version.
.\packaging\release\cut_release.ps1 -Version 0.2.0-alpha.5

# Dry run — validates and builds, skips git mutations.
.\packaging\release\cut_release.ps1 -DryRun

# Publish immediately (default is --draft so notes can be edited on GitHub).
.\packaging\release\cut_release.ps1 -Version 0.2.0 -Publish

# Supply hand-written release notes.
.\packaging\release\cut_release.ps1 -NotesFile .\notes.md
```

Flow:

1. Validate the working tree is clean and the current branch is `main`.
2. Resolve the new version (auto-increment alpha/beta/rc suffix, or `-Version`).
3. Verify `v<NewVersion>` doesn't exist locally or on `origin`.
4. Rewrite `APP_VERSION` in `version.py` and commit the bump.
5. `pytest -q`.
6. Build portable, run `smoke_test_portable.py`, create portable zip.
7. Build installer (explicit `-AppVersion` to defeat any iss-default leak).
8. Generate release notes from `git log <last-tag>..HEAD --oneline` (or copy `-NotesFile`) to `dist/release/release-notes-<version>.md`.
9. Create annotated tag; push `main` + tag to `origin`.
10. `gh release create` as `--prerelease --draft` with both assets attached. With `-Publish`, the `--draft` flag is dropped.

`-DryRun` performs validation and builds (so artifacts are real and inspectable) but skips: `version.py` write, commit, tag, push, and `gh release create`. Dry-run builds reflect the **current** checked-in version, not the proposed new version — use `build_test.ps1` for "what would the new version's artifacts look like" before invoking the real release.

## Requirements

- Python 3.10+ in `venv312\` (auto-detected) or on `PATH`.
- Inno Setup 6 (`ISCC.exe`) at a standard install location.
- `gh` CLI logged in with `repo` scope (`gh auth status`).
- Clean working tree on `main` before running `cut_release.ps1`.

## CI release build

`.github/workflows/release-build.yml` automates the build on `windows-latest`.

**Triggers:**
- `push` of a `v*` tag — full build + attaches both artifacts to the existing GitHub Release.
- `workflow_dispatch` — full build, artifacts only (no release upload).

**Flow:** checkout → Python 3.12 → `pip install -r requirements.txt -r packaging/pyinstaller/requirements-build.txt` → pytest → `build_portable.ps1` (calls `gen_version_info.py` to embed Windows version metadata, then PyInstaller) → `smoke_test_portable.py` → portable zip → `choco install innosetup` → `build_installer.ps1 -AppVersion <v>` → `upload-artifact` (portable + installer) → on tag, `gh release upload <tag> --clobber` onto the release created by `cut_release.ps1`.

**Note on `-PythonExe`:** the workflow does NOT pass `-PythonExe` to `build_portable.ps1`, so it falls back to the `python` on `PATH` supplied by `actions/setup-python`. The `venv312\` local path check will be absent on the runner, which is the correct fallback.

**Dependency constraints:** `frida`, `pywebview`, `watchdog`, `UnityPy`, and `Pillow` are unpinned in `requirements.txt` but constrained for release builds via `constraints-release.txt` at the repo root. CI installs with `-c constraints-release.txt` so artifacts are reproducible. To refresh constraints after a compatibility test: run `build_test.ps1` on a clean Windows environment, confirm `smoke_test_portable.py` passes, then run `pip freeze` in the build venv and update the versions in `constraints-release.txt`. Open a PR with the new versions noted. Do not update constraints speculatively.

**Code signing:** a commented SignPath slot-in is present in the workflow between the installer build and artifact upload steps (see issue #154).

## Code signing

See `packaging/installer/README.md` for the SmartScreen/AV expectations on unsigned builds.

Product / packaging bugs that the orchestrator does **not** fix (they're tracked in GitHub Issues) ride through these scripts unchanged.
