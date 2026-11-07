"""Self-check: every gesture passes the safety checker, the checker rejects bad motion, and the stage server's HTTP API
works against the kinematic sim. Never touches hardware.  Run: python robot/check_gestures.py
"""
import json, threading, time, urllib.error, urllib.request
import numpy as np
import show, stage

ck = show.Checker()
ok, bad, unsafe = show.load_library(ck)
assert not bad, f"rejected gestures: {bad}"
assert {"stage", "rest", "idle_breathe", "wave", "bow", "slash", "block", "die_dramatically"} <= set(ok), sorted(ok)
for g in ok.values():
    assert 0.5 <= g.max_speed <= 1.5, (g.name, g.max_speed)
print(f"{len(ok)} gestures pass; {len(unsafe)} unsafe pairs")
# Facing each other: the main character gestures must still play there (checked with the 15 cm gap and lean-back).
facing = {n for n, g in ok.items() if "each_other" in g.stances}
assert {"idle_breathe", "wave", "bow", "slash", "thrust", "block", "roar", "nod", "shake_head"} <= facing, sorted(facing)
assert not facing & set(show.FOLD)
print(f"{len(facing)} gestures also play facing each other")


def fake(name, *keys):
    return show.Gesture({"name": name, "keys": [{"t": t, "q": list(q)} for t, q in keys]})


# The checker must refuse: too fast, into the table, past a joint limit, leaning back (tips the table), and
# (online) into the other arm.
assert any("speed" in p for p in ck.check(fake("fast", (0, [0] * 6), (0.2, [0.8, 0, 0, 0, 0, 0]))))
assert any("table" in p for p in ck.check(fake("dive", (0, [0] * 6), (3, [0, 1.3, -0.6, -0.8, 0, 0]))))
assert any("j4 reaches" in p for p in ck.check(fake("limit", (0, [0] * 6), (3, [0, 0, 0, 1.9, 0, 0]))))
assert any("leans back" in p for p in ck.check(fake("lean", (0, [0] * 6), (3, [0, -0.3, 0.4, 0, 0, 0]))))
print("checker rejects fast / table / limit / lean-back motions")

st = stage.Stage(stage.KinArms(), "kin", warp=4)
st.thread.start()
httpd = stage.serve(st, 0)
port = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()


def call(path, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=None if body is None else json.dumps(body).encode(),
                                 method="GET" if body is None else "POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


code, lib = call("/gestures")
assert code == 200 and len(lib) == len(ok) and all(g["duration_s"] > 0 for g in lib)
assert call("/state")[1]["rejected"] == {}
assert call("/stage", {"speed": 1.0})[0] == 200
st.wait()
assert np.allclose(st.cmd_q["left"], show.stage_pose("left")) and np.allclose(st.cmd_q["right"], show.stage_pose("right"))
code, r = call("/play", {"arm": "left", "gesture": "slash", "speed": 1.0})
assert code == 200, r
code, r = call("/play", {"arm": "right", "gesture": "block"})
assert code == 200, r
time.sleep(0.3)
s = call("/state")[1]["arms"]
assert s["left"]["playing"] == "slash" and s["right"]["playing"] == "block", s
assert call("/freeze", {})[0] == 200
time.sleep(0.2)
q = st.cmd_q["left"].copy(); time.sleep(0.2)
assert np.allclose(q, st.cmd_q["left"]), "freeze did not hold"
assert call("/play", {"arm": "both", "gesture": "nope"})[0] == 409
assert call("/play", {"arm": "middle", "gesture": "wave"})[0] == 409
assert call("/play", {"gesture": "wave", "speed": 9})[1]["speed"] <= 1.5  # clamped to the gesture's max
assert call("/play", {"arm": "left", "gesture": "nod", "mode": "queue"})[1]["queued"] == ["left"]
st.wait()
assert call("/idle", {})[0] == 200
time.sleep(0.5)
assert call("/state")[1]["arms"]["left"]["playing"] == "idle_breathe"
assert call("/grip", {"arm": "right", "value": 0.0})[0] == 200
time.sleep(0.5)
assert call("/state")[1]["arms"]["right"]["grip"] < 0.05
assert call("/bogus", {})[0] == 404
print("HTTP API ok: /gestures /stage /play /freeze /idle /grip, queue, refusals")

assert call("/stance", {"stance": "sideways"})[0] == 409
assert call("/stance", {"stance": "each_other"})[1]["stance"] == "each_other"
st.wait()
assert call("/state")[1]["arms"]["left"]["playing"] == "idle_breathe"  # idle arms keep breathing in the new stance
assert call("/stage", {})[0] == 200
st.wait()
assert all(np.allclose(st.cmd_q[s], show.stage_pose(s, "each_other")) for s in show.SIDES)
assert st.cmd_q["left"][0] < 0 < st.cmd_q["right"][0]  # yawed toward each other
assert call("/play", {"arm": "left", "gesture": "slash"})[0] == 200
st.wait()
assert np.allclose(st.cmd_q["left"], show.stage_pose("left", "each_other"))  # gestures return to the stance pose
if "look_at_other" in ok and "each_other" not in ok["look_at_other"].stances:
    code, r = call("/play", {"arm": "left", "gesture": "look_at_other"})
    assert code == 409 and "each_other" in r["error"], r
assert call("/stance", {"stance": "audience"})[0] == 200
st.wait()
assert np.allclose(st.cmd_q["left"], show.stage_pose("left"))
print("stances ok: /stance each_other and back, gestures play relative to it")

# Online arm-arm check: two poses that point the props at each other are refused together.
with st.lock:
    clash = {s: st._pose_plan(s, "clash", show.stage_pose(s) + show.mirror([-0.9, 0.2, 0, 0, 0, 0], s), 1.0)
             for s in show.SIDES}
    try:
        st._commit(clash)
        raise AssertionError("clash not refused")
    except stage.StageError as e:
        assert "from the" in str(e), e
print("arm-to-arm clash refused")

httpd.shutdown()
stage.shutdown(st, lambda: None, None)
assert max(np.abs(st.cmd_q[s] - show.HOME[s]).max() for s in show.SIDES) < 1e-6, "not back at rest"
print("shutdown returns to rest\nALL CHECKS PASSED")
