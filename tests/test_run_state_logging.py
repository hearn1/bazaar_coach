import io

from run_state import RunState


def test_state_change_logging_is_cp1252_safe(monkeypatch):
    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
    monkeypatch.setattr("sys.stdout", stream)

    state = RunState(event_source="mono")
    state.process({
        "event": "state_change",
        "ts": "2026-01-01T00:00:00+00:00",
        "from_state": "Unknown",
        "to_state": "EncounterState",
    })

    stream.flush()
    stream.seek(0)
    assert "Unknown -> EncounterState" in stream.buffer.getvalue().decode("cp1252")
