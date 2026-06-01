"""Regression test for issue #167: doctor must not crash on legacy code pages.

The "Bazaar Coach Doctor" shortcut runs the windowed BazaarCoach.exe, whose
console stream can be backed by a legacy code page (e.g. cp932 on a Japanese
locale). The doctor check messages contain em-dashes (U+2014), so an un-guarded
``print()`` raised ``UnicodeEncodeError`` and killed the command. ``doctor.main``
must reconfigure stdio (utf-8 + backslashreplace) before producing any output.

Same bug class as #152/#157, which only fixed the live-capture worker path.
"""

import io
import sys

import doctor


def _make_report():
    """A minimal report whose message carries the offending em-dash (U+2014)."""
    return {
        "generated_at": "2026-06-01T00:00:00Z",
        "checks": [
            {
                "name": "retention",
                "status": "ok",
                # U+2014 EM DASH — the exact character from the #167 traceback.
                "message": "disabled — runs are kept indefinitely until pruned",
            }
        ],
    }


def test_doctor_main_survives_cp932_stdout(monkeypatch):
    """doctor.main(['doctor']) must not raise on a cp932-backed stdout."""
    monkeypatch.setattr(doctor, "collect_doctor_report", _make_report)

    buffer = io.BytesIO()
    cp932_stdout = io.TextIOWrapper(buffer, encoding="cp932", newline="")
    monkeypatch.setattr(sys, "stdout", cp932_stdout)

    # Before the fix this raises UnicodeEncodeError ('cp932' codec can't encode
    # character '—'); after the fix main() reconfigures stdout to utf-8.
    rc = doctor.main(["doctor"])

    cp932_stdout.flush()
    assert rc == 0
    assert b"retention" in buffer.getvalue()
