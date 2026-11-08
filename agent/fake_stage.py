"""Fake stage server: the robot stage's HTTP API with no arms, printing what the arms would do.

    python3 fake_stage.py [--port 8770]

Same API as the real stage server (robot/): GET /gestures, GET /state, POST /play {"arm","gesture","speed"},
GET /duets, POST /duet {"name","speed"}, POST /talk {"arm","on"}, POST /freeze, /idle, /stage, /rest, /stance {"stance": "audience"|"each_other"}, /props {"arm","on","kind"}. Standard library only.
"""
from __future__ import annotations

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

GESTURES = {
    "against_the_wind": 4.2, "bang_table": 5.3, "beat_chest": 4.1, "bite": 3.2, "block": 4.5, "bow": 2.9,
    "celebrate": 4.8, "chef_kiss": 4.2, "climb_ladder": 5.0, "cower": 3.8, "cry": 4.4, "die_dramatically": 3.0,
    "dodge": 2.7, "double_take": 4.4, "present": 2.48, "drive": 5.3, "duck": 3.3, "eat": 4.5, "elevator": 4.2, "eye_roll": 4.8,
    "facepalm": 4.4, "flick": 4.1, "gallop": 3.0, "grab": 2.5, "gripper_clap": 2.4, "hop": 4.7, "idle_breathe": 4.0,
    "invisible_wall": 5.2, "italian": 5.9, "italian_exasperated": 6.0, "juggle": 4.7, "knee_slap": 5.9, "knock":
    5.6, "laugh": 4.2, "laugh_hard": 5.7, "lean_on_nothing": 4.9, "lift_heavy": 4.6, "look_around": 3.8,
    "look_at_other": 2.8, "nod": 2.5, "pick_lars": 15.6, "place_lars": 16.1, "point_at_audience": 2.8, "pounce":
    3.5, "pull": 3.2, "push": 3.4, "rest": 3.0, "revive": 5.5, "roar": 3.6, "shake_head": 2.9, "shoot": 3.4, "sigh":
    4.4, "slash": 3.9, "slow_turn": 5.9, "smell_flower": 5.2, "sneak": 4.6, "sneeze": 3.5, "snore": 5.8, "stage":
    3.0, "startle": 3.0, "stir_frantic": 3.6, "stir_lazy": 4.4, "stomp": 4.6, "ta_da": 4.1, "taste_sour": 3.6,
    "throw": 2.9, "thrust": 3.0, "tightrope": 6.0, "trapped_in_box": 5.6, "tremble_excited": 3.1, "ufo_crash": 5.5,
    "ufo_fly": 5.9, "ufo_hover": 4.0, "ufo_land": 5.7, "wave": 4.7
}
DUETS = {
    "car_chase": 8.0, "cheers": 7.5, "copycat": 7.3, "cross_swords": 7.7, "elbow_nudge": 7.5, "hand_through_wall":
    7.6, "heckle": 7.1, "invisible_ball": 6.0, "mirror": 7.9, "place_hat": 7.2, "poke": 7.4, "quick_draw": 7.7,
    "straight_man": 7.5, "tip_hats": 5.6, "ufo_abduct": 7.9
}
ENSEMBLES = {
    "car_ride": 7.7, "cauldron": 8.0, "chomp": 7.7, "clap": 8.2, "drum_roll": 7.8, "explosion": 7.4, "kiss":
    7.2, "gate_opening": 7.6, "heart": 8.1, "high_five": 7.4, "hug": 7.6, "once_upon_a_time": 7.3, "pull_rope":
    13.7, "rocket": 5.9, "row_boat": 7.7, "rub_hands": 7.9, "shrug": 4.7, "storm": 4.9, "the_end": 4.0, "this_big":
    3.5, "waves": 3.7, "wings": 7.1
}
# name -> the stage's "sound" ({"name", "at"}), copied from robot/gestures and robot/duets
SOUNDS: dict[str, dict] = {
    "bang_table": {'name': 'bonk', 'at': [1.485, 4.122]}, "bite": {'name': 'chomp', 'at': [1.138, 1.947]},
    "chef_kiss": {'name': 'kiss', 'at': 2.22}, "die_dramatically": {'name': 'body_fall', 'at': 1.528},
    "double_take": {'name': None, 'at': 2.342}, "flick": {'name': 'whoosh', 'at': 1.55}, "knock":
    {'name': None, 'at': [0.87, 1.34, 1.81]}, "lean_on_nothing": {'name': None, 'at': 2.82}, "roar": {'name':
    'roar', 'at': 0.9}, "shoot": {'name': 'gunshot', 'at': 1.6}, "slash": {'name': 'sword_swing', 'at': 1.95},
    "stomp": {'name': None, 'at': [1.554, 3.738]}, "ta_da": {'name': 'tada', 'at': 1.992}, "throw": {'name':
    'whoosh', 'at': 1.317}, "ufo_crash": {'name': 'crash', 'at': 4.9}, "chomp": {'name': 'chomp', 'at': [2.7, 4.01,
    5.6]}, "cross_swords": {'name': 'sword_clash', 'at': 3.94}, "drum_roll": {'name': 'drum_roll', 'at': 1.38},
    "explosion": {'name': 'explosion', 'at': 3.1}, "quick_draw": {'name': 'gunshot', 'at': 3.64},
    "kiss": {'name': 'kiss', 'at': 3.3}
}
T0 = time.monotonic()
state = {"mode": "rest", "stance": "audience", "props": {"left": False, "right": False}, "arms": {"left": None, "right": None}}  # arm -> {"gesture", "until"} while playing


def log(msg: str) -> None:
    print(f"[{time.monotonic() - T0:7.2f}s] {msg}", flush=True)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:  # quiet the default access log
        pass

    def _reply(self, code: int, body: object) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path == "/gestures":
            arms = ["left", "right", "both"]
            self._reply(200, [{"name": n, "duration_s": d, "arms": arms, "side": "right" if n.endswith("_lars") else None,
                               "sound": SOUNDS.get(n)} for n, d in GESTURES.items()])
        elif self.path == "/duets":
            self._reply(200, [{"name": n, "kind": "duet", "duration_s": d, "sound": SOUNDS.get(n)} for n, d in DUETS.items()]
                        + [{"name": n, "kind": "ensemble", "duration_s": d, "sound": SOUNDS.get(n)}
                           for n, d in ENSEMBLES.items()])
        elif self.path == "/state":
            now = time.monotonic()
            arms = {a: (g["gesture"] if g and g["until"] > now else None) for a, g in state["arms"].items()}
            self._reply(200, {"mode": state["mode"], "stance": state["stance"], "props": state["props"],
                              "playing": arms})
        else:
            self._reply(404, {"error": "not found"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._reply(400, {"error": "bad json"})
        if self.path == "/play":
            arm, gesture, speed = body.get("arm"), body.get("gesture"), float(body.get("speed") or 1.0)
            if arm not in ("left", "right", "both") or gesture not in GESTURES:
                log(f"REJECT play {body}")
                return self._reply(400, {"error": f"unknown arm or gesture: {body}"})
            duration = GESTURES[gesture] / speed
            for a in (("left", "right") if arm == "both" else (arm,)):
                state["arms"][a] = {"gesture": gesture, "until": time.monotonic() + duration}
            state["mode"] = "playing"
            log(f"PLAY   {arm:>5}: {gesture} ({duration:.1f}s)")
            return self._reply(200, {"ok": True, "duration_s": duration, "speed": speed, "starts_in": 0.3})
        if self.path == "/duet":
            name = body.get("name")
            if name not in DUETS | ENSEMBLES:
                log(f"REJECT duet {body}")
                return self._reply(409, {"ok": False, "error": f"unknown duet: {body}"})
            duration = (DUETS | ENSEMBLES)[name] / float(body.get("speed") or 1.0)
            for a in ("left", "right"):
                state["arms"][a] = {"gesture": f"duet:{name}", "until": time.monotonic() + duration}
            log(f"DUET   {name} ({duration:.1f}s)")
            return self._reply(200, {"ok": True, "duet": name, "speed": float(body.get("speed") or 1.0), "starts_in": 0.0})
        if self.path == "/talk":
            log(f"TALK   {body.get('arm', 'both'):>5}: {'on' if body.get('on', True) else 'off'}")
            return self._reply(200, {"ok": True})
        if self.path == "/stance":
            if body.get("stance") not in ("audience", "each_other"):
                log(f"REJECT stance {body}")
                return self._reply(409, {"ok": False, "error": f"unknown stance: {body}"})
            state["stance"] = body["stance"]
            log(f"STANCE facing {body['stance']}")
            return self._reply(200, {"ok": True, "stance": body["stance"]})
        if self.path == "/props":
            for a in (("left", "right") if body.get("arm") in (None, "both") else (body.get("arm"),)):
                state["props"][a] = (body.get("kind") or True) if body.get("on", True) else False
            log(f"PROPS  {state['props']}")
            return self._reply(200, {"ok": True, "props": state["props"]})
        if self.path in ("/freeze", "/idle", "/stage", "/rest"):
            state["mode"] = self.path[1:]
            state["arms"] = {"left": None, "right": None}
            log({"/freeze": "FREEZE (hold pose)", "/idle": "IDLE   (breathing)",
                 "/stage": "STAGE  (to stage pose)", "/rest": "REST   (fold arms)"}[self.path])
            return self._reply(200, {"ok": True})
        self._reply(404, {"error": "not found"})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8770)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    log(f"fake stage on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
