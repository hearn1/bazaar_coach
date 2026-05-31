# Bazaar Coach

Licensed under the MIT License — see [LICENSE](LICENSE). Third-party component licenses: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Code-signing policy: [CODE_SIGNING.md](CODE_SIGNING.md). Privacy and data collection: [PRIVACY.md](PRIVACY.md).

Bazaar Coach is a Windows coaching plugin for *The Bazaar*. It captures every run decision into a local SQLite database, scores them against hero build catalogs, and shows live coaching through an in-game overlay.

Hero catalogs ship for Karnok, Mak, Dooley, Vanessa, Pygmalien, Jules, and Stelle.

## Download and install

1. Open the latest release: <https://github.com/hearn1/bazaar_coach/releases>.
2. Under **Assets**, grab the installer — the file named `BazaarCoachSetup-<version>.exe` (e.g. `BazaarCoachSetup-0.2.0-alpha.4.exe`). The `BazaarCoach-Portable-<version>.zip` next to it is the no-installer build; only grab that if you specifically want a portable copy.
3. Run the installer and accept the prompts. No admin rights are required for a per-user install; Windows may show a SmartScreen "Windows protected your PC" warning on first launch — click **More info** → **Run anyway**. See [packaging/installer/README.md](packaging/installer/README.md) for why an unsigned alpha build trips SmartScreen and antivirus.
4. After install, look for a Start Menu folder named **Bazaar Coach** with shortcuts for the main app and the Doctor / support commands.

## Verifying your download

Each release attaches a `SHA256SUMS-<version>.txt` listing the hashes of the installer
and portable zip. Verify your download before running it:

```powershell
Get-FileHash -Algorithm SHA256 .\BazaarCoachSetup-<version>.exe
# compare against SHA256SUMS-<version>.txt on the release page
```

## How to use it

1. Launch **Bazaar Coach** from the Start Menu.
2. Launch *The Bazaar*.
3. The overlay (the small always-on-top window that floats over the game) and dashboard (a web page at `http://127.0.0.1:5555` you can open in your browser) start and wait quietly if the game is not running yet.
4. Play normally.
5. Session logs land in `%LOCALAPPDATA%\BazaarCoach\logs\`.

Two binaries ship in the install directory:

| Binary | Purpose |
| --- | --- |
| `BazaarCoach.exe` | Windowed gameplay app (no console output) |
| `BazaarCoachCLI.exe` | Console support commands |

## What it does for you

Bazaar Coach watches your run and tracks everything you do so it can score your decisions and surface coaching in the overlay:

- Shop offers, picks, and the cards you passed on
- Skill offers and picks
- Map / event node choices
- Skips (left a shop without buying)
- Sells and inventory moves
- Combat outcomes (PvE wins / losses, PvP wins / losses)
- Run metadata: hero, session, timestamps, outcome
- Live game context: day, hour, gold, health, prestige, PvP record (when the in-game Mono capture is active)

> **How "skipped" shops are tracked.** Bazaar Coach records a skip every time you leave a shop without buying. The overlay's **Review** tab only *highlights* a skip when that shop contained items relevant to your current build — and it lists exactly which build-relevant cards you passed on. Skips from shops with nothing relevant are still recorded, just not flagged. The marker appears once you leave the shop, not while you're browsing it.

## Troubleshooting and support log

If something goes wrong, the single most useful file to share is the latest session log at:

```
%LOCALAPPDATA%\BazaarCoach\logs\coach_YYYYMMDD_HHMMSS.log
```

Each launch writes a fresh `coach_<timestamp>.log`. Grab the most recent one and attach it when filing an issue at <https://github.com/hearn1/bazaar_coach/issues>.

You can also re-run diagnostics on demand from the install directory:

```powershell
& "$env:LOCALAPPDATA\Programs\Bazaar Coach\<version>\BazaarCoachCLI.exe" doctor
& "$env:LOCALAPPDATA\Programs\Bazaar Coach\<version>\BazaarCoachCLI.exe" export-diagnostics
```

## Uninstall

The uninstaller prompts once:

> Remove all Bazaar Coach user data from `%APPDATA%` and `%LOCALAPPDATA%`?

- **No** — removes installed app files, keeps user data.
- **Yes** — removes installed app files and deletes both `%LOCALAPPDATA%\BazaarCoach` and `%APPDATA%\BazaarCoach`.

## Updates

Update checks run in the background and surface in the dashboard / overlay when a new GitHub Release is available. They are non-blocking and never call placeholder URLs.

---

## For developers

Running from source, packaging, the database schema, and architecture notes live in **[DEVELOPMENT.md](DEVELOPMENT.md)**. `CLAUDE.md` carries the deeper architecture/data-flow notes.
