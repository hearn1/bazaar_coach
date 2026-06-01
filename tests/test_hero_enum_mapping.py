"""
test_hero_enum_mapping.py

Regression guard for issue #167 ("Starting run as Dooley, app tracks run as
Stelle").

Root cause: the in-game ``EHero`` enum was hardcoded with Dooley=3 and Stelle=5,
but live capture shows Dooley=5 and Stelle=3. A player starting Dooley therefore
read enum value 5, which the old map labeled "Stelle" — so the run row, scoring
catalog, dashboard, and overlay all showed the wrong hero.

The reporter (who owns every hero except Stelle and Mak) confirmed Pygmalien,
Vanessa, Jules, and Karnok tracked correctly, and only Dooley was mislabeled —
pinning the error to the Dooley/Stelle pair specifically.

The hero enum is duplicated in two runtime-relevant places that MUST agree:
  - schema.py            E_HERO  (canonical Python; imported by msgpack_decoder)
  - capture_mono_agent.js E_HERO  (the active Mono read path's hardcoded copy)
"""

import json
import re
from pathlib import Path

import pytest

from schema import E_HERO

_REPO_ROOT = Path(__file__).resolve().parent.parent


def test_dooley_and_stelle_are_not_swapped():
    # The exact pair the bug swapped.
    assert E_HERO[5] == "Dooley"
    assert E_HERO[3] == "Stelle"


def test_player_tested_heroes_resolve_correctly():
    # Empirically confirmed by the #167 reporter playing each of these.
    assert E_HERO[1] == "Pygmalien"
    assert E_HERO[2] == "Vanessa"
    assert E_HERO[4] == "Jules"
    assert E_HERO[7] == "Karnok"


def test_parse_player_maps_dooley_enum_value():
    """A Dooley snapshot carries Hero==5; it must decode to 'Dooley', not 'Stelle'."""
    pytest.importorskip("msgpack")  # alternate decoder; not in the minimal test venv
    from msgpack_decoder import parse_player

    # PlayerSnapshotDTO: [Hero, Attributes, ...]
    player = parse_player([5, {}])
    assert player["hero"] == "Dooley"

    player = parse_player([3, {}])
    assert player["hero"] == "Stelle"


def _extract_js_e_hero() -> dict:
    text = (_REPO_ROOT / "capture_mono_agent.js").read_text(encoding="utf-8")
    m = re.search(r"const E_HERO = (\{[^}]*\});", text)
    assert m, "Could not locate E_HERO literal in capture_mono_agent.js"
    # JS object literal with integer keys / double-quoted string values is valid
    # JSON once the bare int keys are quoted.
    js_obj = re.sub(r"(\d+):", r'"\1":', m.group(1))
    return {int(k): v for k, v in json.loads(js_obj).items()}


def test_js_agent_hero_enum_matches_schema():
    """The active Mono read path's hardcoded copy must match the canonical map.

    If these drift, the live overlay/dashboard hero can silently diverge from
    everything Python computes — which is exactly how #167 went unnoticed.
    """
    assert _extract_js_e_hero() == E_HERO
