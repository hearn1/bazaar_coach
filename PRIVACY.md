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

The app makes two kinds of outbound connections:

### 1. Update checks (background, opt-out)

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

### 2. Static content refresh (manual, on demand)

Running `coach.py refresh-content` or `coach.py refresh-images` fetches card data and images from the game's CDN (`playthebazaar.com`). These commands are run manually or on first setup; they are not background operations.

## Report-an-issue flow

The dashboard includes a **Report an issue** button that opens a prefilled GitHub issue creation page in your browser. The prefill includes:

- The app version and your OS version string.
- The file path of the latest session log (so you know which file to attach manually).
- A prompt to describe the problem and reproduction steps.

**No file is uploaded automatically.** The button opens a URL in your browser with a prefilled form body. You review the issue, attach the log file manually if you choose, and submit it yourself. Nothing is sent without your explicit action.

The implementation is in `web/report_issue.py`; the GitHub URL is constructed client-side and opened via the browser — the app itself makes no HTTP request.

## Local data retention

Bazaar Coach stores run history and low-level diagnostic capture data locally in the SQLite database.

The **run-history retention** setting (`coach.db_retention_days` in `settings.json`) is opt-in and disabled by default (`0`). When set to a value ≥ 90, completed run-history records older than the threshold are deleted at startup, including rows in `runs`, `decisions`, and `combat_results`. Active and in-progress runs are never touched.

Low-level captured API/Mono snapshot tables (`api_game_states`, `api_cards`, `api_player_attrs`, `api_messages`) are **not** deleted by this setting. They remain in the local database for diagnostics. They are not uploaded by Bazaar Coach. A future full diagnostic data purge feature may remove those tables separately.

No data is sent to any remote service as part of the retention process.

## Third-party network connections

Bazaar Coach does not embed analytics, telemetry, or crash reporting. No data is sent to Anthropic, any ad network, or any third party other than the `api.github.com` update check described above.

Third-party component licenses are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
