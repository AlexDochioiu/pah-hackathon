"""Sound tags: parsed out of the narration, never spoken, played at their spot in the speech; the music
loops, ducks, crossfades and stops."""
import asyncio
import time

import numpy as np
from livekit import rtc
from lkfakes.fake_session import FakeActions, create_session, run_session

import sounds
from director import Director
from gestures import DEFAULT_GESTURES, GestureBook, GestureSink, GestureSync, Tag, TagParser, strip_tags, tags_first
from sounds import SoundBook, Soundboard
from storyteller import Storyteller, build_instructions

BOOK = GestureBook(DEFAULT_GESTURES)
LIBRARY = SoundBook.load()


class FakeHandle:
    def __init__(self, log, what):
        self.log, self.what, self.stopped = log, what, False
        self._done = asyncio.get_event_loop().create_future()

    def stop(self):
        self.stopped = True
        self.log.append((time.time(), "stop", self.what))
        if not self._done.done():
            self._done.set_result(None)

    async def wait_for_playout(self):
        await self._done


class FakePlayer:
    """BackgroundAudioPlayer's play() without a room: records what would play."""

    def __init__(self):
        self.log, self.handles = [], []

    def play(self, cfg, loop=False):
        src = cfg.source
        what = f"sfx:{src.rsplit('/', 1)[1].removesuffix('.ogg')}" if isinstance(src, str) else "music"
        self.log.append((time.time(), "play", what))
        handle = FakeHandle(self.log, what)
        handle.source = src
        self.handles.append(handle)
        return handle


def test_sound_tags_parse_and_never_reach_the_voice():
    parser = TagParser()
    pieces = parser.feed("[music:spooky] The door opens. [sfx: Door Creak] Eek! [music:none]") + parser.flush()
    assert [p for p in pieces if isinstance(p, Tag)] == [
        Tag("music", "spooky", "[music:spooky]"), Tag("sfx", "door_creak", "[sfx: Door Creak]"),
        Tag("music", "none", "[music:none]")]
    assert strip_tags("BOOM! [sfx:explosion] Wow [music:battle]") == "BOOM!  Wow "


def test_tags_first_leaves_sound_tags_where_they_are():
    async def run(text):
        async def src():
            for i in range(0, len(text), 4):
                yield text[i:i + 4]
        return "".join([t async for t in tags_first(src())])

    text = 'Knock knock. [talk:left] "Who is there?" [sfx:rimshot] The dragon sneezes [sfx:explosion] BOOM! [right:roar]'
    assert asyncio.run(run(text)) == ('Knock knock. [talk:left] "Who is there?" [sfx:rimshot] [right:roar] The dragon '
                                      'sneezes [sfx:explosion] BOOM! ')


def test_library_and_fuzzy_names():
    assert len(LIBRARY.sfx) >= 60 and len(LIBRARY.music) >= 15
    assert all(len(tracks) >= 2 for tracks in LIBRARY.music.values()), {m: len(t) for m, t in LIBRARY.music.items()}
    for name in ("explosion", "rimshot", "crickets", "sad_trombone", "applause", "frog", "gunshot", "cannon", "laugh",
                 "rifle", "gallop", "debris"):  # laugh, rifle, gallop, debris: owner, round 4
        assert name in LIBRARY.sfx
    for name in sounds.OFFERED_NOT:  # on disk, never offered or played
        assert name not in LIBRARY.sfx and LIBRARY.resolve_sfx(name) in (None, "footsteps", "flyby", "spell", "laugh")
    assert not list(sounds.SOUNDS_DIR.glob("sfx/*/record_scratch.ogg"))
    assert all(len(n) <= 16 and n.replace("_", "").isalpha() for n in LIBRARY.sfx)  # short, tag-safe names
    assert LIBRARY.resolve_sfx("explosion") == "explosion"
    assert LIBRARY.resolve_sfx("big explosion") == "explosion_big"
    assert LIBRARY.resolve_sfx("loud gunshot") == "gunshot"
    assert LIBRARY.resolve_sfx("ribbit") == "frog"
    assert LIBRARY.resolve_sfx("kiss") is None and LIBRARY.resolve_sfx("mwah") is None
    assert LIBRARY.resolve_sfx("evil_laugh") is None and LIBRARY.resolve_sfx("scratch") is None
    assert LIBRARY.resolve_sfx("stomp") == "footsteps" and LIBRARY.resolve_sfx("sparkle") == "spell"
    assert LIBRARY.resolve_sfx("kaboom") == "explosion"
    assert LIBRARY.resolve_sfx("laughter") == "laugh" and LIBRARY.resolve_sfx("laugh_track") == "laugh"
    assert LIBRARY.resolve_sfx("laugh_big") == "laugh" and LIBRARY.resolve_sfx("rifle") == "rifle"
    assert LIBRARY.resolve_sfx("ba-dum-tss") == "rimshot"
    assert LIBRARY.resolve_sfx("explosoin") == "explosion"
    assert LIBRARY.resolve_sfx("xylophone_solo") is None
    assert LIBRARY.resolve_music("Spooky") == "spooky"
    assert LIBRARY.resolve_music("scary") == "spooky"
    assert LIBRARY.resolve_music("polka") is None
    assert LIBRARY.resolve_music("jazz club") == "lounge" and LIBRARY.resolve_music("cowboy") == "western"
    tracks = [p for ps in LIBRARY.music.values() for p in ps]
    total = sum(p.stat().st_size for p in [*LIBRARY.sfx.values(), *tracks])
    assert total < 40e6
    assert max(p.stat().st_size for p in tracks) < 3e6


def test_prompt_lists_the_library():
    text = build_instructions(BOOK, LIBRARY)
    assert "{{SFX}}" not in text and "{{MUSIC}}" not in text
    assert "- weapons: " in text and "rimshot (ba-dum-tss" in text and "- spooky:" in text


def test_soundboard_cues():
    async def run():
        player = FakePlayer()
        board = Soundboard(LIBRARY, player)
        board.cue("sfx", "explosion")
        board.cue("sfx", "no_such_sound_at_all")  # dropped with a log line
        board.cue("music", "calm")
        board.cue("music", "calm")  # already on: not restarted
        board.cue("music", "battle")  # crossfade: calm fades out as battle fades in
        board.cue("music", "none")
        board.cue("music", "spooky")
        board.cue("sfx", "rimshot")
        board.stop_all()  # STOP
        await asyncio.sleep(0)
        return player, board
    player, board = asyncio.run(run())
    log = [(k, w) for _, k, w in player.log]
    assert log[:8] == [
        ("play", "sfx:explosion"), ("play", "music"), ("stop", "music"), ("play", "music"), ("stop", "music"),
        ("play", "music"), ("play", "sfx:rimshot"), ("stop", "music")]
    assert sorted(log[8:]) == [("stop", "sfx:explosion"), ("stop", "sfx:rimshot")]  # STOP silences the sfx too
    assert board.music_name is None and all(h.stopped for h in player.handles)


def test_mood_tracks_take_turns():
    async def run():
        player = FakePlayer()
        board = Soundboard(LIBRARY, player)
        paths = []
        board._music_frames = lambda path, track: paths.append(path) or object()
        for _ in range(2):
            board.cue("music", "spooky")
            board.cue("music", "none")
        return paths
    paths = asyncio.run(run())
    assert len(paths) == 2 and paths[0] != paths[1] and all(p.parent.name == "spooky" for p in paths)


def test_music_loops_ducks_and_cuts():
    async def run():
        player = FakePlayer()
        board = Soundboard(LIBRARY, player)
        board.cue("music", "sad")
        (handle,) = player.handles
        frames = handle.source.__aiter__()
        first = await frames.__anext__()
        board.duck("agent", True)  # the narrator starts talking
        gains = []
        for _ in range(int(sounds.DUCK_S / 0.02) + 5):  # ~20 ms frames: well past the glide down
            await frames.__anext__()
            gains.append(board._music[1].gain)
        board.duck("agent", False)
        for _ in range(int(sounds.UNDUCK_S / 0.02) + 5):
            await frames.__anext__()
        back = board._music[1].gain
        board.stop_all()
        rest = [f async for f in frames]  # the stream ends at once instead of looping or fading
        return first, gains, back, rest
    first, gains, back, rest = asyncio.run(run())
    assert isinstance(first, rtc.AudioFrame) and first.sample_rate == 48000
    assert np.all(np.diff(gains) <= 1e-6) and abs(gains[-1] - sounds.MUSIC_DUCKED) < 1e-6
    assert abs(back - sounds.MUSIC_VOLUME) < 1e-6
    assert rest == []


def test_sink_fires_sound_tags():
    cued, forwarded = [], []

    class Collect:
        async def capture_text(self, t):
            forwarded.append(t)

        def flush(self):
            pass

    sink = GestureSink(BOOK, lambda *a, **k: None, lambda: None, next_in_chain=Collect(),
                       on_sound=lambda kind, name: cued.append((kind, name)))

    async def run():
        for c in ["[music:spoo", "ky] It opens [sfx:door_creak] slowly. [mus", "ic:none]"]:
            await sink.capture_text(c)
        sink.flush()
    asyncio.run(run())
    assert cued == [("music", "spooky"), ("sfx", "door_creak"), ("music", "none")]
    assert "[" not in "".join(forwarded)


# --- end to end, as tests/test_session.py: scripted LLM, fake TTS audio playing out in real time ------------
SCENE = ("[music:spooky] Deep in the forest a door creaks open. [left:sneak] The little knight tiptoes in, "
         "slowly, slowly, past the sleeping troll, [sfx:footsteps] past the pile of bones, past the great big pot of soup, and "
         "then she trips. [sfx:explosion] KA-BOOM! [right:roar] [music:none]")


class FakeStage:
    def __init__(self):
        self.events, self.busy_until = [], 0.0

    def play(self, arm, gesture, duration, **_):
        self.events.append((time.time(), "play", f"{arm}:{gesture}"))

    def freeze(self):
        self.events.append((time.time(), "freeze", ""))

    def idle(self):
        self.send("/idle")

    def send(self, path, body=None):
        self.events.append((time.time(), path, ""))


async def run_scene(actions):
    tts_inputs = []

    async def record(text):
        async for chunk in text:
            tts_inputs.append(chunk)
            yield chunk

    from gestures import strip_tags_for_tts
    session = create_session(actions, extra_kwargs={
        "tts_text_transforms": [strip_tags_for_tts, record], "use_tts_aligned_transcript": False})
    session.tts.update_options(fake_audio_duration=4.0)
    stage, player = FakeStage(), FakePlayer()
    board = Soundboard(LIBRARY, player)
    director = Director(session, stage, idle_after_s=0.3, continue_after_s=0.3)
    sync = GestureSync(BOOK, stage.play, director.request_continue, on_sound=board.cue)
    speaking = []
    session.on("agent_state_changed", lambda ev: (speaking.append(time.time()) if ev.new_state == "speaking" else None,
                                                  board.duck("agent", ev.new_state == "speaking")))
    original_start = session.start

    async def start(agent, **kw):
        await original_start(agent, **kw)
        sync.install(session.output)
    session.start = start

    t0 = await run_session(session, Storyteller(instructions="test"), drain_delay=6)
    return t0, stage, player, speaking, "".join(tts_inputs)


async def test_sounds_fire_at_their_spot_in_the_speech():
    actions = FakeActions()
    actions.add_user_speech(0.5, 1.5, "tell me a spooky story")
    actions.add_llm(SCENE)
    t0, stage, player, speaking, tts_text = await run_scene(actions)

    start = speaking[0]
    log = [(round(t - start, 2), k, w) for t, k, w in player.log]
    print("sounds:", log, "gestures:", [(round(t - start, 2), w) for t, k, w in stage.events if k == "play"])
    assert [(k, w) for _, k, w in log] == [("play", "music"), ("play", "sfx:footsteps"), ("play", "sfx:explosion"),
                                          ("stop", "music")]
    music_on, stomp, boom, music_off = (t for t, _, _ in log)
    # The LLM wrote the whole scene at once, so sounds fired on arrival would all land together. They must land
    # where they were written, spread across the 4 s of speech. Only relative positions are checked (no wall-clock
    # bounds), so a busy machine that stretches playout doesn't fail the test.
    assert music_on <= stomp <= boom <= music_off and music_off - music_on > 2.0
    spoken = strip_tags(SCENE)
    expected = {w: len(strip_tags(SCENE.split(f"[sfx:{w}]")[0])) / len(spoken) for w in ("footsteps", "explosion")}
    at = {"footsteps": (stomp - music_on) / (music_off - music_on), "explosion": (boom - music_on) / (music_off - music_on)}
    for w in at:  # within a fifth of the speech of where the tag sits in the text (pacing is by syllables, not chars)
        assert abs(at[w] - expected[w]) < 0.2, (w, at, expected)
    assert "[" not in tts_text and "sfx" not in tts_text and "KA-BOOM" in tts_text


async def test_interruption_cuts_later_sounds():
    actions = FakeActions()
    actions.add_user_speech(0.5, 1.5, "tell me a spooky story")
    actions.add_llm(SCENE)
    actions.add_user_speech(3.0, 4.0, "make it a happy story")
    t0, stage, player, speaking, _ = await run_scene(actions)
    assert [(k, w) for _, k, w in player.log] == [("play", "music")]  # the explosion was never reached
