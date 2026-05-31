"""Generate assets/icon.ico for Bazaar Coach from the brand art source.

Source of truth is assets/bazaar-coach-icon.png (1024x1024 RGBA): the amber
rounded-square badge with the dark navy "Bc" monogram. This script downsamples
it into a multi-resolution .ico so every Windows surface (shortcut, installer,
taskbar, Explorer tile) reads cleanly from 16px up to 256px.

Run from the repo root:
    venv312\\Scripts\\python.exe assets\\make_icon.py

To swap the brand art, replace bazaar-coach-icon.png with a new square export
and re-run; the multi-size export below stays the same.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "bazaar-coach-icon.png"
OUT = HERE / "icon.ico"
SIZES = [16, 24, 32, 48, 64, 128, 256]


def main() -> None:
    img = Image.open(SOURCE).convert("RGBA")
    if img.width != img.height:
        raise SystemExit(f"source must be square, got {img.size}")
    # Pillow downsamples the master into every requested size and packs them
    # into one multi-resolution .ico.
    img.save(OUT, format="ICO", sizes=[(s, s) for s in SIZES])
    print(f"wrote {OUT} from {SOURCE.name} ({', '.join(f'{s}x{s}' for s in SIZES)})")


if __name__ == "__main__":
    main()
