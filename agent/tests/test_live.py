"""Live chat (open mic): the mode choice, the interruption knobs, and what a real cut-in does to the stage."""
import asyncio
import time
from types import SimpleNamespace as Ev

import pytest

from director import Director, PushToTalk
from gestures import DEFAULT_GESTURES, GestureBook, GestureSync, Hit
from live import LiveTurns, interruption_options, is_live
from sounds import SoundBook, Soundboard
from test_ptt import Participant, Recorder, Stage


class Session(Recorder):
    """Keeps the event handlers so a test can emit session events in order, like AgentSession does."""

    def __init__(self):
        super().__init__()
        self.handlers = {}
        self.current_speech = None

    def on(self, name, fn):
        self.handlers.setdefault(name, []).append(fn)

    def emit(self, name, **ev):
        for fn in self.handlers.get(name, []):
            fn(Ev(**ev))


def live_setup():
    session, stage, hushed = Session(), Stage(), []
    director = Director(session, stage, idle_after_s=0.05, continue_after_s=0.05)
    LiveTurns(session, director, lambda: hushed.append(1))
    return session, stage, director, hushed


def test_mode_comes_from_the_dispatch_metadata():
    from app import dispatch_metadata
    assert is_live(dispatch_metadata("live")) and not is_live(dispatch_metadata(None))
    assert dispatch_metadata("hold") == "" and dispatch_metadata(None) == ""  # push-to-talk: no metadata, as before
    assert not is_live(dispatch_metadata("show")) and not is_live("not json") and not is_live('["live"]')


def test_interruption_defaults(monkeypatch):
    for k in ("LIVE_INTERRUPT_MODE", "LIVE_MIN_INTERRUPT_S", "LIVE_MIN_INTERRUPT_WORDS", "LIVE_RESUME",
              "LIVE_FALSE_INTERRUPT_S"):
        monkeypatch.delenv(k, raising=False)
    assert interruption_options() == {"enabled": True, "mode": "adaptive", "min_duration": 0.6, "min_words": 2,
                                      "resume_false_interruption": True, "false_interruption_timeout": 2.0}


def test_interruption_knobs(monkeypatch):
    monkeypatch.setenv("LIVE_INTERRUPT_MODE", "vad")
    monkeypatch.setenv("LIVE_MIN_INTERRUPT_S", "1.2")
    monkeypatch.setenv("LIVE_MIN_INTERRUPT_WORDS", "0")
    monkeypatch.setenv("LIVE_RESUME", "0")
    monkeypatch.setenv("LIVE_FALSE_INTERRUPT_S", "3")
    opts = interruption_options()
    assert (opts["mode"], opts["min_duration"], opts["min_words"]) == ("vad", 1.2, 0)
    assert not opts["resume_false_interruption"] and opts["false_interruption_timeout"] == 3.0
    monkeypatch.setenv("LIVE_INTERRUPT_MODE", "auto")
    assert "mode" not in interruption_options()  # LiveKit picks


def test_interruption_options_reach_livekit():
    from livekit.agents.voice.turn import _resolve_interruption
    resolved = _resolve_interruption(interruption_options())
    assert resolved["min_duration"] == 0.6 and resolved["backchannel_boundary"] == (1.0, 1.0)  # the rest: LiveKit's


class Speech:
    """A SpeechHandle: done callbacks, and whether it ended interrupted."""

    def __init__(self):
        self.callbacks, self.interrupted = [], False

    def add_done_callback(self, fn):
        self.callbacks.append(fn)

    def finish(self, interrupted=False):
        self.interrupted = interrupted
        for fn in self.callbacks:
            fn(self)


def start_speaking(session):
    speech = session.current_speech = Speech()
    session.emit("speech_created", speech_handle=speech)
    session.emit("agent_state_changed", old_state="thinking", new_state="speaking")
    return speech


async def test_noise_alone_does_not_freeze_the_arms():
    session, stage, director, hushed = live_setup()
    start_speaking(session)
    session.emit("user_state_changed", old_state="listening", new_state="speaking")  # VAD heard something
    session.emit("user_state_changed", old_state="speaking", new_state="listening")
    assert stage.sent == [] and hushed == []  # the voice never stopped for it: the arms keep acting


async def test_a_paused_voice_keeps_the_arms_acting_until_it_resumes():
    session, stage, director, hushed = live_setup()
    speech = start_speaking(session)
    director.continue_pending = True  # the scene's [next] was already heard
    session.emit("user_state_changed", old_state="listening", new_state="speaking")
    session.emit("agent_state_changed", old_state="speaking", new_state="listening")  # LiveKit paused the voice
    session.emit("user_state_changed", old_state="speaking", new_state="listening")  # only noise: no words
    await asyncio.sleep(0.15)
    assert stage.sent == [] and hushed == []  # no freeze, no idle, no next scene while paused
    assert "reply" not in session.calls and director.continue_pending
    session.emit("agent_state_changed", old_state="listening", new_state="speaking")  # false interruption: resumed
    assert not director.holding
    speech.finish()
    session.emit("agent_state_changed", old_state="speaking", new_state="listening")
    await asyncio.sleep(0.15)
    assert stage.sent == [] and hushed == [] and "reply" in session.calls  # the story carries on to its next scene


async def test_a_real_cut_in_freezes_and_hushes_like_a_press():
    session, stage, director, hushed = live_setup()
    speech = start_speaking(session)
    director.continue_pending = True
    session.emit("user_state_changed", old_state="listening", new_state="speaking")
    session.emit("agent_state_changed", old_state="speaking", new_state="listening")  # paused first
    assert stage.sent == []
    speech.finish(interrupted=True)  # the listener's words came: LiveKit cancels the paused speech
    assert stage.sent == ["/freeze"] and hushed == [1] and not director.continue_pending
    await asyncio.sleep(0.15)
    assert stage.sent == ["/freeze"]  # no idling while the listener talks
    session.emit("user_state_changed", old_state="speaking", new_state="listening")
    await asyncio.sleep(0.15)
    assert stage.sent == ["/freeze", "/idle"]


async def test_a_cut_in_without_a_pause_freezes_too():  # LIVE_RESUME=0: LiveKit cancels at once
    session, stage, director, hushed = live_setup()
    speech = start_speaking(session)
    session.emit("user_state_changed", old_state="listening", new_state="speaking")
    speech.finish(interrupted=True)
    assert stage.sent == ["/freeze"] and hushed == [1]


async def test_the_end_of_a_line_or_an_unplayed_reply_is_not_a_cut_in():
    session, stage, director, hushed = live_setup()
    start_speaking(session).finish()
    session.emit("agent_state_changed", old_state="speaking", new_state="listening")
    unplayed = Speech()
    session.emit("speech_created", speech_handle=unplayed)  # a reply discarded before it was heard
    unplayed.finish(interrupted=True)
    assert "/freeze" not in stage.sent and hushed == []


async def test_a_turn_between_scenes_drops_the_next_scene():
    session, stage, director, hushed = live_setup()
    session.emit("agent_state_changed", old_state="speaking", new_state="listening")
    director.continue_pending = True
    session.emit("user_input_transcribed", is_final=False, transcript="make it")
    assert director.continue_pending
    session.emit("user_input_transcribed", is_final=True, transcript="make it a dragon")
    assert not director.continue_pending
    await asyncio.sleep(0.15)
    assert "reply" not in session.calls  # the user's turn gets the reply, not the next scene


async def test_push_to_talk_press_hushes_too():
    session, stage, room, hushed = Recorder(), Stage(), Participant(), []
    director = Director(session, stage, idle_after_s=0.05, continue_after_s=0.05)
    PushToTalk(session, director, lambda: hushed.append(1)).register(room)
    await room.methods["start_turn"](None)
    assert hushed == [1] and stage.sent == ["/freeze"]


async def test_a_cut_in_cancels_the_hit_sounds_still_to_come():
    sounds, starts = [], []
    book = GestureBook({**DEFAULT_GESTURES, "shoot": 3.4}, sounds={"shoot": ("gunshot", [0.05])})
    sync = GestureSync(book, lambda arm, name, secs, on_start=None: starts.append(on_start), lambda: None,
                       on_sound=lambda _k, n: sounds.append(n))
    sync.cancel_sounds()  # before install: nothing to cancel
    sync.install(Ev(audio=None, transcription=None))  # text-only output: the sink fires as text arrives
    await sync.sink.capture_text("[left:shoot] Bang! ")
    await sync.sink.capture_text("[left:shoot] Again! ")
    starts[0](time.monotonic(), 1.0)  # the first one started: its gunshot is 50 ms out
    sync.cancel_sounds()
    starts[1](time.monotonic(), 1.0)  # the stage answers the second after the cut
    await sync.sink.capture_text("[sfx:laser] ")  # and an [sfx] the old sentence would have taken
    await asyncio.sleep(0.15)
    assert sounds == ["laser"]  # played when spoken, as in any later sentence


def test_a_cancelled_hit_ignores_late_cues():
    played = []
    hit = Hit([0.0], "bonk", played.append, sync_s=0.0)
    hit.cancel()
    hit.started(None)  # refused after the cut: a pending [sfx] would play at once
    hit.sfx("roar")
    assert played == []


class Handle:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True

    async def wait_for_playout(self):
        await asyncio.sleep(10)


class Player:
    def __init__(self):
        self.handles = []

    def play(self, _cfg, loop=False):
        self.handles.append(Handle())
        return self.handles[-1]


async def test_stop_sfx_keeps_the_music():
    book = SoundBook.load()
    if not book.sfx or not book.music:
        pytest.skip("no sounds library")
    board = Soundboard(book, Player())
    board.music(next(iter(book.music)))
    board.sfx(next(iter(book.sfx)))
    music, sfx = board.player.handles
    board.stop_sfx()
    assert sfx.stopped and not music.stopped and board.music_name


# --- end to end through a real AgentSession (LiveKit's fakes): pause, resume, cut ---------------------------------

async def run_live(actions, monkeypatch):
    from lkfakes.fake_session import create_session, run_session
    from storyteller import Storyteller
    from gestures import strip_tags_for_tts
    from test_session import FakeStage

    monkeypatch.setenv("LIVE_INTERRUPT_MODE", "vad")  # adaptive needs LiveKit Cloud
    monkeypatch.setenv("LIVE_MIN_INTERRUPT_WORDS", "0")  # so a wordless noise can pause the voice
    session = create_session(actions, can_pause_audio=True, turn_handling={"interruption": interruption_options()},
                             extra_kwargs={"tts_text_transforms": [strip_tags_for_tts],
                                           "use_tts_aligned_transcript": False})
    session.tts.update_options(fake_audio_duration=4.0)
    stage, hushed, states = FakeStage(), [], []
    director = Director(session, stage, idle_after_s=0.3, continue_after_s=0.3)
    sync = GestureSync(GestureBook(DEFAULT_GESTURES), stage.play, director.request_continue)
    session.on("agent_state_changed", lambda ev: states.append((time.time(), ev.new_state)))
    session.on("agent_false_interruption", lambda ev: states.append((time.time(), f"false_interruption:{ev.resumed}")))
    original_start = session.start

    async def start(agent, **kw):  # as storyteller.py: the hook after start(), then the live stage direction
        await original_start(agent, **kw)
        sync.install(session.output)
        LiveTurns(session, director, lambda: hushed.append(time.time()))
    session.start = start
    t0 = await run_session(session, Storyteller(instructions="test"), drain_delay=8)
    return t0, stage, hushed, states


async def test_live_false_interruption_resumes_with_the_arms_in_sync(monkeypatch):
    from lkfakes.fake_session import FakeActions
    from test_session import STORY
    actions = FakeActions()
    actions.add_user_speech(0.5, 1.5, "tell me a story")
    actions.add_llm(STORY)
    actions.add_user_speech(3.2, 4.2, "")  # a noise mid-story: the voice pauses, no words come, it resumes
    t0, stage, hushed, states = await run_live(actions, monkeypatch)
    print([(round(t - t0, 2), s) for t, s in states], [(round(t - t0, 2), k, w) for t, k, w in stage.events])

    assert any(s == "false_interruption:True" for _, s in states)  # LiveKit did pause and resume the voice
    kinds = [(k, w) for _, k, w in stage.events]
    assert ("freeze", "") not in kinds and hushed == []  # the arms were never stopped
    roar = [t for t, k, w in stage.events if w == "right:roar"]
    resumed = [t for t, s in states if s == "speaking"][1]
    assert roar and roar[0] > resumed  # the tag after the pause fired with the resumed voice, not before


async def test_live_real_interruption_freezes_once_the_speech_is_cut(monkeypatch):
    from lkfakes.fake_session import FakeActions
    from test_session import STORY
    actions = FakeActions()
    actions.add_user_speech(0.5, 1.5, "tell me a story")
    actions.add_llm(STORY)
    actions.add_user_speech(3.2, 4.2, "make the dragon friendly")  # real words: the turn commits, the speech is cut
    actions.add_llm("Oh, a friendly dragon then!")
    t0, stage, hushed, states = await run_live(actions, monkeypatch)
    print([(round(t - t0, 2), s) for t, s in states], [(round(t - t0, 2), k, w) for t, k, w in stage.events])

    kinds = [(k, w) for _, k, w in stage.events]
    assert ("play", "left:wave") in kinds and ("play", "right:roar") not in kinds
    freezes = [t - t0 for t, k, _ in stage.events if k == "freeze"]
    assert len(freezes) == 1 and len(hushed) == 1
    assert freezes[0] > 3.2 + 0.6  # not at the first noise: once the interruption was real
