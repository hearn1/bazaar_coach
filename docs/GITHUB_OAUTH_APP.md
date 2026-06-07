# GitHub OAuth App — Configuration and Setup

This document is for contributors and release maintainers. End users do not need to read or action anything here.

## Current behavior (no OAuth required)

Bazaar Coach does **not** use a GitHub OAuth App today. All GitHub-facing behavior works without authentication:

| Feature | How it works |
| --- | --- |
| App update checks | Calls the public GitHub Releases API (`/repos/hearn1/bazaar_coach/releases/latest`) — no auth needed for public repos |
| "Report an issue" | Builds a prefilled `github.com/hearn1/bazaar_coach/issues/new` URL and opens it in the user's browser. The user submits the issue themselves — no API call is made by the app |

The target repo is read from `updates.github_repo` in `settings.json` (default `hearn1/bazaar_coach`), with a hard-coded fallback in `update_checker.py`.

OAuth App plumbing is **not** implemented. This document defines what a future implementation would require so the contract is clear before any code is written.

## When OAuth would be needed

A GitHub OAuth App becomes relevant if Bazaar Coach is extended to:

- Submit issues directly via the GitHub API without opening a browser
- Access private repos or release assets
- Raise the unauthenticated API rate limit (60 req/hour → 5,000 req/hour) — currently not a concern given infrequent update checks

## Future OAuth App setup (for maintainers)

If OAuth is implemented, follow these steps to create an app:

1. Go to **GitHub → Settings → Developer settings → OAuth Apps → New OAuth App**.
2. Fill in the fields:
   - **Application name:** `Bazaar Coach`
   - **Homepage URL:** `https://github.com/hearn1/bazaar_coach`
   - **Authorization callback URL:** see [Callback URL strategy](#callback-url-strategy) below
3. Click **Register application**.
4. Copy the **Client ID** (public, safe to ship in source).
5. Generate a **Client Secret** — treat this as a password; see [Secret handling](#secret-handling).

### Callback URL strategy

Bazaar Coach is a Windows desktop app without a persistent server reachable from GitHub. The right approaches, in preference order:

1. **GitHub Device Flow** — no callback URL required. The user visits a GitHub URL and enters a code. This is the preferred approach for desktop apps because it works without any local server and is simpler to package.
2. **Loopback redirect** — callback URL `http://127.0.0.1:5555/oauth/github/callback`. The app's local Flask server catches the redirect. This requires the OAuth App to be registered with that exact URL and will not work if the port is in use.

Do not use a hosted redirect service or hard-code a production URL into the packaged build.

## Required configuration values

| Variable | Required | Notes |
| --- | --- | --- |
| `BAZAAR_COACH_GITHUB_OAUTH_CLIENT_ID` | Yes, if OAuth enabled | Public; safe in source/env |
| `BAZAAR_COACH_GITHUB_OAUTH_CLIENT_SECRET` | Only for confidential (server-side) flow | Do **not** bundle in installer or portable zip |
| `BAZAAR_COACH_GITHUB_OAUTH_CALLBACK_URL` | Only if using loopback redirect | Must match the registered OAuth App exactly |
| `BAZAAR_COACH_GITHUB_OAUTH_ENABLED` | Optional flag | Defaults to `false` |
| `BAZAAR_COACH_GITHUB_REPO` | Optional override | Defaults to `updates.github_repo` / `hearn1/bazaar_coach` |

See `docs/examples/github_oauth.env.example` for a local development template.

## Secret handling

**Never commit real OAuth secrets.** Specifically:

- Do not add `CLIENT_SECRET` to `settings.json`, `coach.py`, or any file tracked by git.
- Do not bundle a `CLIENT_SECRET` into a PyInstaller build or Inno Setup installer. Users would be able to extract it.
- If OAuth is implemented, prefer the **Device Flow** (no client secret at all) or a server-side token exchange where the secret lives only on a server you control — not inside the packaged app.
- If a secret is accidentally committed, rotate it immediately via the GitHub OAuth App settings page. Rotation is instant.
- Token storage (access tokens obtained after auth) requires a separate security design: at minimum, store tokens in the Windows Credential Manager or encrypted local storage, not plain-text `settings.json`.

## Development setup example

1. Create a GitHub OAuth App in your personal account (see steps above).
2. Copy `docs/examples/github_oauth.env.example` to `.env` in the repo root (`.env` is gitignored).
3. Fill in your `CLIENT_ID` and optionally `CLIENT_SECRET`.
4. Load the env file before running coach (`python-dotenv` or manual `$env:VAR = "value"` in PowerShell).

```powershell
# PowerShell — set manually before running coach
$env:BAZAAR_COACH_GITHUB_OAUTH_ENABLED = "false"
$env:BAZAAR_COACH_GITHUB_OAUTH_CLIENT_ID = "your_client_id_here"
venv312\Scripts\python.exe coach.py
```

## Packaging and release notes

- Do **not** bake `CLIENT_SECRET` into any packaged build (installer or portable zip).
- `CLIENT_ID` is public and may be included in source or environment, but confirm the GitHub OAuth App documentation for your chosen flow before shipping it.
- Update checks do not require OAuth; they use the public Releases API. Do not add OAuth as a dependency for update checks.
- The existing unauthenticated "Report an issue" fallback (browser URL) must remain functional even if OAuth is disabled or misconfigured. Missing or invalid OAuth config must not break startup, update checks, or issue reporting.

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `invalid_client` from GitHub | Wrong or missing `CLIENT_ID` | Verify the `CLIENT_ID` matches the registered OAuth App |
| Callback URL mismatch | Registered URL does not match the redirect used at runtime | Update the OAuth App's "Authorization callback URL" on GitHub |
| `401 Unauthorized` from GitHub API | Token missing, expired, or wrong scope | Re-authorize; ensure `public_repo` scope is requested |
| `403 Forbidden` | User's token lacks the required scope | Re-authorize with the correct scopes |
| Rate limit errors (60 req/hour) | Unauthenticated requests hitting public API | For update checks, evaluate whether rate limits are actually a problem before adding OAuth |
| `404 Not Found` on issues endpoint | Wrong repo in `updates.github_repo` or missing `public_repo` scope | Confirm the repo name and token scopes |

## Open questions (to answer before implementing OAuth)

- Device Flow vs. loopback redirect — which fits the UX better for an always-on-top overlay app?
- Should `updates.github_repo` remain the single repo config, or should a separate `github.repo` key be introduced for issue reporting vs. update checks?
- Where do access tokens live once obtained — Windows Credential Manager, encrypted file, or session-only (no persistence)?
- Is OAuth needed for the initial issue-reporting feature, or only if direct API submission replaces the browser-URL approach?
