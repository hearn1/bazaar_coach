"""Catch future drift between PRIVACY.md and the app's known outbound hosts."""

import app_paths

KNOWN_HOSTS = [
    "api.github.com",
    "raw.githubusercontent.com",
    "playthebazaar.com",
    "fonts.googleapis.com",
    "fonts.gstatic.com",
]


def _read_privacy() -> str:
    return (app_paths.repo_dir() / "PRIVACY.md").read_text(encoding="utf-8")


def test_privacy_doc_lists_all_known_outbound_hosts():
    content = _read_privacy()
    missing = [host for host in KNOWN_HOSTS if host not in content]
    assert not missing, (
        f"PRIVACY.md is missing these outbound hosts: {missing}. "
        "Update PRIVACY.md to document the new host or remove it from KNOWN_HOSTS."
    )


def test_privacy_doc_distinguishes_automatic_from_manual():
    content = _read_privacy()
    assert "automatic" in content.lower(), "PRIVACY.md should label automatic startup requests"
    assert "manual" in content.lower(), "PRIVACY.md should label manual/on-demand requests"


def test_privacy_doc_describes_opt_out_for_update_check():
    content = _read_privacy()
    assert "updates.enabled" in content, (
        "PRIVACY.md must document the settings.json opt-out for the update check"
    )


def test_privacy_doc_describes_opt_out_for_build_refresh():
    content = _read_privacy()
    assert "--no-refresh-builds" in content, (
        "PRIVACY.md must document the --no-refresh-builds opt-out for the catalog refresh"
    )
