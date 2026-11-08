"""Show mode: the scripted hackathon presentation (agent/show.md), one act per presenter-clicker cue.

The page (app.py /show) keeps the act cursor (`Cursor`) and sends `show_play {"act": n}` to the worker, where
`ShowRunner` cuts whatever is playing (arms freeze) and speaks the act through `session.say()`. `say()` runs the
same nodes an LLM reply does: the TTS text transforms and `tts_node` (tags never reach the voice; per-character
voices), `transcription_node` (`tags_first`) and the GestureSync outputs, so gestures, talk mouths, duets and
sounds fire in step with playout exactly as in the live story mode. The audio of every act is synthesized
ahead of time (act 1 first) and cached on disk, so a cue starts speaking without waiting for the TTS.

Only the `(live)` act lets the LLM speak afterwards; until then any LLM reply is interrupted at once.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import os
import re
import time
import wave
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gestures import PICK_TAG, PLACE_TAG, GestureBook, GestureSink, Tag, TagParser, strip_tags, talk_arm
from sounds import SFX_TAG, SOUND_TAGS

logger = logging.getLogger("storyteller.show")

HERE = Path(__file__).parent
SHOW_MD = HERE / "show.md"
CACHE_DIR = HERE / ".cache" / "show"  # pre-synthesized act audio (gitignored)
DEFAULTS = {"SHOW_NAME": "Stage Hands", "HOST_NAME": "Lars", "LEAD_PAUSE_S": "0.3"}
HUMAN, SCRIPTED, LIVE = "human", "scripted", "live"
# app.py hands cues to the worker here on localhost: about a quarter second quicker than an RPC through LiveKit
CUE_PORT = int(os.getenv("SHOW_CUE_PORT", "8792"))
WORDS_PER_S = 2.6  # the narrator's pace, for estimates before the audio exists
ON = ("on", "yes", "true", "1")

_ACT = re.compile(r"^Act\s+(\d+)\s*[:.\-–—]\s*(.+?)\s*$", re.I)
_KIND = re.compile(r"\s*\((human|live)\)\s*$", re.I)
_CHAIN = re.compile(r"^\s*[a-z]+:[a-z_]+(\s*\|\s*[a-z]+:[a-z_]+)*\s*$")
_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_COMMENT = re.compile(r"<!--(.*?)-->", re.S)


def is_show(metadata: str | None) -> bool:
    """The job's dispatch metadata (app.py /token?mode=show) asks for show mode."""
    try:
        return json.loads(metadata or "{}").get("mode") == "show"
    except (ValueError, AttributeError):
        return False


# --- the script ------------------------------------------------------------------------------------------------

def parse_config(text: str) -> dict[str, str]:
    """`KEY = value  # comment` lines; fences, blank lines and `#` lines are skipped."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = re.sub(r"(^|\s)#.*$", "", line).strip()
        if not line or line.startswith("`") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


def resolve_chain(value: str, known: Callable[[str, str], bool] | None) -> str:
    """`left:point_back_left | left:look_at_other` -> the first option the stage has, as a tag. Without a gesture
    list (the page) the first one; if the stage has none of them, the last one (GestureSink maps it fuzzily)."""
    options = [o.strip() for o in value.split("|") if o.strip()]
    for opt in options:
        arm, _, name = opt.partition(":")
        if known is None or known(arm, name):
            return f"[{opt}]"
    return f"[{options[-1]}]" if options else ""


def book_knows(book: GestureBook) -> Callable[[str, str], bool]:
    def known(arm: str, name: str) -> bool:
        if arm in (PICK_TAG, PLACE_TAG):  # [pick:lars]: a guest actor the stage has baked
            return book.guest(arm, name) is not None
        return name in (book.duets if arm in ("duet", "ensemble") else book.durations)
    return known


@dataclass
class Act:
    number: int
    title: str
    kind: str   # HUMAN | SCRIPTED | LIVE
    text: str   # tags inline, names filled in: what say() gets
    note: str = ""  # the <!-- comments -->, for the presenters

    @property
    def spoken(self) -> str:
        text = re.sub(r"\s+", " ", strip_tags(self.text))
        return re.sub(r" ([,.!?;:])", r"\1", text).strip()

    @property
    def first_line(self) -> str:
        """The teleprompter hint: the act's opening words, tags removed."""
        spoken = self.spoken
        if self.kind == HUMAN:
            return spoken
        out = ""
        for sentence in re.findall(r"[^.!?…]+[.!?…]+[\"”']?|\S.*$", spoken):
            out = f"{out} {sentence.strip()}".strip()
            if len(out) >= 40:
                break
        return out[:160]

    def split_lead(self) -> tuple[str, str]:
        """(the tags before the first word, the rest): the lead fires the moment the cue lands."""
        parser, lead, rest = TagParser(), [], ""
        pieces = parser.feed(self.text) + parser.flush()
        for i, p in enumerate(pieces):
            if isinstance(p, Tag) and talk_arm(p) is None:  # a talk tag belongs to its quote: it starts the rest
                lead.append(p)
            elif not isinstance(p, str) or p.strip():
                rest = "".join(x if isinstance(x, str) else x.raw for x in pieces[i:])
                break
        return " ".join(t.raw for t in lead), rest.strip()

    def lead_has_sound(self) -> bool:
        lead, _ = self.split_lead()
        return any(t.arm in SOUND_TAGS for t in TagParser().feed(lead) if isinstance(t, Tag))

    def estimate_s(self, durations: dict[str, float] | None = None, sfx: dict[str, float] | None = None) -> float:
        """Speech at WORDS_PER_S plus whatever the tags after the last word still play (the final bow, the
        applause). Duets after the last word run one after the other."""
        if self.kind == HUMAN:
            return round(len(self.spoken.split()) / WORDS_PER_S, 1)
        durations, sfx = durations or {}, sfx or {}
        speech = len(self.spoken.split()) / WORDS_PER_S
        parser = TagParser()
        pieces = parser.feed(self.text) + parser.flush()
        words = [i for i, p in enumerate(pieces) if isinstance(p, str) and re.search(r"\w", p)]
        tail = [p for p in pieces[words[-1] + 1:] if isinstance(p, Tag)] if words else []
        # gesture tags move to the start of the last sentence, so they get its speaking time back
        last_sentence = re.split(r"(?<=[.!?…])\s+", self.spoken)[-1]
        head_start = len(last_sentence.split()) / WORDS_PER_S
        duets = solos = sounds = 0.0
        for t in tail:
            if t.arm in ("duet", "ensemble"):
                duets += durations.get(t.name, 6.0)
            elif t.arm == SFX_TAG:
                sounds = max(sounds, sfx.get(t.name, 3.0))
            elif t.arm in ("left", "right", "both"):
                solos = max(solos, durations.get(t.name, 3.0))
        return round(speech + max(duets - head_start, solos - head_start, sounds, 0.0), 1)


@dataclass
class Script:
    config: dict[str, str]
    acts: list[Act]
    live_context: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def show_name(self) -> str:
        return self.config.get("SHOW_NAME") or DEFAULTS["SHOW_NAME"]

    @property
    def host_name(self) -> str:
        return self.config.get("HOST_NAME") or DEFAULTS["HOST_NAME"]

    @property
    def founders(self) -> list[str]:
        return [self.config[k].partition("@")[0].strip() for k in sorted(self.config) if k.startswith("FOUNDER_")]

    @property
    def lead_pause_s(self) -> float:
        try:
            return float(self.config.get("LEAD_PAUSE_S") or DEFAULTS["LEAD_PAUSE_S"])
        except ValueError:
            return float(DEFAULTS["LEAD_PAUSE_S"])

    @property
    def last(self) -> int:
        return max((a.number for a in self.acts), default=0)

    def act(self, number: int) -> Act | None:
        return next((a for a in self.acts if a.number == number), None)

    @classmethod
    def load(cls, path: Path = SHOW_MD, book: GestureBook | None = None) -> Script:
        return cls.parse(path.read_text(), book)

    @classmethod
    def parse(cls, text: str, book: GestureBook | None = None) -> Script:
        sections: list[tuple[str, str]] = []
        for chunk in re.split(r"^## ", text, flags=re.M)[1:]:
            head, _, body = chunk.partition("\n")
            sections.append((head.strip(), body))
        config = dict(DEFAULTS)
        for head, body in sections:
            if head.lower() == "config":
                config.update(parse_config(body))
        values, warnings = _values(config, book_knows(book) if book else None)
        acts, live = [], ""
        for head, body in sections:
            if m := _ACT.match(head):
                title, kind = m.group(2), SCRIPTED
                if k := _KIND.search(title):
                    title, kind = title[: k.start()].strip(), k.group(1).lower()
                note = " ".join(c.strip() for c in _COMMENT.findall(body))
                acts.append(Act(int(m.group(1)), _fill(title, values, config, warnings), kind,
                                _fill(_COMMENT.sub("", body), values, config, warnings),
                                _fill(note, values, config, warnings)))
            elif head.lower() == "live context":
                live = _fill(_COMMENT.sub("", body), values, config, warnings)
        acts.sort(key=lambda a: a.number)
        return cls(config, acts, live, warnings)

    def live_prompt(self) -> str:
        return f"\n\n# On stage now: the live demo\n\n{self.live_context}\n" if self.live_context else ""

    def to_json(self, durations: dict[str, float] | None = None) -> dict:
        return {"show_name": self.show_name, "host_name": self.host_name, "founders": self.founders,
                "warnings": self.warnings,
                "acts": [{"act": a.number, "title": a.title, "kind": a.kind, "first_line": a.first_line,
                          "note": a.note, "estimate_s": a.estimate_s(durations)} for a in self.acts]}


def _values(config: dict[str, str], known: Callable[[str, str], bool] | None) -> tuple[dict[str, str], list[str]]:
    """Placeholder -> text. Gesture chains become one tag; FOUNDER_n is the name, POINT_n the tag for their spot."""
    values: dict[str, str] = {}
    warnings: list[str] = []
    for key, value in config.items():
        values[key] = resolve_chain(value, known) if _CHAIN.match(value) else value
        if key.startswith("FOUNDER_"):
            name, _, spot = value.partition("@")
            values[key] = name.strip()
            n = key.removeprefix("FOUNDER_")
            chain = config.get(f"SPOT_{spot.strip()}", "")
            values[f"POINT_{n}"] = resolve_chain(chain, known) if chain else ""
            if spot.strip() and not chain:
                warnings.append(f"FOUNDER_{n}: no SPOT_{spot.strip()} in the config")
    return values, warnings


def _fill(text: str, values: dict[str, str], config: dict[str, str], warnings: list[str]) -> str:
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("```"):
            continue
        if m := re.match(r"^\{\?([A-Za-z_][A-Za-z0-9_]*)\}\s*", line):
            if config.get(m.group(1), "").lower() not in ON:
                continue
            line = line[m.end():]
        lines.append(line)

    def sub(m: re.Match) -> str:
        if m.group(1) in values:
            return values[m.group(1)]
        warnings.append(f"unknown placeholder {{{m.group(1)}}}")
        return ""

    return re.sub(r"\s+", " ", _PLACEHOLDER.sub(sub, " ".join(lines))).strip()


# --- the cursor (app.py keeps it, so it survives STOP, a new room and a page reload) -------------------------------

class Cursor:
    """Which act the show is on. Next plays the act after it, Prev plays the one before (Prev from act 1 goes back
    to act 0: nothing plays, ready), a number key jumps there. Each returns the act to play, or None (at the end)."""

    def __init__(self) -> None:
        self.act = 0

    def next(self, last: int) -> int | None:
        if self.act >= last:
            return None
        self.act += 1
        return self.act

    def prev(self) -> int:
        self.act = max(0, self.act - 1)
        return self.act

    def jump(self, n: int, last: int) -> int:
        self.act = max(0, min(n, last))
        return self.act


# --- the worker side ---------------------------------------------------------------------------------------------

class ShowRunner:
    """Plays the acts in the storyteller session (agent/storyteller.py creates it in show mode)."""

    FRAME_MS = 50  # cached audio is replayed in frames this long

    def __init__(self, session: Any, agent: Any, script: Script, stage: Any, director: Any, book: GestureBook, *,
                 on_sound: Callable[[str, str], None] | None = None, cache_dir: Path = CACHE_DIR,
                 voice_key: str = "", room_name: str = "") -> None:
        self.session, self.agent, self.script, self.stage, self.director = session, agent, script, stage, director
        self.cache_dir, self.voice_key, self.room_name = cache_dir, voice_key, room_name
        self._lead = GestureSink(book, stage.play, director.request_continue, next_in_chain=None,
                                 on_face=stage.face, on_talk=stage.talk, on_sound=on_sound)
        self.act = 0
        self.state = "ready"   # ready | playing | done | live
        self.live = False      # the LLM may speak (after the live act's text)
        self.audio: dict[int, list] = {}  # act -> pre-synthesized frames
        self.marks: dict[int, Any] = {}  # act -> its voice-run marks (voices.RunMarks), pinning gestures to the audio
        self.durations: dict[int, float] = {}
        self.latency_ms: int | None = None  # cue -> first voice frame, last cue
        self._participant: Any = None
        self._task: asyncio.Task | None = None
        self._prime: asyncio.Task | None = None
        self._local: asyncio.Task | None = None
        self._cue_at = 0.0
        self._gen = 0
        session.on("speech_created", self._on_speech)
        session.on("agent_state_changed", self._on_agent_state)

    # wiring
    def register(self, participant: Any) -> None:
        self._participant = participant
        participant.register_rpc_method("show_play", self._rpc)

    async def _rpc(self, data: Any) -> str:
        try:
            act = int(json.loads(getattr(data, "payload", "") or "{}").get("act", 0))
        except (ValueError, TypeError):
            act = 0
        self.play(act)
        return json.dumps(self.status())

    def start(self, cue_port: int | None = CUE_PORT) -> None:
        """Synthesize every act in the background (act 1 first; the page shows which are primed) and take cues
        from app.py on localhost."""
        self._prime = asyncio.create_task(self.prime())
        if cue_port:
            self._local = asyncio.create_task(self.serve_cues(cue_port))
        self._publish()

    async def serve_cues(self, port: int) -> None:
        """POST 127.0.0.1:<port>/play {"act", "room"}. Another room's cue gets 409, so app.py falls back to the
        RPC; while an old session still holds the port (it is closing), keep trying."""
        from aiohttp import web

        async def play(req: web.Request) -> web.Response:
            body = await req.json()
            if body.get("room") != self.room_name:
                return web.json_response({"error": "another room"}, status=409)
            self.play(int(body.get("act", 0)))
            return web.json_response(self.status())

        app = web.Application()
        app.add_routes([web.post("/play", play)])
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        try:
            while True:
                try:
                    await web.TCPSite(runner, "127.0.0.1", port, reuse_address=True).start()
                    logger.info("show: cues on 127.0.0.1:%d", port)
                    break
                except OSError:
                    await asyncio.sleep(2.0)
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()

    def status(self) -> dict:
        return {"act": self.act, "state": self.state, "primed": sorted(self.audio),
                "durations": self.durations, "latency_ms": self.latency_ms}

    def _publish(self) -> None:
        if self._participant is None:
            return
        s = self.status()
        attrs = {"show.act": str(s["act"]), "show.state": s["state"], "show.primed": ",".join(map(str, s["primed"])),
                 "show.durations": json.dumps({str(k): round(v, 1) for k, v in s["durations"].items()}),
                 "show.latency_ms": "" if s["latency_ms"] is None else str(s["latency_ms"])}
        try:
            res = self._participant.set_attributes(attrs)
            if inspect.isawaitable(res):
                asyncio.ensure_future(res)
        except Exception as e:  # noqa: BLE001 - the page just shows stale state
            logger.warning("show: could not publish state: %s", e)

    # playback
    def play(self, number: int) -> None:
        """A cue: cut whatever plays (arms freeze, voice stops, an LLM reply is dropped), then play the act."""
        self._cue_at = time.perf_counter()
        self._gen += 1
        self.latency_ms = None
        self._cut()
        act = self.script.act(number)
        self.act = number if act else 0
        self.live = False
        if act is None or act.kind == HUMAN:
            self.state = "ready"
            logger.info("show: act %d (%s): ready", self.act, act.title if act else "-")
            self._publish()
            return
        self.state = "playing"
        logger.info("show: act %d (%s)", act.number, act.title)
        self._publish()
        self._task = asyncio.create_task(self._run(act, self._gen))

    def _cut(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self.director.cut()  # freeze the arms, drop a pending next scene
        try:
            self.session.interrupt(force=True)
            self.session.clear_user_turn()
        except RuntimeError as e:  # the session is closing
            logger.info("show: interrupt: %s", e)

    async def _run(self, act: Act, gen: int) -> None:
        lead, rest = act.split_lead()
        if lead:  # the record scratch, the snap to the audience: now, not when the voice starts
            await self._lead.capture_text(lead)
            self._lead.flush()
            if rest and act.lead_has_sound():
                await asyncio.sleep(self.script.lead_pause_s)
        if act.kind == LIVE:
            self.live = True  # from here the LLM may answer (its replies queue behind this line)
        if not rest:
            self._finish(act, gen)
            return
        frames = self.audio.get(act.number)
        kwargs = {"audio": _replay(frames)} if frames else {}
        if not frames:
            logger.info("show: act %d not primed yet, streaming the TTS", act.number)
        handle = self.session.say(rest, add_to_chat_ctx=True, **kwargs)
        if frames and (marks := self.marks.get(act.number)) is not None:
            self.agent._marks[handle] = marks  # what the skipped tts_node would have left for transcription_node
        logger.info("show: act %d say() at +%d ms", act.number, (time.perf_counter() - self._cue_at) * 1000)
        await handle
        if gen == self._gen and not handle.interrupted:
            self._finish(act, gen)

    def _finish(self, act: Act, gen: int) -> None:
        if gen != self._gen:
            return
        self.state = "live" if act.kind == LIVE else "done"
        self._publish()

    def _on_speech(self, ev: Any) -> None:
        if getattr(ev, "source", "say") != "say" and not self.live:
            logger.info("show: dropped an LLM reply outside the live act")
            ev.speech_handle.interrupt(force=True)

    def _on_agent_state(self, ev: Any) -> None:
        if ev.new_state == "speaking" and self.state == "playing" and self.latency_ms is None:
            self.latency_ms = round((time.perf_counter() - self._cue_at) * 1000)
            logger.info("show: act %d voice started %d ms after the cue", self.act, self.latency_ms)
            self._publish()

    # pre-synthesis
    async def prime(self) -> None:
        acts = sorted((a for a in self.script.acts if a.kind != HUMAN and a.split_lead()[1]),
                      key=lambda a: (a.number != 1, a.number))
        for act in acts:
            try:
                frames, marks = await self._audio_for(act.split_lead()[1])
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - that act streams its TTS when cued
                logger.warning("show: could not pre-synthesize act %d: %s", act.number, e)
                continue
            if frames:
                self.audio[act.number] = frames
                if marks is not None:
                    self.marks[act.number] = marks
                self.durations[act.number] = sum(f.duration for f in frames)
                logger.info("show: act %d primed (%.1f s)", act.number, self.durations[act.number])
                self._publish()

    def cache_key(self, text: str) -> str:
        try:  # a new tts_node (per-character voices) changes the audio, so it changes the key too
            node = inspect.getsource(type(self.agent).tts_node)
        except (OSError, TypeError):
            node = ""
        transforms = [getattr(t, "__name__", str(t)) for t in self.session.options.tts_text_transforms or []]
        blob = json.dumps([text, self.voice_key, node, transforms])
        return hashlib.sha1(blob.encode()).hexdigest()[:16]

    async def _audio_for(self, text: str) -> tuple[list, Any]:
        path = self.cache_dir / f"{self.cache_key(text)}.wav"
        if path.exists():
            return _read_wav(path, self.FRAME_MS), _read_marks(path)
        frames, marks = await self.synthesize(text)
        try:
            _write_wav(path, frames)
            _write_marks(path, marks)
            return _read_wav(path, self.FRAME_MS), marks  # the same even frames as a cache hit
        except Exception as e:  # noqa: BLE001 - keep it in memory then
            logger.warning("show: could not cache %s: %s", path.name, e)
        return frames, marks

    async def synthesize(self, text: str) -> tuple[list, Any]:
        """The exact path say() takes: the session's TTS text transforms, then the agent's tts_node. Returns the
        frames and the voice-run marks the tts_node keyed by the current speech (here a stand-in for one)."""
        from livekit.agents.voice import ModelSettings
        from livekit.agents.voice.agent_activity import _SpeechHandleContextVar
        from livekit.agents.voice.transcription.text_transforms import _apply_text_transforms

        async def source() -> AsyncIterator[str]:
            yield text

        stream: Any = source()
        if transforms := self.session.options.tts_text_transforms:
            stream = _apply_text_transforms(stream, transforms)
        speech = _Synthesis()
        token = _SpeechHandleContextVar.set(speech)  # type: ignore[arg-type]
        try:
            node = self.agent.tts_node(stream, ModelSettings())
            if inspect.isawaitable(node):
                node = await node
            frames = [f async for f in node] if node is not None else []
        finally:
            _SpeechHandleContextVar.reset(token)
        marks = getattr(self.agent, "_marks", None)
        return frames, marks.get(speech) if marks is not None else None

    async def aclose(self) -> None:
        for task in (self._task, self._prime, self._local):
            if task and not task.done():
                task.cancel()


class _Synthesis:
    """Stands in for the speech handle while an act is synthesized ahead of time: storyteller's tts_node keys the
    reply's voice-run marks by it."""

    id = "show_synthesis"  # LiveKit tags the TTS metrics with the current speech's id


async def _replay(frames: list) -> AsyncIterator[Any]:
    for f in frames:
        yield f


def _write_wav(path: Path, frames: list) -> None:
    if not frames or len({(f.sample_rate, f.num_channels) for f in frames}) != 1:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(frames[0].num_channels)
        w.setsampwidth(2)
        w.setframerate(frames[0].sample_rate)
        for f in frames:
            w.writeframes(bytes(f.data))
    tmp.replace(path)


def _read_wav(path: Path, frame_ms: int) -> list:
    from livekit import rtc

    with wave.open(str(path), "rb") as w:
        rate, channels, data = w.getframerate(), w.getnchannels(), w.readframes(w.getnframes())
    step = rate * frame_ms // 1000 * channels * 2
    return [rtc.AudioFrame(data[i:i + step], rate, channels, len(data[i:i + step]) // (2 * channels))
            for i in range(0, len(data), step)]



def _write_marks(path: Path, marks: Any) -> None:
    if marks is not None and marks.total is not None:
        path.with_suffix(".json").write_text(json.dumps(
            {"starts": marks.starts, "times": marks.times, "letters": marks.letters, "total": marks.total}))


def _read_marks(path: Path) -> Any:
    from voices import RunMarks

    try:
        data = json.loads(path.with_suffix(".json").read_text())
    except (OSError, ValueError):
        return None
    marks = RunMarks()
    marks.starts, marks.times, marks.letters, marks.total = data["starts"], data["times"], data["letters"], data["total"]
    marks.closed = True  # complete: the transcript never waits on it
    return marks
