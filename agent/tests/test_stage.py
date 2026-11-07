"""Stage client against fake_stage.py, over real HTTP."""
import asyncio
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import fake_stage
from gestures import Stage


async def test_stage_client_against_fake_stage(capsys):
    server = ThreadingHTTPServer(("127.0.0.1", 0), fake_stage.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}"
    stage = Stage(url)
    try:
        gestures = await stage.fetch_gestures()
        assert gestures and gestures["slash"] == 1.2
        assert (await stage.fetch_duets())["cheers"] > 3
        stage.play("left", "slash", gestures["slash"])
        stage.send("/play", {"arm": "left", "gesture": "moonwalk"})  # rejected, logged, never raised
        await asyncio.sleep(0.3)
        state = json.load(urllib.request.urlopen(f"{url}/state"))
        assert state["playing"]["left"] == "slash"
        stage.face("each_other")
        stage.play("both", "bow", 2.5)  # sent after the face: must arrive after it
        await asyncio.sleep(0.3)
        state = json.load(urllib.request.urlopen(f"{url}/state"))
        assert state["stance"] == "each_other" and state["playing"]["right"] == "bow"
        stage.busy_until = 0.0  # as if the bow had finished
        stage.play("left", "nod", 0.2)
        stage.play("duet", "cheers", 9.7)  # waits for the nod (0.2 s + margin) before it is sent
        await asyncio.sleep(0.15)
        assert json.load(urllib.request.urlopen(f"{url}/state"))["playing"]["right"] != "duet:cheers"
        await asyncio.sleep(0.6)
        state = json.load(urllib.request.urlopen(f"{url}/state"))
        assert state["playing"]["left"] == state["playing"]["right"] == "duet:cheers"
        stage.freeze()
        stage.send("/idle")
        await asyncio.sleep(0.3)
        out = capsys.readouterr().out
        assert "PLAY    left: slash" in out and "REJECT" in out and "FREEZE" in out and "IDLE" in out
        assert out.index("STANCE facing each_other") < out.index("PLAY    both: bow")
    finally:
        await stage.aclose()
        server.shutdown()


async def test_stage_down_falls_back():
    stage = Stage("http://127.0.0.1:9")  # nothing listens there
    assert await stage.fetch_gestures() is None
    stage.freeze()  # must not raise
    await stage.aclose()
