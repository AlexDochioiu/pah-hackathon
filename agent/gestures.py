"""Gesture tags: parse them out of the narration, keep them out of the voice, and fire them on the arms
when the spoken words reach them.

The LLM writes `[left:slash]`, `[right:cower]`, `[both:bow]` inline, plus `[next]` at the end of a scene
it wants to carry on from, and `[face:each_other]` / `[face:audience]` to turn the two actors toward each other
or back to the audience (the stage server's /stance; gestures then play facing that way), `[pick:lars]` /
`[place:lars]` to have the right arm pick up a prop (Lars the frog doll) from its dock and put it back (/play pick_lars on the
arm the stage lists for it), and `[duet:cheers]` for
a two-arm duet (the stage server's /duet: both arms together, they touch). `[ensemble:storm]` makes both arms one
thing, action or mood for a beat (also /duet; the stage blends in from either stance and back). `[talk:left]` right before a character's
quoted line flaps that arm's gripper like a talking mouth (/talk) from the opening quote's line to its closing quote. Tags flow through the reply untouched except for two places:

- the TTS: `voices.VoiceSplitter` (in `Storyteller.tts_node`) drops every tag, like `strip_tags_for_tts` does.
- `GestureSync`, a TranscriptSynchronizer wrapped around the session's outputs: it releases the reply's
  text in step with audio playout, and `GestureSink` downstream of it fires each tag as the playout
  reaches it. An interruption stops the release, so tags after the cut never fire.
"""
from __future__ import annotations

import asyncio
import difflib
import functools
import logging
import os
import re
import time
import weakref
from collections.abc import AsyncIterable, Callable
from dataclasses import dataclass

import aiohttp
from livekit.agents import tokenize
from livekit.agents.voice import io
from livekit.agents.voice.transcription import TranscriptSynchronizer

from sounds import SFX_TAG, SOUND_TAGS, SOUNDS_DIR  # [sfx:explosion], [music:spooky]: fired here, played by sounds.Soundboard

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
    "stare": "look_at_other", "watch": "look_at_other", "hit": "slash", "chase": "gallop", "flee": "gallop",
    "prowl": "sneak", "patrol": "sneak", "grow": "roar", "shout": "roar", "thank": "bow", "agree": "nod",
    "refuse": "shake_head", "shudder": "cower", "fall": "die_dramatically", "trip": "die_dramatically",
    # round 3 (Nov 7): table contact, stirring, Italian hands, face acting
    "dead": "die_dramatically", "revive": "revive", "wake_up": "revive", "come_back": "revive", "recover": "revive",
    "bang": "bang_table", "pound": "bang_table", "smash": "bang_table", "demand": "bang_table", "angry": "bang_table",
    "stir": "stir_lazy", "brew": "stir_lazy", "cook": "stir_lazy", "mix": "stir_lazy", "whisk": "stir_frantic",
    "hurry": "stir_frantic", "argue": "italian", "complain": "italian", "explain": "italian", "insist": "italian",
    "what": "italian", "mamma_mia": "italian_exasperated", "exasperated": "italian_exasperated",
    "give_up": "italian_exasperated", "perfect": "chef_kiss", "delicious": "chef_kiss", "chef": "chef_kiss",
    "bah": "flick", "dismiss": "flick", "whatever": "flick", "shoot": "shoot", "fire": "shoot", "blast": "shoot",
    "zap": "shoot", "search": "look_around", "spot": "double_take", "notice": "double_take",
    "surprise": "double_take", "startle": "startle", "flinch": "startle", "gasp": "startle", "scare": "startle",
    "leap": "pounce", "pounce": "pounce", "jump_out": "pounce", "duck": "duck", "take_cover": "duck",
    "dodge": "dodge", "swerve": "dodge", "weave": "dodge", "stomp": "stomp", "stamp": "stomp", "land": "stomp",
    "march": "stomp", "bite": "bite", "chomp": "bite", "snap": "bite", "eat": "eat", "munch": "eat", "chew": "eat",
    "laugh": "laugh", "giggle": "laugh", "sigh": "sigh", "bored": "sigh", "snore": "snore", "sleep": "snore",
    "yawn": "snore", "sneeze": "sneeze", "cough": "sneeze", "cry": "cry", "sob": "cry", "weep": "cry",
    "beat_chest": "beat_chest", "show_off": "beat_chest", "hop": "hop", "jump": "hop", "skip": "hop",
    "excited": "tremble_excited", "grab": "grab", "snatch": "grab", "catch": "grab", "steal": "grab",
    "pull": "pull", "drag": "pull", "tug": "pull", "push": "push", "shove": "push", "ram": "push",
    "lift": "lift_heavy", "heave": "lift_heavy", "carry": "lift_heavy", "throw": "throw", "toss": "throw",
    "hurl": "throw", "punch": "slash", "win": "celebrate",
    # duets and ensembles (resolve_duet)
    "high_five": "high_five", "celebrate_together": "high_five", "team_up": "high_five", "kiss": "kiss",
    "smooch": "kiss", "kissed": "kiss", "true_loves_kiss": "kiss", "true_love's_kiss": "kiss",
    "cauldron": "cauldron", "witches": "cauldron",
    "drum": "drum_roll", "drum_roll": "drum_roll", "hat": "place_hat", "crown": "place_hat",
    # stand-up comedy (round 3b)
    "joke": "ta_da", "punchline": "ta_da", "nailed_it": "ta_da",
    "deadpan": "slow_turn", "awkward": "slow_turn", "slow_turn": "slow_turn", "roll_eyes": "eye_roll",
    "eye_roll": "eye_roll", "ugh": "eye_roll", "ta_da": "ta_da", "tada": "ta_da", "voila": "ta_da",
    "present": "ta_da", "reveal": "ta_da", "facepalm": "facepalm", "groan": "facepalm", "embarrassed": "facepalm",
    "knee_slap": "knee_slap", "hilarious": "knee_slap", "crack_up": "laugh_hard", "lol": "laugh_hard",
    "guffaw": "laugh_hard", "laugh_hard": "laugh_hard", "double_take": "double_take",
    "nudge": "elbow_nudge", "get_it": "elbow_nudge", "straight_man": "straight_man", "straight_face": "straight_man",
    "heckle": "heckle", "mock": "heckle", "tease": "heckle",
    # mime (round 3b): an invisible world, crisp stops
    "trapped": "trapped_in_box", "stuck": "trapped_in_box", "box": "trapped_in_box", "cage": "trapped_in_box",
    "wall": "invisible_wall", "barrier": "invisible_wall", "window": "invisible_wall", "mime": "invisible_wall",
    "climb": "climb_ladder", "ladder": "climb_ladder", "wind": "against_the_wind", "blizzard": "against_the_wind",
    "struggle": "against_the_wind", "juggle": "juggle", "smell": "smell_flower", "sniff": "smell_flower",
    "flower": "smell_flower", "sour": "taste_sour", "lemon": "taste_sour", "yuck": "taste_sour",
    "disgusting": "taste_sour", "knock": "knock", "door": "knock", "open_door": "knock", "balance": "tightrope",
    "tightrope": "tightrope", "wobble": "tightrope", "elevator": "elevator", "descend": "elevator",
    "downstairs": "elevator", "lean": "lean_on_nothing", "slip": "lean_on_nothing", "oops": "lean_on_nothing",
    "mirror": "mirror",
    # toys (the stage refuses these unless that arm holds the toy: [props:left car] / [props:left ufo])
    "drive": "drive", "race": "drive", "park": "drive", "honk": "drive", "vroom": "drive", "car": "drive",
    "fly": "ufo_fly", "invade": "ufo_fly", "ufo": "ufo_fly", "hover": "ufo_hover", "beam_up": "ufo_hover",
    "abduct": "ufo_hover", "alien": "ufo_hover", "touch_down": "ufo_land", "crash": "ufo_crash",
}
# Duet-only words: the same word means a different solo ("catch" alone is grab), checked first by resolve_duet.
DUET_SYNONYMS = {"catch": "invisible_ball", "ball": "invisible_ball", "play_catch": "invisible_ball",
                 "mirror": "mirror", "copy": "mirror", "through_the_wall": "hand_through_wall"}
# Toy moves need that toy in the arm's hand (the stage refuses them otherwise); without it the arm plays the
# empty-hands move that looks most like the action, so the beat is never left unacted.
TOY_MOVES = {"drive": "car", "ufo_fly": "ufo", "ufo_hover": "ufo", "ufo_land": "ufo", "ufo_crash": "ufo"}
TOY_FALLBACK = {"drive": "gallop", "ufo_fly": "hop", "ufo_hover": "grab", "ufo_land": "stomp",
                "ufo_crash": "die_dramatically"}


def without_toy(name: str, held: list[str | None]) -> str:
    """The move to play when the arms (held: each one's toy kind or None) may lack the toy `name` needs."""
    toy = TOY_MOVES.get(name)
    return TOY_FALLBACK[name] if toy and any(h != toy for h in held) else name


CONTINUE_TAG = "next"
TALK_TAG = "talk"  # [talk:left] "..." -> POST /talk on, off at the closing quote
QUOTES_OPEN, QUOTES_CLOSE = '"“', '"”'
DUET_TAG = "duet"  # [duet:cross_swords] -> POST /duet
ENSEMBLE_TAG = "ensemble"  # [ensemble:storm] -> POST /duet too: the stage's ensemble-kind duets
DUET_MARGIN_S = 0.3  # the stage's blend into a gesture makes it a bit longer than its duration
FACE_TAG = "face"  # [face:each_other] / [face:audience] -> POST /stance
PROPS_TAG = "props"  # [props:left|right|both] -> POST /props on for that arm ([props:left car]: a known toy, its
# own gestures); [props:none] -> off for both
PICK_TAG, PLACE_TAG = "pick", "place"  # [pick:lars] / [place:lars] -> POST /play pick_lars / place_lars (its arm)
FACINGS = {"each_other": "each_other", "other": "each_other", "eachother": "each_other", "together": "each_other",
           "audience": "audience", "front": "audience", "us": "audience", "out": "audience", "forward": "audience"}
MAX_TAG_LEN = 48  # a "[" left open longer than this is prose, not a tag
_TAG_BODY = re.compile(r"^\s*([A-Za-z]+)\s*(?::\s*([A-Za-z][A-Za-z _=-]*?)\s*)?$")  # "=": [voice:left=deep]


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
        # a sound tag right after the last sentence stays with it (before this one's gesture tags)
        lead = re.match(r"(\s|\[\s*(?:sfx|music)\s*:[^\]]*\])*", buf, re.I).group(0)
        out = lead + "".join(t + " " for t in tags) + re.sub(r" {2,}", " ", buf[len(lead):]) + "".join(ctrl)
        tags, ctrl, buf = [], [], ""
        return out

    async for chunk in text:
        out = ""
        for piece in parser.feed(chunk):
            if isinstance(piece, Tag):
                if talk_arm(piece) is None and piece.arm not in SOUND_TAGS:
                    (ctrl if piece.arm is None else tags).append(piece.raw)
                    continue
                # a talk or sound tag stays where it is (a talk tag just before its quote, a laugh track right
                # after its punchline); a sentence may end right before it
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


SFX_SYNC_S = float(os.getenv("SFX_SYNC_MS", "0")) / 1000  # ponytail: calibration knob, added to every sound on a
# gesture hit: the real arm reaches a keyframe late (command latency + tracking lag) and the room audio lags too; tune
# it on the real arms by ear and eye


@functools.lru_cache(maxsize=None)
def sfx_lead(name: str) -> float:
    """Seconds from a sound's start to its own hit, so that lands on the gesture's: the first 25 ms window within
    6 dB of its loudest (a swish builds up to it, a fall tumbles before the thud). 0 if unreadable."""
    try:
        import av
        import numpy as np
        with av.open(str(next((SOUNDS_DIR / "sfx").rglob(f"{name}.ogg")))) as c:
            frames = list(c.decode(audio=0))
        rate, x = frames[0].sample_rate, np.concatenate([f.to_ndarray().mean(0) for f in frames])
        n = int(rate * 0.025)
        rms = np.sqrt((x[: len(x) // n * n].reshape(-1, n) ** 2).mean(1))
        return round(float(np.argmax(rms >= rms.max() / 2)) * n / rate, 3)
    except Exception:  # noqa: BLE001 - a missing or odd file just plays unshifted
        return 0.0


class Hit:
    """A fired gesture's hits (seconds from its t = 0 at speed 1) and the sound that lands on them: its default
    sound on every hit, or an explicit [sfx] from the same sentence on the first one instead. The times are fixed once
    the stage answers when the gesture starts (`started`); each sound starts its `sfx_lead` early."""

    def __init__(self, ats: list[float], default: str | None, play: Callable[[str], None],
                 sync_s: float | None = None) -> None:
        self.ats, self.default, self._play = ats, default, play
        self.sync_s = SFX_SYNC_S if sync_s is None else sync_s
        self.when: list[float] | None = None  # monotonic hit times; None until the stage answers
        self.explicit: str | None = None
        self.refused = False
        self.scheduled: list[tuple[str, float]] = []
        self._timers: list[asyncio.TimerHandle] = []
        self._played: set[str] = set()

    def started(self, t0: float | None, speed: float = 1.0) -> None:
        """t0: monotonic time of the gesture's t = 0 (None: the stage refused it); speed: what the stage plays it at."""
        if t0 is None:
            self.refused = True
            if self.explicit:
                self._play(self.explicit)
            return
        self.when = [t0 + at / max(speed, 1e-3) + self.sync_s for at in self.ats]
        self._arm()

    def sfx(self, name: str) -> None:
        """An explicit [sfx:name] in the gesture's sentence: it plays on the first hit instead of the default."""
        if self.refused or (self.when is not None and self.when[-1] < time.monotonic()):  # too late to sync
            if name not in self._played:
                self._play(name)
            return
        self.explicit = name
        if self.when is not None:
            self._arm()

    def _arm(self) -> None:
        for h in self._timers:
            h.cancel()
        if self.when is None:
            return
        now, loop = time.monotonic(), asyncio.get_event_loop()
        if self.explicit:
            ahead = [w for w in self.when if w >= now] or self.when[-1:]
            self.scheduled = [(self.explicit, ahead[0])]
        else:
            self.scheduled = [(self.default, w) for w in self.when if self.default and w >= now]
        self._timers = [loop.call_later(max(0.0, w - sfx_lead(n) - now), self._fire, n) for n, w in self.scheduled]

    def _fire(self, name: str) -> None:
        self._played.add(name)
        self._play(name)

    def cancel(self) -> None:
        """The listener cut in and the arms froze: the hits never land, so their sounds never play."""
        for h in self._timers:
            h.cancel()
        self._timers = []
        self._play = lambda _name: None  # also for a stage reply (started) or an [sfx] that comes after this


class GestureBook:
    """The gestures the stage knows, and mapping of whatever the LLM wrote onto them."""

    def __init__(self, gestures: dict[str, float], duets: dict[str, float] | None = None,
                 ensembles: dict[str, float] | None = None, about: dict[str, str] | None = None,
                 sides: dict[str, str] | None = None,
                 sounds: dict[str, tuple[str | None, list[float]]] | None = None) -> None:
        # pick_<x> / place_<x>: a guest actor's pick-up and put-back, only through their tags, on their own arm
        self.guests = {n: d for n, d in gestures.items() if n.startswith((PICK_TAG + "_", PLACE_TAG + "_"))}
        self.sides = {n: (sides or {}).get(n) or "right" for n in self.guests}
        self.durations = {n: d for n, d in gestures.items() if n not in self.guests}
        self.ensembles = dict(ensembles or {})  # name -> seconds; both arms as one thing
        self.duets = {**(duets or {}), **self.ensembles}  # name -> seconds; either tag plays any of them
        self.about = dict(about or {})  # name -> what it looks like (from the stage server)
        self.sounds = dict(sounds or {})  # gesture/duet name -> (default sfx or None, hit times s): its "sound"

    def resolve_duet(self, name: str) -> str | None:
        name = re.sub(r"[\s-]+", "_", name.strip().lower())
        for syn in (DUET_SYNONYMS, SYNONYMS):
            if name not in self.duets and syn.get(name) in self.duets:
                return syn[name]
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

    def guest(self, verb: str, who: str) -> str | None:
        """`[pick:lars]` -> "pick_lars" if the stage has it."""
        name = f"{verb}_{re.sub(r'[\s-]+', '_', who.strip().lower())}"
        return name if name in self.guests else None

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
        self.sides: dict[str, str] = {}  # one-arm-only gesture (pick_lars...) -> its arm, filled by fetch_gestures
        self.sounds: dict[str, tuple[str | None, list[float]]] = {}  # name -> (default sfx, hit times), likewise
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
            self.sides.update({g["name"]: g["side"] for g in data if g.get("name") and g.get("side")})
            self._sounds(data)
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
                self._sounds(data)
                return {d["name"]: float(d.get("duration_s") or 8.0) for d in data
                        if d.get("name") and d.get("kind", "duet") == kind}
        except Exception as e:  # noqa: BLE001 - no duets then
            logger.warning("no duets from the stage server (%s)", e)
            return {}

    def _sounds(self, data: list[dict]) -> None:
        for g in data:
            if g.get("name") and isinstance(snd := g.get("sound"), dict) and snd.get("at") is not None:
                at = snd["at"] if isinstance(snd["at"], list) else [snd["at"]]
                self.sounds[g["name"]] = (snd.get("name") or None, [float(a) for a in at])

    async def _post(self, path: str, body: dict | None = None, delay: float = 0.0,
                    on_reply: Callable[[dict | None], None] | None = None) -> None:
        if delay:
            await asyncio.sleep(delay)
        reply = None
        try:
            async with self._order, self._session().post(f"{self.url}{path}", json=body or {}) as resp:
                if resp.status >= 400:
                    logger.warning("stage %s %s -> %s %s", path, body or "", resp.status, await resp.text())
                elif on_reply:
                    reply = await resp.json(content_type=None)
        except Exception as e:  # noqa: BLE001
            logger.warning("stage %s failed: %s", path, e)
        if on_reply:
            on_reply(reply if isinstance(reply, dict) else None)

    def send(self, path: str, body: dict | None = None, delay: float = 0.0,
             on_reply: Callable[[dict | None], None] | None = None) -> asyncio.Task:
        task = asyncio.create_task(self._post(path, body, delay, on_reply))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def play(self, arm: str, gesture: str, duration_s: float, speed: float | None = None,
             on_start: Callable[[float | None, float], None] | None = None) -> None:
        """on_start(t0, speed): once the stage answers, the monotonic time the gesture's own t = 0 comes (after the
        stage's blend into it, its "starts_in") and the speed it plays at; t0 None if refused."""
        if speed is None:  # STAGE_SPEED: every gesture and duet (read per call: .env loads after this import)
            speed = float(os.getenv("STAGE_SPEED", "1.0"))
        logger.info("gesture %s:%s", arm, gesture)

        def started(reply: dict | None) -> None:
            if on_start:
                on_start(None if reply is None else time.monotonic() + float(reply.get("starts_in") or 0.0),
                         float(reply.get("speed") or speed) if reply else speed)
        cb = started if on_start else None
        if arm == DUET_TAG:  # needs both arms free: let the gestures already fired finish first
            now = time.monotonic()
            wait = self.busy_until + DUET_MARGIN_S - now if self.busy_until > now else 0.0
            self.busy_until = now + wait + duration_s / speed
            self._pending = self.send("/duet", {"name": gesture, "speed": speed}, delay=wait, on_reply=cb)
            return
        if arm == PROPS_TAG:  # gesture = the arm that takes a prop ("left", or "left_car": a known toy), or "none"
            who, _, kind = gesture.partition("_")
            on = who in ARMS
            logger.info("props %s", gesture)
            self.send("/props", {"arm": who if on else "both", "on": on, **({"kind": kind} if on and kind else {})})
            return
        self.busy_until = max(self.busy_until, time.monotonic() + duration_s / speed)
        self.send("/play", {"arm": arm, "gesture": gesture, "speed": speed}, on_reply=cb)

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
        on_sound: Callable[[str, str], None] | None = None,
    ) -> None:
        super().__init__(label="GestureSink", next_in_chain=next_in_chain)
        self._book, self._play, self._on_continue, self._on_face = book, play, on_continue, on_face
        self._on_talk, self._on_sound = on_talk, on_sound
        self._parser = TagParser()
        self._talker: str | None = None  # the arm whose mouth is talking
        self._quoted = False               # its quote has opened
        self._toys: dict[str, str | None] = {"left": None, "right": None}  # toy kind each arm holds ([props:left car])
        self._hit: Hit | None = None  # the hits of this sentence's gesture: its [sfx] tags land on them
        self._ended = False  # the text so far ends a sentence: the next word starts a new one
        self._hits: weakref.WeakSet[Hit] = weakref.WeakSet()  # alive while a sound is still to come

    def cancel_sounds(self) -> None:
        """An interruption: drop every hit sound still scheduled."""
        for hit in list(self._hits):
            hit.cancel()
        self._hit = None  # the next reply's [sfx] plays when spoken, not on a cancelled hit

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
                if p.strip():  # a sound tag right after a sentence still belongs to it; the next word doesn't
                    if self._ended:
                        self._hit = None
                    self._ended = p.rstrip().rstrip("\"'”’)")[-1:] in tuple(".!?…")
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
            elif p.arm == "voice":
                pass  # [voice:left=deep] casts a character voice: handled before the TTS (voices.py)
            elif p.arm == PROPS_TAG:
                who = p.name.strip().lower()  # "left", or "left_car" ([props:left car]): a toy the stage knows
                arm, _, kind = who.partition("_")
                if arm in ARMS or who in ("none", "off", "no"):
                    for side in self._toys:
                        if arm not in ARMS or arm in (side, "both"):
                            self._toys[side] = (kind or None) if arm in ARMS else None
                    self._play(PROPS_TAG, who if arm in ARMS else "none", 0.0)
                else:
                    logger.info("dropped %s", p.raw)
            elif p.arm in (PICK_TAG, PLACE_TAG):
                if name := self._book.guest(p.arm, p.name):
                    self._play(self._book.sides[name], name, self._book.guests[name])
                else:
                    logger.info("dropped %s: no such guest on the stage", p.raw)
            elif p.arm in (DUET_TAG, ENSEMBLE_TAG):
                if name := self._book.resolve_duet(p.name):
                    self._fire(DUET_TAG, name, self._book.duets[name])
                else:
                    logger.info("dropped %s: unknown duet", p.raw)
            elif p.arm in SOUND_TAGS:
                if p.arm == SFX_TAG and self._hit:
                    self._hit.sfx(p.name)  # lands on the gesture's hit, not when its words are spoken
                elif self._on_sound:
                    self._on_sound(p.arm, p.name)
            elif p.arm not in ARMS:
                logger.info("dropped %s: unknown arm", p.raw)
            elif (name := self._book.resolve(p.name)) is None:
                logger.info("dropped %s: unknown gesture", p.raw)
            else:
                held = [self._toys[s] for s in (("left", "right") if p.arm == "both" else (p.arm,))]
                if (name := without_toy(name, held)) in self._book.durations:
                    self._fire(p.arm, name, self._book.durations[name])
        return text

    def _fire(self, arm: str, name: str, secs: float) -> None:
        """Play it; a gesture with hits gets its sound (or the sentence's [sfx]) scheduled on them."""
        if self._ended:  # the first tag of a new sentence (tags_first puts them before its words)
            self._hit, self._ended = None, False
        if (snd := self._book.sounds.get(name)) is None:
            self._play(arm, name, secs)
            return
        hit = Hit(snd[1], snd[0], lambda n: self._on_sound(SFX_TAG, n) if self._on_sound else None)
        self._hit = hit
        self._hits.add(hit)
        self._play(arm, name, secs, on_start=hit.started)

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
                 on_talk: Callable[[str, bool], None] | None = None,
                 on_sound: Callable[[str, str], None] | None = None) -> None:
        self._book, self._play, self._on_continue, self._on_face = book, play, on_continue, on_face
        self._on_talk, self._on_sound = on_talk, on_sound
        self.sync: TranscriptSynchronizer | None = None
        self.sink: GestureSink | None = None

    def cancel_sounds(self) -> None:
        """An interruption: hit sounds still scheduled never play (see Hit.cancel)."""
        if self.sink:
            self.sink.cancel_sounds()

    def install(self, output: io.AgentOutput) -> None:
        sink = self.sink = GestureSink(self._book, self._play, self._on_continue, next_in_chain=output.transcription,
                                       on_face=self._on_face, on_talk=self._on_talk, on_sound=self._on_sound)
        if output.audio is None:  # text-only (console --text): nothing to sync to, fire as the text streams
            logger.warning("no audio output: gestures fire as the text arrives, not synced to speech")
            output.transcription = sink
            return
        self.sync = TranscriptSynchronizer(
            next_in_chain_audio=output.audio, next_in_chain_text=sink, hyphenate_word=hyphenate_without_tags
        )
        output.audio = self.sync.audio_output
        output.transcription = self.sync.text_output
