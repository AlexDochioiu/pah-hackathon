"""Push-to-talk wiring: what each RPC does to the session and the stage."""
import asyncio

from director import Director, PushToTalk


class Recorder:
    def __init__(self):
        self.calls = []
        self.input = self

    def on(self, *_):
        pass

    def interrupt(self):
        self.calls.append("interrupt")

    def clear_user_turn(self):
        self.calls.append("clear")

    def set_audio_enabled(self, on):
        self.calls.append(f"audio={on}")

    def commit_user_turn(self, **_):
        self.calls.append("commit")
        fut = asyncio.get_running_loop().create_future()
        fut.set_result("a story about a cat")
        return fut

    def generate_reply(self, **_):
        self.calls.append("reply")


class Stage:
    busy_until = 0.0

    def __init__(self):
        self.sent = []

    def freeze(self):
        self.sent.append("/freeze")

    def idle(self):
        self.send("/idle")

    def send(self, path, body=None):
        self.sent.append(path)


class Participant:
    def __init__(self):
        self.methods = {}

    def register_rpc_method(self, name, handler):
        self.methods[name] = handler


async def test_hold_freezes_and_listens_release_commits():
    session, stage, room = Recorder(), Stage(), Participant()
    director = Director(session, stage, idle_after_s=0.05, continue_after_s=0.05)
    director.agent_state = "listening"
    director.continue_pending = True  # a scene ended with [next]: holding the button cancels the carry-on
    PushToTalk(session, director).register(room)
    assert set(room.methods) == {"start_turn", "end_turn", "cancel_turn"}

    assert await room.methods["start_turn"](None) == "ok"
    assert session.calls == ["interrupt", "clear", "audio=True"]
    assert stage.sent == ["/freeze"] and not director.continue_pending
    await asyncio.sleep(0.15)
    assert stage.sent == ["/freeze"]  # no idling while the button is held

    await room.methods["end_turn"](None)
    assert session.calls[3:] == ["audio=False", "commit"]
    await asyncio.sleep(0.15)
    assert stage.sent == ["/freeze", "/idle"]  # quiet stage after release: idles as before
    assert "reply" not in session.calls  # the commit itself asks for the reply, not the director


async def test_late_commit_does_not_release_a_newer_hold():
    session, stage, room = Recorder(), Stage(), Participant()
    director = Director(session, stage, idle_after_s=0.05, continue_after_s=0.05)
    director.agent_state = "listening"
    PushToTalk(session, director).register(room)
    await room.methods["start_turn"](None)
    await room.methods["end_turn"](None)
    await room.methods["start_turn"](None)  # pressed again before the first transcript came back
    await asyncio.sleep(0.15)
    assert director.holding and "/idle" not in stage.sent


async def test_cancel_discards_the_turn():
    session, stage, room = Recorder(), Stage(), Participant()
    PushToTalk(session, Director(session, stage, idle_after_s=1, continue_after_s=1)).register(room)
    await room.methods["start_turn"](None)
    await room.methods["cancel_turn"](None)
    assert session.calls == ["interrupt", "clear", "audio=True", "audio=False", "clear"]
