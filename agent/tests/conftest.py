import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path[:0] = [str(HERE.parent), str(HERE)]  # agent modules, and the vendored lkfakes package

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def plain_timing(monkeypatch):
    """Most tests check where tags fire, not the sync maths: no lead, no playout delay, no sfx budget, no fill.
    The tests of those turn them on."""
    import gestures
    import sounds
    import storyteller
    monkeypatch.setattr(gestures, "TIMING", gestures.Timing())
    t = gestures.TIMING
    t.lead, t.audio_s, t.arm_lag_s, t.rtt_s = False, 0.0, 0.0, 0.0
    monkeypatch.setattr(sounds, "SFX_MIN_GAP_S", 0.0)
    monkeypatch.setattr(gestures, "TALK_TAIL_S", 0.0)
    monkeypatch.setattr(gestures, "TALK_MIN_S", 0.0)
    monkeypatch.setattr(storyteller, "FILL_GESTURES", False)
    yield t
