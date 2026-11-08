"""Sound effects and mood music, cued by tags in the narration, played into the room on LiveKit's
BackgroundAudioPlayer track (the web page plays every agent audio track, so they come out of the laptop).

The LLM writes `[sfx:explosion]` where a sound happens and `[music:spooky]` when the mood changes (`[music:none]`
stops it). gestures.py's GestureSink fires them as the spoken words reach them, like the gestures, and
`tags_first` leaves them where they were written (a rimshot belongs after its punchline, not before).

- One-shots play well under the voice (`SFX_VOLUME`) and are rare: in a story at most one every `SFX_MIN_GAP_S`
  (12 s); a cue inside the gap is dropped (logged), a gesture's own hit sound included. Scripted show acts skip
  the budget.
- `OFFERED_NOT` files stay on disk but are not offered to the LLM, and an `[sfx:x]` for one maps to a near sound or
  is dropped (harsh, clipped, too long). A gesture's own curated hit sound (stage `sound`, cue kind "hit") still
  plays one (the kiss ensemble's kiss).
- `[music:mood]` picks one of that mood's tracks (they take turns, so stories don't repeat). It loops and is ducked: `MUSIC_DUCKED` while the narrator or the listener speaks, `MUSIC_VOLUME` in the
  gaps, gliding between the two. A new track crossfades with the old one.
- The files in sounds/ are trimmed and loudness-normalised (sfx levelled Nov 7 to max(loudest 200 ms, loudest
  3 s + 3 LU) = -18 LUFS, true peak <= -2 dBTP; music at -20 LUFS integrated); sounds/LICENSES.md says where each
  came from.
"""
from __future__ import annotations

import asyncio
import contextlib
import difflib
import logging
import os
import random
import re
from collections.abc import AsyncIterator
from pathlib import Path

import numpy as np
from livekit import rtc
from livekit.agents import AgentSession
from livekit.agents.utils.audio import audio_frames_from_file
from livekit.agents.voice.background_audio import AudioConfig, BackgroundAudioPlayer, PlayHandle

logger = logging.getLogger("storyteller.sounds")

SOUNDS_DIR = Path(__file__).parent / "sounds"
SFX_TAG, MUSIC_TAG = "sfx", "music"
SOUND_TAGS = (SFX_TAG, MUSIC_TAG)
MUSIC_OFF = ("none", "off", "stop", "no", "silence", "quiet", "nothing")

SFX_VOLUME = float(os.getenv("SFX_VOLUME", "0.2"))  # -12 dB from Nov 7's 0.8: a hit sits ~6 LU under the narrator
SFX_MIN_GAP_S = float(os.getenv("SFX_MIN_GAP_S", "12"))  # story budget: at most one sfx per this many seconds
SFX_BUFFER_MS = int(os.getenv("SFX_BUFFER_MS", "150"))  # the sound track's send queue (LiveKit's default 400 + a
# 100 ms mix block kept every sfx ~0.5 s behind its cue while music played); what is queued is cued that much early
SFX_BLOCK_S = 0.02  # mix block
# Kept on disk, never offered or played (Nov 7 listening proxy, kb/storyteller.md): clipped (gallop), squashed or
# droning (rifle, spaceship), sub-bass mud on a small speaker (stomp, debris), piercing hiss (magic, kiss), too long and
# hammy (evil_laugh); laugh_track / laugh_big sound canned (owner, Nov 7: replacements on a review page).
OFFERED_NOT = {"gallop", "evil_laugh", "rifle", "stomp", "debris", "spaceship", "magic", "kiss", "laugh_track",
               "laugh_big"}
MUSIC_VOLUME = float(os.getenv("MUSIC_VOLUME", "0.35"))  # nobody talking
MUSIC_DUCKED = float(os.getenv("MUSIC_DUCKED", "0.15"))  # under speech: ~17 dB below the voice
MUSIC_FADE_S = 1.5  # crossfade between tracks, fade-in of the first one
DUCK_S, UNDUCK_S = 0.25, 1.2  # glide time down to MUSIC_DUCKED and back up

# Short notes for the prompt, only where the name alone doesn't say it. Every file is listed either way.
ABOUT = {
    "blaster": "a retro sci-fi blaster", "laser_small": "a small pew", "cannon": "a pirate cannon",
"unsheathe": "a sword drawn from its sheath", "clang": "a metal clang",
    "crash": "something heavy smashes", "body_fall": "someone falls, thud",
    "flyby": "a spaceship zooms past", "teleport": "beam in or out",
    "skid": "tyres screech", "roar": "a dragon roar", "growl": "a monster growl", "chomp": "one big bite",
    "spell": "a spell is cast", "poof": "vanish in smoke",
    "zip": "a quick cartoon zip", "tada": "a tada fanfare", "drum_roll": "before a reveal",
    "rimshot": "ba-dum-tss, after a punchline", "crickets": "nobody laughed, after a flop",
    "sad_trombone": "wah-wah-waaah, a flop",
    "slide_whistle": "a cartoon fall", "gasp": "the crowd gasps",
    "audience_ooh": "the audience goes ooooh", "audience_aww": "the audience goes awww",
}
MUSIC_ABOUT = {
    "adventure": "a cheerful journey or quest", "calm": "a gentle village, a peaceful morning",
    "fairytale": "once upon a time, princesses and magic", "spooky": "ghosts, a dark forest",
    "mystery": "a detective, a puzzle, sneaking", "battle": "a fight, danger", "chase": "running, racing, escaping",
    "epic": "a huge, heroic moment", "sad": "a sad or tender moment", "romantic": "love, a sweet moment",
    "victory": "the heroes win, a celebration", "space": "outer space, robots, the future",
    "underwater": "under the sea", "medieval": "castles, knights, a tavern", "pirate": "ships and pirates",
    "western": "cowboys, the wild west", "funny": "a silly, cartoonish scene", "lounge": "the comedy club: stand-up",
}
SFX_SYNONYMS = {
    "boom": "explosion", "kaboom": "explosion", "blast": "explosion", "bomb": "explosion", "big_explosion": "explosion_big",
    "huge_explosion": "explosion_big", "small_explosion": "explosion_small", "little_explosion": "explosion_small",
    "smash": "crash", "bang": "crash", "rubble": "crash", "collapse": "crash", "shatter": "glass", "window": "glass",
    "break": "wood_break", "snap": "wood_break", "crack": "wood_break", "metal": "clang", "thud": "body_fall",
    "fall": "body_fall", "faint": "body_fall", "cartoon_fall": "slide_whistle",
    "sword": "sword_clash", "clash": "sword_clash", "swing": "sword_swing", "slash": "sword_swing",
    "draw": "unsheathe", "blade": "unsheathe", "pew": "laser", "zap": "laser", "beam": "laser", "shot": "gunshot",
    "gun": "gunshot", "pistol": "gunshot", "machine_gun": "gunshot", "burst": "gunshot", "ricochet": "ricochet",
    "arrows": "arrow", "bow": "arrow", "crossbow": "arrow", "thunk": "arrow_hit", "shield": "shield_hit",
    "block": "shield_hit", "hit": "punch", "thwack": "punch", "smack": "punch", "boot": "kick",
    "spell": "spell", "cast": "spell", "sparkle": "spell", "twinkle": "spell", "wand": "spell", "smoke": "poof",
    "vanish": "poof", "disappear": "poof", "warp": "teleport", "beam_up": "teleport", "zoom": "zip", "dash": "zip",
    "plop": "pop", "bubble": "pop", "bump": "bonk", "head": "bonk",
    "lightning": "thunder", "storm": "thunder", "raining": "rain", "drizzle": "rain", "breeze": "wind",
    "gust": "wind", "flames": "fire", "campfire": "fire", "burn": "fire", "slam": "door_slam", "door": "door_creak",
    "creak": "door_creak", "ding": "bell", "ring": "bell", "chime": "bell", "tick": "clock", "tock": "clock",
    "siren": "alarm", "water": "splash", "swoosh": "whoosh", "swish": "whoosh", "bounce": "boing", "spring": "boing",
    "jump": "boing", "horn": "car_horn", "honk": "car_horn", "beep": "car_horn", "car": "car_engine",
    "engine": "car_engine", "rev": "car_engine", "screech": "skid", "brakes": "skid", "tyres": "skid", "tires": "skid",
    "rocket": "flyby", "ufo": "flyby", "zoom_past": "flyby", "walk": "footsteps", "steps": "footsteps",
    "giant": "footsteps", "flap": "wings", "wing": "wings", "fly": "wings", "bite": "chomp",
    "nom": "chomp", "growling": "growl", "monster": "growl", "dragon": "roar", "howl": "wolf", "tweet": "bird",
    "chirp": "bird", "meow": "cat", "bark": "dog", "woof": "dog", "ribbit": "frog", "croak": "frog",
    "drumroll": "drum_roll", "fanfare": "tada", "clap": "applause",
    "claps": "applause", "clapping": "applause", "hooray": "cheer", "crowd": "cheer", "cheering": "cheer",
    "badumtss": "rimshot", "ba_dum_tss": "rimshot",
    "drum": "rimshot", "cricket": "crickets", "silence": "crickets", "trombone": "sad_trombone", "fail": "sad_trombone",
    "wah_wah": "sad_trombone", "ooh": "audience_ooh", "oooh": "audience_ooh", "aww": "audience_aww",
    "awww": "audience_aww", "whistle": "slide_whistle",
    # the unoffered files, to their nearest offered sound (none for the rest)
    "rifle": "gunshot", "stomp": "footsteps", "debris": "crash", "spaceship": "flyby", "magic": "spell",
}
MUSIC_SYNONYMS = {
    "quest": "adventure", "journey": "adventure", "happy": "adventure", "scary": "spooky", "creepy": "spooky",
    "horror": "spooky", "ghost": "spooky", "detective": "mystery", "suspense": "mystery", "sneaky": "mystery",
    "fight": "battle", "action": "battle", "danger": "battle", "run": "chase", "race": "chase", "escape": "chase",
    "heroic": "epic", "grand": "epic", "fairy_tale": "fairytale", "magic": "fairytale", "princess": "fairytale",
    "gentle": "calm", "peaceful": "calm", "lullaby": "calm", "village": "calm", "tender": "sad",
    "melancholy": "sad", "love": "romantic", "romance": "romantic", "win": "victory", "triumph": "victory",
    "celebration": "victory", "party": "victory", "scifi": "space", "sci_fi": "space", "future": "space",
    "robots": "space", "ocean": "underwater", "sea": "pirate", "ship": "pirate", "pirates": "pirate",
    "castle": "medieval", "knight": "medieval", "tavern": "medieval", "cowboy": "western", "wild_west": "western",
    "comedy": "funny", "cartoon": "funny", "silly": "funny", "quirky": "funny", "jazz": "lounge", "club": "lounge",
    "comedy_club": "lounge", "standup": "lounge", "stand_up": "lounge",
}


def _norm(name: str) -> str:
    return re.sub(r"[\s-]+", "_", name.strip().lower())


def _resolve(name: str, known: dict, synonyms: dict[str, str]) -> str | None:
    """Exact name, then a synonym, then a close spelling, then any word of it ("loud_gunshot" -> gunshot)."""
    name = _norm(name)
    if name in known:
        return name
    if (syn := synonyms.get(name)) in known:
        return syn
    if close := difflib.get_close_matches(name, list(known), n=1, cutoff=0.7):
        return close[0]
    for word in name.split("_"):
        if word in known:
            return word
        if (syn := synonyms.get(word)) in known:
            return syn
    return None


class SoundBook:
    """The library on disk. One-shots: sounds/sfx/<group>/<name>.ogg (the name is the tag; the group only sorts
    the prompt's list). Music: sounds/music/<mood>/<track>.ogg, several tracks per mood."""

    def __init__(self, sfx: dict[str, Path], music: dict[str, list[Path]], groups: dict[str, str] | None = None) -> None:
        self.sfx, self.music = dict(sfx), {m: list(t) for m, t in music.items()}
        self.groups = dict(groups or {})  # sfx name -> group
        self.hidden: dict[str, Path] = {}  # OFFERED_NOT files: only a gesture's curated hit sound plays them

    @classmethod
    def load(cls, root: Path = SOUNDS_DIR) -> SoundBook:
        sfx, groups, music, hidden = {}, {}, {}, {}
        for p in sorted((root / "sfx").glob("*/*.ogg")):
            if p.stem not in OFFERED_NOT:
                sfx[p.stem], groups[p.stem] = p, p.parent.name
            else:
                hidden[p.stem] = p
        for p in sorted((root / "music").glob("*/*.ogg")):
            music.setdefault(p.parent.name, []).append(p)
        book = cls(sfx, music, groups)
        book.hidden = hidden
        return book

    def resolve_sfx(self, name: str) -> str | None:
        if _norm(name) in OFFERED_NOT:  # never a close spelling of a dropped file ("kiss" -> "kick")
            return SFX_SYNONYMS.get(_norm(name)) if SFX_SYNONYMS.get(_norm(name)) in self.sfx else None
        return _resolve(name, self.sfx, SFX_SYNONYMS)

    def resolve_music(self, name: str) -> str | None:
        return _resolve(name, self.music, MUSIC_SYNONYMS)

    def describe_sfx(self) -> str:
        """One line per group: `- weapons: gunshot, rifle (a machine-gun burst), ...`."""
        by_group: dict[str, list[str]] = {}
        for n in self.sfx:
            by_group.setdefault(self.groups.get(n, "other"), []).append(f"{n} ({ABOUT[n]})" if n in ABOUT else n)
        return "\n".join(f"- {g}: {', '.join(names)}" for g, names in by_group.items()) or "none today"

    def describe_music(self) -> str:
        return "\n".join(f"- {n}: {MUSIC_ABOUT[n]}" if n in MUSIC_ABOUT else f"- {n}" for n in self.music) or "none today"


class _Track:
    """One playing music track: its ducking gain, and a flag that ends it at once."""

    def __init__(self, gain: float) -> None:
        self.cut = False
        self.gain = gain


class Soundboard:
    """Plays the tags: `cue("sfx", "explosion")`, `cue("music", "spooky")`, `cue("music", "none")`.

    `player` is a started BackgroundAudioPlayer (or anything with its `play(AudioConfig, loop=) -> PlayHandle`);
    `start()` makes and publishes one. Until then, cues are logged and dropped."""

    def __init__(self, book: SoundBook, player: BackgroundAudioPlayer | None = None) -> None:
        self.book = book
        self.player = player
        self.music_name: str | None = None
        self._music: tuple[PlayHandle, _Track] | None = None
        self._sfx: set[PlayHandle] = set()
        self._ducked = {"agent": False, "user": False}
        self._next_track: dict[str, int] = {}  # mood -> which of its tracks plays next (they take turns)
        self.min_gap_s = SFX_MIN_GAP_S
        self._last_sfx = -1e9  # loop time of the last sfx played under the budget

    async def start(self, room: rtc.Room, session: AgentSession) -> None:
        player = BackgroundAudioPlayer()
        # A short send queue and small mix blocks: with LiveKit's defaults (400 ms queue, 100 ms blocks) the mixer
        # runs ~0.4 s ahead of real time whenever music plays, so every sfx came out ~0.5 s after its cue.
        # ponytail: private attributes of livekit-agents 1.8; drop this if a release exposes the sizes.
        with contextlib.suppress(Exception):
            old = (player._audio_mixer, player._audio_source)
            player._audio_source = rtc.AudioSource(48000, 1, queue_size_ms=SFX_BUFFER_MS)
            player._audio_mixer = rtc.AudioMixer(48000, 1, blocksize=int(48000 * SFX_BLOCK_S), capacity=1,
                                                 stream_timeout_ms=200)
            for o in old:
                await o.aclose()
        try:
            await player.start(room=room)
        except Exception as e:  # noqa: BLE001 - no sound is better than no show
            logger.warning("background audio failed to start (%s): no sfx or music this session", e)
            return
        self.player = player
        logger.info("sfx volume %.2f, budget one per %.0f s, send queue %d ms", SFX_VOLUME, self.min_gap_s,
                    SFX_BUFFER_MS)
        session.on("agent_state_changed", lambda ev: self.duck("agent", ev.new_state == "speaking"))
        session.on("user_state_changed", lambda ev: self.duck("user", ev.new_state == "speaking"))

    def duck(self, who: str, on: bool) -> None:
        self._ducked[who] = on

    def target(self) -> float:
        return MUSIC_DUCKED if any(self._ducked.values()) else MUSIC_VOLUME

    def backlog(self) -> float:
        """Seconds of sound already queued ahead of real time on the sound track: an sfx cued now is heard this
        much later (plus the network), so callers cue it this much early."""
        source = getattr(self.player, "_audio_source", None)
        try:
            return float(source.queued_duration) + SFX_BLOCK_S if source is not None else 0.0
        except Exception:  # noqa: BLE001
            return 0.0

    def cue(self, kind: str, name: str, budget: bool = True) -> None:
        if kind in (SFX_TAG, "hit"):
            self.sfx(name, budget, curated=kind == "hit")
        elif kind == MUSIC_TAG:
            self.music(name)

    def sfx(self, name: str, budget: bool = True, curated: bool = False) -> bool:
        """Play a one-shot. Under the budget (stories), one inside `min_gap_s` of the last is dropped. True if it
        plays. `curated`: a gesture's own hit sound, which may be one not offered to the LLM."""
        resolved = name if curated and name in self.book.hidden else self.book.resolve_sfx(name)
        if resolved is None:
            logger.info("dropped [sfx:%s]: unknown or unoffered sound", name)
            return False
        now = asyncio.get_running_loop().time()
        if budget and now - self._last_sfx < self.min_gap_s:
            logger.info("dropped [sfx:%s]: budget (%.1f s since the last, gap %.0f s)", resolved,
                        now - self._last_sfx, self.min_gap_s)
            return False
        if budget:
            self._last_sfx = now
        logger.info("sfx %s", resolved)
        if self.player is None:
            return True
        path = self.book.sfx.get(resolved) or self.book.hidden[resolved]
        handle = self.player.play(AudioConfig(str(path), volume=SFX_VOLUME))
        self._sfx.add(handle)
        asyncio.ensure_future(self._forget(handle))
        return True

    async def _forget(self, handle: PlayHandle) -> None:
        with contextlib.suppress(Exception):
            await handle.wait_for_playout()
        self._sfx.discard(handle)

    def music(self, name: str) -> None:
        if _norm(name) in MUSIC_OFF:
            self.stop_music()
            return
        resolved = self.book.resolve_music(name)
        if resolved is None:
            logger.info("dropped [music:%s]: unknown music", name)
            return
        if resolved == self.music_name:
            return  # already playing: keep it going rather than restarting it
        tracks = self.book.music[resolved]
        turn = self._next_track.get(resolved, random.randrange(len(tracks)))
        self._next_track[resolved] = (turn + 1) % len(tracks)
        path = tracks[turn % len(tracks)]
        logger.info("music %s (%s)", resolved, path.stem)
        self.stop_music(fade=True)  # fades out while the new one fades in: a crossfade
        self.music_name = resolved
        if self.player is not None:
            track = _Track(self.target())
            source = self._music_frames(path, track)
            self._music = (self.player.play(AudioConfig(source, fade_in=MUSIC_FADE_S, fade_out=MUSIC_FADE_S)), track)

    def stop_music(self, fade: bool = True) -> None:
        if self.music_name:
            logger.info("music off")
        self.music_name = None
        if self._music is not None:
            handle, track = self._music
            track.cut = not fade  # ends the stream at the next frame instead of after the fade-out
            handle.stop()
            self._music = None

    def stop_sfx(self) -> None:
        """The listener cut in: the sound effects stop with the voice (the music keeps going, ducked)."""
        for handle in list(self._sfx):
            handle.stop()
        self._sfx.clear()

    def stop_all(self) -> None:
        """STOP / end of the show: silence everything at once."""
        self.stop_music(fade=False)
        for handle in list(self._sfx):
            handle.stop()
        self._sfx.clear()

    async def aclose(self) -> None:
        self.stop_all()
        if isinstance(self.player, BackgroundAudioPlayer):
            await self.player.aclose()
        self.player = None

    async def _music_frames(self, path: Path, track: _Track) -> AsyncIterator[rtc.AudioFrame]:
        """The track, looped until cut, with the ducking gain gliding toward target() sample by sample."""
        while not track.cut:
            frames = audio_frames_from_file(str(path))
            try:
                async for frame in frames:
                    if track.cut:
                        return
                    yield self._duck_frame(frame, track)
            finally:
                await frames.aclose()

    def _duck_frame(self, frame: rtc.AudioFrame, track: _Track) -> rtc.AudioFrame:
        n, target = frame.samples_per_channel, self.target()
        glide = DUCK_S if target < track.gain else UNDUCK_S
        step = max(1e-3, MUSIC_VOLUME - MUSIC_DUCKED) / max(1.0, glide * frame.sample_rate)
        ramp = track.gain + np.sign(target - track.gain) * step * np.arange(1, n + 1, dtype=np.float32)
        ramp = np.minimum(ramp, target) if target >= track.gain else np.maximum(ramp, target)
        track.gain = float(ramp[-1])
        data = np.frombuffer(frame.data, dtype=np.int16).astype(np.float32)
        data *= np.repeat(ramp, frame.num_channels) if frame.num_channels > 1 else ramp
        return rtc.AudioFrame(data=np.clip(data, -32768, 32767).astype(np.int16).tobytes(),
                              sample_rate=frame.sample_rate, num_channels=frame.num_channels, samples_per_channel=n)
