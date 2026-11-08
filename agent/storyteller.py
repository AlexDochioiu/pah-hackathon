"""The storyteller: a LiveKit voice agent that makes up a story live and has two robot arms act it out.

    uv run storyteller.py console   # talk to it in this terminal (mic + speaker)
    uv run storyteller.py dev       # serve it to LiveKit Cloud rooms (web playground, phone calls)
    uv run app.py                   # hold-to-talk web page; runs this worker with PTT=1 (see app.py)

See README.md for setup. The prompt lives in prompt.md; gesture timing in gestures.py; freeze/idle/next-scene
logic in director.py.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import weakref
from pathlib import Path

from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import (Agent, AgentServer, AgentSession, JobContext, TurnHandlingOptions, cli, inference, llm,
                            room_io, tts)
from livekit.agents.voice.agent_activity import _SpeechHandleContextVar  # pairs a reply's tts and transcript
from livekit.agents.voice.room_io.types import NoiseCancellationParams
from livekit.plugins import noise_cancellation

import gestures as gestures_mod
from director import CONTINUE_INSTRUCTIONS, Director, PushToTalk
from gestures import DEFAULT_GESTURES, GestureBook, GestureSync, Stage, tags_first
from live import LiveTurns, interruption_options, is_live
from show import Script, ShowRunner, is_show
from sounds import SoundBook, Soundboard
from voices import (DEFAULT_CAST, HOST_VOICE, PALETTE, STORY_VOICE, RunMarks, Tidy, describe_palette, stream_tts,
                    timestamp_runs, voice_gain, voiced_tts, with_gain)

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")

# --- Models, all on LiveKit Inference (one LIVEKIT_API_KEY, billed to the project's credits) -------------
# Catalogue: https://docs.livekit.io/agents/models/  Override any of these in .env.
STT_MODEL = os.getenv("STT_MODEL", "assemblyai/universal-3-6-pro")
LLM_MODEL = os.getenv("LLM_MODEL", "google/gemma-4-31b-it")  # LiveKit's latency-optimised voice default
TTS_MODEL = os.getenv("TTS_MODEL", "fishaudio/s2.1-pro")  # expressive: emotion, pacing, laughs
# The narrator: an unnamed storyteller in story mode, Lars the presenter in show mode (owner, Nov 7)
STORY_TTS_VOICE = os.getenv("STORY_VOICE") or STORY_VOICE  # (the old TTS_VOICE is ignored: it set one voice for both)
HOST_TTS_VOICE = os.getenv("HOST_VOICE", HOST_VOICE)
FILL_GESTURES = os.getenv("FILL_GESTURES", "1") == "1"  # a sentence with nothing moving gets a light move


def narrator_voice(show: bool) -> str:
    return HOST_TTS_VOICE if show else STORY_TTS_VOICE


CHARACTER_VOICES = os.getenv("CHARACTER_VOICES", "1") == "1"  # 0: the narrator reads every line (voices.py)
EXPRESSIVE = os.getenv("EXPRESSIVE", "1") == "1"  # let the LLM steer the voice's emotion inline

STAGE_URL = os.getenv("STAGE_URL", "http://127.0.0.1:8770")
IDLE_AFTER_S = float(os.getenv("IDLE_AFTER_S", "1.5"))  # quiet stage -> POST /idle after this long
NEXT_SCENE_AFTER_S = float(os.getenv("NEXT_SCENE_AFTER_S", "0.8"))  # pause between scenes for interjections
# Push-to-talk (app.py sets these): the mic only counts while the page's talk button is held.
PTT = os.getenv("PTT", "0") == "1"
AGENT_NAME = os.getenv("AGENT_NAME", "storyteller")

logger = logging.getLogger("storyteller")


WHO_STORY = ("You are the storyteller: a warm, playful narrator with no name of your own. You are always the "
             "narrator, never a character in the story.")
WHO_SHOW = ("You are {host}, a small green plush frog with a big presenter personality: you sit on the table and host "
            "the show. In the live demo you are the narrator, never a character in the story.")


def build_instructions(book: GestureBook, sounds: SoundBook | None = None, script: Script | None = None,
                       show: bool = False) -> str:
    sounds = sounds or SoundBook.load()
    script = script or Script.load(book=book)  # HOST_NAME lives in show.md's config
    who = WHO_SHOW.format(host=script.host_name) if show else WHO_STORY
    return ((HERE / "prompt.md").read_text().replace("{{WHO}}", who).replace("{{HOST_NAME}}", script.host_name)
            .replace("{{GESTURES}}", book.describe())
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


STORY_WRAP_S = float(os.getenv("STORY_WRAP_S", "105"))  # a story (or comedy set) past this: wrap up in a beat or two
STORY_END_S = float(os.getenv("STORY_END_S", "150"))    # ...past this: end it now
WRAP_NOTE = ("\n\n# Time\nThis story has run about {secs} seconds and should last about two minutes: head for the "
             "climax now and end it within one or two replies, with the proper ending (\"The end!\", the bow) and the "
             "short ask for another one.")
END_NOTE = ("\n\n# Time\nThis story is over time: end it in this reply, with \"The end!\", the bow, and the short "
            "ask for another one.")
_CODE_TAGS = re.compile(r"\[(?:mark|fill):[^\]]*\] ?")
_NEW_STORY = re.compile(r"\b(new|another|different|other)\b.*\b(story|one|show|set|tale)\b|\bcomedy\b|\bstory about\b",
                        re.I)


class StoryClock:
    """Times each story or comedy set: from its first casting or music tag as spoken to its final bow (or
    the_end ensemble), interruptions included. Logs the length, and past `STORY_WRAP_S` tells the LLM to wrap up
    (a note on its instructions and on the next-scene prompt), firmer past `STORY_END_S`.

    In the show's live demo (`arm()`, from ShowRunner) the clock starts at the volunteer's ask instead.

    It also marks a new story for the casting lock (Storyteller.new_story): a story's end, or a listener turn that
    asks for a new story or comedy."""

    def __init__(self, session: Any, agent: Any, director: Director) -> None:
        self._session, self._agent, self._director = session, agent, director
        self.started: float | None = None
        self.lengths: list[float] = []
        self._base = agent.instructions
        self._task: asyncio.Task | None = None
        self._armed = False  # start at the next listener turn
        self._noted = False
        session.on("user_input_transcribed", self._on_user)

    def on_tag(self, tag: Any) -> None:
        if getattr(self._agent, "scripted", False):  # a show cue cut in: that ends the story
            self.end()
            return
        if self.started is None and (tag.arm in ("voice", "music") or tag.name == "present"):
            self.start()
        elif self.started is not None and ((tag.arm == "both" and tag.name == "bow")
                                           or (tag.arm in ("ensemble", "duet") and tag.name == "the_end")):
            self.end()

    def arm(self) -> None:
        self._armed = True

    def start(self) -> None:
        self._armed = False
        self.started = time.monotonic()
        logger.info("story starts")
        self._task = asyncio.ensure_future(self._watch())

    def end(self) -> None:
        if self.started is None:
            return
        secs = time.monotonic() - self.started
        self.lengths.append(secs)
        logger.info("story length: %d:%02d (%.0f s)", secs // 60, secs % 60, secs)
        self.started = None
        if self._task:
            self._task.cancel()
        if self._noted:
            self._note("")
        self._agent.new_story()

    def _on_user(self, ev: Any) -> None:
        if not getattr(ev, "is_final", True):
            return
        if self._armed and self.started is None:
            self.start()
        elif _NEW_STORY.search(getattr(ev, "transcript", "") or ""):
            if self.started is not None:
                self.end()  # cut short for a new one
            else:
                self._agent.new_story()

    def _note(self, note: str) -> None:
        self._noted = bool(note)
        self._director.continue_instructions = CONTINUE_INSTRUCTIONS + (
            "" if not note else " " + note.split("\n")[-1])
        res = self._agent.update_instructions(self._base + note)
        if asyncio.iscoroutine(res):
            asyncio.ensure_future(res)

    async def _watch(self) -> None:
        await asyncio.sleep(STORY_WRAP_S)
        logger.info("story at %.0f s: asking for the wrap-up", STORY_WRAP_S)
        self._note(WRAP_NOTE.format(secs=int(STORY_WRAP_S)))
        await asyncio.sleep(max(0.0, STORY_END_S - STORY_WRAP_S))
        logger.info("story at %.0f s: asking for the end now", STORY_END_S)
        self._note(END_NOTE)


class Storyteller(Agent):
    def __init__(self, *, instructions: str, voices: dict[str, tts.TTS] | None = None,
                 book: GestureBook | None = None, host_only: bool = False, narrator: str = STORY_VOICE) -> None:
        super().__init__(instructions=instructions)
        self.narrator = narrator  # the session TTS's Fish voice id, for its playout gain (voices.VOICE_GAIN_DB)
        self.voices = voices or {}  # palette name -> TTS; a voice missing here is read by the narrator
        self.cast = dict(DEFAULT_CAST)  # arm -> palette name, recast by [voice:left=...] tags
        self.cast_locked: set[str] = set()  # arms cast in this story: no recast until a new one (new_story)
        self.book = book
        self.host_only = host_only  # show mode: the host reads every line, quotes included (owner, Nov 7)
        self.scripted = False  # a scripted show act is playing: no fill moves, no sfx budget, no story clock
        self._marks: weakref.WeakKeyDictionary[object, RunMarks] = weakref.WeakKeyDictionary()  # per reply

    def new_story(self) -> None:
        if self.cast_locked:
            logger.info("new story: the arms may be cast again")
        self.cast_locked.clear()

    async def llm_node(self, chat_ctx, tools, model_settings):
        """The LLM's text, tidied before anything reads it (voice, transcript, history): no written laughter, no
        stage directions, no endless character lines (voices.Tidy)."""
        tidy = Tidy()
        chat_ctx = chat_ctx.copy()  # the code's own [mark:…]/[fill:…] tags stay out of what the LLM reads
        for i, item in enumerate(chat_ctx.items):
            if getattr(item, "role", None) == "assistant" and any(isinstance(c, str) and "[" in c for c in item.content):
                chat_ctx.items[i] = item.model_copy(update={"content": [
                    _CODE_TAGS.sub("", c) if isinstance(c, str) else c for c in item.content]})
        async for chunk in Agent.default.llm_node(self, chat_ctx, tools, model_settings):
            if isinstance(chunk, str):
                text = chunk
            elif isinstance(chunk, llm.ChatChunk) and chunk.delta and chunk.delta.content and not chunk.delta.tool_calls:
                text = chunk.delta.content
            else:
                yield chunk
                continue
            if out := tidy.feed(text):
                yield out
        if out := tidy.flush():
            yield out

    async def transcription_node(self, text, model_settings):
        speech = _SpeechHandleContextVar.get(None)
        # gestures fire early enough to hit on their words; each voice run is pinned to its audio
        placed = tags_first(text, self.book, lambda: FILL_GESTURES and not self.scripted)
        async for chunk in timestamp_runs(placed, lambda: self._marks.get(speech) if speech else None):
            yield chunk

    async def tts_node(self, text, model_settings):
        """Narration in the session's voice, each character's quoted lines in its own (voices.py), each voice at
        its own playout gain so they all sit at the storyteller's loudness."""
        activity = self._get_activity_or_raise()
        expressive = activity._resolve_expressive_options() is not None

        def narrate(t):
            return with_gain(Agent.default.tts_node(self, t, model_settings), voice_gain(self.narrator))

        def speak(name: str):
            if (voice := self.voices.get(name)) is None:
                return narrate
            voice._set_expressive(expressive)  # what the default tts_node does for the session TTS
            gain = voice_gain(PALETTE[name].id)
            return lambda t: with_gain(stream_tts(voice, t, self.session.conn_options.tts_conn_options), gain)

        if self.host_only:
            speak = lambda name: narrate  # noqa: E731 - every quoted line in the host's voice
        marks = RunMarks()
        if speech := _SpeechHandleContextVar.get(None):
            self._marks[speech] = marks  # for this reply's transcription_node
        async for frame in voiced_tts(text, narrate, speak, self.cast, marks, locked=self.cast_locked):
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
                       await stage.fetch_duets("ensemble"), stage.about, stage.sides, stage.sounds)
    logger.info("gestures: %s; duets: %s", ", ".join(book.names), book.describe_duets())
    show = is_show(ctx.job.metadata)  # the page's /show route: scripted acts on cue (show.py)
    live = is_live(ctx.job.metadata)  # the story page's "Live chat" toggle: open mic (live.py)
    ptt = PTT and not live
    script = Script.load(book=book)
    voice = narrator_voice(show)
    gestures_mod.SESSION_T0 = time.monotonic()  # sync log times from here

    session = AgentSession(
        stt=inference.STT(model=STT_MODEL, language="en"),
        llm=inference.LLM(model=LLM_MODEL),
        tts=inference.TTS(model=TTS_MODEL, voice=voice),
        turn_handling=TurnHandlingOptions(
            # LiveKit's turn detector decides when the listener is done; in push-to-talk the button release does
            turn_detection="manual" if ptt else inference.TurnDetector(),
            preemptive_generation={"enabled": True},  # start the reply before the turn is confirmed over
            **({"interruption": interruption_options()} if live else {}),  # noisy-room guards (live.py)
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
    gestures_mod.TIMING.backlog = board.backlog
    # one TTS per character voice, connected now: a cold connection would add ~1.5 s to a character's first line.
    # Show mode: none (the host reads every line).
    voices = ({name: inference.TTS(model=TTS_MODEL, voice=v.id) for name, v in PALETTE.items()}
              if CHARACTER_VOICES and not show else {})
    for voice in voices.values():
        voice.prewarm()

    async def shutdown() -> None:
        await board.aclose()  # STOP or the end of the session: music and sfx go silent at once
        stage.send("/rest")
        await stage.aclose()
        for voice in voices.values():
            await voice.aclose()

    ctx.add_shutdown_callback(shutdown)

    agent = Storyteller(instructions=build_instructions(book, sound_book, script, show=show)
                        + (script.live_prompt() if show else ""), voices=voices, book=book, host_only=show,
                        narrator=narrator_voice(show))
    clock = StoryClock(session, agent, director)  # ~2-minute stories: logs each one's length, nudges a wrap-up
    gestures = GestureSync(book, stage.play, director.request_continue, stage.face, stage.talk,
                           on_sound=lambda kind, name: board.cue(kind, name, budget=not agent.scripted),
                           on_tag=clock.on_tag)
    stage.on_dream = lambda text: session.say(text)  # [dream:pick_lars]: the host voices Dream-1's attempt

    def dream_oops(msg: dict) -> None:  # a Dream-1 miss in a live story: its sound, then the LLM riffs on it
        board.cue("sfx", str(msg.get("sfx") or "sad_trombone"), budget=False)
        session.generate_reply(instructions=f"Dream-1's pick just missed ({msg.get('kind', 'a miss')}): the arm "
                               "facepalmed and our own pick takes over. One short, funny line about it, then go on.")

    stage.on_dream_oops = dream_oops  # show mode: ShowRunner swaps in a DREAM_OOPS quip
    await session.start(
        agent=agent,
        room=ctx.room,
        room_options=room_io.RoomOptions(audio_input=room_io.AudioInputOptions(noise_cancellation=_noise_cancellation)),
    )
    gestures.install(session.output)  # after start: wraps the outputs start() set up
    if ptt:
        session.input.set_audio_enabled(False)  # deaf until the button is held
    await ctx.connect()
    await board.start(ctx.room, session)
    _listen_playout(ctx.room)

    def hush() -> None:  # the listener cut in: sound effects stop with the voice (the music only ducks)
        board.stop_sfx()
        gestures.cancel_sounds()

    if ptt:
        PushToTalk(session, director, hush).register(ctx.room.local_participant)
        logger.info("push-to-talk: listening only while the talk button is held")
    elif live:
        LiveTurns(session, director, hush)
        logger.info("live chat: open mic, turn detector, interruptions %s", interruption_options())

    stage.face("audience")  # a new show starts facing the audience (and at the stage pose)
    stage.idle()  # ...and breathing: the arms are never dead still
    if show:  # act 0: silent until the first cue
        runner = ShowRunner(session, agent, script, stage, director, book,
                            on_sound=lambda kind, name: board.cue(kind, name, budget=False),
                            voice_key=f"{TTS_MODEL}/{voice}/host-only", room_name=ctx.room.name,
                            on_live=clock.arm)
        runner.register(ctx.room.local_participant)
        runner.start()
        ctx.add_shutdown_callback(runner.aclose)
        logger.info("show mode: %s, %d acts", script.show_name, len(script.acts))
        return
    session.generate_reply(instructions="Greet the listener and ask: a story, or a comedy show?")


def _listen_playout(room: rtc.Room) -> None:
    """The page measures its playout delay (WebRTC jitter buffer + network + audio output, index.html) and publishes
    it as participant attributes; gesture timing uses it (gestures.Timing.audio_s)."""
    def update(changed: dict, participant: rtc.Participant) -> None:
        attrs = participant.attributes or {}
        try:
            if "playout_ms" in attrs:
                gestures_mod.TIMING.audio_s = max(0.0, min(1.5, float(attrs["playout_ms"]) / 1000))
                gestures_mod.TIMING.measured = True
            if attrs.get("playout_bg_ms"):
                gestures_mod.TIMING.sound_s = max(0.0, min(1.5, float(attrs["playout_bg_ms"]) / 1000))
        except ValueError:
            return
        if "playout_ms" in changed:
            logger.info("listener playout delay: voice %.0f ms, sounds %s ms", gestures_mod.TIMING.audio_s * 1000,
                        attrs.get("playout_bg_ms", "?"))
    room.on("participant_attributes_changed", update)


if __name__ == "__main__":
    cli.run_app(server)
