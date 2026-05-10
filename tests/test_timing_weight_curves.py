"""Pinned regression tests for TIMING_PROFILE_CURVES after P1-G steepening.

Function under test: scorer._late_archetype_timing_weight(arch, *, day, phase)

Progress formula: progress = (day - 1) / 12  for 1 < day < 13
                  progress = 0.0              for day <= 1
                  progress = 1.0              for day >= 13
Weight formula:   weight = curve["base"] + curve["slope"] * progress

Tempo crossover (weight == 1.0) after P1-G:
  1.20 + (-0.45) * p = 1.0  →  p = 0.2/0.45 ≈ 0.4444  →  day ≈ 6.33
So day=6 is still slightly above 1.0 (≈1.0125); day=7 first goes below 1.0 (≈0.975).
"""
import pytest
import scorer


def _w(profile, day):
    arch = {"timing_profile": profile}
    return scorer._late_archetype_timing_weight(arch, day=day, phase="late")


def test_tempo_curve_pinned_after_p1g_steepening():
    # day=1: progress=0.0, weight=1.20 + (-0.45)*0.0 = 1.200
    assert _w("tempo", 1)  == pytest.approx(1.200,  abs=1e-3)
    # day=4: progress=3/12=0.25, weight=1.20 - 0.1125 = 1.0875
    assert _w("tempo", 4)  == pytest.approx(1.0875, abs=1e-3)
    # day=6: progress=5/12≈0.4167, weight=1.20 - 0.1875 = 1.0125  (still > 1.0)
    assert _w("tempo", 6)  == pytest.approx(1.0125, abs=1e-3)
    # day=7: progress=6/12=0.5, weight=1.20 - 0.225 = 0.975  (first day < 1.0)
    assert _w("tempo", 7)  == pytest.approx(0.9750, abs=1e-3)
    # day=10: progress=9/12=0.75, weight=1.20 - 0.3375 = 0.8625
    assert _w("tempo", 10) == pytest.approx(0.8625, abs=1e-3)


def test_tempo_crossover_at_day7_not_day6():
    """Crossover to < 1.0 occurs between day 6 and 7 (at approx day 6.33)."""
    assert _w("tempo", 6) > 1.0
    assert _w("tempo", 7) < 1.0


def test_scaling_curve_unchanged_by_p1g():
    # day=1: progress=0.0, weight=0.82 + 0.33*0.0 = 0.820
    assert _w("scaling", 1)  == pytest.approx(0.820, abs=1e-3)
    # day=7: progress=0.5,  weight=0.82 + 0.33*0.5 = 0.985
    assert _w("scaling", 7)  == pytest.approx(0.985, abs=1e-3)
    # day=13: progress=1.0, weight=0.82 + 0.33*1.0 = 1.150
    assert _w("scaling", 13) == pytest.approx(1.150, abs=1e-3)


def test_exodia_curve_unchanged_by_p1g():
    # day=1: progress=0.0, weight=0.72 + 0.48*0.0 = 0.720
    assert _w("exodia", 1)  == pytest.approx(0.720, abs=1e-3)
    # day=7: progress=0.5,  weight=0.72 + 0.48*0.5 = 0.960
    assert _w("exodia", 7)  == pytest.approx(0.960, abs=1e-3)
    # day=13: progress=1.0, weight=0.72 + 0.48*1.0 = 1.200
    assert _w("exodia", 13) == pytest.approx(1.200, abs=1e-3)


def test_neutral_curve_flat():
    for d in (1, 6, 10, 13):
        assert _w("neutral", d) == pytest.approx(1.0, abs=1e-9)
