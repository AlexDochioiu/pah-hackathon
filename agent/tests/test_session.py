"""End to end through a real AgentSession with LiveKit's own fakes (no credentials): VAD, STT, LLM and TTS
are scripted, audio plays out in real time, and the stage is a recorder."""
import time

from storyteller import Storyteller
from lkfakes.fake_session import FakeActions, create_session, run_session

from director import CONTINUE_INSTRUCTIONS, Director
from gestures import DEFAULT_GESTURES, GestureBook, GestureSync, strip_tags_for_tts

STORY = ("Once upon a time a knight waved hello. [left:wave] The whole wide kingdom waved back at her with big happy "
         "smiles, and then far away a dragon woke up, stretched, yawned and wandered over the hills. At last it "
         "roared. [right:roar] [next]")  # tags after their sentence, as the LLM writes them: tags_first moves them


class FakeStage:
    def __init__(self):
        self.events, self.busy_until = [], 0.0

    def play(self, arm, gesture, duration):
        self.events.append((time.time(), "play", f"{arm}:{gesture}"))

    def freeze(self):
        self.events.append((time.time(), "freeze", ""))

    def send(self, path, body=None):
        self.events.append((time.time(), path, ""))


async def run(actions):
    tts_inputs = []

    async def record(text):
        async for chunk in text:
            tts_inputs.append(chunk)
            yield chunk

    session = create_session(actions, extra_kwargs={
        "tts_text_transforms": [strip_tags_for_tts, record], "use_tts_aligned_transcript": False})
    session.tts.update_options(fake_audio_duration=4.0)
    stage = FakeStage()
    director = Director(session, stage, idle_after_s=0.3, continue_after_s=0.3)
    sync = GestureSync(GestureBook(DEFAULT_GESTURES), stage.play, director.request_continue)
    speaking = []
    session.on("agent_state_changed",
               lambda ev: speaking.append(time.time()) if ev.new_state == "speaking" else None)
    replies = []
    original_reply = session.generate_reply

    def generate_reply(**kw):
        replies.append(kw.get("instructions"))
        return original_reply(**kw)
    session.generate_reply = generate_reply
    original_start = session.start

    async def start(agent, **kw):  # install exactly as storyteller.py does: right after start()
        await original_start(agent, **kw)
        sync.install(session.output)
    session.start = start

    t0 = await run_session(session, Storyteller(instructions="test"), drain_delay=6)
    history = [m.text_content for m in session.history.messages() if m.role == "assistant"]
    return t0, stage, speaking, "".join(tts_inputs), history, replies


async def test_gestures_follow_speech():
    actions = FakeActions()
    actions.add_user_speech(0.5, 1.5, "tell me a story")
    actions.add_llm(STORY)
    t0, stage, speaking, tts_text, history, replies = await run(actions)

    plays = [(t - t0, what) for t, kind, what in stage.events if kind == "play"]
    assert [w for _, w in plays] == ["left:wave", "right:roar"]
    start = speaking[0] - t0
    wave, roar = plays[0][0] - start, plays[1][0] - start
    print(f"speech starts {start:.2f}s; wave +{wave:.2f}s, roar +{roar:.2f}s")
    assert 0 <= wave < 0.5  # moved to the start of its sentence: the first word
    assert 2.5 < roar < 4.5  # at its sentence near the end of the 4 s of audio, not when the LLM wrote it
    assert "[" not in tts_text and "knight" in tts_text
    assert "[left:wave]" in history[0]  # the LLM keeps seeing its own tags
    assert replies == [CONTINUE_INSTRUCTIONS]  # [next] was heard, so the next scene was asked for


async def test_interruption_freezes_and_cancels_later_gestures():
    actions = FakeActions()
    actions.add_user_speech(0.5, 1.5, "tell me a story")
    actions.add_llm(STORY)
    actions.add_user_speech(3.5, 4.5, "make the dragon friendly")  # mid-story
    t0, stage, speaking, _, history, replies = await run(actions)

    kinds = [(k, w) for _, k, w in stage.events]
    print([(round(t - t0, 2), k, w) for t, k, w in stage.events])
    assert ("play", "left:wave") in kinds
    assert ("play", "right:roar") not in kinds  # cut before the narration got there
    assert ("freeze", "") in kinds
    freeze_at = [t for t, k, _ in stage.events if k == "freeze"][-1] - t0
    assert 3.4 < freeze_at < 4.0  # the moment the listener starts talking
    assert "[right:roar]" not in history[0]  # history holds only what was heard
    assert replies == []  # the cut scene's [next] never played: no automatic next scene
