"""Stage direction around the conversation: freeze the arms when the listener talks, idle them when the
stage goes quiet, and carry the story on to its next scene."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from gestures import Stage

logger = logging.getLogger("storyteller.director")

CONTINUE_INSTRUCTIONS = "Continue the story with the next scene, straight from where you left off."


class Director:
    def __init__(self, session: Any, stage: Stage, *, idle_after_s: float, continue_after_s: float) -> None:
        self._session, self._stage = session, stage
        self._idle_after_s, self._continue_after_s = idle_after_s, continue_after_s
        self.user_state, self.agent_state = "listening", "initializing"
        self.continue_pending = False  # the last scene ended with [next] and was heard to the end
        self._timer: asyncio.Task | None = None
        session.on("user_state_changed", self._on_user_state)
        session.on("agent_state_changed", self._on_agent_state)

    def request_continue(self) -> None:
        """GestureSink calls this when the spoken narration reaches a [next] tag."""
        self.continue_pending = True

    def _on_user_state(self, ev: Any) -> None:
        self.user_state = ev.new_state
        if ev.new_state == "speaking":
            # "wait, what?": the arms hold their pose the moment the listener starts talking
            self.continue_pending = False
            self._cancel()
            self._stage.freeze()
        else:
            self._reschedule()

    def _on_agent_state(self, ev: Any) -> None:
        self.agent_state = ev.new_state
        self._reschedule()

    def _cancel(self) -> None:
        if self._timer and not self._timer.done():
            self._timer.cancel()
        self._timer = None

    def _reschedule(self) -> None:
        self._cancel()
        if self.agent_state == "listening" and self.user_state != "speaking":
            self._timer = asyncio.create_task(self._quiet_stage())

    async def _quiet_stage(self) -> None:
        """Runs while the agent listens and nobody talks; any state change cancels it."""
        if self.continue_pending:
            await asyncio.sleep(self._continue_after_s)
            self.continue_pending = False
            logger.info("next scene")
            self._session.generate_reply(instructions=CONTINUE_INSTRUCTIONS)
            return
        # let the last gesture (often the final bow) finish before relaxing the arms
        await asyncio.sleep(max(self._idle_after_s, self._stage.busy_until - time.monotonic()))
        self._stage.send("/idle")
