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
from pathlib import Path

from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import Agent, AgentServer, AgentSession, JobContext, TurnHandlingOptions, cli, inference, room_io
from livekit.agents.voice.room_io.types import NoiseCancellationParams
from livekit.plugins import noise_cancellation

from director import Director, PushToTalk
from gestures import DEFAULT_GESTURES, GestureBook, GestureSync, Stage, strip_tags_for_tts, tags_first
from sounds import SoundBook, Soundboard

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")

# --- Models, all on LiveKit Inference (one LIVEKIT_API_KEY, billed to the project's credits) -------------
# Catalogue: https://docs.livekit.io/agents/models/  Override any of these in .env.
STT_MODEL = os.getenv("STT_MODEL", "assemblyai/universal-3-6-pro")
LLM_MODEL = os.getenv("LLM_MODEL", "google/gemma-4-31b-it")  # LiveKit's latency-optimised voice default
TTS_MODEL = os.getenv("TTS_MODEL", "fishaudio/s2.1-pro")  # expressive: emotion, pacing, laughs
TTS_VOICE = os.getenv("TTS_VOICE", "e3cd384158934cc9a01029cd7d278634")  # "Laura", a confident narrator
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
            .replace("{{SFX}}", sounds.describe_sfx()).replace("{{MUSIC}}", sounds.describe_music()))


class Storyteller(Agent):
    async def transcription_node(self, text, model_settings):
        async for chunk in tags_first(text):  # gestures start with their sentence, not after it
            yield chunk


def _noise_cancellation(params: NoiseCancellationParams) -> rtc.NoiseCancellationOptions:
    if params.participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
        return noise_cancellation.BVCTelephony()
    return noise_cancellation.BVC()


server = AgentServer(num_idle_processes=1) if PTT else AgentServer()  # PTT: a warm process, so the greeting is quick


@server.rtc_session(agent_name=AGENT_NAME)
async def entrypoint(ctx: JobContext) -> None:
    stage = Stage(STAGE_URL)
    book = GestureBook(await stage.fetch_gestures() or DEFAULT_GESTURES, await stage.fetch_duets(),
                       await stage.fetch_duets("ensemble"), stage.about)
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
        # Gestures ride on the LLM text, which the voice must never read out...
        tts_text_transforms=[strip_tags_for_tts, "filter_markdown", "filter_emoji"],
        # ...and must reach the transcript, which a TTS-aligned transcript would replace with the TTS's words.
        use_tts_aligned_transcript=False,
    )
    director = Director(session, stage, idle_after_s=IDLE_AFTER_S, continue_after_s=NEXT_SCENE_AFTER_S)
    sound_book = SoundBook.load()
    board = Soundboard(sound_book)  # sfx + music on their own room track; started once the room is connected
    gestures = GestureSync(book, stage.play, director.request_continue, stage.face, stage.talk, on_sound=board.cue)

    async def shutdown() -> None:
        await board.aclose()  # STOP or the end of the session: music and sfx go silent at once
        stage.send("/rest")
        await stage.aclose()

    ctx.add_shutdown_callback(shutdown)

    await session.start(
        agent=Storyteller(instructions=build_instructions(book, sound_book)),
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
