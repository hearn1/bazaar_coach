# Bazaar Coach — Release Scripts

Two PowerShell scripts that wrap the existing portable + installer build flow
into single-command operations. They orchestrate the scripts under
`packaging/pyinstaller/` and `packaging/installer/` — they do not replace them.

## Scripts

### `build_test.ps1` — pre-release test artifact

Build a full artifact set (portable zip + installer) using the current
`APP_VERSION` in `version.py`. No git or GitHub side effects.

```powershell
# Default: pytest + portable build + smoke test + zip + installer.
.\packaging\release\build_test.ps1

# Faster iteration (skip pytest and smoke).
.\packaging\release\build_test.ps1 -SkipTests -SkipSmoke

# Build and silently install locally (per-user, no UAC prompt).
.\packaging\release\build_test.ps1 -Install
```

Output:

- `dist/BazaarCoach/` — portable onedir build
- `dist/BazaarCoach-Portable-<version>.zip`
- `dist/installer/BazaarCoachSetup-<version>.exe`

When `-Install` is omitted, the script prints the silent-install command for
you to copy-paste — handy when you want to eyeball the installer first.

### `cut_release.ps1` — full release cut

Bump version, build artifacts, tag, push, and create a draft GitHub Release
with both assets attached.

```powershell
# Auto-increment the trailing -alpha.N / -beta.N / -rc.N suffix.
.\packaging\release\cut_release.ps1

# Explicit version.
.\packaging\release\cut_release.ps1 -Version 0.2.0-alpha.4

# Dry run — validates everything, builds artifacts, skips git mutations.
.\packaging\release\cut_release.ps1 -DryRun

# Publish immediately (default is --draft so notes can be edited on GitHub).
.\packaging\release\cut_release.ps1 -Version 0.2.0 -Publish

# Supply hand-written release notes.
.\packaging\release\cut_release.ps1 -NotesFile .\notes.md
```

Flow:

1. Validate working tree clean and current branch is `main`.
2. Resolve new version (auto-increment alpha/beta/rc suffix, or `-Version`).
3. Verify `v<NewVersion>` tag doesn't exist locally or on `origin`.
4. Rewrite `APP_VERSION` in `version.py` and commit the bump.
5. Run `pytest -q`.
6. Build portable, run `smoke_test_portable.py`, create portable zip.
7. Build installer (explicit `-AppVersion` to defeat any iss-default leak).
8. Generate release notes from `git log <last-tag>..HEAD --oneline`
   (or copy `-NotesFile` content) to `dist/release/release-notes-<version>.md`.
9. Create annotated tag and push `main` + tag to `origin`.
10. `gh release create` as `--prerelease --draft` with the zip + installer
    attached. With `-Publish`, the `--draft` flag is dropped.

`-DryRun` performs validation and builds (so artifacts are real and
inspectable) but skips: version.py write, commit, tag, push, and
`gh release create`. The dry-run builds reflect the **current** checked-in
version, not the proposed new version — use `build_test.ps1` for "what would
the new version's artifacts look like" before invoking the real release.

## Requirements

- Python 3.10+ in `venv312\` (auto-detected) or on `PATH`.
- Inno Setup 6 (`ISCC.exe`) installed (any standard install location).
- `gh` CLI logged in with `repo` scope (`gh auth status`).
- Clean working tree on `main` before running `cut_release.ps1`.

## Out of scope (file as separate issues)

These known packaging/install bugs are documented in `ROADMAP.md` and are
**not** addressed by these scripts — the orchestration runs today's build
flow as-is:

- Discord Alpha P1-1: terminal window flashing during wait-for-game
- Discord Alpha P1-2: uninstaller "Yes, remove user data" not working
- Discord Alpha P1-3: runtime DB leaking into `_internal`
- Discord Alpha P1-4: installed GUI exe support commands silent in terminal

CI release automation (e.g., `.github/workflows/release.yml`) and code
signing are not addressed here. The release flow is local-only, matching
the project's Windows-targeted runtime (Frida).
