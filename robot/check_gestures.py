"""Self-check: every gesture passes the safety checker, the checker rejects bad motion, and the stage server's HTTP API
works against the kinematic sim. Never touches hardware.  Run: python robot/check_gestures.py
"""
import json, threading, time, urllib.error, urllib.request
import numpy as np
import duets, show, stage

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
sc = ck.scene
for side in show.SIDES:
    sc.set(side, show.stage_pose(side, "each_other"))
sc.forward()
eye = {side: sc.d.site_xpos[sc.site[side]].copy() for side in show.SIDES}
gaze = sc.d.site_xmat[sc.site["left"]].reshape(3, 3)[:, 2]  # the wrist camera looks along the grasp axis
yaw = np.degrees(np.arctan2(-gaze[1], gaze[0]))
miss = np.degrees(np.arccos(gaze @ (eye["right"] - eye["left"]) / np.linalg.norm(eye["right"] - eye["left"])))
assert yaw >= 60 and miss <= 30, (yaw, miss)  # empty hands: a deep turn, the eyes on the other's head
print(f"{len(facing)} gestures also play facing each other (heads {yaw:.0f} deg in, eyes {miss:.0f} deg off the other's)")


def fake(name, *keys):
    return show.Gesture({"name": name, "keys": [{"t": t, "q": list(q)} for t, q in keys]})


# The checker must refuse: too fast, into the table, past a joint limit, leaning back (tips the table), and
# (online) into the other arm.
assert any("speed" in p for p in ck.check(fake("fast", (0, [0] * 6), (0.2, [0.8, 0, 0, 0, 0, 0]))))
assert any("table" in p for p in ck.check(fake("dive", (0, [0] * 6), (3, [0, 1.3, -0.6, -0.8, 0, 0]))))
assert any("j4 reaches" in p for p in ck.check(fake("limit", (0, [0] * 6), (3, [0, 0, 0, 1.9, 0, 0]))))
assert any("leans back" in p for p in ck.check(fake("lean", (0, [0] * 6), (3, [0, -0.3, 0.4, 0, 0, 0]))))
print("checker rejects fast / table / limit / lean-back motions")
# Table contact (round 3): only gestures that declare it, only with empty hands, only the gripper, gently enough.
bang = ok["bang_table"]
assert any("empty hands only" in p for p in ck.check(bang, "audience_props"))
assert not ck.check(bang) and not ck.check(ok["die_dramatically"])
assert any("the hand goes" in p for p in ck.check(show.Gesture(dict(bang.d, table=dict(min=0.0, impact=0.3)))))
assert any("comes down at" in p for p in ck.check(show.Gesture(dict(bang.d, table=dict(min=-0.01, impact=0.1)))))
dive = fake("dive", (0, [0] * 6), (3, [0, 1.3, -0.6, -0.8, 0, 0])).d
assert any("off the hand" in p for p in ck.check(show.Gesture(dict(dive, table=dict(min=-0.5, impact=9.0)))))
ck.set_props(False)
print("table contact: empty hands only, depth, impact speed and link clearance enforced")
# Sounds on hits: known sfx names, hit times inside the move, and a table move's hits are its contact frames.
SFX = {f.stem for f in (show.pathlib.Path(__file__).parent.parent / "agent" / "sounds" / "sfx").glob("*/*.ogg")}
for g in ok.values():
    if snd := g.sound:
        ats = snd["at"] if isinstance(snd["at"], list) else [snd["at"]]
        assert (snd["name"] is None or snd["name"] in SFX) and all(0 <= a <= g.duration for a in ats), (g.name, snd)
        if g.table:
            for st_ in ("audience", "each_other"):
                Q, _ = g.samples("left", stance=st_)
                hand = ck.scene.lows("left", Q)[0]
                for a in ats:
                    i = int(round(a * show.HZ))
                    assert hand[max(i - 3, 0):i + 4].min() < hand.min() + 0.01, (g.name, st_, a)  # within 60 ms, 1 cm
print(f"sounds ok: {sum(1 for g in ok.values() if g.sound)} gestures hit on their contact / peak frames")

# Duets: all pass; an undeclared close pair (< 15 cm) and a fast final approach (> 5 cm/s inside 5 cm) are refused.
dc = duets.DuetChecker(ck)
dok, dbad = duets.load_duets(dc)
assert not dbad and {"cross_swords", "cheers", "poke", "quick_draw", "tip_hats", "copycat", "place_hat", "elbow_nudge",
                     "straight_man", "heckle", "car_chase", "ufo_abduct", "mirror", "invisible_ball",
                     "hand_through_wall"} <= set(dok), \
    (sorted(dok), dbad)
for d in dok.values():
    assert d.max_speed <= 1 and min(d.stats["closest_contact_cm"].values(), default=1) > 0 and d.stats["closest_other_cm"] >= 15, d.stats
base = duets.load(duets.DUET_DIR / "cross_swords.json").d
assert any("not a declared contact" in p for p in dc.check(duets.Duet(dict(base, contact=[]))))
fast = duets.Duet(dict(base, keys=[dict(k, t=k["t"] * 0.5) for k in base["keys"]]))
assert any("closes at" in p for p in dc.check(fast)), dc.check(fast)
ens = {n for n, d in dok.items() if d.kind == "ensemble"}
assert {"chomp", "wings", "this_big", "shrug", "rocket", "waves", "storm", "gate_opening", "explosion", "row_boat",
        "once_upon_a_time", "the_end", "car_ride", "high_five", "fist_bump", "cauldron", "drum_roll"} <= ens, sorted(ens)
print(f"{len(dok)} duets pass ({len(ens)} of them ensembles); undeclared contact and fast approach refused")

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
assert code == 200 and 0 <= r["starts_in"] < 2, r  # the blend in, for the agent's hit-timed sounds
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
assert call("/play", {"arm": "left", "gesture": "stir_lazy"})[0] == 200  # a loop repeats until the next gesture
time.sleep(1.5 * ok["stir_lazy"].duration / st.warp)
assert call("/state")[1]["arms"]["left"]["playing"] == "stir_lazy"
assert call("/play", {"arm": "left", "gesture": "nod"})[0] == 200
st.wait()
assert call("/idle", {})[0] == 200
print("HTTP API ok: /gestures /stage /play /freeze /idle /grip, queue, refusals, loops")

assert call("/talk", {"arm": "left", "on": True})[0] == 200  # the mouth flaps on top of the idle breathing
G = []
for _ in range(60):
    G.append(st.mouth["left"].g if st.mouth["left"].g is not None else st.cmd_g["left"]); time.sleep(0.01)
assert max(G) - min(G) > 0.2 and call("/state")[1]["arms"]["left"]["talking"], G
assert call("/talk", {"arm": "left", "on": False})[0] == 200
time.sleep(0.5)
assert st.mouth["left"].g is None and not st.dq["left"].any()  # back to the plan's grip, no head offset
assert call("/talk", {"arm": "both"})[0] == 200 and call("/freeze", {})[0] == 200
assert not any(st.mouth[s].on for s in show.SIDES)  # freeze shuts the mouths
time.sleep(0.5)
assert call("/idle", {})[0] == 200
print("talking mouth ok: /talk on and off, freeze stops it")

assert call("/stance", {"stance": "sideways"})[0] == 409
assert call("/stance", {"stance": "each_other"})[1]["stance"] == "each_other"
st.wait(); time.sleep(0.1)  # the next tick starts the breathing
assert call("/state")[1]["arms"]["left"]["playing"] == "idle_breathe"  # idle arms keep breathing in the new stance
assert call("/stage", {})[0] == 200
st.wait()
assert all(np.allclose(st.cmd_q[s], show.stage_pose(s, "each_other")) for s in show.SIDES)
assert st.cmd_q["left"][0] < 0 < st.cmd_q["right"][0]  # yawed toward each other
assert call("/play", {"arm": "left", "gesture": "slash"})[0] == 200
st.wait()
assert np.allclose(st.cmd_q["left"], show.stage_pose("left", "each_other"))  # gestures return to the stance pose
# Props: empty hands by default; holding one turns each_other shallow, blocks that arm's mouth, keeps its grip and
# refuses hand-contact duets.
assert call("/state")[1]["props"] == {"left": False, "right": False}
assert call("/props", {"arm": "left", "on": True})[1]["stance"] == "each_other_props"
st.wait()
assert all(np.allclose(st.cmd_q[s], show.stage_pose(s, "each_other_props")) for s in show.SIDES)
assert st.ck.scene.props == {"left": True, "right": True}  # everything is checked with both props now
code, r = call("/talk", {"arm": "left"})
assert code == 409 and "prop" in r["error"], r
assert call("/talk", {"arm": "right"})[0] == 200 and call("/talk", {"arm": "right", "on": False})[0] == 200
g0 = st.cmd_g["left"]
assert call("/play", {"arm": "left", "gesture": "gripper_clap"})[0] == 200
assert np.allclose(st.plan["left"].G, g0)  # holding a prop: the gesture keeps the grip
st.wait()
hands = [n for n, d in dok.items() if d.props is False]
code, r = call("/duet", {"name": hands[0]})
assert code == 409 and "empty hands" in r["error"], r
assert call("/props", {"arm": "left", "on": False})[1]["stance"] == "each_other"
st.wait()
assert all(np.allclose(st.cmd_q[s], show.stage_pose(s, "each_other")) for s in show.SIDES)
assert st.ck.scene.props == {"left": False, "right": False}
print("props ok: /props turns each_other shallow, blocks the mouth, keeps the grip, refuses hand duets; off again")
# Toys: a held car plays only its own gestures; its gestures need it.
assert call("/play", {"arm": "left", "gesture": "drive"})[0] == 409  # no car in hand
assert call("/props", {"arm": "left", "on": True, "kind": "car"})[0] == 200
st.wait()
assert st.ck.scene.props == {"left": "car", "right": True}
assert call("/play", {"arm": "left", "gesture": "slash"})[0] == 409
assert call("/play", {"arm": "left", "gesture": "ufo_hover"})[0] == 409
assert call("/duet", {"name": "cheers"})[0] == 409
code, r = call("/play", {"arm": "left", "gesture": "drive"})
assert code == 200, r
st.wait()
assert call("/duet", {"name": "car_chase"})[0] == 409  # a toy duet plays only with exactly its props: one car is not two
assert call("/props", {"arm": "right", "on": True, "kind": "car"})[0] == 200
st.wait()
code, r = call("/duet", {"name": "car_chase"})
assert code == 200 and r["starts_in"] >= 0, r
st.wait()
assert call("/duet", {"name": "ufo_abduct"})[0] == 409  # cars in hand, not the UFO
assert call("/props", {"arm": "right", "on": False})[0] == 200
st.wait()
assert call("/props", {"arm": "left", "on": False})[0] == 200 and call("/props", {"arm": "left", "kind": "kite"})[0] == 409
st.wait()
print("toys ok: a held car plays drive only, its gestures need it; toy duets need exactly their toys")
assert call("/play", {"arm": "left", "gesture": "die_dramatically"})[0] == 200
assert not st.mouth["left"].on
st.wait()
assert call("/play", {"arm": "left", "gesture": "revive"})[0] == 200  # up from the table again
st.wait()
assert np.allclose(st.cmd_q["left"], show.stage_pose("left", "each_other"))
print("die_dramatically then revive ok")
if "look_at_other" in ok and "each_other" not in ok["look_at_other"].stances:
    code, r = call("/play", {"arm": "left", "gesture": "look_at_other"})
    assert code == 409 and "each_other" in r["error"], r
assert call("/stance", {"stance": "audience"})[0] == 200
st.wait()
assert np.allclose(st.cmd_q["left"], show.stage_pose("left"))
print("stances ok: /stance each_other and back, gestures play relative to it")

kinds = {d["name"]: d["kind"] for d in call("/duets")[1]}
assert kinds["cheers"] == "duet" and kinds["storm"] == "ensemble", kinds
assert call("/stance", {"stance": "each_other"})[0] == 200  # a duet from the facing stance returns to it
st.wait()
assert call("/play", {"arm": "left", "gesture": "wave"})[0] == 200
code, r = call("/duet", {"name": "cheers"})
assert code == 409 and "free" in r["error"], r  # both arms must be free
st.wait()
assert call("/duet", {"name": "cheers"})[0] == 200
time.sleep(0.2)
assert call("/state")[1]["duet"] == "cheers"
assert call("/play", {"arm": "right", "gesture": "nod"})[0] == 409  # nothing else while a duet plays
st.wait()
assert all(np.allclose(st.cmd_q[s], show.stage_pose(s, "each_other")) for s in show.SIDES)
assert call("/duet", {"name": "shrug"})[0] == 200  # an ensemble also blends in from the facing stance and back
st.wait()
assert all(np.allclose(st.cmd_q[s], show.stage_pose(s, "each_other")) for s in show.SIDES)
assert call("/duet", {"name": "cross_swords"})[0] == 200
time.sleep(1.5)
assert call("/freeze", {})[0] == 200  # both arms brake on the duet's path and hold
time.sleep(0.3)
q = {s: st.cmd_q[s].copy() for s in show.SIDES}; time.sleep(0.2)
assert all(np.allclose(q[s], st.cmd_q[s]) for s in show.SIDES), "duet freeze did not hold"
code, r = call("/idle", {})
assert code == 409 and "finishing" in r["error"], r  # the next request first finishes the duet, slowly
st.wait()
assert all(np.allclose(st.cmd_q[s], show.stage_pose(s, "each_other")) for s in show.SIDES)
assert call("/stance", {"stance": "audience"})[0] == 200
st.wait()
print("duets ok: /duets, /duet, both-arms-free and duet-playing refusals, freeze and finish")

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

assert call("/duet", {"name": "cheers"})[0] == 200
time.sleep(0.5)
httpd.shutdown()
stage.shutdown(st, lambda: None, None)  # mid-duet: the duet ends on its path, then the arms fold
assert max(np.abs(st.cmd_q[s] - show.HOME[s]).max() for s in show.SIDES) < 1e-6, "not back at rest"
print("shutdown returns to rest\nALL CHECKS PASSED")
