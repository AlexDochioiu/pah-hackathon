"""Gesture tags: parse them out of the narration, keep them out of the voice, and fire them on the arms
when the spoken words reach them.

The LLM writes `[left:slash]`, `[right:cower]`, `[both:bow]` inline, plus `[next]` at the end of a scene
it wants to carry on from, and `[face:each_other]` / `[face:audience]` to turn the two actors toward each other
or back to the audience (the stage server's /stance; gestures then play facing that way), and `[duet:cheers]` for
a two-arm duet (the stage server's /duet: both arms together, they touch). `[ensemble:storm]` makes both arms one
thing, action or mood for a beat (also /duet; the stage blends in from either stance and back). `[talk:left]` right before a character's
quoted line flaps that arm's gripper like a talking mouth (/talk) from the opening quote's line to its closing quote. Tags flow through the reply untouched except for two places:

- `strip_tags_for_tts`, a TTS text transform: the voice never sees a tag.
- `GestureSync`, a TranscriptSynchronizer wrapped around the session's outputs: it releases the reply's
  text in step with audio playout, and `GestureSink` downstream of it fires each tag as the playout
  reaches it. An interruption stops the release, so tags after the cut never fire.
"""
from __future__ import annotations

import asyncio
import difflib
import logging
import re
import time
from collections.abc import AsyncIterable, Callable
from dataclasses import dataclass

import aiohttp
from livekit.agents import tokenize
from livekit.agents.voice import io
from livekit.agents.voice.transcription import TranscriptSynchronizer

logger = logging.getLogger("storyteller.gestures")

ARMS = ("left", "right", "both")
DEFAULT_GESTURES: dict[str, float] = {  # name -> seconds, used when the stage server is down
    "wave": 2.0, "bow": 2.5, "nod": 1.0, "shake_head": 1.5, "slash": 1.2, "thrust": 1.2, "block": 1.0,
    "celebrate": 2.5, "cower": 2.0, "die_dramatically": 3.0, "gallop": 2.5, "roar": 2.0, "sneak": 2.5,
    "look_at_other": 1.5, "point_at_audience": 1.5, "gripper_clap": 1.5, "idle_breathe": 3.0,
}
# Words the LLM reaches for that aren't gesture names; mapped before fuzzy matching.
SYNONYMS = {
    "attack": "slash", "strike": "slash", "swing": "slash", "chop": "slash", "stab": "thrust",
    "lunge": "thrust", "poke": "thrust", "defend": "block", "parry": "block", "shield": "block",
    "cheer": "celebrate", "dance": "celebrate", "hooray": "celebrate", "yay": "celebrate", "clap": "gripper_clap",
    "hide": "cower", "tremble": "cower", "shiver": "cower", "scared": "cower", "die": "die_dramatically",
    "faint": "die_dramatically", "collapse": "die_dramatically", "ride": "gallop", "run": "gallop",
    "growl": "roar", "tiptoe": "sneak", "creep": "sneak", "hello": "wave", "goodbye": "wave", "yes": "nod",
    "no": "shake_head", "point": "point_at_audience", "look": "look_at_other", "breathe": "idle_breathe",
    "idle": "idle_breathe", "curtsy": "bow",
    # story verbs that went untagged in a 12-story sample (Nov 7), mapped to the gesture that looks most like them
    "spot": "look_at_other", "notice": "look_at_other", "stare": "look_at_other", "watch": "look_at_other",
    "grab": "thrust", "snatch": "thrust", "catch": "thrust", "push": "thrust", "shove": "thrust", "leap": "thrust",
    "pounce": "thrust", "throw": "slash", "toss": "slash", "hit": "slash", "punch": "slash", "smash": "slash",
    "jump": "celebrate", "win": "celebrate", "eat": "gripper_clap", "chomp": "gripper_clap", "bite": "gripper_clap",
    "munch": "gripper_clap", "laugh": "nod", "agree": "nod", "refuse": "shake_head", "shudder": "cower",
    "startle": "cower", "flinch": "cower", "fall": "die_dramatically", "trip": "die_dramatically",
    "chase": "gallop", "flee": "gallop", "race": "gallop", "prowl": "sneak", "patrol": "sneak", "grow": "roar",
    "shout": "roar", "thank": "bow",
}
CONTINUE_TAG = "next"
TALK_TAG = "talk"  # [talk:left] "..." -> POST /talk on, off at the closing quote
QUOTES_OPEN, QUOTES_CLOSE = '"“', '"”'
DUET_TAG = "duet"  # [duet:cross_swords] -> POST /duet
ENSEMBLE_TAG = "ensemble"  # [ensemble:storm] -> POST /duet too: the stage's ensemble-kind duets
DUET_MARGIN_S = 0.3  # the stage's blend into a gesture makes it a bit longer than its duration
FACE_TAG = "face"  # [face:each_other] / [face:audience] -> POST /stance
FACINGS = {"each_other": "each_other", "other": "each_other", "eachother": "each_other", "together": "each_other",
           "audience": "audience", "front": "audience", "us": "audience", "out": "audience", "forward": "audience"}
MAX_TAG_LEN = 48  # a "[" left open longer than this is prose, not a tag
_TAG_BODY = re.compile(r"^\s*([A-Za-z]+)\s*(?::\s*([A-Za-z][A-Za-z _-]*?)\s*)?$")


@dataclass(frozen=True)
class Tag:
    arm: str | None  # None for control tags such as [next]
    name: str
    raw: str


def parse_tag(raw: str) -> Tag | None:
    """`[left:slash]` -> Tag('left', 'slash'); `[next]` -> Tag(None, 'next'). Not a tag -> None."""
    m = _TAG_BODY.match(raw[1:-1])
    if not m:
        return None
    first, second = m.group(1).lower(), m.group(2)
    if second is None:
        return Tag(None, first, raw)
    return Tag(first, re.sub(r"[\s-]+", "_", second.strip().lower()), raw)


class TagParser:
    """Streaming splitter: feed text chunks, get back text pieces and Tags in order.

    A tag split across chunks is held until its `]` arrives. Any short `[...]` span counts as markup and is
    removed from the text; only well-formed ones come back as Tags."""

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, text: str) -> list[str | Tag]:
        self._buf += text
        out: list[str | Tag] = []
        while self._buf:
            start = self._buf.find("[")
            if start < 0:
                out.append(self._buf)
                self._buf = ""
                break
            if start:
                out.append(self._buf[:start])
                self._buf = self._buf[start:]
            end = self._buf.find("]")
            if end < 0:
                if len(self._buf) > MAX_TAG_LEN:  # never closed: prose
                    out.append(self._buf[0])
                    self._buf = self._buf[1:]
                    continue
                break  # wait for more text
            if end > MAX_TAG_LEN:
                out.append(self._buf[0])
                self._buf = self._buf[1:]
                continue
            raw, self._buf = self._buf[: end + 1], self._buf[end + 1 :]
            if tag := parse_tag(raw):
                out.append(tag)
        return [p for p in out if p != ""]

    def flush(self) -> list[str | Tag]:
        rest, self._buf = self._buf, ""
        return [rest] if rest else []


def strip_tags(text: str) -> str:
    parser = TagParser()
    return "".join(p for p in parser.feed(text) + parser.flush() if isinstance(p, str))


async def strip_tags_for_tts(text: AsyncIterable[str]) -> AsyncIterable[str]:
    """TTS text transform (AgentSession(tts_text_transforms=[...])): drop every tag before synthesis."""
    parser = TagParser()
    async for chunk in text:
        if clean := "".join(p for p in parser.feed(chunk) if isinstance(p, str)):
            yield clean
    if clean := "".join(p for p in parser.flush() if isinstance(p, str)):
        yield clean


_SENTENCE_END = ".!?…\"'”’)"


async def tags_first(text: AsyncIterable[str]) -> AsyncIterable[str]:
    """Transcription transform: move each sentence's gesture tags to the start of that sentence.

    The LLM tends to write a tag after the sentence it acts out, so played in place the gesture starts once the
    words are already spoken. A tag belongs to the sentence it ends or sits in; `[next]` stays at the end.
    ponytail: holds one sentence of text (the LLM runs well ahead of speech), so the transcript lags by at most that."""
    parser = TagParser()
    tags: list[str] = []
    ctrl: list[str] = []
    buf = ""

    def emit() -> str:
        nonlocal tags, ctrl, buf
        lead = buf[: len(buf) - len(buf.lstrip())]
        out = lead + "".join(t + " " for t in tags) + re.sub(r" {2,}", " ", buf[len(lead):]) + "".join(ctrl)
        tags, ctrl, buf = [], [], ""
        return out

    async for chunk in text:
        out = ""
        for piece in parser.feed(chunk):
            if isinstance(piece, Tag):
                if talk_arm(piece) is None:
                    (ctrl if piece.arm is None else tags).append(piece.raw)
                    continue
                # a talk tag stays where it is (just before its quote); a sentence may end right before it
                if buf[-1:].isspace() and buf.rstrip()[-1:] in _SENTENCE_END:
                    out += emit()
                buf += piece.raw
                continue
            for c in piece:
                if not c.isspace() and buf[-1:].isspace() and buf.rstrip()[-1:] in _SENTENCE_END:
                    out += emit()
                buf += c
        if out:
            yield out
    for piece in parser.flush():
        buf += piece if isinstance(piece, str) else piece.raw
    if rest := emit():
        yield rest


def talk_arm(tag: Tag) -> str | None:
    """`[talk:left]` (or `[left:talk]`) -> "left"; any other tag -> None."""
    if tag.arm == TALK_TAG and tag.name in ("left", "right"):
        return tag.name
    if tag.arm in ("left", "right") and tag.name == TALK_TAG:
        return tag.arm
    return None


def hyphenate_without_tags(word: str) -> list[str]:
    """Synchronizer pacing: a tag takes no speaking time."""
    word = re.sub(r"\[[^\]]{0,%d}\]" % MAX_TAG_LEN, "", word)
    return tokenize.basic.hyphenate_word(word) if word.strip() else []


class GestureBook:
    """The gestures the stage knows, and mapping of whatever the LLM wrote onto them."""

    def __init__(self, gestures: dict[str, float], duets: dict[str, float] | None = None,
                 ensembles: dict[str, float] | None = None, about: dict[str, str] | None = None) -> None:
        self.durations = dict(gestures)
        self.ensembles = dict(ensembles or {})  # name -> seconds; both arms as one thing
        self.duets = {**(duets or {}), **self.ensembles}  # name -> seconds; either tag plays any of them
        self.about = dict(about or {})  # name -> what it looks like (from the stage server)

    def resolve_duet(self, name: str) -> str | None:
        name = re.sub(r"[\s-]+", "_", name.strip().lower())
        close = difflib.get_close_matches(name, list(self.duets), n=1, cutoff=0.6)
        return name if name in self.duets else close[0] if close else None

    def _line(self, name: str, secs: float) -> str:
        """`- slash (1.2s): Wind up high... Use for: attack, strike, swing.` The verbs come from SYNONYMS, so the
        LLM sees which story actions each gesture can play."""
        verbs = [v for v, g in SYNONYMS.items() if g == name]
        line = f"- {name} ({secs:.1f}s)"
        if about := self.about.get(name, "").strip():
            line += f": {about}"
        if verbs:
            line += f" Use for: {', '.join(verbs)}."
        return line

    def describe_duets(self, ensembles: bool = False) -> str:
        return "\n".join(self._line(n, d) for n, d in self.duets.items()
                         if (n in self.ensembles) == ensembles) or "none today"

    @property
    def names(self) -> list[str]:
        return list(self.durations)

    def resolve(self, name: str) -> str | None:
        name = re.sub(r"[\s-]+", "_", name.strip().lower())
        if name in self.durations:
            return name
        if (syn := SYNONYMS.get(name)) and syn in self.durations:
            return syn
        close = difflib.get_close_matches(name, self.names, n=1, cutoff=0.6)
        if close:
            return close[0]
        for word in name.split("_"):  # "big_roar" -> roar, "sword_slash" -> slash
            if word in self.durations:
                return word
            if (syn := SYNONYMS.get(word)) and syn in self.durations:
                return syn
        return None

    def describe(self) -> str:
        return "\n".join(self._line(n, d) for n, d in self.durations.items() if n not in ("stage", "rest"))


class Stage:
    """Client for the stage server's HTTP API (robot/ owns the server; agent/fake_stage.py fakes it).

    Every call is fire-and-forget with a short timeout: a slow or dead stage never stalls the voice."""

    def __init__(self, url: str) -> None:
        self.url = url.rstrip("/")
        self.about: dict[str, str] = {}  # gesture/duet name -> description, filled by fetch_gestures/fetch_duets
        self._http: aiohttp.ClientSession | None = None
        self._tasks: set[asyncio.Task] = set()
        self._pending: asyncio.Task | None = None  # a duet waiting for the arms to be free
        self._order = asyncio.Lock()  # requests reach the stage in the order sent ([face:...] then [both:bow])
        self.busy_until = 0.0  # monotonic time the last fired gesture should be done

    def _session(self) -> aiohttp.ClientSession:
        if self._http is None or self._http.closed:
            self._http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2.0))
        return self._http

    async def fetch_gestures(self) -> dict[str, float] | None:
        try:
            async with self._session().get(f"{self.url}/gestures") as resp:
                resp.raise_for_status()
                data = await resp.json()
            gestures = {g["name"]: float(g.get("duration_s") or 1.5) for g in data if g.get("name")}
            self.about.update({g["name"]: str(g.get("about") or "") for g in data if g.get("name")})
            return gestures or None
        except Exception as e:  # noqa: BLE001 - any failure means "use the defaults"
            logger.warning("stage server at %s unreachable (%s); using the default gesture list", self.url, e)
            return None

    async def fetch_duets(self, kind: str = "duet") -> dict[str, float]:
        """The stage's duets of one kind: "duet" (two characters) or "ensemble" (both arms as one thing)."""
        try:
            async with self._session().get(f"{self.url}/duets") as resp:
                resp.raise_for_status()
                data = await resp.json()
                self.about.update({d["name"]: str(d.get("about") or "") for d in data if d.get("name")})
                return {d["name"]: float(d.get("duration_s") or 8.0) for d in data
                        if d.get("name") and d.get("kind", "duet") == kind}
        except Exception as e:  # noqa: BLE001 - no duets then
            logger.warning("no duets from the stage server (%s)", e)
            return {}

    async def _post(self, path: str, body: dict | None = None, delay: float = 0.0) -> None:
        if delay:
            await asyncio.sleep(delay)
        try:
            async with self._order, self._session().post(f"{self.url}{path}", json=body or {}) as resp:
                if resp.status >= 400:
                    logger.warning("stage %s %s -> %s %s", path, body or "", resp.status, await resp.text())
        except Exception as e:  # noqa: BLE001
            logger.warning("stage %s failed: %s", path, e)

    def send(self, path: str, body: dict | None = None, delay: float = 0.0) -> asyncio.Task:
        task = asyncio.create_task(self._post(path, body, delay))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def play(self, arm: str, gesture: str, duration_s: float, speed: float = 1.0) -> None:
        logger.info("gesture %s:%s", arm, gesture)
        if arm == DUET_TAG:  # needs both arms free: let the gestures already fired finish first
            now = time.monotonic()
            wait = self.busy_until + DUET_MARGIN_S - now if self.busy_until > now else 0.0
            self.busy_until = now + wait + duration_s / speed
            self._pending = self.send("/duet", {"name": gesture, "speed": speed}, delay=wait)
            return
        self.busy_until = max(self.busy_until, time.monotonic() + duration_s / speed)
        self.send("/play", {"arm": arm, "gesture": gesture, "speed": speed})

    def talk(self, arm: str, on: bool) -> None:
        logger.info("talk %s %s", arm, "on" if on else "off")
        self.send("/talk", {"arm": arm, "on": on})

    def face(self, stance: str) -> None:
        logger.info("face %s", stance)
        self.send("/stance", {"stance": stance})

    def freeze(self) -> None:
        self.busy_until = 0.0
        if self._pending:  # the listener interrupted: a duet still waiting never starts
            self._pending.cancel()
        self.send("/freeze")

    async def aclose(self) -> None:
        if self._tasks:
            await asyncio.wait(self._tasks, timeout=2.0)
        if self._http:
            await self._http.close()


class GestureSink(io.TextOutput):
    """Sits right after the transcript synchronizer: sees the reply's text as it is spoken, fires its
    gesture tags, and passes the text on without them."""

    def __init__(
        self,
        book: GestureBook,
        play: Callable[[str, str, float], None],
        on_continue: Callable[[], None],
        *,
        next_in_chain: io.TextOutput | None,
        on_face: Callable[[str], None] | None = None,
        on_talk: Callable[[str, bool], None] | None = None,
    ) -> None:
        super().__init__(label="GestureSink", next_in_chain=next_in_chain)
        self._book, self._play, self._on_continue, self._on_face = book, play, on_continue, on_face
        self._on_talk = on_talk
        self._parser = TagParser()
        self._talker: str | None = None  # the arm whose mouth is talking
        self._quoted = False               # its quote has opened

    def _talk(self, arm: str | None) -> None:
        if self._talker and self._on_talk:
            self._on_talk(self._talker, False)
        self._talker, self._quoted = arm, False
        if arm and self._on_talk:
            self._on_talk(arm, True)

    def _scan_quotes(self, text: str) -> None:
        """The talking mouth stops at its quote's closing mark (the text arrives as it is spoken)."""
        for c in text:
            if not self._talker:
                return
            if not self._quoted and c in QUOTES_OPEN:
                self._quoted = True
            elif self._quoted and c in QUOTES_CLOSE:
                self._talk(None)

    def _handle(self, pieces: list[str | Tag]) -> str:
        text = ""
        for p in pieces:
            if isinstance(p, str):
                text += p
                self._scan_quotes(p)
            elif (who := talk_arm(p)) is not None:
                self._talk(who)
            elif p.arm is None:
                if p.name == CONTINUE_TAG:
                    self._on_continue()
            elif p.arm == FACE_TAG:
                if (facing := FACINGS.get(p.name)) and self._on_face:
                    self._on_face(facing)
                else:
                    logger.info("dropped %s", p.raw)
            elif p.arm in (DUET_TAG, ENSEMBLE_TAG):
                if name := self._book.resolve_duet(p.name):
                    self._play(DUET_TAG, name, self._book.duets[name])
                else:
                    logger.info("dropped %s: unknown duet", p.raw)
            elif p.arm not in ARMS:
                logger.info("dropped %s: unknown arm", p.raw)
            elif (name := self._book.resolve(p.name)) is None:
                logger.info("dropped %s: unknown gesture", p.raw)
            else:
                self._play(p.arm, name, self._book.durations[name])
        return text

    async def capture_text(self, text: str) -> None:
        clean = self._handle(self._parser.feed(text))
        if clean and self.next_in_chain:
            await self.next_in_chain.capture_text(clean)

    def flush(self) -> None:
        # an interrupted segment flushes too; whatever is held back was never spoken, so drop its tags
        rest = "".join(p for p in self._parser.flush() if isinstance(p, str))
        self._talk(None)  # end of the reply, or an interruption: the mouth shuts
        if self.next_in_chain:
            if rest:
                asyncio.ensure_future(self.next_in_chain.capture_text(rest))
            self.next_in_chain.flush()


class GestureSync:
    """Wraps the session's audio + transcription outputs in our own TranscriptSynchronizer, whose text
    output feeds a GestureSink. Call `install()` after `session.start()` has set the outputs up."""

    def __init__(self, book: GestureBook, play: Callable[[str, str, float], None],
                 on_continue: Callable[[], None], on_face: Callable[[str], None] | None = None,
                 on_talk: Callable[[str, bool], None] | None = None) -> None:
        self._book, self._play, self._on_continue, self._on_face = book, play, on_continue, on_face
        self._on_talk = on_talk
        self.sync: TranscriptSynchronizer | None = None

    def install(self, output: io.AgentOutput) -> None:
        sink = GestureSink(self._book, self._play, self._on_continue, next_in_chain=output.transcription,
                           on_face=self._on_face, on_talk=self._on_talk)
        if output.audio is None:  # text-only (console --text): nothing to sync to, fire as the text streams
            logger.warning("no audio output: gestures fire as the text arrives, not synced to speech")
            output.transcription = sink
            return
        self.sync = TranscriptSynchronizer(
            next_in_chain_audio=output.audio, next_in_chain_text=sink, hyphenate_word=hyphenate_without_tags
        )
        output.audio = self.sync.audio_output
        output.transcription = self.sync.text_output
