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
import os
import re
from collections.abc import AsyncIterable, AsyncIterator, Callable
from dataclasses import dataclass

import numpy as np
from livekit import rtc
from livekit.agents import utils
from livekit.agents.types import NOT_GIVEN
from livekit.agents.voice import io

from gestures import MAX_TAG_LEN, QUOTES_CLOSE, QUOTES_OPEN, parse_tag, talk_arm

logger = logging.getLogger("storyteller.voices")

# Two narrators: the story mode's is an unnamed storyteller, the show's is Lars, the presenter (owner's picks, Nov 7).
STORY_VOICE = "72324f5951924b2eb27faaf5630153b4"  # "David - British Storyteller": the storyteller
HOST_VOICE = "d657aa381ad444e393d7f6ff0f8cc2f0"   # "Deadpool better": Lars hosts the show (its only voice)


@dataclass(frozen=True)
class Voice:
    id: str  # Fish Audio voice (reference) id
    about: str  # what the LLM is told when it casts


PALETTE: dict[str, Voice] = {
    "deep": Voice("5becb4c89d67471f96137c91b22e02c6", "very deep, raspy male: dragons, giants, kings, bears"),
    "squeaky": Voice("396be6334bcc41f68af3070e595aee76", "high, squeaky chipmunk voice: mice, birds, fairies, bugs"),
    "old": Voice("f7af7c05cc6042d8a8f90be8bb233044", "old, crackly, raspy goblin: wizards, grandpas, trolls"),
    "child": Voice("a3d56467a193409984ce245f8f371c9a", "bright, bouncy child: kids, puppies, little heroes"),
    "witch": Voice("d056e4cabd094165b66a1acd65e00cc0", "menacing, dramatic British woman: witches, wicked queens"),
    "pirate": Voice("4d78f878f023424cb77975a6dea180a6", "gruff old sea dog: pirates, sailors, scallywags, captains"),
}
# Near misses for voices that are gone (the robot voice sounded awful, owner Nov 7): the closest one left.
ALIASES = {"robot": "deep", "robotic": "deep", "machine": "deep", "alien": "deep", "android": "deep",
           "cyborg": "deep", "computer": "deep"}
DEFAULT_CAST = {"left": "deep", "right": "squeaky"}  # until the story casts its own

# Playout gain per Fish voice id, in dB. Out of the TTS the voices are up to 12 LU apart (squeaky the quietest).
# Measured Nov 7 night: the mean integrated loudness of three takes per voice (two identical lines and the round-2
# casting sample); the storyteller's -17.4 LUFS is the reference. The owner kept only the witch's boost and the
# child's cut (round 4, Nov 8): the other boosts are back at 0 dB, so those voices play as the TTS sends them. A soft
# knee keeps a boosted peak from clipping (`apply_gain`).
VOICE_GAIN_DB: dict[str, float] = {
    STORY_VOICE: 0.0,             # -17.4 LUFS: the reference
    HOST_VOICE: 0.0,              # Lars, -23.1 (a +5.5 boost was declined)
    PALETTE["deep"].id: 0.0,      # -20.7 (+3.5 declined)
    PALETTE["squeaky"].id: 0.0,   # -29.3 (+12 declined)
    PALETTE["old"].id: 0.0,       # -23.2 (+6 declined)
    PALETTE["child"].id: -1.0,    # -16.5
    PALETTE["witch"].id: 5.5,     # -23.0
    PALETTE["pirate"].id: 0.0,    # -23.2 (+6 declined)
}
VOICE_GAIN = os.getenv("VOICE_GAIN", "1") == "1"  # 0: every voice as the TTS sends it
_KNEE, _CEIL = 0.8, 0.94  # of full scale: samples past the knee bend smoothly toward the ceiling (-0.5 dBFS)


def voice_gain(voice_id: str) -> float:
    """The linear playout gain for a Fish voice id (1.0 for an unmeasured voice, or with VOICE_GAIN=0)."""
    return 10 ** (VOICE_GAIN_DB.get(voice_id, 0.0) / 20) if VOICE_GAIN else 1.0


def apply_gain(frame: rtc.AudioFrame, gain: float) -> rtc.AudioFrame:
    """`frame` scaled by `gain`; samples past `_KNEE` of full scale bend toward `_CEIL`, never clip."""
    if gain == 1.0:
        return frame
    x = np.frombuffer(frame.data, dtype=np.int16).astype(np.float32) * (gain / 32768.0)
    over = np.abs(x) > _KNEE
    if over.any():
        a = np.abs(x[over])
        x[over] = np.sign(x[over]) * (_KNEE + (_CEIL - _KNEE) * np.tanh((a - _KNEE) / (_CEIL - _KNEE)))
    out = np.clip(np.round(x * 32768.0), -32768, 32767).astype(np.int16)
    return rtc.AudioFrame(out.tobytes(), frame.sample_rate, frame.num_channels, frame.samples_per_channel,
                          userdata=dict(frame.userdata))


async def with_gain(frames: AsyncIterable[rtc.AudioFrame], gain: float) -> AsyncIterator[rtc.AudioFrame]:
    async for frame in frames:
        yield apply_gain(frame, gain)
VOICE_TAG = "voice"
_CAST_TAG = re.compile(r"^\[\s*voice\s*:\s*(left|right)\s*[=:]\s*([A-Za-z][A-Za-z _-]*?)\s*\]$", re.IGNORECASE)


def resolve_voice(name: str) -> str | None:
    """A palette name, a near miss (`squeak`, `robotic`) or a word in the description (`dragon`) -> palette name."""
    name = re.sub(r"[\s-]+", "_", name.strip().lower())
    if name in PALETTE:
        return name
    if name in ALIASES:
        return ALIASES[name]
    if close := difflib.get_close_matches(name, PALETTE, n=1, cutoff=0.6):
        return close[0]
    for word in (w for w in name.split("_") if len(w) >= 3):
        if word in ALIASES:
            return ALIASES[word]
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
    Time-stamping the transcript at each run start pins them to the audio at every switch.

    `times[k]` is known once runs 0..k-1 are synthesised (their lengths add up), which is well before they have
    played: synthesis runs ahead of playout. (It used to be known only once run k-1 had *played*: a run longer than
    the wait, like a minute of written laughter, timed out and left the rest of the reply untimed, so nothing fired
    for 72 s and then everything at once, Nov 7.)"""

    WAIT_S = 30.0  # never hold the transcript longer than this for one mark (a stuck TTS must not stall gestures)

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

    async def until(self, ready: Callable[[], bool], what: str = "a mark") -> bool:
        try:
            async with asyncio.timeout(self.WAIT_S):
                while not ready() and not self.closed:
                    await self._changed.wait()
        except TimeoutError:  # this one goes on untimed; the next ones are still waited for
            logger.warning("voice run marks late: no %s after %.0f s; that stretch goes on untimed", what, self.WAIT_S)
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
                    if await marks.until(lambda: len(marks.times) > run, f"start time for voice run {run}"):
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
        if await marks.until(lambda: marks.total is not None, "end time for the reply"):
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
    marks: RunMarks | None = None, locked: set[str] | None = None,
) -> AsyncIterator[rtc.AudioFrame]:
    """Split `text` into voice runs; synthesise every run as soon as it starts (all in parallel), and yield the
    audio in order. `narrate` is the narrator's synthesis, `speak(palette_name)` a character's; `cast` maps an
    arm to its palette name and is updated by `[voice:...]` tags. `marks` gets where each run starts. `locked`: the
    arms already cast in this story; a recast of one is dropped (two characters per story, owner Nov 7)."""
    splitter = VoiceSplitter()
    marks = marks or RunMarks()
    runs = utils.aio.Chan[tuple[str, utils.aio.Chan[rtc.AudioFrame]]]()
    tasks: list[asyncio.Task] = []

    durations: list[float | None] = []  # each run's audio, once synthesised
    split_done = False

    def settle() -> None:
        """times[k] = the audio before run k, as soon as every run before it is synthesised."""
        while len(marks.times) < len(durations) and None not in durations[: len(marks.times)]:
            marks.times.append(sum(durations[: len(marks.times)]))
        if split_done and None not in durations and marks.total is None:
            marks.total = sum(durations)
        marks.changed()

    async def synthesise(k: int, synth: Synth, text_ch: utils.aio.Chan[str], frames: utils.aio.Chan[rtc.AudioFrame]):
        length = 0.0
        try:
            async for frame in synth(text_ch):
                length += frame.duration
                frames.send_nowait(frame)
        except Exception:
            logger.exception("voice run failed")
        finally:
            frames.close()
            durations[k] = length
            settle()

    async def split() -> None:
        current: tuple[str | None, utils.aio.Chan[str]] | None = None

        def take(items: list) -> None:
            nonlocal current
            for item in items:
                if isinstance(item, Cast):
                    if locked is not None and item.arm in locked:
                        if cast.get(item.arm) != item.voice:
                            logger.info("dropped recast: the %s arm keeps the %s voice until a new story",
                                        item.arm, cast.get(item.arm))
                        continue
                    logger.info("cast: %s arm speaks in the %s voice", item.arm, item.voice)
                    cast[item.arm] = item.voice
                    if locked is not None:
                        locked.add(item.arm)
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
                    durations.append(None)
                    tasks.append(asyncio.create_task(synthesise(len(durations) - 1, synth, text_ch, frames)))
                    runs.send_nowait((voice or "narrator", frames))
                    marks.starts.append(marks.letters)
                    settle()
                    current = (voice, text_ch)
                    text_ch.send_nowait(piece)
                marks.letters += _letters(piece)
            marks.changed()

        nonlocal split_done
        try:
            async for chunk in text:
                take(splitter.feed(chunk))
            take(splitter.flush())
        finally:
            if current:
                current[1].close()
            runs.close()
            split_done = True
            settle()

    splitting = asyncio.create_task(split())
    try:
        async for voice, frames in runs:
            async for frame in frames:
                frame.userdata["voice"] = voice  # for logs and the sample renderer
                yield frame
        await splitting
        settle()
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


# --- what the voice must never read out ------------------------------------------------------------------------
_LAUGH_TOKEN = r"(?:(?:[mb]w)?a?(?:h+[aeiou]+){2,}h*|hah?)"  # ha, haha, HAHAHA, heheh, mwahaha ("he" is a word)
_LAUGH_RUN = re.compile(rf"\b{_LAUGH_TOKEN}\b(?:[\s,!.…-]+\b{_LAUGH_TOKEN}\b)*([!.…]*)", re.I)
_LAUGH_SYLLABLE = re.compile(r"h+[aeiou]+", re.I)
_DIRECTION = re.compile(r"\*[^*\n]{1,60}\*|\((?=[^()]*\b(?:laugh|chuckl|giggl|snort|snicker|guffaw|cackl|wheez|sigh)"
                        r")[^()]{0,60}\)", re.I)
_MARKUP = re.compile(r"(\[[^\]]{0,%d}\]|<[^>]{0,80}>)" % MAX_TAG_LEN)
QUOTE_WORDS = 25       # a character's line is cut at the first sentence end after this many words...
QUOTE_HARD_WORDS = 40  # ...or here, whatever


def _calm_laughs(text: str) -> str:
    """Runs of written laughter (three syllables or more) -> "ha ha"; stage directions (*laughs*, (chuckles)) go."""
    def laugh(m: re.Match) -> str:
        if len(_LAUGH_SYLLABLE.findall(m.group(0))) < 3:
            return m.group(0)
        return "ha ha" + (m.group(1)[:1] or "")
    parts = _MARKUP.split(text)  # tags and expressive markup pass untouched
    return "".join(p if n % 2 else _LAUGH_RUN.sub(laugh, _DIRECTION.sub("", p)) for n, p in enumerate(parts))


class Tidy:
    """Streaming: the LLM's text as it should be spoken. Written laughter collapses to "ha ha" (a minute of TTS
    reading "HAHAHA..." stalled a comedy set, Nov 7), stage directions go, and a quoted line is cut at a sentence end
    after `QUOTE_WORDS` words (or at `QUOTE_HARD_WORDS`), skipping to its closing quote. Holds back only an unfinished
    word, tag, direction or laugh."""

    def __init__(self) -> None:
        self._buf = ""
        self._quoted = False
        self._words = 0
        self._skip = False
        self._in_word = False
        self._markup = ""  # "[" or "<" while inside one

    def feed(self, text: str) -> str:
        self._buf += text
        cut = self._safe(self._buf)
        ready, self._buf = self._buf[:cut], self._buf[cut:]
        return self._quotes(_calm_laughs(ready))

    def flush(self) -> str:
        ready, self._buf = self._buf, ""
        return self._quotes(_calm_laughs(ready))

    @staticmethod
    def _safe(buf: str) -> int:
        """How much of buf can go now: up to its last space, but not into an open tag, markup or direction, nor a
        laugh that may go on."""
        if len(buf) > 600:
            return len(buf)
        cut = max(buf.rfind(" "), buf.rfind("\n")) + 1
        for open_, close in (("[", "]"), ("<", ">"), ("*", "*"), ("(", ")")):
            i = buf.rfind(open_, 0, cut)
            if i >= 0 and buf.find(close, i + 1, cut) < 0 and (open_ != "*" or buf.count("*", 0, cut) % 2):
                cut = min(cut, i)
        if m := re.search(rf"(?:\b{_LAUGH_TOKEN}\b[\s,!.…-]*)+$", buf[:cut], re.I):
            cut = m.start()
        return cut

    def _quotes(self, text: str) -> str:
        out = []
        for c in text:
            if self._markup:
                self._markup = "" if c == ("]" if self._markup == "[" else ">") else self._markup
                out.append(c)
                continue
            if c in "[<":
                self._markup = c
                out.append(c)
                continue
            if not self._quoted and c in QUOTES_OPEN:
                self._quoted, self._words, self._skip = True, 0, False
            elif self._quoted and c in QUOTES_CLOSE:
                self._quoted, self._skip = False, False
            elif self._skip:
                continue
            elif self._quoted:
                if c.isalnum() and not self._in_word:
                    self._words += 1
                    if self._words > QUOTE_HARD_WORDS:
                        self._skip = True
                        logger.info("a character's line ran past %d words: cut", QUOTE_HARD_WORDS)
                        out.append("…")
                        continue
                if c in ".!?…" and self._words >= QUOTE_WORDS:
                    out.append(c)
                    self._skip = True
                    logger.info("a character's line ran past %d words: cut at its sentence end", QUOTE_WORDS)
                    continue
            self._in_word = c.isalnum() or (self._in_word and c in "'’-")
            out.append(c)
        return "".join(out)


async def tidy_speech(text: AsyncIterable[str]) -> AsyncIterator[str]:
    tidy = Tidy()
    async for chunk in text:
        if out := tidy.feed(chunk):
            yield out
    if out := tidy.flush():
        yield out
