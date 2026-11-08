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
import json
import logging
import os
import re
import shlex
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
    "give_up": "italian_exasperated", "shoot": "shoot", "fire": "shoot", "blast": "shoot",
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
    "drum": "drum_roll", "drum_roll": "drum_roll", "hat": "tip_hats",  # place_hat cut (Nov 7): a prop on the other arm's head risks its camera
    # stand-up comedy (round 3b)
    "joke": "ta_da", "punchline": "ta_da", "nailed_it": "ta_da",
    "deadpan": "slow_turn", "awkward": "slow_turn", "slow_turn": "slow_turn", "roll_eyes": "eye_roll",
    "eye_roll": "eye_roll", "ugh": "eye_roll", "ta_da": "ta_da", "tada": "ta_da", "voila": "ta_da",
    "reveal": "ta_da", "present": "present", "introduce": "present",
    "presenting": "present", "here_i_am": "present", "rise": "present", "facepalm": "facepalm", "groan": "facepalm", "embarrassed": "facepalm",
    "knee_slap": "knee_slap", "hilarious": "knee_slap", "crack_up": "laugh_hard", "lol": "laugh_hard",
    "guffaw": "laugh_hard", "laugh_hard": "laugh_hard", "double_take": "double_take",
    "nudge": "elbow_nudge", "get_it": "elbow_nudge", "straight_man": "straight_man", "straight_face": "straight_man",
    "heckle": "heckle", "mock": "heckle", "tease": "heckle",
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
DREAM_TAG = "dream"  # [dream:pick_lars] -> robot/dream_run.py: Dream-1 picks Lars through our checker, else pick_lars
DREAM_TASKS = {"pick_lars": "pick up the green pen and hold it up"}  # Nov 8: the wand, not Lars (owner)
#  # Dream-1 task text (the real picks, Nov 7); other names: "pick_x" -> "pick x"
DREAM_BUSY_S = 90.0  # no fills meanwhile (the preview opens in ~15 s, then up to --pick-s 60 s)
# DREAM_ARGS (env): more dream_run.py options, e.g. "--replay robot/lars/dream_pick.json" for a rehearsal, no Dream call
DREAM_RUN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "robot", "dream_run.py")
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


_SENTENCE_END = ".!?…"
_CLOSERS = "\"'”’)*"
STATEFUL_TAGS = (FACE_TAG, PROPS_TAG, PICK_TAG, PLACE_TAG)  # a gesture never moves before these
# After a sentence ends, these open the next one; gesture, [sfx] and [next] tags there still belong to the old one.
OPENS_SENTENCE = (TALK_TAG, "voice", FACE_TAG, PROPS_TAG, PICK_TAG, PLACE_TAG, "music")
HIT_SOUND = "hit"  # on_sound kind for a gesture's own default sound
FILL_TAG = "fill"  # [fill:light]: added to a sentence with nothing moving; the sink picks the arm and the move
MARK_TAG = "mark"  # [mark:left=slash]: the word a moved tag was aimed at; the sink logs how close its hit landed
FILL_MIN_WORDS = 4
PRESENT = "present"
_WORDS = re.compile(r"\S+\s*|\s+")


def _moves(tag: Tag) -> bool:
    return tag.arm in ARMS or tag.arm in (DUET_TAG, ENSEMBLE_TAG, FILL_TAG)


def _speed() -> float:
    return float(os.getenv("STAGE_SPEED", "1.0"))


LATE_SYL = 0.5  # a tag may snap this many syllables (~0.2 s) past where its lead wants it, no more


def _syllables(word: str) -> float:
    """A word's speaking time in syllables, the pause after it included: the voice stops ~0.3-0.6 s after a
    sentence and ~0.2-0.3 s after a clause (measured on the show's acts, Nov 8), so a tag aimed past one isn't
    snapped to the later word."""
    w = word.strip()
    if not w:
        return 0
    end = re.sub(r"\[[^\]]*\]", "", w).rstrip(_CLOSERS)[-1:]
    return len(hyphenate_without_tags(w)) + (1.0 if end in _SENTENCE_END else 0.5 if end in ",;:—" else 0)


def _is_word(it: list) -> bool:
    return it[2] is None and bool(it[0].strip())


class _Placer:
    """tags_first's state: text not yet passed on, as word items `[text, syllables, None]` and tag items
    `[raw, 0, Tag]`; the open sentence starts at `cur`."""

    def __init__(self, book: GestureBook | None, fill: Callable[[], bool] | None, timing: Timing) -> None:
        self.book, self.fill, self.t = book, fill, timing
        self.items: list[list] = []
        self.cur = 0
        self.presented: set[str] = set()  # arms that rose ([x:present]) this reply

    def tag(self, tag: Tag) -> None:
        self.items.append([tag.raw, 0, tag])

    def text(self, text: str) -> str:
        out = ""
        for w in _WORDS.findall(text):
            last = self.items[-1] if self.items else None
            if last is not None and last[2] is None and w[:1].strip() and last[0][-1:].strip():
                last[0] += w  # a word split across chunks
                last[1] = _syllables(last[0])
                continue
            if w.strip() and (start := self._sentence_start()) is not None:
                out += self._close(start)
            self.items.append([w, _syllables(w), None])
        return out

    def flush(self) -> str:
        return self._close(len(self.items), final=True)

    def _sentence_start(self) -> int | None:
        """A word starts: where the next sentence begins if the last word ended one, else None."""
        last = next((i for i in range(len(self.items) - 1, self.cur - 1, -1) if _is_word(self.items[i])), None)
        if last is None or self.items[last][0].rstrip().rstrip(_CLOSERS)[-1:] not in tuple(_SENTENCE_END):
            return None
        return next((i for i in range(last + 1, len(self.items))
                     if (t := self.items[i][2]) is not None and (t.arm in OPENS_SENTENCE or talk_arm(t))),
                    len(self.items))

    def _pos(self) -> list[float]:
        """pos[i]: the syllables before item i."""
        pos, n = [], 0
        for it in self.items:
            pos.append(n)
            n += it[1]
        return [*pos, n]

    def _close(self, b: int, final: bool = False) -> str:
        """Direct and place the sentence items[cur:b], then pass on what no later tag can still move into."""
        a = self.cur
        sent = self._direct(self.items[a:b])
        self.items[a:b] = sent
        self._place(a, a + len(sent))
        pos, b = self._pos(), self.cur
        if final:
            e = len(self.items)
        else:
            reach = pos[b] - (self.t.max_lead_s * max(1.0, self.t.rate) if self.t.lead else 0)
            e = max((k for k in range(b + 1) if pos[k] <= reach and (k == b or _is_word(self.items[k]))), default=0)
        out = "".join(it[0] for it in self.items[:e])
        self.items, self.cur = self.items[e:], (0 if final else self.cur - e)
        return re.sub(r" {2,}", " ", out)

    def _direct(self, sent: list[list]) -> list[list]:
        """Code-side stage direction: `[arm:present]` at the start of a casting sentence whose arm has not risen
        yet this reply; `[fill:light]` at the end of a sentence of words with nothing moving."""
        tags = [it[2] for it in sent if it[2] is not None]
        book = self.book
        for t in tags:
            if t.arm in ARMS and book is not None and book.resolve(t.name) == PRESENT:
                self.presented.update(("left", "right") if t.arm == "both" else (t.arm,))
        out = list(sent)
        added = False
        for t in tags:
            arm = (m := re.match(r"(left|right)", t.name)) and m.group(1) if t.arm == "voice" else None
            if arm and arm not in self.presented and book is not None and PRESENT in book.durations:
                self.presented.add(arm)
                raw = f"[{arm}:{PRESENT}]"
                out.insert(0, [raw, 0, Tag(arm, PRESENT, raw)])  # first: the arm rises as it is named
                added = True
        moving = added or any(_moves(t) or talk_arm(t) or t.arm in (PICK_TAG, PLACE_TAG) for t in tags)
        if not moving and sum(map(_is_word, out)) >= FILL_MIN_WORDS and self.fill is not None and self.fill():
            raw = f"[{FILL_TAG}:light]"
            out.append([raw, 0, Tag(FILL_TAG, "light", raw)])
        return out

    def _place(self, a: int, b: int) -> None:
        items, pos, rate = self.items, self._pos(), max(1.0, self.t.rate)
        words = [i for i in range(a, b) if _is_word(items[i])]
        tags = [i for i in range(a, b) if items[i][2] is not None]
        before: dict[int, list[list]] = {}  # item index -> tags to put in front of it (b: the sentence's end)
        moved: set[int] = set()
        marks: list[tuple[int, Tag]] = []

        def put(k: int, i: int) -> None:
            before.setdefault(k, []).append([items[i][0] + " ", 0, items[i][2]])
            moved.add(i)

        # the first tag may lead into the sentences before (the text held back for it), back to their last moving
        # tag (_bound): capped at the sentence's start, the act's tags hit 0.4-2.4 s after their words (Nov 8)
        prev, hit_tag = 0, None
        for i in tags:
            tag = items[i][2]
            if tag.arm is None:  # [next]: at the end
                if i != b - 1:
                    put(b, i)
                continue
            if not _moves(tag):
                continue
            bound = max(prev, self._bound(i, a))
            if not any(w > i for w in words):  # after the sentence's words: for the whole sentence
                target = min(pos[i], pos[a] + self.t.sentence_hit_s * rate)
            else:  # right after its word: aimed at the middle of that word
                target = pos[i] - next((items[w][1] / 2 for w in reversed(words) if w < i), 0)
            name, hits = self._hits(tag)
            want = target - self.t.gesture_lead(hits[0] if hits else None, _speed()) * rate if self.t.lead else pos[a]
            k = self._spot(want, bound, i, a, pos)
            if k != i:
                put(k, i)
            prev = k
            if hits and hit_tag is None:
                hit_tag = i
            if self.t.lead and name:
                marks.append((self._spot(target, k, i, a, pos, nearest=True), tag))
        for i in tags:
            tag = items[i][2]
            if tag.arm != SFX_TAG:
                continue
            if hit_tag is not None:  # lands on that gesture's hit (Hit): right after its tag
                if hit_tag in moved:
                    k = next(k for k, its in before.items() if any(x[2] is items[hit_tag][2] for x in its))
                    before[k].insert(next(n for n, x in enumerate(before[k]) if x[2] is items[hit_tag][2]) + 1,
                                     [items[i][0] + " ", 0, tag])
                    moved.add(i)
                elif i != hit_tag + 1:
                    before.setdefault(hit_tag + 1, []).insert(0, [items[i][0] + " ", 0, tag])
                    moved.add(i)
            elif self.t.lead:
                k = self._spot(pos[i] - self.t.sound_lead(tag.name) * rate, max(prev, self._bound(i, a)), i, a, pos)
                if k != i:
                    put(k, i)
        for k, tag in marks:
            raw = f"[{MARK_TAG}:{tag.arm}={tag.name}]"
            before.setdefault(k, []).append([raw, 0, Tag(MARK_TAG, f"{tag.arm}={tag.name}", raw)])
        out: list[list] = []
        for k in range(b + 1):
            out.extend(before.get(k, []))
            if k < b and k not in moved:
                out.append(items[k])
        self.items[0:b] = out
        self.cur = len(out)

    def _bound(self, i: int, a: int) -> int:
        """The earliest spot item i may move to: after the last stateful tag before it, and after any tag an
        earlier sentence already placed."""
        for j in range(i - 1, -1, -1):
            if (t := self.items[j][2]) is not None and (t.arm in STATEFUL_TAGS or (j < a and (_moves(t) or t.arm == SFX_TAG))):
                return j + 1
        return 0

    def _spot(self, want: float, bound: int, i: int, a: int, pos: list[float], nearest: bool = False) -> int:
        """The spot in [bound, i] (a word's start, the sentence start or the bound) nearest `want` syllables;
        i itself (stay) if want is at or past it."""
        if want >= pos[i] and not nearest:
            return i
        spots = [k for k in range(bound, i + 1) if k in (a, bound, i) or _is_word(self.items[k])]
        if nearest:
            return min(spots, key=lambda k: (abs(pos[k] - want), k))
        # a tag: the last spot not later than `want` (a hit a little early reads as on the word, a late one doesn't)
        early = [k for k in spots if pos[k] <= want + LATE_SYL]
        return max(early, key=lambda k: (pos[k], -k)) if early else min(spots, key=lambda k: (pos[k], k))

    def _hits(self, tag: Tag) -> tuple[str | None, list[float]]:
        if self.book is None or tag.arm == FILL_TAG:
            return None, []
        name = (self.book.resolve_duet(tag.name) if tag.arm in (DUET_TAG, ENSEMBLE_TAG)
                else self.book.resolve(tag.name))
        return name, list((self.book.sounds.get(name) or (None, []))[1]) if name else []


async def tags_first(text: AsyncIterable[str], book: GestureBook | None = None,
                     fill: Callable[[], bool] | None = None, timing: Timing | None = None) -> AsyncIterable[str]:
    """Transcription transform: fire each gesture tag early enough that its hit lands on its words.

    A tag right after a word is aimed at that word; one after the sentence's last word at `SENTENCE_HIT_S` into
    the sentence. It moves earlier by `Timing.gesture_lead` (the stage's blend, the gesture's own first hit, the
    arm's lag, minus the listener's playout delay) at the voice's speaking rate, never before a face/props/pick
    tag or a tag placed earlier, and an [sfx] in a gesture's sentence goes right after it (it lands on its hit).
    A `[mark:arm=name]` stays where the tag was aimed, for the sync log. `[next]` goes to the sentence's end.
    With `GESTURE_LEAD=0` every gesture tag goes to its sentence's start (the old behaviour).
    Code-side stage direction too: `[arm:present]` before a casting, `[fill:light]` for a motionless sentence
    (`fill()` says whether it is on). ponytail: holds one sentence plus `max_lead_s` of text (the LLM runs well
    ahead of speech)."""
    placer = _Placer(book, fill, timing or TIMING)
    parser = TagParser()
    async for chunk in text:
        out = ""
        for piece in parser.feed(chunk):
            if isinstance(piece, Tag):
                placer.tag(piece)
            else:
                out += placer.text(piece)
        if out:
            yield out
    for piece in parser.flush():
        placer.text(piece) if isinstance(piece, str) else placer.tag(piece)
    if rest := placer.flush():
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


def _ms(name: str, default: str) -> float:
    return float(os.getenv(name, default)) / 1000


class Timing:
    """The delays between a tag and what the audience sees and hears (kb/storyteller.md "Sync"). Knobs from .env,
    measured live where possible: the page reports its playout delay, the stage its blend and round trip, the
    sink the voice's speaking rate.

    A gesture's hit should land as the listener hears its word. Heard = released + `audio_s` (network + jitter
    buffer + output). The arm's hit = sent + rtt/2 + starts_in + hit_at/speed + `arm_lag_s`. So the tag fires
    `gesture_lead()` seconds before its word is released: `tags_first` moves it that many syllables earlier."""

    def __init__(self) -> None:
        self.audio_s = _ms("AUDIO_DELAY_MS", "250")  # voice playout delay at the listener; the page's measure wins
        self.sound_s: float | None = None  # the sound track's (page-measured; None: same as the voice)
        self.measured = False  # audio_s comes from the page
        self.arm_lag_s = _ms("ARM_LAG_MS", "70")  # the real arm trails its commanded pose (0.1-0.19 rad at speed)
        self.hit_default_s = float(os.getenv("HIT_DEFAULT_S", "0.6"))  # a gesture without hits: its peak, s in
        self.sentence_hit_s = float(os.getenv("SENTENCE_HIT_S", "0.5"))  # a tag for a whole sentence hits this far in
        self.rate = float(os.getenv("SPEECH_RATE", "2.8"))  # syllables/s the voice speaks (measured: host 2.5)
        self.rtt_s, self.starts_in_s = 0.03, 0.35  # stage round trip; its blend into a gesture (median 0.30-0.54)
        self.max_lead_s = 3.5
        self.lead = os.getenv("GESTURE_LEAD", "1") == "1"  # 0: tags fire where written (sentence start)
        self.backlog: Callable[[], float] = lambda: 0.0  # sound already queued on the sound track (Soundboard)

    def ema(self, attr: str, x: float, a: float = 0.3) -> None:
        setattr(self, attr, (1 - a) * getattr(self, attr) + a * x)

    def gesture_lead(self, hit_at: float | None, speed: float = 1.0) -> float:
        at = self.hit_default_s if hit_at is None else hit_at
        lead = self.rtt_s / 2 + self.starts_in_s + at / max(speed, 1e-3) + self.arm_lag_s - self.audio_s
        return min(self.max_lead_s, max(0.0, lead))

    def sound_s_or_voice(self) -> float:
        return self.audio_s if self.sound_s is None else self.sound_s

    def sound_lead(self, name: str) -> float:
        """An [sfx] on a word: its own hit lands as the word is heard."""
        return max(0.0, self.backlog() + self.sound_s_or_voice() - self.audio_s + sfx_lead(name))

    def hit_offset(self) -> float:
        """Added to a gesture's commanded hit to get when its sound must be cued: the arm's lag, minus the sound
        track's queue and playout."""
        return SFX_SYNC_S + self.arm_lag_s - self.backlog() - self.sound_s_or_voice()


TIMING = Timing()
SESSION_T0 = time.monotonic()  # sync log times are seconds since the worker started (storyteller resets it)


def t_rel(t: float | None = None) -> float:
    return (time.monotonic() if t is None else t) - SESSION_T0


class Hit:
    """A fired gesture's hits (seconds from its t = 0 at speed 1) and the sound that lands on them: its default
    sound on every hit, or an explicit [sfx] from the same sentence on the first one instead. The times are fixed once
    the stage answers when the gesture starts (`started`); each sound starts its `sfx_lead` early."""

    def __init__(self, ats: list[float], default: str | None, play: Callable[[str], None],
                 sync_s: float | None = None) -> None:
        self.ats, self.default, self._play = ats, default, play
        self.sync_s = sync_s  # None: Timing.hit_offset() when the stage answers (SFX_SYNC_MS + arm lag - sound delay)
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
        sync = TIMING.hit_offset() if self.sync_s is None else self.sync_s
        self.when = [t0 + at / max(speed, 1e-3) + sync for at in self.ats]
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


HIT_GAP_S = float(os.getenv("HIT_GAP_MS", "200")) / 1000  # a gesture waits for the same arm's last hit + this
HIT_WAIT_MAX_S = 1.5  # ...but never longer


def _sides(arm: str) -> tuple[str, ...]:
    return ("left", "right") if arm == "both" else (arm,) if arm in ("left", "right") else ()


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
        self.duet_from = self.duet_until = 0.0  # a duet holds both arms then: solos and mouths are dropped
        self.hit_at = {"left": 0.0, "right": 0.0}  # each arm's last gesture's first visible hit (monotonic)
        self.idling = False  # /idle sent since the last freeze: arms breathe between gestures instead of holding
        self._delayed: set[asyncio.Task] = set()  # sends waiting their turn (a freeze cancels them)
        self.dreaming = False  # robot/dream_run.py running ([dream:...])
        self.on_dream: Callable[[str], None] | None = None  # speaks its lines (storyteller wires session.say)
        # a Dream-1 miss ({"event": "oops", "kind", "text", "sfx", ...}): show mode quips, live mode lets the LLM riff;
        # unset, its text goes to on_dream. The reaction gesture already played through /play (dream_run.py).
        self.on_dream_oops: Callable[[dict], None] | None = None
        self.dream_args = ""  # more dream_run.py options (the show's DREAM_ARGS config), after the env's DREAM_ARGS
        self._dream_proc: asyncio.subprocess.Process | None = None

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
            async with self._order:
                sent = time.monotonic()
                async with self._session().post(f"{self.url}{path}", json=body or {}) as resp:
                    if resp.status >= 400:
                        logger.warning("stage %s %s -> %s %s", path, body or "", resp.status, await resp.text())
                    elif on_reply:
                        reply = await resp.json(content_type=None)
                TIMING.ema("rtt_s", min(1.0, time.monotonic() - sent), 0.2)
        except Exception as e:  # noqa: BLE001
            logger.warning("stage %s failed: %s", path, e)
        if on_reply:
            on_reply(reply if isinstance(reply, dict) else None)

    def send(self, path: str, body: dict | None = None, delay: float = 0.0,
             on_reply: Callable[[dict | None], None] | None = None) -> asyncio.Task:
        task = asyncio.create_task(self._post(path, body, delay, on_reply))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        if delay > 0:
            self._delayed.add(task)
            task.add_done_callback(self._delayed.discard)
        return task

    def idle(self) -> None:
        """Arms breathe (the stage's idle_breathe) now and after every gesture, instead of holding still."""
        self.idling = True
        self.send("/idle")

    def play(self, arm: str, gesture: str, duration_s: float, speed: float | None = None,
             on_start: Callable[[float | None, float], None] | None = None) -> None:
        """on_start(t0, speed): once the stage answers, the monotonic time the gesture's own t = 0 comes (after the
        stage's blend into it, its "starts_in") and the speed it plays at; t0 None if refused."""
        if arm == DREAM_TAG:
            self.dream(gesture)
            return
        if speed is None:  # STAGE_SPEED: every gesture and duet (read per call: .env loads after this import)
            speed = float(os.getenv("STAGE_SPEED", "1.0"))
        now = time.monotonic()
        if arm in ARMS and self.duet_from <= now < self.duet_until:
            logger.info("dropped %s:%s: a duet holds both arms", arm, gesture)
            if on_start:
                on_start(None, speed)
            return
        if arm != PROPS_TAG and not self.idling:
            self.idle()  # after a freeze (or at the start): breathe between gestures from now on
        hits = (self.sounds.get(gesture) or (None, []))[1]

        def started(reply: dict | None) -> None:
            t0 = None
            if reply is not None:
                starts_in = float(reply.get("starts_in") or 0.0)
                TIMING.ema("starts_in_s", min(3.0, starts_in), 0.2)
                t0 = time.monotonic() - TIMING.rtt_s / 2 + starts_in
                if arm in self.hit_at:
                    self.hit_at[arm] = t0 + ((hits[0] if hits else TIMING.hit_default_s) / speed) + TIMING.arm_lag_s
                logger.info("stage %s:%s starts in %.2f s (rtt %.0f ms)", arm, gesture, starts_in, TIMING.rtt_s * 1000)
            if on_start:
                on_start(t0, float(reply.get("speed") or speed) if reply else speed)
        cb = started
        if arm == DUET_TAG:  # needs both arms free: let the gestures already fired finish first
            wait = self.busy_until + DUET_MARGIN_S - now if self.busy_until > now else 0.0
            self.busy_until = now + wait + duration_s / speed
            self.duet_from = now + wait
            self.duet_until = self.busy_until + TIMING.starts_in_s + DUET_MARGIN_S
            self._pending = self.send("/duet", {"name": gesture, "speed": speed}, delay=wait, on_reply=cb)
            return
        if arm == PROPS_TAG:  # gesture = the arm that takes a prop ("left", or "left_car": a known toy), or "none"
            who, _, kind = gesture.partition("_")
            on = who in ARMS
            logger.info("props %s", gesture)
            self.send("/props", {"arm": who if on else "both", "on": on, **({"kind": kind} if on and kind else {})})
            return
        # the arm's last gesture keeps going until its hit has been seen (the stage's newest-wins would cut it)
        wait = max(0.0, min(HIT_WAIT_MAX_S, max(self.hit_at.get(a, 0.0) for a in _sides(arm)) + HIT_GAP_S - now))
        self.busy_until = max(self.busy_until, now + wait + duration_s / speed)
        for a in _sides(arm):  # until the stage answers: a guess, so a quick next tag waits for this hit too
            self.hit_at[a] = now + wait + TIMING.starts_in_s + (hits[0] if hits else TIMING.hit_default_s) / speed
        if wait > 0.05:
            logger.info("gesture %s:%s waits %.2f s for the last one's hit", arm, gesture, wait)
        self.send("/play", {"arm": arm, "gesture": gesture, "speed": speed}, delay=wait, on_reply=cb)

    def dream(self, name: str) -> None:
        """[dream:pick_lars]: robot/dream_run.py asks Dream-1 (no motion), our checker passes or refuses each chunk,
        and on a refusal our own pick_lars plays. Its "say" lines go to on_dream (the host speaks them)."""
        if self.dreaming:
            logger.info("dropped dream:%s: one is running", name)
            return
        self.dreaming = True
        task = asyncio.create_task(self._dream(name))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _dream(self, name: str) -> None:
        text = DREAM_TASKS.get(name, name.replace("_", " "))
        try:
            proc = await asyncio.create_subprocess_exec(
                "python3", DREAM_RUN, "--stage", self.url, "--task", text, *shlex.split(os.getenv("DREAM_ARGS", "")),
                *shlex.split(self.dream_args), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            self._dream_proc = proc
            async for line in proc.stdout:
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                logger.info("dream %s", msg)
                if msg.get("event") == "say" and self.on_dream:
                    self.on_dream(str(msg.get("text", "")))
                elif msg.get("event") == "oops":
                    if self.on_dream_oops:
                        self.on_dream_oops(msg)
                    elif self.on_dream:
                        self.on_dream(str(msg.get("text", "")))
            await proc.wait()
        except Exception as e:  # noqa: BLE001 - the show goes on
            logger.warning("dream:%s failed: %s", name, e)
        finally:
            self.dreaming, self._dream_proc = False, None

    def stop_dream(self) -> None:
        """A show cut: end dream_run.py (its patter and chunks stop; the cut's freeze holds the arms)."""
        if self._dream_proc and self._dream_proc.returncode is None:
            self._dream_proc.terminate()

    def talk(self, arm: str, on: bool) -> None:
        if on and self.duet_from <= time.monotonic() < self.duet_until:
            logger.info("dropped talk %s: a duet holds both arms", arm)
            return
        logger.info("talk %s %s", arm, "on" if on else "off")
        self.send("/talk", {"arm": arm, "on": on})

    def face(self, stance: str) -> None:
        wait = max(0.0, self.duet_until - time.monotonic())  # a turn mid-duet is refused: turn once it ends
        logger.info("face %s%s", stance, f" in {wait:.1f} s, after the duet" if wait else "")
        self.send("/stance", {"stance": stance}, delay=wait)

    def freeze(self) -> None:
        self.busy_until = self.duet_from = self.duet_until = 0.0
        self.hit_at = {"left": 0.0, "right": 0.0}
        self.idling = False
        if self._pending:  # the listener interrupted: a duet still waiting never starts
            self._pending.cancel()
        for task in list(self._delayed):  # gestures waiting their turn never start either
            task.cancel()
        self.send("/freeze")

    async def aclose(self) -> None:
        if self._tasks:
            await asyncio.wait(self._tasks, timeout=2.0)
        if self._http:
            await self._http.close()


FILL_MOVES = {"audience": ("look_at_other", "nod", "look_around"), "each_other": ("nod", "look_at_other", "look_around")}
TALK_TAIL_S = 0.12  # the mouth shuts this long after the last word of its quote is heard
TALK_MIN_S = 0.4    # ...and is open at least this long (a one-word quote is released as a single token)


class GestureSink(io.TextOutput):
    """Sits right after the transcript synchronizer: sees the reply's text as it is spoken, fires its
    gesture tags, and passes the text on without them."""

    def __init__(
        self,
        book: GestureBook,
        play: Callable[..., None],
        on_continue: Callable[[], None],
        *,
        next_in_chain: io.TextOutput | None,
        on_face: Callable[[str], None] | None = None,
        on_talk: Callable[[str, bool], None] | None = None,
        on_sound: Callable[[str, str], None] | None = None,
        on_tag: Callable[[Tag], None] | None = None,
    ) -> None:
        super().__init__(label="GestureSink", next_in_chain=next_in_chain)
        self._book, self._play, self._on_continue, self._on_face = book, play, on_continue, on_face
        self._on_talk, self._on_sound, self._on_tag = on_talk, on_sound, on_tag
        self._parser = TagParser()
        self._talker: str | None = None  # the arm whose mouth is talking
        self._quoted = False               # its quote has opened
        self._mouth: dict[str, bool] = {}  # arm -> open, as last sent
        self._mouth_at: dict[str, float] = {}  # arm -> when it opened
        self._mouth_timer: dict[str, asyncio.TimerHandle] = {}
        self._toys: dict[str, str | None] = {"left": None, "right": None}  # toy kind each arm holds ([props:left car])
        self._holding: dict[str, bool] = {"left": False, "right": False}  # a guest (Lars) in that hand
        self._facing = "audience"
        self._until: dict[str, float] = {"left": 0.0, "right": 0.0}  # each arm busy with our last gesture until
        self._last_arm = "left"
        self._fills = 0
        self._hit: Hit | None = None  # the gesture just fired: an [sfx] right after its tag lands on its hit
        self._sync: dict[tuple[str, str], list[dict]] = {}  # (arm, name) -> fired, awaiting their mark
        self._reply_t: float | None = None  # first release of this reply, and its syllables since
        self._reply_syl = 0
        self._hits: weakref.WeakSet[Hit] = weakref.WeakSet()  # alive while a sound is still to come

    def cancel_sounds(self) -> None:
        """An interruption: drop every hit sound still scheduled."""
        for hit in list(self._hits):
            hit.cancel()
        self._hit = None  # the next reply's [sfx] plays when spoken, not on a cancelled hit

    # the talking mouth
    def _set_mouth(self, arm: str, on: bool, delay: float) -> None:
        if (h := self._mouth_timer.pop(arm, None)) is not None:
            h.cancel()
        if self._mouth.get(arm, False) == on:
            return  # e.g. the next quote opens before the last one's mouth shut: it just stays open

        def send() -> None:
            self._mouth_timer.pop(arm, None)
            self._mouth[arm] = on
            if on:
                self._mouth_at[arm] = time.monotonic()
            if self._on_talk:
                self._on_talk(arm, on)
        if delay <= 0:
            send()
            return
        try:
            self._mouth_timer[arm] = asyncio.get_running_loop().call_later(delay, send)
        except RuntimeError:  # no loop (a sync test): now
            send()

    def _talk(self, arm: str | None, word: str = "", now: bool = False) -> None:
        """Mouths follow the voice as heard: open `audio_s` after the quote is released, shut after its last word."""
        if self._talker:
            off = 0.0 if now else TIMING.audio_s + len(hyphenate_without_tags(word)) / 2 / max(1.0, TIMING.rate) + TALK_TAIL_S
            opened = self._mouth_at.get(self._talker, 0.0) if self._mouth.get(self._talker) else time.monotonic() + TIMING.audio_s
            if not now:
                off = max(off, opened + TALK_MIN_S - time.monotonic())
            self._set_mouth(self._talker, False, off)
        self._talker, self._quoted = arm, False
        if arm:
            self._set_mouth(arm, True, 0.0 if now else TIMING.audio_s)

    def _scan_quotes(self, text: str) -> None:
        """The talking mouth stops at its quote's closing mark (the text arrives as it is spoken)."""
        for n, c in enumerate(text):
            if not self._talker:
                return
            if not self._quoted and c in QUOTES_OPEN:
                self._quoted = True
            elif self._quoted and c in QUOTES_CLOSE:
                self._talk(None, re.split(r"\s", text[:n])[-1])

    def _handle(self, pieces: list[str | Tag]) -> str:
        text = ""
        for p in pieces:
            if isinstance(p, str):
                text += p
                self._scan_quotes(p)
                if p.strip():
                    self._hit = None  # words came after the gesture: a later [sfx] is its own
                    self._rate(p)
                continue
            if self._on_tag:
                self._on_tag(p)
            if (who := talk_arm(p)) is not None:
                self._talk(who)
            elif p.arm is None:
                if p.name == CONTINUE_TAG:
                    self._on_continue()
            elif p.arm == MARK_TAG:
                self._marked(p.name)
            elif p.arm == FILL_TAG:
                self._fill()
            elif p.arm == FACE_TAG:
                if (facing := FACINGS.get(p.name)) and self._on_face:
                    self._facing = facing
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
                    side = self._book.sides[name]
                    self._holding[side] = p.arm == PICK_TAG
                    self._busy(side, self._book.guests[name])
                    self._play(side, name, self._book.guests[name])
                else:
                    logger.info("dropped %s: no such guest on the stage", p.raw)
            elif p.arm == DREAM_TAG:
                self._busy("both", DREAM_BUSY_S)
                self._play(DREAM_TAG, p.name.strip().lower(), 0.0)
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
                    self._fire(p.arm, name, self._book.durations[name], tag=p.name)
        return text

    def _busy(self, arm: str, secs: float) -> None:
        until = time.monotonic() + TIMING.starts_in_s + secs / max(_speed(), 1e-3)
        for a in ("left", "right") if arm in ("both", DUET_TAG) else (arm,):
            self._until[a] = max(self._until.get(a, 0.0), until)

    def _fill(self) -> None:
        """A sentence with nothing moving: a light move on a free arm, the last active one first."""
        now = time.monotonic()
        for arm in (self._last_arm, "right" if self._last_arm == "left" else "left"):
            if self._until[arm] > now or self._toys[arm] or self._holding[arm] or self._mouth.get(arm):
                continue
            moves = [m for m in FILL_MOVES.get(self._facing, FILL_MOVES["audience"]) if m in self._book.durations]
            if not moves:
                return
            move = moves[self._fills % len(moves)]
            self._fills += 1
            logger.info("fill: %s:%s (a sentence with nothing moving)", arm, move)
            self._fire(arm, move, self._book.durations[move], fill=True)
            return

    def _fire(self, arm: str, name: str, secs: float, tag: str | None = None, fill: bool = False) -> None:
        """Play it; a gesture with hits gets its sound (or the [sfx] right after its tag) scheduled on them."""
        if arm in ("left", "right") and not fill:
            self._last_arm = arm
        self._busy(arm, secs)
        snd = self._book.sounds.get(name)
        # the stage's own sound for this gesture is "hit" (curated: plays even if not offered to the LLM, e.g. kiss)
        hit = Hit(snd[1], snd[0], lambda n: self._on_sound(HIT_SOUND if n == snd[0] else SFX_TAG, n)
                  if self._on_sound else None) if snd else None
        self._hit = hit
        if hit is not None:
            self._hits.add(hit)
        rec = {"arm": arm, "name": name, "sent": time.monotonic(), "hit": None, "heard": None, "hitobj": hit,
               "ats": snd[1] if snd else []}
        if not fill:
            for key in {(arm, name), (arm, tag or name)}:
                self._sync.setdefault(key, []).append(rec)

        def started(t0: float | None, speed: float = 1.0) -> None:
            if hit is not None:
                hit.started(t0, speed)
            if t0 is not None:
                rec["hit"] = t0 + (rec["ats"][0] if rec["ats"] else TIMING.hit_default_s) / max(speed, 1e-3) + TIMING.arm_lag_s
            self._log_sync(rec)
        self._play(arm, name, secs, on_start=started)

    def _marked(self, body: str) -> None:
        """The word a moved tag was aimed at is being released now: heard `audio_s` from now."""
        arm, _, name = body.partition("=")
        recs = self._sync.get((arm, name)) or self._sync.get((arm, self._book.resolve(name) or name)) or []
        rec = next((r for r in recs if r["heard"] is None), None)
        if rec is None:
            return
        rec["heard"] = time.monotonic() + TIMING.audio_s
        self._log_sync(rec)

    def _log_sync(self, rec: dict) -> None:
        if rec["heard"] is None or rec["hit"] is None or rec.get("logged"):
            return
        rec["logged"] = True
        for recs in self._sync.values():
            if rec in recs:
                recs.remove(rec)
        hit = rec["hitobj"]
        sfx = ", ".join(f"{n} cued {t_rel(w - sfx_lead(n)):.2f}" for n, w in (hit.scheduled if hit else []))
        logger.info("sync %s:%s: sent %.2f, word heard %.2f, hit %.2f (%+.0f ms)%s", rec["arm"], rec["name"],
                    t_rel(rec["sent"]), t_rel(rec["heard"]), t_rel(rec["hit"]), (rec["hit"] - rec["heard"]) * 1000,
                    f"; sfx {sfx}" if sfx else "")

    def _rate(self, text: str) -> None:
        """The voice's speaking rate, from how fast the synchronizer releases the words (for Timing.rate)."""
        now = time.monotonic()
        if self._reply_t is None:
            self._reply_t, self._reply_syl = now, 0
            return
        self._reply_syl += sum(_syllables(w) for w in text.split())  # pauses too, as tags_first counts them

    async def capture_text(self, text: str) -> None:
        clean = self._handle(self._parser.feed(text))
        if clean and self.next_in_chain:
            await self.next_in_chain.capture_text(clean)

    def flush(self) -> None:
        # an interrupted segment flushes too; whatever is held back was never spoken, so drop its tags
        rest = "".join(p for p in self._parser.flush() if isinstance(p, str))
        self._talk(None, now=True)  # end of the reply, or an interruption: the mouth shuts
        for arm in list(self._mouth_timer):
            self._set_mouth(arm, False, 0.0)
        if self._reply_t is not None and (secs := time.monotonic() - self._reply_t) > 4 and self._reply_syl > 10:
            TIMING.ema("rate", min(6.0, max(1.5, self._reply_syl / secs)), 0.3)
        self._reply_t = None
        self._sync.clear()
        if self.next_in_chain:
            if rest:
                asyncio.ensure_future(self.next_in_chain.capture_text(rest))
            self.next_in_chain.flush()


class GestureSync:
    """Wraps the session's audio + transcription outputs in our own TranscriptSynchronizer, whose text
    output feeds a GestureSink. Call `install()` after `session.start()` has set the outputs up."""

    def __init__(self, book: GestureBook, play: Callable[..., None],
                 on_continue: Callable[[], None], on_face: Callable[[str], None] | None = None,
                 on_talk: Callable[[str, bool], None] | None = None,
                 on_sound: Callable[[str, str], None] | None = None,
                 on_tag: Callable[[Tag], None] | None = None) -> None:
        self._book, self._play, self._on_continue, self._on_face = book, play, on_continue, on_face
        self._on_talk, self._on_sound, self._on_tag = on_talk, on_sound, on_tag
        self.sync: TranscriptSynchronizer | None = None
        self.sink: GestureSink | None = None

    def cancel_sounds(self) -> None:
        """An interruption: hit sounds still scheduled never play (see Hit.cancel)."""
        if self.sink:
            self.sink.cancel_sounds()

    def install(self, output: io.AgentOutput) -> None:
        sink = self.sink = GestureSink(self._book, self._play, self._on_continue, next_in_chain=output.transcription,
                                       on_face=self._on_face, on_talk=self._on_talk, on_sound=self._on_sound,
                                       on_tag=self._on_tag)
        if output.audio is None:  # text-only (console --text): nothing to sync to, fire as the text streams
            logger.warning("no audio output: gestures fire as the text arrives, not synced to speech")
            output.transcription = sink
            return
        self.sync = TranscriptSynchronizer(
            next_in_chain_audio=output.audio, next_in_chain_text=sink, hyphenate_word=hyphenate_without_tags
        )
        output.audio = self.sync.audio_output
        output.transcription = self.sync.text_output
