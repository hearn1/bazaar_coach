# Privacy and Data Collection

Bazaar Coach is a local-only coaching tool. This document describes exactly what data the app stores, what it transmits, and what controls you have.

## What is stored locally

All data Bazaar Coach collects stays on your machine. Nothing is uploaded automatically.

When running as an installed packaged build, mutable data is written to two directories under your Windows user profile:

| Location | Contents |
|----------|----------|
| `%LOCALAPPDATA%\BazaarCoach\` | SQLite database (`bazaar_runs.db`), session logs (`logs\`), static card cache, refreshed build catalogs |
| `%APPDATA%\BazaarCoach\` | `settings.json` |

The SQLite database records every run decision (offered cards, chosen card, rejected cards, scores), combat outcomes, live game context (day, hour, gold, health), and run metadata. Session logs mirror the app's stdout/stderr output.

Development runs (running from source without a packaged build) keep all mutable data in the repository root instead of the user-profile directories above.

The **Uninstall** flow prompts once whether to remove both `%LOCALAPPDATA%\BazaarCoach` and `%APPDATA%\BazaarCoach`. Choosing **No** keeps your run history; choosing **Yes** deletes it.

## Network traffic

The app may make the following outbound requests during normal startup and use:

### 1. Update checks (automatic at startup, opt-out)

At startup the app checks for new releases by calling:

```
https://api.github.com/repos/hearn1/bazaar_coach/releases/latest
```

This request identifies itself with a `User-Agent: BazaarCoach/<version>` header. No personal data, run data, or game data is included. The request is read-only — the app never writes to GitHub on your behalf.

**To opt out**, set `updates.enabled` to `false` in `settings.json` (located at `%APPDATA%\BazaarCoach\settings.json`):

```json
{
  "updates": {
    "enabled": false
  }
}
```

When `updates.enabled` is `false` the update check is skipped entirely and no request is made to `api.github.com`.

### 2. Build catalog refresh (automatic at startup, opt-out)

At startup the app fetches the latest hero build catalogs from the coach repo's published main branch:

```
https://raw.githubusercontent.com/hearn1/bazaar_coach/main/builds/<hero>_builds.json
```

No run data or personal data is sent — only standard HTTPS request metadata. If the refresh fails (network unavailable, rate-limited, or a malformed response), the app falls back silently to the catalogs bundled with the installer.

**To opt out**, launch the app with the `--no-refresh-builds` flag:

```
coach.py --no-refresh-builds
```

There is no `settings.json` key for this option at this time.

### 3. Static content refresh (manual, on-demand only)

Running `coach.py refresh-content` or `coach.py refresh-images` fetches card data and images from the game's CDN (`playthebazaar.com`). These commands are invoked manually or during first-time setup; they are **not** automatic background operations.

No run data or personal data is sent.

### 4. Fonts (automatic when the dashboard or overlay opens)

The dashboard and overlay load typography from Google Fonts at runtime:

```
https://fonts.googleapis.com
https://fonts.gstatic.com
```

These requests are made by the WebView2 browser engine each time the dashboard or overlay is displayed. No run data or personal data is included. The fonts are not bundled in the installer.

## Report-an-issue flow

The dashboard includes a **Report an issue** button that opens a prefilled GitHub issue creation page in your browser. The prefill includes:

- The app version and your OS version string.
- The file path of the latest session log (so you know which file to attach manually).
- A prompt to describe the problem and reproduction steps.

**No file is uploaded automatically.** The button opens a URL in your browser with a prefilled form body. You review the issue, attach the log file manually if you choose, and submit it yourself. Nothing is sent without your explicit action.

The implementation is in `web/report_issue.py`; the GitHub URL is constructed client-side and opened via the browser — the app itself makes no HTTP request.

## Third-party network connections

Bazaar Coach does not embed analytics, telemetry, or crash reporting. No data is sent to Anthropic, any ad network, or any third party other than the outbound connections listed above.

| Host | Purpose | When |
|------|---------|------|
| `api.github.com` | Update check | Automatic at startup (opt-out via `updates.enabled`) |
| `raw.githubusercontent.com` | Build catalog refresh | Automatic at startup (opt-out via `--no-refresh-builds`) |
| `playthebazaar.com` | Card/static content refresh | Manual only (`refresh-content` / `refresh-images` commands) |
| `fonts.googleapis.com` | Dashboard/overlay fonts | Automatic when dashboard or overlay opens |
| `fonts.gstatic.com` | Dashboard/overlay fonts | Automatic when dashboard or overlay opens |

Third-party component licenses are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
