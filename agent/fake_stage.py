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
    "bang_table": 6.9, "beat_chest": 6.9, "bite": 3.2, "block": 4.5, "bow": 2.9, "celebrate": 4.8, "chef_kiss": 3.4,
    "cower": 3.8, "cry": 4.4, "die_dramatically": 5.8, "dodge": 2.7, "double_take": 4.4, "drive": 15.0, "duck": 3.3,
    "eat": 4.5, "eye_roll": 4.8, "facepalm": 4.4, "flick": 2.7, "gallop": 3.0, "grab": 2.5, "gripper_clap": 2.4,
    "hop": 4.7, "idle_breathe": 4.0, "italian": 5.4, "italian_exasperated": 7.2, "knee_slap": 5.9, "laugh": 4.2,
    "laugh_hard": 6.0, "lift_heavy": 4.6, "look_around": 3.8, "look_at_other": 2.8, "mic_drop": 6.1, "nod": 2.5,
    "point_at_audience": 2.8, "pounce": 3.5, "pull": 3.2, "push": 3.4, "revive": 5.5, "roar": 3.6, "shake_head":
    2.9, "shoot": 5.0, "sigh": 4.4, "slash": 3.9, "slow_turn": 6.0, "sneak": 4.6, "sneeze": 3.5, "snore": 6.2,
    "startle": 3.0, "stir_frantic": 8.2, "stir_lazy": 10.5, "stomp": 4.6, "ta_da": 4.1, "throw": 2.9, "thrust": 3.0,
    "tremble_excited": 3.1, "ufo_crash": 11.5, "ufo_fly": 13.6, "ufo_hover": 8.0, "ufo_land": 13.0, "wave": 4.7
}
DUETS = {"cheers": 8.7, "copycat": 7.3, "cross_swords": 8.4, "elbow_nudge": 7.1, "heckle": 8.1, "place_hat": 8.8,
         "poke": 8.1, "quick_draw": 8.9, "straight_man": 9.6, "tip_hats": 5.6}
ENSEMBLES = {"car_ride": 8.6, "cauldron": 10.4, "chomp": 9.3, "clap": 8.2, "drum_roll": 9.0, "explosion": 8.6,
             "fist_bump": 7.2, "gate_opening": 9.9, "heart": 8.1, "high_five": 8.1, "hug": 7.6, "once_upon_a_time":
             8.1, "pull_rope": 13.7, "rocket": 5.9, "row_boat": 8.2, "rub_hands": 10.0, "shrug": 4.7, "storm": 4.9,
             "the_end": 4.0, "this_big": 3.5, "waves": 3.7, "wings": 7.1}
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
            self._reply(200, [{"name": n, "duration_s": d, "arms": arms} for n, d in GESTURES.items()])
        elif self.path == "/duets":
            self._reply(200, [{"name": n, "kind": "duet", "duration_s": d} for n, d in DUETS.items()]
                        + [{"name": n, "kind": "ensemble", "duration_s": d} for n, d in ENSEMBLES.items()])
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
            return self._reply(200, {"ok": True, "duration_s": duration})
        if self.path == "/duet":
            name = body.get("name")
            if name not in DUETS | ENSEMBLES:
                log(f"REJECT duet {body}")
                return self._reply(409, {"ok": False, "error": f"unknown duet: {body}"})
            duration = (DUETS | ENSEMBLES)[name] / float(body.get("speed") or 1.0)
            for a in ("left", "right"):
                state["arms"][a] = {"gesture": f"duet:{name}", "until": time.monotonic() + duration}
            log(f"DUET   {name} ({duration:.1f}s)")
            return self._reply(200, {"ok": True, "duet": name})
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
