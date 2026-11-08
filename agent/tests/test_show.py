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
from show import HUMAN, LIVE, SCRIPTED, SHOW_MD, Cursor, Script, ShowRunner, is_show, resolve_chain
from sounds import SoundBook
from storyteller import Storyteller, build_instructions

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
LEAD_PAUSE_S = 0.1
```

## Act 0: Cold open (human)
<!-- a note for the presenters -->
Hi, so, it's like a robot?

## Act 1: One
[sfx:record_scratch] [face:audience]
[talk:left] "Stop." [left:shake_head] Hello there everyone, I am {HOST_NAME} of {SHOW_NAME}, nice to meet you all today. Meet {FOUNDER_1}! {POINT_1} [right:wave]
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
    assert (s.show_name, s.host_name, s.founders, s.lead_pause_s) == ("Test Show", "Kermit", ["Ann", "Bo"], 0.1)
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


def test_lead_tags_fire_first_and_talk_tags_stay():
    lead, rest = Script.parse(MINI).act(1).split_lead()
    assert lead == "[sfx:record_scratch] [face:audience]"
    assert rest.startswith('[talk:left] "Stop."')
    assert Script.parse(MINI).act(1).lead_has_sound() and not Script.parse(MINI).act(2).lead_has_sound()


def test_first_line_and_estimates():
    s = Script.parse(MINI)
    assert s.act(1).first_line.startswith('"Stop." Hello there everyone')
    assert "[" not in s.act(1).first_line
    assert s.act(0).first_line == "Hi, so, it's like a robot?"
    two = s.act(2)
    words = len(two.spoken.split())
    # the bow moves to the start of the last (only) sentence, so it ends 2.5 s in: speech covers it
    assert two.estimate_s({"bow": 2.5}) == round(words / 2.6, 1)
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
            if talk_arm(t):
                continue
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
    # the takeover cuts in with the scratch and the snap to the audience, before any word
    assert s.act(1).split_lead()[0] == "[sfx:record_scratch] [face:audience]"
    # pointing gestures don't exist yet: their placeholders fall back
    assert "[left:look_at_other]" in s.act(2).text and "[right:double_take]" in s.act(1).text
    # the pre-demo slot is about three minutes
    total = sum(a.estimate_s(book.durations | book.duets) for a in s.acts if a.number <= 4)
    assert 120 < total < 200, total


def test_lars_pick_line_uses_the_baked_guest_tags():
    text = SHOW_MD.read_text().replace("LARS_PICK = off", "LARS_PICK = on")
    act1 = Script.parse(text, stage_book()).act(1).text
    assert "[pick:lars]" in act1 and "[place:lars]" in act1
    after = act1.split("[place:lars]")[1].split("[")[0]  # no tag for the right arm while Lars goes down
    assert len(after.split()) >= 10, after
    assert "[pick:lars]" not in Script.parse(text, GestureBook({"double_take": 1.0, "nod": 1.0})).act(1).text


def test_prompt_uses_the_host_name():
    text = build_instructions(stage_book(), SoundBook.load(), Script.load())
    assert text.startswith("You are Lars, a small green plush frog") and "{{HOST_NAME}}" not in text


# --- cue playback -----------------------------------------------------------------------------------------------

class RecStage:
    def __init__(self):
        self.events, self.busy_until = [], 0.0

    def _rec(self, *what):
        self.events.append((time.perf_counter(), *what))

    def play(self, arm, gesture, duration):
        self._rec("play", f"{arm}:{gesture}")

    def face(self, stance):
        self._rec("face", stance)

    def talk(self, arm, on):
        self._rec("talk", f"{arm}:{'on' if on else 'off'}")

    def freeze(self):
        self._rec("freeze", "")

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
        # instantly: the arms freeze, then the lead: the scratch and the snap to the audience
        assert show.kinds()[:3] == [("freeze", ""), ("sfx", "record_scratch"), ("face", "audience")]
        await asyncio.sleep(4.0)
        events = show.kinds()
        print([(round(t - t0, 2), k, w) for t, k, w in show.stage.events])
        assert [e for e in events if e[0] in ("play", "talk")] == [  # shake_head moved to its sentence's start
            ("play", "left:shake_head"), ("talk", "left:on"), ("talk", "left:off"),
            ("play", "left:look_at_other"), ("play", "right:wave")]
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
        assert ("talk", "left:off") in show.kinds()  # the mouth shut on the cut
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
        print(f"cue -> voice: primed {primed} ms (0.1 s lead pause), streamed {streamed} ms")
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
            ("play", "left:shake_head"), ("talk", "left:on"), ("talk", "left:off"), ("play", "right:wave")]
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
