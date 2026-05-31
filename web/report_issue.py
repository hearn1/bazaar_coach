"""
web/report_issue.py — Business logic for the Report an Issue feature.

Keeps server.py routes-only by housing:
  - latest_log_file()   — newest coach_*.log in logs_dir() by mtime
  - build_issue_body()  — markdown body template for a prefilled GitHub issue
  - issue_info()        — assembles the full URL + paths payload for the route
"""

import platform
import urllib.parse
from pathlib import Path
from typing import Optional

import app_paths
import settings
from version import APP_VERSION
from update_checker import DEFAULT_GITHUB_REPO


def latest_log_file() -> Optional[Path]:
    """Return the newest coach_*.log in app_paths.logs_dir(), or None."""
    logs = app_paths.logs_dir()
    try:
        candidates = sorted(logs.glob("coach_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        return candidates[0] if candidates else None
    except OSError:
        return None


def _resolve_github_repo() -> str:
    """Return the configured github_repo, falling back to the package default."""
    try:
        settings.load()
        repo = settings.get("updates.github_repo")
        if repo and isinstance(repo, str) and "/" in repo:
            return repo
    except Exception:
        pass
    return DEFAULT_GITHUB_REPO


def build_issue_body(description: str, log_path: Optional[Path]) -> str:
    """Build the markdown body for a prefilled GitHub issue."""
    player_log = app_paths.find_player_log()
    player_log_line = str(player_log) if player_log else "_not found_"
    log_path_line = str(log_path) if log_path else "_no log yet — describe the problem manually_"

    desc_block = description.strip() if description and description.strip() else "_(describe the problem)_"

    return (
        f"**Version:** {APP_VERSION}\n"
        f"**OS:** {platform.platform()}\n"
        "\n"
        "**What happened**\n"
        f"{desc_block}\n"
        "\n"
        "**Steps to reproduce**\n"
        "1. \n"
        "\n"
        "---\n"
        "**Attach your latest log before submitting:**\n"
        f"Coach log: `{log_path_line}`\n"
        f"Player.log: `{player_log_line}`\n"
        "\n"
        "_Drag the highlighted coach log file into this issue, then submit._\n"
    )


def issue_info(title: str, description: str) -> dict:
    """
    Build and return the full payload for /api/report-issue/info.

    Returns a dict with:
      - issue_url: prefilled GitHub issues/new URL
      - log_path:  str path to latest coach log (or None)
      - log_dir:   str path to logs directory
    """
    repo = _resolve_github_repo()
    log = latest_log_file()
    body = build_issue_body(description, log)

    params: dict = {"body": body, "labels": "bug"}
    if title and title.strip():
        params["title"] = title.strip()

    url = f"https://github.com/{repo}/issues/new?" + urllib.parse.urlencode(params)

    return {
        "ok": True,
        "issue_url": url,
        "log_path": str(log) if log else None,
        "log_dir": str(app_paths.logs_dir()),
    }
