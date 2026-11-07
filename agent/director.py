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
        self.holding = False  # push-to-talk: the listener holds the talk button
        self._timer: asyncio.Task | None = None
        session.on("user_state_changed", self._on_user_state)
        session.on("agent_state_changed", self._on_agent_state)

    def request_continue(self) -> None:
        """GestureSink calls this when the spoken narration reaches a [next] tag."""
        self.continue_pending = True

    def hold(self) -> None:
        """Push-to-talk button down: the same freeze as speech, and no idling until it is released."""
        self.holding = True
        self._listener_cut_in()

    def release(self) -> None:
        self.holding = False
        self._reschedule()

    def _listener_cut_in(self) -> None:
        # "wait, what?": the arms hold their pose the moment the listener starts talking
        self.continue_pending = False
        self._cancel()
        self._stage.freeze()

    def _on_user_state(self, ev: Any) -> None:
        self.user_state = ev.new_state
        if ev.new_state == "speaking":
            self._listener_cut_in()
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
        if self.agent_state == "listening" and self.user_state != "speaking" and not self.holding:
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


class PushToTalk:
    """Manual turns for the hold-to-talk page (agent/app.py): the session hears the mic only while the button is
    held. The page calls these as RPC methods on the agent's participant."""

    # LiveKit waits this long for a final transcript after the release. If the STT already sent one while the
    # button was still held, it waits for a second one that never comes, so keep it short: on timeout the
    # interim transcript is used.
    TRANSCRIPT_TIMEOUT_S = 2.5

    def __init__(self, session: Any, director: Director) -> None:
        self._session, self._director = session, director
        self._turn = 0

    def register(self, participant: Any) -> None:
        for name, fn in (("start_turn", self.start_turn), ("end_turn", self.end_turn),
                         ("cancel_turn", self.cancel_turn)):
            participant.register_rpc_method(name, self._rpc(fn))

    @staticmethod
    def _rpc(fn):
        async def handler(_data: Any) -> str:
            fn()
            return "ok"
        return handler

    def start_turn(self) -> None:
        self._turn += 1
        self._director.hold()
        self._session.interrupt()
        self._session.clear_user_turn()
        self._session.input.set_audio_enabled(True)

    def end_turn(self) -> None:
        self._session.input.set_audio_enabled(False)
        heard = self._session.commit_user_turn(transcript_timeout=self.TRANSCRIPT_TIMEOUT_S)
        turn = self._turn
        heard.add_done_callback(lambda fut: self._committed(fut, turn))

    def cancel_turn(self) -> None:
        self._session.input.set_audio_enabled(False)
        self._session.clear_user_turn()
        self._director.release()

    def _committed(self, fut: asyncio.Future, turn: int) -> None:
        # the arms stay frozen until the transcript is in and the reply starts, not idle in between
        if turn == self._turn:
            self._director.release()
        if fut.cancelled():
            return
        if (err := fut.exception()) is not None:
            logger.warning("push-to-talk commit failed: %s", err)
            return
        logger.info("push-to-talk heard: %r", fut.result())
