"""Character voices: the narrator speaks in one voice, and each arm's quoted lines in that character's own voice.

The LLM text reaches `Storyteller.tts_node` with its tags still in it. `VoiceSplitter` cuts it into runs of one
voice: a `[talk:left]` tag hands the left character the text from its opening quote to its closing quote
(the same span the arm's mouth flaps for, see gestures.GestureSink); everything else is narration. Each run is
synthesised by its own TTS stream, started as soon as the run's first words arrive, so a character's line is
already synthesising while the narration before it plays (a cold switch would cost the TTS's ~1-1.5 s
first-byte time). `[voice:left=deep]` at casting picks a palette voice for an arm; the default is
`DEFAULT_CAST`. All voices are public Fish Audio voices, reached through LiveKit Inference.
"""
from __future__ import annotations

import asyncio
import difflib
import logging
import re
from collections.abc import AsyncIterable, AsyncIterator, Callable
from dataclasses import dataclass

from livekit import rtc
from livekit.agents import utils
from livekit.agents.types import NOT_GIVEN
from livekit.agents.voice import io

from gestures import MAX_TAG_LEN, QUOTES_CLOSE, QUOTES_OPEN, parse_tag, talk_arm

logger = logging.getLogger("storyteller.voices")

NARRATOR_VOICE = "532f7f4adb32420fa4f5c95e9b13be9d"  # Lars the host: "frog", warm and enthusiastic (owner pick; Laura was e3cd3841...)


@dataclass(frozen=True)
class Voice:
    id: str  # Fish Audio voice (reference) id
    about: str  # what the LLM is told when it casts


PALETTE: dict[str, Voice] = {
    "deep": Voice("5becb4c89d67471f96137c91b22e02c6", "very deep, raspy male: dragons, giants, kings, bears"),
    "squeaky": Voice("d7cdeff76ac94ee6bd23d39e019ad72a", "high, squeaky cartoon voice: mice, birds, fairies, bugs"),
    "old": Voice("f7af7c05cc6042d8a8f90be8bb233044", "old, crackly, raspy goblin: wizards, grandpas, trolls"),
    "child": Voice("4fcb3a423c61415fb35604eba567d95f", "bright, bouncy child: kids, puppies, little heroes"),
    "witch": Voice("d056e4cabd094165b66a1acd65e00cc0", "menacing, dramatic British woman: witches, wicked queens"),
    "robot": Voice("175d7d5358b644d2ad2e8b7e4e13ece3", "flat, distorted robot: robots, machines, aliens"),
}
DEFAULT_CAST = {"left": "deep", "right": "squeaky"}  # until the story casts its own
VOICE_TAG = "voice"
_CAST_TAG = re.compile(r"^\[\s*voice\s*:\s*(left|right)\s*[=:]\s*([A-Za-z][A-Za-z _-]*?)\s*\]$", re.IGNORECASE)


def resolve_voice(name: str) -> str | None:
    """A palette name, a near miss (`squeak`, `robotic`) or a word in the description (`dragon`) -> palette name."""
    name = re.sub(r"[\s-]+", "_", name.strip().lower())
    if name in PALETTE:
        return name
    if close := difflib.get_close_matches(name, PALETTE, n=1, cutoff=0.6):
        return close[0]
    for word in (w for w in name.split("_") if len(w) >= 3):
        for key, voice in PALETTE.items():
            if re.search(rf"\b{re.escape(word)}", voice.about):
                return key
    return None


def describe_palette() -> str:
    return "\n".join(f"- {name}: {v.about}" for name, v in PALETTE.items())


@dataclass(frozen=True)
class Cast:
    arm: str
    voice: str  # palette name


class VoiceSplitter:
    """Streaming: feed LLM text chunks, get `(arm or None, text)` runs and `Cast`s back, tags removed.

    A run's arm is the character speaking it (`[talk:left]` up to the closing quote) or None for narration.
    Tags are dropped from the text like `gestures.strip_tags_for_tts` does; quotes inside `<...>` expressive
    markup don't count."""

    def __init__(self) -> None:
        self._buf = ""
        self._talker: str | None = None
        self._quoted = False
        self._markup = False

    def feed(self, text: str) -> list[tuple[str | None, str] | Cast]:
        self._buf += text
        out: list[tuple[str | None, str] | Cast] = []
        buf, i = self._buf, 0
        while i < len(buf):
            c = buf[i]
            if c == "[" and not self._markup:
                end = buf.find("]", i)
                if end < 0 and len(buf) - i <= MAX_TAG_LEN:
                    break  # wait for the rest of the tag
                if 0 <= end and end - i <= MAX_TAG_LEN:
                    self._tag(buf[i : end + 1], out)
                    i = end + 1
                    continue
            voice = self._talker if self._quoted else None
            if c == "<":
                self._markup = True
            elif c == ">":
                self._markup = False
            elif self._talker and not self._markup:
                if not self._quoted and c in QUOTES_OPEN:
                    self._quoted, voice = True, self._talker
                elif self._quoted and c in QUOTES_CLOSE:
                    self._talker, self._quoted = None, False
            self._emit(out, voice, c)
            i += 1
        self._buf = buf[i:]
        return out

    def flush(self) -> list[tuple[str | None, str] | Cast]:
        rest, self._buf = self._buf, ""
        out: list[tuple[str | None, str] | Cast] = []
        if rest:
            self._emit(out, self._talker if self._quoted else None, rest)
        return out

    def _tag(self, raw: str, out: list) -> None:
        if m := _CAST_TAG.match(raw):
            if voice := resolve_voice(m.group(2)):
                out.append(Cast(m.group(1).lower(), voice))
            else:
                logger.info("dropped %s: unknown voice", raw)
        elif (tag := parse_tag(raw)) and (arm := talk_arm(tag)):
            self._talker, self._quoted = arm, False
        # any other short [...] is a gesture or markup: never spoken

    @staticmethod
    def _emit(out: list, voice: str | None, text: str) -> None:
        if out and isinstance(out[-1], tuple) and out[-1][0] == voice:
            out[-1] = (voice, out[-1][1] + text)
        else:
            out.append((voice, text))


Synth = Callable[[AsyncIterable[str]], AsyncIterable[rtc.AudioFrame]]


def _letters(text: str) -> int:
    return sum(c.isalnum() for c in text)


class RunMarks:
    """Where one reply's voice runs start, shared by its tts node (which writes) and its transcription node (which
    reads): `starts[k]` counts the spoken letters before run k, `times[k]` the seconds of audio before it.

    The transcript synchronizer paces words at a fixed syllable rate until a reply's audio is complete, so with
    voices that speak at very different speeds the mouths and gestures drifted by seconds (measured up to 3 s).
    Time-stamping the transcript at each run start pins them to the audio at every switch."""

    WAIT_S = 8.0  # never hold the transcript longer than this for a mark (a stuck TTS must not stall gestures)

    def __init__(self) -> None:
        self.starts: list[int] = []
        self.times: list[float] = []
        self.letters = 0  # letters the tts node has split so far
        self.total: float | None = None  # all audio, once synthesised
        self.closed = False
        self._changed = asyncio.Event()

    def changed(self) -> None:
        self._changed.set()
        self._changed = asyncio.Event()

    def close(self) -> None:
        self.closed = True
        self.changed()

    async def until(self, ready: Callable[[], bool]) -> bool:
        try:
            async with asyncio.timeout(self.WAIT_S):
                while not ready() and not self.closed:
                    await self._changed.wait()
        except TimeoutError:
            logger.warning("voice run marks late: transcript goes on untimed")
            self.close()
        return ready()


async def timestamp_runs(text: AsyncIterable[str], find_marks: Callable[[], RunMarks | None]) -> AsyncIterator[str]:
    """Transcription transform: start each voice run's text with a `TimedString` carrying the run's audio start
    (and end the reply with its total), so the synchronizer releases it, tags included, when its audio plays.
    Letters inside `[...]` tags don't count (the tts node never sees them). `find_marks()` is asked once the
    first text arrives (the tts node has started by then); None (no audio, e.g. console --text) passes through."""
    marks: RunMarks | None = None
    letters, run, in_tag, first = 0, 0, False, True
    held: str | None = None  # the last piece: it gets the reply's end time

    async for chunk in text:
        if first:
            marks, first = find_marks(), False
        if marks is None:
            yield chunk
            continue
        # every piece is a TimedString: the synchronizer counts only TimedString text between two marks, so a
        # plain str would leave it short of the text it was given and slow every word after it down
        pieces: list[io.TimedString] = []
        cut, start = 0, NOT_GIVEN
        for i, c in enumerate(chunk):
            if c == "[":
                in_tag = True
            elif c == "]":
                in_tag = False
            elif not in_tag and c.isalnum():
                if letters >= marks.letters and not marks.closed:
                    await marks.until(lambda: marks.letters > letters)
                if run < len(marks.starts) and letters >= marks.starts[run]:
                    if await marks.until(lambda: len(marks.times) > run):
                        t, at = marks.times[run], _span_start(chunk, cut, i)
                        if at > cut:
                            pieces.append(io.TimedString(chunk[cut:at], start_time=start))
                        if i > at:  # the tags and quote leading into the run take no time: they fire as it starts
                            pieces.append(io.TimedString(chunk[at:i], start_time=t, end_time=t))
                            cut, start = i, NOT_GIVEN
                        else:
                            cut, start = i, t
                    run += 1
                letters += 1
        if cut < len(chunk):
            pieces.append(io.TimedString(chunk[cut:], start_time=start))
        for piece in pieces:
            if held is not None:
                yield held
            held = piece
    if held is not None and marks is not None:
        if await marks.until(lambda: marks.total is not None):
            held = io.TimedString(held, start_time=getattr(held, "start_time", NOT_GIVEN), end_time=marks.total)
        yield held


def _span_start(chunk: str, lo: int, i: int) -> int:
    """Back up from the run's first letter over the tags, quotes and spaces just before it: the synchronizer
    paces by characters, so a `[talk:left] "` left at the end of the run before would fire ~0.7 s early."""
    while i > lo:
        c = chunk[i - 1]
        if c.isspace() or c in QUOTES_OPEN or c in QUOTES_CLOSE:
            i -= 1
        elif c == "]" and (j := chunk.rfind("[", lo, i - 1)) >= 0:
            i = j
        else:
            break
    return i


async def voiced_tts(
    text: AsyncIterable[str], narrate: Synth, speak: Callable[[str], Synth], cast: dict[str, str],
    marks: RunMarks | None = None,
) -> AsyncIterator[rtc.AudioFrame]:
    """Split `text` into voice runs; synthesise every run as soon as it starts (all in parallel), and yield the
    audio in order. `narrate` is the narrator's synthesis, `speak(palette_name)` a character's; `cast` maps an
    arm to its palette name and is updated by `[voice:...]` tags. `marks` gets where each run starts."""
    splitter = VoiceSplitter()
    marks = marks or RunMarks()
    runs = utils.aio.Chan[tuple[str, utils.aio.Chan[rtc.AudioFrame]]]()
    tasks: list[asyncio.Task] = []

    async def synthesise(synth: Synth, text_ch: utils.aio.Chan[str], frames: utils.aio.Chan[rtc.AudioFrame]):
        try:
            async for frame in synth(text_ch):
                frames.send_nowait(frame)
        except Exception:
            logger.exception("voice run failed")
        finally:
            frames.close()

    async def split() -> None:
        current: tuple[str | None, utils.aio.Chan[str]] | None = None

        def take(items: list) -> None:
            nonlocal current
            for item in items:
                if isinstance(item, Cast):
                    logger.info("cast: %s arm speaks in the %s voice", item.arm, item.voice)
                    cast[item.arm] = item.voice
                    continue
                arm, piece = item
                voice = cast.get(arm) if arm else None
                if current and (current[0] == voice or not _letters(piece)):
                    current[1].send_nowait(piece)  # same voice, or just spaces/punctuation: stays in this run
                elif _letters(piece):  # (leading spaces before anything is said are dropped)
                    if current:
                        current[1].close()
                    text_ch, frames = utils.aio.Chan[str](), utils.aio.Chan[rtc.AudioFrame]()
                    synth = narrate if voice is None else speak(voice)
                    tasks.append(asyncio.create_task(synthesise(synth, text_ch, frames)))
                    runs.send_nowait((voice or "narrator", frames))
                    marks.starts.append(marks.letters)
                    current = (voice, text_ch)
                    text_ch.send_nowait(piece)
                marks.letters += _letters(piece)
            marks.changed()

        try:
            async for chunk in text:
                take(splitter.feed(chunk))
            take(splitter.flush())
        finally:
            if current:
                current[1].close()
            runs.close()

    splitting = asyncio.create_task(split())
    played = 0.0
    try:
        async for voice, frames in runs:
            marks.times.append(played)
            marks.changed()
            async for frame in frames:
                frame.userdata["voice"] = voice  # for logs and the sample renderer
                played += frame.duration
                yield frame
        await splitting
        marks.total = played
    finally:
        marks.close()
        await utils.aio.cancel_and_wait(splitting, *tasks)


async def stream_tts(tts, text: AsyncIterable[str], conn_options=None) -> AsyncIterator[rtc.AudioFrame]:
    """One streaming synthesis on `tts` (a LiveKit `tts.TTS` that streams, like `inference.TTS`)."""
    kw = {"conn_options": conn_options} if conn_options else {}
    async with tts.stream(**kw) as stream:
        async def forward() -> None:
            async for chunk in text:
                stream.push_text(chunk)
            stream.end_input()

        task = asyncio.create_task(forward())
        try:
            async for ev in stream:
                yield ev.frame
        finally:
            await utils.aio.cancel_and_wait(task)
