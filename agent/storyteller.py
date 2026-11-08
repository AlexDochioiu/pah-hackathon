"""Sparky the storyteller: a LiveKit voice agent that makes up a story live and has two robot arms act it out.

    uv run storyteller.py console   # talk to it in this terminal (mic + speaker)
    uv run storyteller.py dev       # serve it to LiveKit Cloud rooms (web playground, phone calls)
    uv run app.py                   # hold-to-talk web page; runs this worker with PTT=1 (see app.py)

See README.md for setup. The prompt lives in prompt.md; gesture timing in gestures.py; freeze/idle/next-scene
logic in director.py.
"""
from __future__ import annotations

import logging
import os
import weakref
from pathlib import Path

from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import (Agent, AgentServer, AgentSession, JobContext, TurnHandlingOptions, cli, inference, room_io,
                            tts)
from livekit.agents.voice.agent_activity import _SpeechHandleContextVar  # pairs a reply's tts and transcript
from livekit.agents.voice.room_io.types import NoiseCancellationParams
from livekit.plugins import noise_cancellation

from director import Director, PushToTalk
from gestures import DEFAULT_GESTURES, GestureBook, GestureSync, Stage, tags_first
from sounds import SoundBook, Soundboard
from voices import DEFAULT_CAST, NARRATOR_VOICE, PALETTE, RunMarks, describe_palette, stream_tts, timestamp_runs, voiced_tts

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")

# --- Models, all on LiveKit Inference (one LIVEKIT_API_KEY, billed to the project's credits) -------------
# Catalogue: https://docs.livekit.io/agents/models/  Override any of these in .env.
STT_MODEL = os.getenv("STT_MODEL", "assemblyai/universal-3-6-pro")
LLM_MODEL = os.getenv("LLM_MODEL", "google/gemma-4-31b-it")  # LiveKit's latency-optimised voice default
TTS_MODEL = os.getenv("TTS_MODEL", "fishaudio/s2.1-pro")  # expressive: emotion, pacing, laughs
TTS_VOICE = os.getenv("TTS_VOICE", NARRATOR_VOICE)  # Lars, the host
CHARACTER_VOICES = os.getenv("CHARACTER_VOICES", "1") == "1"  # 0: the narrator reads every line (voices.py)
EXPRESSIVE = os.getenv("EXPRESSIVE", "1") == "1"  # let the LLM steer the voice's emotion inline

STAGE_URL = os.getenv("STAGE_URL", "http://127.0.0.1:8770")
IDLE_AFTER_S = float(os.getenv("IDLE_AFTER_S", "1.5"))  # quiet stage -> POST /idle after this long
NEXT_SCENE_AFTER_S = float(os.getenv("NEXT_SCENE_AFTER_S", "0.8"))  # pause between scenes for interjections
# Push-to-talk (app.py sets these): the mic only counts while the page's talk button is held.
PTT = os.getenv("PTT", "0") == "1"
AGENT_NAME = os.getenv("AGENT_NAME", "storyteller")

logger = logging.getLogger("storyteller")


def build_instructions(book: GestureBook, sounds: SoundBook | None = None) -> str:
    sounds = sounds or SoundBook.load()
    return ((HERE / "prompt.md").read_text().replace("{{GESTURES}}", book.describe())
            .replace("{{DUETS}}", book.describe_duets()).replace("{{ENSEMBLES}}", book.describe_duets(ensembles=True))
            .replace("{{SFX}}", sounds.describe_sfx()).replace("{{MUSIC}}", sounds.describe_music())
            .replace("{{VOICES}}", describe_palette())
            .replace("{{GUESTS}}", GUEST_LARS.format(secs=f"{book.guests['pick_lars']:.0f}")
                     if book.guest("pick", "lars") and book.guest("place", "lars") else ""))


GUEST_LARS = """# Lars the frog doll

Lars, a small green plush frog doll, waits on a dock at the edge of the stage. The right arm can pick Lars up
mid-story with `[pick:lars]` (about {secs} s; give that arm no other tag until Lars is up) and then acts with Lars in
its hand: its gestures carry Lars, and its mouth is full, so no talk tag for that arm. Before the story ends, the right
arm must put Lars back with `[place:lars]`, in its own sentence, with no other tag for that arm in it or the next
sentence. Both need the arms facing the audience: write `[face:audience]` in the sentence before, if they were facing
each other. At most one pick-up of Lars per story.
"""


class Storyteller(Agent):
    def __init__(self, *, instructions: str, voices: dict[str, tts.TTS] | None = None) -> None:
        super().__init__(instructions=instructions)
        self.voices = voices or {}  # palette name -> TTS; a voice missing here is read by the narrator
        self.cast = dict(DEFAULT_CAST)  # arm -> palette name, recast by [voice:left=...] tags
        self._marks: weakref.WeakKeyDictionary[object, RunMarks] = weakref.WeakKeyDictionary()  # per reply

    async def transcription_node(self, text, model_settings):
        speech = _SpeechHandleContextVar.get(None)
        # gestures start with their sentence, not after it; each voice run is pinned to its audio
        async for chunk in timestamp_runs(tags_first(text), lambda: self._marks.get(speech) if speech else None):
            yield chunk

    async def tts_node(self, text, model_settings):
        """Narration in the session's voice, each character's quoted lines in its own (voices.py)."""
        activity = self._get_activity_or_raise()
        expressive = activity._resolve_expressive_options() is not None

        def narrate(t):
            return Agent.default.tts_node(self, t, model_settings)

        def speak(name: str):
            if (voice := self.voices.get(name)) is None:
                return narrate
            voice._set_expressive(expressive)  # what the default tts_node does for the session TTS
            return lambda t: stream_tts(voice, t, self.session.conn_options.tts_conn_options)

        marks = RunMarks()
        if speech := _SpeechHandleContextVar.get(None):
            self._marks[speech] = marks  # for this reply's transcription_node
        async for frame in voiced_tts(text, narrate, speak, self.cast, marks):
            yield frame


def _noise_cancellation(params: NoiseCancellationParams) -> rtc.NoiseCancellationOptions:
    if params.participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
        return noise_cancellation.BVCTelephony()
    return noise_cancellation.BVC()


server = AgentServer(num_idle_processes=1) if PTT else AgentServer()  # PTT: a warm process, so the greeting is quick


@server.rtc_session(agent_name=AGENT_NAME)
async def entrypoint(ctx: JobContext) -> None:
    stage = Stage(STAGE_URL)
    book = GestureBook(await stage.fetch_gestures() or DEFAULT_GESTURES, await stage.fetch_duets(),
                       await stage.fetch_duets("ensemble"), stage.about, stage.sides)
    logger.info("gestures: %s; duets: %s", ", ".join(book.names), book.describe_duets())

    session = AgentSession(
        stt=inference.STT(model=STT_MODEL, language="en"),
        llm=inference.LLM(model=LLM_MODEL),
        tts=inference.TTS(model=TTS_MODEL, voice=TTS_VOICE),
        turn_handling=TurnHandlingOptions(
            # LiveKit's turn detector decides when the listener is done; in push-to-talk the button release does
            turn_detection="manual" if PTT else inference.TurnDetector(),
            preemptive_generation={"enabled": True},  # start the reply before the turn is confirmed over
        ),
        expressive=EXPRESSIVE,
        # Gestures ride on the LLM text, which the voice must never read out: Storyteller.tts_node drops the tags
        # (it needs the talk tags to pick each line's voice, so they get past these filters)...
        tts_text_transforms=["filter_markdown", "filter_emoji"],
        # ...and must reach the transcript, which a TTS-aligned transcript would replace with the TTS's words.
        use_tts_aligned_transcript=False,
    )
    director = Director(session, stage, idle_after_s=IDLE_AFTER_S, continue_after_s=NEXT_SCENE_AFTER_S)
    sound_book = SoundBook.load()
    board = Soundboard(sound_book)  # sfx + music on their own room track; started once the room is connected
    gestures = GestureSync(book, stage.play, director.request_continue, stage.face, stage.talk, on_sound=board.cue)
    # one TTS per character voice, connected now: a cold connection would add ~1.5 s to a character's first line
    voices = {name: inference.TTS(model=TTS_MODEL, voice=v.id) for name, v in PALETTE.items()} if CHARACTER_VOICES else {}
    for voice in voices.values():
        voice.prewarm()

    async def shutdown() -> None:
        await board.aclose()  # STOP or the end of the session: music and sfx go silent at once
        stage.send("/rest")
        await stage.aclose()
        for voice in voices.values():
            await voice.aclose()

    ctx.add_shutdown_callback(shutdown)

    await session.start(
        agent=Storyteller(instructions=build_instructions(book, sound_book), voices=voices),
        room=ctx.room,
        room_options=room_io.RoomOptions(audio_input=room_io.AudioInputOptions(noise_cancellation=_noise_cancellation)),
    )
    gestures.install(session.output)  # after start: wraps the outputs start() set up
    if PTT:
        session.input.set_audio_enabled(False)  # deaf until the button is held
    await ctx.connect()
    await board.start(ctx.room, session)
    if PTT:
        PushToTalk(session, director).register(ctx.room.local_participant)
        logger.info("push-to-talk: listening only while the talk button is held")

    stage.face("audience")  # a new show starts facing the audience (and at the stage pose)
    session.generate_reply(instructions="Greet the listener and ask: a story, or a comedy show?")


if __name__ == "__main__":
    cli.run_app(server)
