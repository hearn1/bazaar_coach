"""Refresh writable build catalogs from the coach repo's published main branch."""

from __future__ import annotations

import argparse
import json
import os
import random
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import requests

import app_paths
import scorer

RAW_BASE_URL = "https://raw.githubusercontent.com/hearn1/bazaar_coach/main"
REQUEST_TIMEOUT_SECONDS = 12

_RETRYABLE_STATUSES = {429, 502, 503, 504}
_RETRY_JITTER_MIN = 0.8
_RETRY_JITTER_MAX = 2.0
_RETRY_AFTER_MAX = 5.0


@dataclass(frozen=True)
class HeroRefreshResult:
    hero: str
    filename: str
    status: str
    message: str


def builds_dir() -> Path:
    """Writable directory for refreshed catalogs."""
    return app_paths.data_dir() / "builds"


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            temp_name = handle.name
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        temp_name = None
    finally:
        if temp_name:
            try:
                Path(temp_name).unlink(missing_ok=True)
            except OSError:
                pass


def _retry_delay(response: object | None) -> float:
    """Return how long to sleep before the single retry attempt."""
    if response is not None:
        try:
            after = float(getattr(response, "headers", {}).get("Retry-After", ""))
            return min(after, _RETRY_AFTER_MAX)
        except (ValueError, TypeError):
            pass
    return random.uniform(_RETRY_JITTER_MIN, _RETRY_JITTER_MAX)


def _is_retryable_response(response: object) -> bool:
    return getattr(response, "status_code", None) in _RETRYABLE_STATUSES


def _refresh_one(hero: str, filename: str, *, out_dir: Path) -> HeroRefreshResult:
    url = f"{RAW_BASE_URL}/builds/{filename}"
    response = None
    retried = False

    for attempt in range(2):
        try:
            response = requests.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
        except (requests.Timeout, requests.ConnectionError) as exc:
            if retried:
                return HeroRefreshResult(hero, filename, "skipped", f"fetch failed: {exc}")
            delay = _retry_delay(None)
            print(f"[Builds] INFO {filename}: retryable error ({exc!r}), retrying in {delay:.1f}s")
            time.sleep(delay)
            retried = True
            continue
        except requests.RequestException as exc:
            return HeroRefreshResult(hero, filename, "skipped", f"fetch failed: {exc}")

        if response.status_code == 200:
            break

        if not retried and _is_retryable_response(response):
            delay = _retry_delay(response)
            print(
                f"[Builds] INFO {filename}: HTTP {response.status_code}, retrying in {delay:.1f}s"
            )
            time.sleep(delay)
            retried = True
            response = None
            continue

        return HeroRefreshResult(
            hero,
            filename,
            "skipped",
            f"HTTP {response.status_code} from {url}",
        )

    if response is None or response.status_code != 200:
        return HeroRefreshResult(
            hero,
            filename,
            "skipped",
            f"HTTP {getattr(response, 'status_code', 'unknown')} from {url}",
        )

    content = response.content
    try:
        data = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return HeroRefreshResult(hero, filename, "skipped", f"invalid JSON: {exc}")

    if not isinstance(data, dict):
        return HeroRefreshResult(hero, filename, "skipped", "catalog root is not an object")

    ok, err = scorer.validate_builds_catalog(data)
    if not ok:
        return HeroRefreshResult(hero, filename, "skipped", f"validation failed: {err}")

    destination = out_dir / filename
    try:
        if destination.exists() and destination.read_bytes() == content:
            return HeroRefreshResult(hero, filename, "unchanged", f"unchanged: {destination}")
        _atomic_write_bytes(destination, content)
    except OSError as exc:
        return HeroRefreshResult(hero, filename, "skipped", f"write failed: {exc}")

    return HeroRefreshResult(hero, filename, "updated", f"updated: {destination}")


def refresh_builds(*, out_dir: Path | None = None) -> list[HeroRefreshResult]:
    destination = out_dir or builds_dir()
    results = []
    for hero, filename in sorted(scorer.CATALOG_FILENAMES.items()):
        result = _refresh_one(hero, filename, out_dir=destination)
        level = "WARNING" if result.status == "skipped" else "INFO"
        print(f"[Builds] {level} {filename}: {result.message}")
        results.append(result)
    if any(result.status == "updated" for result in results):
        scorer._load_builds_cached.cache_clear()
    return results


def _summary_counts(results: list[HeroRefreshResult]) -> tuple[int, int, int]:
    updated = sum(1 for result in results if result.status == "updated")
    unchanged = sum(1 for result in results if result.status == "unchanged")
    skipped = sum(1 for result in results if result.status == "skipped")
    return updated, unchanged, skipped


def summarize_results(results: list[HeroRefreshResult]) -> dict:
    """Return a UI/API-friendly summary while preserving CLI semantics."""
    updated, unchanged, skipped = _summary_counts(results)
    if skipped:
        status = "failed"
    elif updated:
        status = "updated"
    else:
        status = "unchanged"
    return {
        "ok": skipped == 0,
        "status": status,
        "updated": updated,
        "unchanged": unchanged,
        "skipped": skipped,
        "results": [asdict(result) for result in results],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh Bazaar build catalogs from GitHub")
    parser.add_argument("--out", type=Path, default=None, help="Writable builds directory override")
    args = parser.parse_args(argv)

    results = refresh_builds(out_dir=args.out)
    updated, unchanged, skipped = _summary_counts(results)
    print(f"refresh-builds: {updated} updated, {unchanged} unchanged, {skipped} skipped (errors)")
    return 1 if skipped else 0


if __name__ == "__main__":
    raise SystemExit(main())
