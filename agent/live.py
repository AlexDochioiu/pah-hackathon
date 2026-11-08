"""Live chat: the story page's open-mic mode (app.py /token?mode=live). LiveKit's VAD (silero, built into
livekit-agents) and turn detector take the turns, and the audience can cut the storyteller off mid-sentence.

Push-to-talk stays the default; the page's mode toggle starts a fresh room in the other mode.

Knobs (agent/.env, read when a session starts), for a noisy hackathon room:
    LIVE_INTERRUPT_MODE        adaptive  "adaptive": LiveKit's model tells a real cut-in from "uh-huh"; "vad": any speech
    LIVE_MIN_INTERRUPT_S       0.6       speech this long (s) before it counts as an interruption (LiveKit: 0.5)
    LIVE_MIN_INTERRUPT_WORDS   2         and at least this many transcribed words (LiveKit: 0; 0 = off)
    LIVE_RESUME                1         a cut-in that turns out to be noise: the voice picks up where it paused
    LIVE_FALSE_INTERRUPT_S     2.0       silence (s) after a cut-in with no words before it counts as noise
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable

from livekit.agents.voice.turn import InterruptionOptions

logger = logging.getLogger("storyteller.live")


def is_live(metadata: str | None) -> bool:
    """The job's dispatch metadata (app.py /token?mode=live) asks for live chat."""
    try:
        return json.loads(metadata or "{}").get("mode") == "live"
    except (ValueError, AttributeError):
        return False


def interruption_options() -> InterruptionOptions:
    mode = os.getenv("LIVE_INTERRUPT_MODE", "adaptive")
    return InterruptionOptions(
        enabled=True,
        **({"mode": mode} if mode in ("adaptive", "vad") else {}),  # anything else: LiveKit picks
        min_duration=float(os.getenv("LIVE_MIN_INTERRUPT_S", "0.6")),
        min_words=int(os.getenv("LIVE_MIN_INTERRUPT_WORDS", "2")),
        resume_false_interruption=os.getenv("LIVE_RESUME", "1") == "1",
        false_interruption_timeout=float(os.getenv("LIVE_FALSE_INTERRUPT_S", "2.0")),
    )


class LiveTurns:
    """Stage direction for live chat. The arms freeze and the sound effects stop when the listener actually cuts
    in (LiveKit stops or pauses the voice for them), as a push-to-talk press does, not on every noise the VAD
    hears. A turn heard between scenes drops the pending next scene, as a press does."""

    def __init__(self, session: Any, director: Any, hush: Callable[[], None]) -> None:
        self._director, self._hush = director, hush
        director.freeze_on_speech = False
        session.on("agent_state_changed", self._on_agent_state)
        session.on("user_input_transcribed", self._on_transcript)

    def _on_agent_state(self, ev: Any) -> None:
        # the Director's handlers ran first (registered earlier), so its user_state is current
        if ev.old_state == "speaking" and ev.new_state == "listening" and self._director.user_state == "speaking":
            logger.info("live: the listener cut in")
            self._director.cut()
            self._hush()

    def _on_transcript(self, ev: Any) -> None:
        if ev.is_final and ev.transcript.strip() and self._director.agent_state != "speaking":
            self._director.cut()
