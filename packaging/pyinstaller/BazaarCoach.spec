# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir spec for Bazaar Coach.

Build from the repository root:
    pyinstaller packaging/pyinstaller/BazaarCoach.spec --noconfirm --clean
"""

from pathlib import Path

ROOT = Path(SPECPATH).parents[1]

datas = [
    (str(ROOT / "dooley_builds.json"), "."),
    (str(ROOT / "karnok_builds.json"), "."),
    (str(ROOT / "mak_builds.json"), "."),
    (str(ROOT / "pygmalien_builds.json"), "."),
    (str(ROOT / "vanessa_builds.json"), "."),
    (str(ROOT / "builds_schema.json"), "."),
    (str(ROOT / "capture_mono.py"), "."),
    (str(ROOT / "README.md"), "."),
    (str(ROOT / "ROADMAP.md"), "."),
]
for path in (ROOT / "web" / "static").rglob("*"):
    if path.is_file():
        relative_parent = path.relative_to(ROOT).parent
        datas.append((str(path), str(relative_parent)))

hiddenimports = [
    "waitress",
    "flask",
    "requests",
    "watchdog",
    "webview",
    "frida",
    "UnityPy",
    "PIL",
    "PIL.Image",
]

block_cipher = None

a = Analysis(
    [str(ROOT / "coach.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tests",
        "pytest",
        "pip",
        "setuptools",
        "wheel",
        "tkinter",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BazaarCoach",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# Console-mode binary for support commands (doctor, refresh-builds,
# export-diagnostics). Same codebase, same Analysis — only console=True
# so output is visible when run from a terminal or Start Menu shortcut.
exe_cli = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BazaarCoachCLI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    exe_cli,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="BazaarCoach",
)
