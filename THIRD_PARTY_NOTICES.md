# Third-Party Notices

Bazaar Coach is licensed under the MIT License (see [LICENSE](LICENSE)).
This file lists all third-party components used at runtime or bundled in
the distributed installer, along with their licenses.

No GPL or commercial-only component is bundled in a way that conflicts with
MIT redistribution. See the Frida note below for the one nuanced case.

---

## Runtime dependencies (`requirements.txt`)

| Component | Version | License | How it is used |
|-----------|---------|---------|----------------|
| Flask | 3.1.3 | BSD 3-Clause | HTTP framework for the dashboard and overlay API |
| Werkzeug | (Flask dep) | BSD 3-Clause | WSGI utilities, used transitively via Flask |
| Jinja2 | (Flask dep) | BSD 3-Clause | Template engine, used transitively via Flask |
| Markupsafe | (Flask dep) | BSD 3-Clause | HTML escaping, used transitively via Jinja2 |
| itsdangerous | (Flask dep) | BSD 3-Clause | Signed cookies/tokens, used transitively via Flask |
| click | (Flask dep) | BSD 3-Clause | CLI utilities, used transitively via Flask |
| waitress | 3.0.2 | ZPL 2.1 (Zope Public License) | Production WSGI server for the dashboard |
| pywebview | unpinned | BSD 3-Clause | Frameless always-on-top overlay window (WebView2) |
| frida | unpinned | wxWindows Library Licence 3.1 (frida-core) / MIT (Python bindings) | Mono managed-memory hooks for decision capture — see note below |
| watchdog | unpinned | Apache 2.0 | File system event watcher (used by update checker) |
| requests | 2.33.1 | Apache 2.0 | HTTP client for card cache and static API fetches |
| UnityPy | unpinned | MIT | Unity asset-bundle parsing for card image extraction |
| Pillow | unpinned | HPND (Historical Permission Notice and Disclaimer — OSI-approved permissive) | Image processing for card image cache |
| jsonschema | 4.23.0 | MIT | Build catalog JSON schema validation |
| pytest | 9.0.3 | MIT | Test framework (test/dev dependency only; not bundled in installer) |

---

## Build / packaging dependencies (`packaging/pyinstaller/requirements-build.txt`)

| Component | Version | License | How it is used |
|-----------|---------|---------|----------------|
| PyInstaller | 6.11.1 | GPL 2.0 with bootloader exception | Packages the Python application into a Windows executable — see note below |

---

## Bundled data

| Component | License | Notes |
|-----------|---------|-------|
| `builds/*.json` hero catalogs | MIT (this repo) | Maintainer's own work — build catalogs authored and curated by Matthew Hearn. Not derived from third-party data under a conflicting license. |
| `builds/builds_schema.json` | MIT (this repo) | Maintainer's own work. |

---

## Notes on nuanced cases

### Frida

`frida-core` (the native instrumentation engine) is licensed under the
[wxWindows Library Licence 3.1](https://frida.re/docs/license/), which is an
LGPL-style permissive license approved by the OSI. The `frida` Python package
(bindings) is MIT-licensed.

Bazaar Coach **does not statically link or redistribute a modified frida-core**.
The `frida` Python package is loaded as a separate dynamic library at runtime;
the application communicates with it via the published Python API. This usage
is consistent with the wxWindows licence's exception for unmodified library use
and does not impose copyleft conditions on this project's MIT code.

### PyInstaller

PyInstaller itself is GPL 2.0, but it includes a [bootloader
exception](https://pyinstaller.org/en/stable/license.html) that explicitly
grants permission to distribute applications built with it under any license,
including MIT/proprietary. PyInstaller is a **build tool only** — it is not
distributed in the installer, only its output (the frozen application) is. The
bootloader exception applies; no GPL obligation is imposed on this project.

### waitress (Zope Public License 2.1)

The ZPL 2.1 is an OSI-approved permissive license. It requires attribution in
documentation but does not impose copyleft conditions. The above listing
satisfies that requirement.

### Pillow (HPND)

The Historical Permission Notice and Disclaimer is an OSI-approved permissive
license, substantially equivalent to MIT. No compatibility concern.

---

## Fonts (served from CDN, not bundled)

The dashboard and overlay load fonts from Google Fonts over the network at
runtime; they are not embedded in the installer.

| Font | License |
|------|---------|
| Syne | OFL 1.1 (SIL Open Font License) |
| DM Sans | OFL 1.1 |
| IBM Plex Mono | OFL 1.1 |

OFL 1.1 is permissive for use and embedding; no copyleft obligation for
web-served fonts.
