"""Generate version_info.txt for PyInstaller Windows version resource.

Reads APP_VERSION from version.py and writes a VSVersionInfo block to
packaging/pyinstaller/version_info.txt.

Usage (from repo root or from CI):
    python packaging/pyinstaller/gen_version_info.py

Called by build_portable.ps1 before PyInstaller so local and CI builds
both embed Windows file-version metadata.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Resolve paths relative to this script regardless of cwd
_SCRIPT_DIR = Path(__file__).parent
_REPO_ROOT = _SCRIPT_DIR.parents[1]
_VERSION_PY = _REPO_ROOT / "version.py"
_OUTPUT = _SCRIPT_DIR / "version_info.txt"


def _read_app_version() -> str:
    text = _VERSION_PY.read_text(encoding="utf-8")
    m = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', text)
    if not m:
        raise RuntimeError(f"Could not parse APP_VERSION from {_VERSION_PY}")
    return m.group(1)


def _version_tuple(version: str) -> tuple[int, int, int, int]:
    """Convert a semver(-prerelease) string to a 4-int Windows file-version tuple.

    '0.2.0-alpha.8' -> (0, 2, 0, 8)
    '1.0.0'         -> (1, 0, 0, 0)
    """
    # Strip any pre-release suffix to get the numeric core
    numeric_part = version.split("-")[0]
    parts = numeric_part.split(".")
    nums = []
    for p in parts[:3]:
        try:
            nums.append(int(p))
        except ValueError:
            nums.append(0)
    while len(nums) < 3:
        nums.append(0)

    # Use the trailing numeric segment of the pre-release label as the 4th int
    fourth = 0
    if "-" in version:
        pre = version.split("-", 1)[1]
        # e.g. "alpha.8" -> 8, "beta.1" -> 1, "rc.2" -> 2
        m = re.search(r"(\d+)\s*$", pre)
        if m:
            fourth = int(m.group(1))

    return (nums[0], nums[1], nums[2], fourth)


def generate(version: str) -> None:
    tup = _version_tuple(version)
    tup_str = ", ".join(str(n) for n in tup)

    content = f"""\
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({tup_str}),
    prodvers=({tup_str}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
        StringTable(
          u'040904B0',
          [StringStruct(u'CompanyName', u'Bazaar Coach'),
           StringStruct(u'FileDescription', u'Bazaar Coach'),
           StringStruct(u'FileVersion', u'{version}'),
           StringStruct(u'InternalName', u'BazaarCoach'),
           StringStruct(u'LegalCopyright', u''),
           StringStruct(u'OriginalFilename', u'BazaarCoach.exe'),
           StringStruct(u'ProductName', u'Bazaar Coach'),
           StringStruct(u'ProductVersion', u'{version}')])
      ]
    ),
    VarFileInfo([VarStruct(u'Translation', [1033, 1200])])
  ]
)
"""
    _OUTPUT.write_text(content, encoding="utf-8")
    print(f"[gen_version_info] Wrote {_OUTPUT} for version {version} {tup}")


def main() -> int:
    version = _read_app_version()
    generate(version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
