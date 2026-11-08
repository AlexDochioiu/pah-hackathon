"""Show mode (show.py, show.md): the script, the act cursor, and cue playback through a real AgentSession with
LiveKit's fakes (audio plays out in real time; the stage is a recorder)."""
import asyncio
import json
import time
from pathlib import Path

import pytest

from director import Director
from gestures import FACINGS, GestureBook, GestureSync, Tag, TagParser, strip_tags_for_tts, talk_arm
from lkfakes.fake_session import FakeActions, create_session
from show import (HUMAN, LETTERS_PER_S, LIVE, SCRIPTED, SHOW_MD, Cursor, Script, ShowRunner, dream_parts, is_show,
                  resolve_chain)
from sounds import SoundBook
from storyteller import Storyteller, build_instructions, narrator_voice

ROBOT = Path(__file__).resolve().parents[2] / "robot"


def stage_book() -> GestureBook:
    """The real stage's gesture and duet lists, from the trajectories robot/stage.py serves."""
    gestures, duets, ensembles = {}, {}, {}
    for f in sorted((ROBOT / "gestures").glob("*.json")):
        j = json.loads(f.read_text())
        gestures[j["name"]] = j["keys"][-1]["t"]
    for f in sorted((ROBOT / "duets").glob("*.json")):
        j = json.loads(f.read_text())
        (ensembles if j.get("kind") == "ensemble" else duets)[j["name"]] = j["keys"][-1]["t"]
    return GestureBook(gestures, duets, ensembles)


MINI = """# test show
## Config
```ini
SHOW_NAME = Test Show     # a comment
HOST_NAME = Kermit
FOUNDER_1 = Ann @ back_left
FOUNDER_2 = Bo @ nowhere
SPOT_back_left = left:point_back_left | left:look_at_other
PICK = off
BUILT_WITH_ACT = 2
BUILT_2 = Thing Two: does two
BUILT_1 = Thing One: does one
```

## Act 0: Cold open (human)
<!-- a note for the presenters -->
Hi, so, it's like a robot?

## Act 1: One
[face:audience]
Stop. [left:shake_head] Hello there everyone, I am {HOST_NAME} of {SHOW_NAME}, nice to meet you all today. Meet {FOUNDER_1}! {POINT_1} [right:wave]
{?PICK} This line is only there when PICK is on.

## Act 2: Two
[face:audience] Second act begins now with a good many words to say. [left:bow]

## Act 3: Demo (live)
Who wants a story? {MYSTERY}

## Live context
You are {HOST_NAME} on stage.
"""


def tags(text: str) -> list[Tag]:
    return [p for p in TagParser().feed(text) if isinstance(p, Tag)]


# --- the script --------------------------------------------------------------------------------------------------

def test_parse_acts_config_and_names():
    s = Script.parse(MINI)
    assert (s.show_name, s.host_name, s.founders) == ("Test Show", "Kermit", ["Ann", "Bo"])
    assert s.built_with == [{"tool": "Thing One", "how": "does one"}, {"tool": "Thing Two", "how": "does two"}]
    assert s.to_json()["built_with_act"] == 2 and s.to_json()["built_with"] == s.built_with
    assert [(a.number, a.kind, a.title) for a in s.acts] == [
        (0, HUMAN, "Cold open"), (1, SCRIPTED, "One"), (2, SCRIPTED, "Two"), (3, LIVE, "Demo")]
    assert s.act(0).note == "a note for the presenters" and "<!--" not in s.act(0).text
    one = s.act(1).text
    assert "I am Kermit of Test Show" in one and "Meet Ann!" in one
    assert "[left:point_back_left]" in one  # no gesture list (the page): the first option
    assert "only there when PICK" not in one  # {?PICK} with PICK = off
    assert s.live_context == "You are Kermit on stage."
    assert s.act(3).text == "Who wants a story?"
    assert any("MYSTERY" in w for w in s.warnings) and any("SPOT_nowhere" in w for w in s.warnings)


def test_point_falls_back_when_the_stage_lacks_the_gesture():
    book = GestureBook({"look_at_other": 2.8, "shake_head": 2.9})
    assert "[left:look_at_other]" in Script.parse(MINI, book).act(1).text
    book = GestureBook({"point_back_left": 2.0, "look_at_other": 2.8})
    assert "[left:point_back_left]" in Script.parse(MINI, book).act(1).text
    assert resolve_chain("right:pick_lars | right:nod", lambda arm, name: False) == "[right:nod]"
    assert resolve_chain("duet:tip_hats", None) == "[duet:tip_hats]"


def test_conditional_line_on():
    s = Script.parse(MINI.replace("PICK = off", "PICK = on"))
    assert "This line is only there when PICK is on." in s.act(1).text


def test_lead_tags_fire_first():
    lead, rest = Script.parse(MINI).act(1).split_lead()
    assert lead == "[face:audience]"
    assert rest.startswith("Stop. [left:shake_head]")


def test_first_line_and_estimates():
    s = Script.parse(MINI)
    assert s.act(1).first_line.startswith("Stop. Hello there everyone")
    assert "[" not in s.act(1).first_line
    assert s.act(0).first_line == "Hi, so, it's like a robot?"
    two = s.act(2)
    letters = sum(c.isalnum() for c in two.spoken)
    # the bow moves to the start of the last (only) sentence, so it ends 2.5 s in: speech covers it
    assert two.estimate_s({"bow": 2.5}) == round(letters / LETTERS_PER_S, 1)
    assert two.estimate_s({"bow": 9.0}) > two.estimate_s({"bow": 2.5})


def test_is_show():
    assert is_show('{"mode": "show"}') and not is_show("") and not is_show("garbage") and not is_show(None)


def test_cursor_next_prev_jump():
    c = Cursor()
    assert [c.next(3), c.next(3), c.next(3), c.next(3)] == [1, 2, 3, None]  # past the end: nothing plays
    assert c.act == 3
    assert c.prev() == 2 and c.prev() == 1 and c.prev() == 0 and c.prev() == 0
    assert c.jump(2, 3) == 2 and c.next(3) == 3
    assert c.jump(9, 3) == 3 and c.jump(0, 3) == 0 and c.next(3) == 1


# --- the real script ------------------------------------------------------------------------------------------------

def test_show_md_is_complete_and_every_tag_exists_on_the_stage():
    book, sounds = stage_book(), SoundBook.load()
    s = Script.load(SHOW_MD, book)
    assert s.warnings == []
    assert (s.show_name, s.host_name, s.founders) == ("Stage Hands", "Lars", ["Alex", "Michael", "Darius"])
    assert [a.kind for a in s.acts] == [HUMAN, SCRIPTED, SCRIPTED, SCRIPTED, LIVE, SCRIPTED]
    assert "Lars" in s.act(1).text and "Stage Hands" in s.act(1).text and "Lars" in s.live_context
    for name in s.founders:
        assert name in s.act(2).text and name in s.act(5).text
    for act in s.acts:
        assert "{" not in act.text and "}" not in act.text, act.number
        for t in tags(act.text):
            # owner, Nov 7: one voice in the show, the host's. The arms never speak, they react.
            assert not talk_arm(t) and t.arm != "voice", (act.number, t.raw)
            if t.arm in ("left", "right", "both"):
                assert t.name in book.durations, (act.number, t.raw)
            elif t.arm in ("duet", "ensemble"):
                assert t.name in book.duets, (act.number, t.raw)
            elif t.arm == "sfx":
                assert sounds.resolve_sfx(t.name) == t.name, (act.number, t.raw)
            elif t.arm == "music":
                assert t.name == "none" or sounds.resolve_music(t.name) == t.name, (act.number, t.raw)
            elif t.arm == "face":
                assert t.name in FACINGS, (act.number, t.raw)
            else:
                pytest.fail(f"act {act.number}: unexpected tag {t.raw}")
    assert '"' not in s.act(1).spoken.replace("…", "")  # no quoted lines left over either
    # the takeover cuts in hard: the snap to the audience, then the first word at once
    assert s.act(1).split_lead()[0] == "[face:audience]"
    assert "[both:present]" in s.act(1).text  # the stage hands are lifted as they're introduced
    # the founder points are real gestures now (Nov 7 night); Lars's point still falls back
    for tag in ("[left:point_back_left]", "[right:point_back_centre]", "[right:point_back_right]"):
        assert tag in s.act(2).text, tag
    assert "[right:double_take]" in s.act(1).text
    assert sum("table" in a.spoken for a in s.acts) == 1  # one table gag, not three
    # the budget (owner, Nov 7; ~5 minutes with the demo): acts 0-3 <= 2:00, the demo intro <= 10 s, the finale <= 20 s
    # (the estimate is the host voice's measured pace plus the tails: within ~2 s of the real audio, Nov 7)
    est = {a.number: a.estimate_s(book.durations | book.duets) for a in s.acts}
    print(est)
    assert 80 < sum(est[n] for n in range(4)) <= 120, est
    assert est[4] <= 10 and est[5] <= 20, est
    # the "Built with" card: what the code really uses, shown during the pitch
    tools = " ".join(b["tool"] for b in s.built_with)
    assert s.built_with_act == 3 and all(b["how"] for b in s.built_with)
    for name in ("LiveKit", "Fish Audio", "YAM", "MuJoCo", "OpenCV", "CC0", "Claude Code", "Dimensional"):
        assert name in tools, name
    assert "Dream" not in tools  # commented out until the Dream-1 pick works on the real arms


def test_lars_pick_line_uses_the_baked_guest_tags():
    text = SHOW_MD.read_text().replace("LARS_PICK = off", "LARS_PICK = on")
    act1 = Script.parse(text, stage_book()).act(1).text
    assert "[pick:lars]" in act1 and "[place:lars]" in act1
    after = act1.split("[place:lars]")[1].split("[")[0]  # no tag for the right arm while Lars goes down
    assert len(after.split()) >= 10, after
    assert "[pick:lars]" not in Script.parse(text, GestureBook({"double_take": 1.0, "nod": 1.0})).act(1).text


def test_story_has_an_unnamed_narrator_and_the_show_has_lars():
    book, sounds, script = stage_book(), SoundBook.load(), Script.load()
    story = build_instructions(book, sounds, script)
    show = build_instructions(book, sounds, script, show=True)
    # (Lars may still turn up in a story as a toy the right arm can pick up, never as the narrator)
    assert story.startswith("You are the storyteller") and "You are Lars" not in story and "host the show" not in story
    assert show.startswith("You are Lars, a small green plush frog")
    assert "{{" not in story and "{{" not in show
    assert narrator_voice(False) != narrator_voice(True)  # David narrates stories, Lars ("Deadpool better") hosts the show


# --- cue playback -----------------------------------------------------------------------------------------------

class RecStage:
    def __init__(self):
        self.events, self.busy_until = [], 0.0

    def _rec(self, *what):
        self.events.append((time.perf_counter(), *what))

    def play(self, arm, gesture, duration, **_):
        self._rec("play", f"{arm}:{gesture}")

    def face(self, stance):
        self._rec("face", stance)

    def talk(self, arm, on):
        self._rec("talk", f"{arm}:{'on' if on else 'off'}")

    def freeze(self):
        self._rec("freeze", "")

    def idle(self):
        self.send("/idle")

    def send(self, path, body=None, delay=0.0):
        self._rec(path, "")


class Show:
    """A started session with GestureSync installed exactly as storyteller.py does, and a ShowRunner."""

    def __init__(self, script: Script, cache: Path, audio_s: float = 3.0):
        self.tts_inputs: list[str] = []

        async def record(text):
            async for chunk in text:
                self.tts_inputs.append(chunk)
                yield chunk

        self.session = create_session(FakeActions(), extra_kwargs={
            "tts_text_transforms": [strip_tags_for_tts, record], "use_tts_aligned_transcript": False})
        self.session.tts.update_options(fake_audio_duration=audio_s)
        self.stage = RecStage()
        self.director = Director(self.session, self.stage, idle_after_s=30, continue_after_s=30)
        self.book = GestureBook({"shake_head": 1.0, "wave": 1.0, "bow": 1.0, "look_at_other": 1.0})
        self.sync = GestureSync(self.book, self.stage.play, self.director.request_continue, self.stage.face,
                                self.stage.talk, on_sound=lambda kind, name: self.stage._rec(kind, name))
        self.agent = Storyteller(instructions="test")
        self.script, self.cache = script, cache

    async def __aenter__(self):
        await self.session.start(self.agent)
        self.sync.install(self.session.output)
        self.runner = ShowRunner(self.session, self.agent, self.script, self.stage, self.director, self.book,
                                 on_sound=lambda kind, name: self.stage._rec(kind, name), cache_dir=self.cache)
        return self

    async def __aexit__(self, *exc):
        await self.runner.aclose()
        await self.session.aclose()

    def kinds(self, since=0.0):
        return [(k, w) for t, k, w in self.stage.events if t >= since]


async def test_cue_plays_the_act_through_the_stage_in_step_with_speech(tmp_path):
    async with Show(Script.parse(MINI, GestureBook({"look_at_other": 1.0})), tmp_path) as show:
        t0 = time.perf_counter()
        show.runner.play(1)
        await asyncio.sleep(0.05)
        # instantly: the arms freeze, then the lead: the snap to the audience
        assert show.kinds()[:2] == [("freeze", ""), ("face", "audience")]
        assert show.agent.host_only and show.agent.scripted
        await asyncio.sleep(4.0)
        events = show.kinds()
        print([(round(t - t0, 2), k, w) for t, k, w in show.stage.events])
        assert [e for e in events if e[0] in ("play", "talk")] == [  # shake_head moved to its sentence's start
            ("play", "left:shake_head"), ("play", "left:look_at_other"), ("play", "right:wave")]
        wave_at = next(t for t, k, w in show.stage.events if w == "right:wave") - t0
        assert 1.5 < wave_at < 3.8  # with its sentence near the end of the 3 s of audio, not at the cue
        assert "[" not in "".join(show.tts_inputs) and "Kermit" in "".join(show.tts_inputs)
        assert show.runner.state == "done"
        history = [m.text_content for m in show.session.history.messages() if m.role == "assistant"]
        assert "[right:wave]" in history[-1]  # the live LLM later sees the act, tags included


async def test_a_new_cue_cuts_the_act_and_starts_the_next(tmp_path):
    async with Show(Script.parse(MINI), tmp_path) as show:
        show.runner.play(1)
        await asyncio.sleep(1.2)  # act 1's voice is about half a second in
        cut = time.perf_counter()
        show.runner.play(2)
        await asyncio.sleep(3.5)
        after = show.kinds(cut)
        print(after)
        assert after[0] == ("freeze", "")
        assert ("face", "audience") in after and ("play", "left:bow") in after
        assert ("play", "right:wave") not in show.kinds()  # act 1 never got there
        assert show.runner.act == 2 and show.runner.state == "done"


async def test_llm_speaks_only_after_the_live_act(tmp_path):
    async with Show(Script.parse(MINI), tmp_path) as show:
        show.runner.play(2)
        await asyncio.sleep(0.2)
        early = show.session.generate_reply(instructions="say something")
        await asyncio.sleep(0.3)
        assert early.interrupted
        show.runner.play(3)
        await asyncio.sleep(3.6)
        assert show.runner.state == "live" and show.runner.live
        late = show.session.generate_reply(instructions="say something")
        await asyncio.sleep(0.3)
        assert not late.interrupted
        show.runner.play(0)  # back to the start: nothing plays, the LLM is shut out again
        assert show.runner.state == "ready" and not show.runner.live


async def test_primed_audio_starts_at_once_and_is_cached(tmp_path):
    async with Show(Script.parse(MINI), tmp_path, audio_s=2.0) as show:
        await show.runner.prime()
        assert sorted(show.runner.audio) == [1, 2, 3]
        assert all(abs(d - 2.0) < 0.1 for d in show.runner.durations.values())
        assert len(list(tmp_path.glob("*.wav"))) == 3
        show.session.tts.update_options(fake_timeout=1.0)  # a slow TTS from here on
        show.runner.play(1)
        await asyncio.sleep(0.6)
        primed = show.runner.latency_ms
        show.runner.audio.pop(2)
        show.runner.play(2)  # not primed: waits for the slow TTS
        await asyncio.sleep(1.8)
        streamed = show.runner.latency_ms
        print(f"cue -> voice: primed {primed} ms, streamed {streamed} ms")
        assert primed is not None and primed < 450
        assert streamed is not None and streamed > 1000

    async with Show(Script.parse(MINI), tmp_path, audio_s=2.0) as again:
        async def boom(text):
            raise AssertionError("should come from the cache")
        again.runner.synthesize = boom
        await again.runner.prime()
        assert sorted(again.runner.audio) == [1, 2, 3]
        # the voice-run marks come back too, so a replayed act's gestures and mouths stay pinned to its audio
        assert sorted(again.runner.marks) == [1, 2, 3] and again.runner.marks[1].total == pytest.approx(2.0, abs=0.1)
        t0 = time.perf_counter()
        again.runner.play(1)
        await asyncio.sleep(2.8)
        assert [e for e in again.kinds(t0) if e[0] in ("play", "talk")] == [
            ("play", "left:shake_head"), ("play", "right:wave")]
        wave_at = next(t for t, k, w in again.stage.events if w == "right:wave") - t0
        assert 0.8 < wave_at < 2.3, wave_at  # near the end of the 2 s of audio
        assert again.runner.state == "done"


async def test_local_cues_only_for_this_room(tmp_path):
    import aiohttp
    async with Show(Script.parse(MINI), tmp_path) as show:
        show.runner.room_name = "story-a"
        server = asyncio.create_task(show.runner.serve_cues(18793))
        await asyncio.sleep(0.3)
        async with aiohttp.ClientSession() as http:
            async with http.post("http://127.0.0.1:18793/play", json={"act": 2, "room": "story-old"}) as r:
                assert r.status == 409  # an old session still on the port: app.py falls back to the RPC
            assert show.runner.act == 0
            async with http.post("http://127.0.0.1:18793/play", json={"act": 2, "room": "story-a"}) as r:
                assert r.status == 200 and (await r.json())["act"] == 2
        assert show.runner.act == 2 and show.runner.state == "playing"
        server.cancel()


AUTO = """# auto
## Act 0: Open (human)
Hi.
## Act 1: One
[face:audience] Act one now.
## Act 2: Two
Act two now.
## Act 3: Three
Act three now.
## Act 4: Demo (live)
Who wants a story?
## Act 5: Finale
Goodnight.
"""


def _record_plays(runner):
    played, play = [], runner.play

    def rec(n):
        played.append(n)
        play(n)
    runner.play = rec
    return played


async def test_auto_advance_runs_acts_one_to_four_and_stops_before_the_finale(tmp_path, monkeypatch):
    import show as show_mod
    monkeypatch.setattr(show_mod, "AUTO_GAP_S", 0.1)
    async with Show(Script.parse(AUTO), tmp_path, audio_s=0.4) as show:
        played = _record_plays(show.runner)
        show.runner.play(1)
        await asyncio.sleep(3.5)
        assert played == [1, 2, 3, 4] and show.runner.state == "live"
        assert show.runner.advanced.startswith("4.")  # the page moves its cursor to act 4
        await asyncio.sleep(1.0)
        assert played == [1, 2, 3, 4]  # never into the finale: that's a manual cue
        show.runner.play(5)
        await asyncio.sleep(1.2)
        assert played == [1, 2, 3, 4, 5] and show.runner.state == "done"


async def test_a_manual_jump_mid_chain_takes_over_and_the_chain_goes_on_from_there(tmp_path, monkeypatch):
    import show as show_mod
    monkeypatch.setattr(show_mod, "AUTO_GAP_S", 0.1)
    async with Show(Script.parse(AUTO), tmp_path, audio_s=0.4) as show:
        played = _record_plays(show.runner)
        show.runner.play(1)
        await asyncio.sleep(0.2)
        show.runner.play(3)  # mid act 1: cuts it, act 2 is skipped
        await asyncio.sleep(2.2)
        assert played == [1, 3, 4]
        show.runner.set_auto(False)  # the page's toggle off: one act at a time
        show.runner.play(2)
        await asyncio.sleep(1.2)
        assert played == [1, 3, 4, 2] and show.runner.state == "done"


# --- Dream-1 in the show (DREAM_PICK, off by default) ----------------------------------------------------------

def test_dream_pick_variant_parses_and_resolves():
    book = stage_book()
    assert "[dream:" not in Script.load(SHOW_MD, book).act(1).text  # off by default
    text = SHOW_MD.read_text().replace("DREAM_PICK = off", "DREAM_PICK = on").replace("LARS_PICK = off", "LARS_PICK = on")
    s = Script.parse(text, book)
    assert s.warnings == []
    act1 = s.act(1)
    # Dream-1's pick wins over LARS_PICK ({?LARS_PICK}{?!DREAM_PICK}); it ends held, our place puts Lars back
    assert "[dream:pick_lars]" in act1.text and "[pick:lars]" not in act1.text and "[place:lars]" in act1.text
    first, second = dream_parts(act1.split_lead()[1])
    assert first.endswith("[dream:pick_lars]") and second.startswith("Hello, back row!")
    assert act1.estimate_s(book.durations | book.duets) > 90  # the Dream-1 run is in the estimate
    assert s.config["DREAM_ARGS"].split()[:3] == ["--speed", "0.5", "--hold"]
    assert len(s.config["DREAM_OOPS"].split("|")) == 3


class DreamStage(RecStage):
    """[dream:...] like gestures.Stage: says a line, misses (an oops), says another, ends."""

    def __init__(self):
        super().__init__()
        self.dreaming, self.on_dream, self.on_dream_oops, self.dream_args = False, None, None, ""

    def play(self, arm, gesture, duration, **_):
        super().play(arm, gesture, duration)
        if arm == "dream":
            self.dreaming = True
            asyncio.get_running_loop().create_task(self._dream())

    async def _dream(self):
        self.on_dream("Let me ask Dream-1 how to pick me up.")
        await asyncio.sleep(0.6)
        self.on_dream_oops({"event": "oops", "kind": "empty_grip", "reaction": "facepalm", "sfx": "sad_trombone",
                            "text": "the default quip"})
        await asyncio.sleep(0.6)
        self.on_dream("Our own move, then!")
        self.dreaming = False


DREAM = """## Config
```ini
DREAM_ARGS = --speed 0.5 --patter 6
DREAM_OOPS = Oops one. | Oops two.
```

## Act 1: One
Watch this. [dream:pick_lars] Hello, back row! [left:bow]
"""


async def test_a_dream_act_waits_for_dream_1_and_a_miss_gets_a_quip_and_a_trombone(tmp_path):
    show = Show(Script.parse(DREAM), tmp_path, audio_s=0.4)
    show.stage = DreamStage()
    show.sync = GestureSync(show.book, show.stage.play, show.director.request_continue, show.stage.face,
                            show.stage.talk)
    async with show:
        assert show.stage.dream_args == "--speed 0.5 --patter 6"
        show.runner.play(1)
        await asyncio.sleep(5.0)
        said = " ".join(show.tts_inputs)
        print(said, show.kinds())
        order = [said.find(x) for x in ("Watch this.", "Let me ask", "Oops", "Our own move", "Hello, back row!")]
        assert -1 not in order and order == sorted(order), said  # the next part waits for Dream-1's lines
        assert "the default quip" not in said
        assert ("sfx", "sad_trombone") in show.kinds()
        plays = [w for k, w in show.kinds() if k == "play"]
        assert plays == ["dream:pick_lars", "left:bow"] and show.runner.state == "done"
