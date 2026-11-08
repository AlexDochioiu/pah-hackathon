"""Fake stage server: the robot stage's HTTP API with no arms, printing what the arms would do.

    python3 fake_stage.py [--port 8770]

Same API as the real stage server (robot/): GET /gestures, GET /state, POST /play {"arm","gesture","speed"},
GET /duets, POST /duet {"name","speed"}, POST /talk {"arm","on"}, POST /freeze, /idle, /stage, /rest, /stance {"stance": "audience"|"each_other"}, /props {"arm","on"}. Standard library only.
"""
from __future__ import annotations

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

GESTURES = {
    "wave": 2.0, "bow": 2.5, "nod": 1.0, "shake_head": 1.5, "slash": 1.2, "thrust": 1.2, "block": 1.0,
    "celebrate": 2.5, "cower": 2.0, "die_dramatically": 3.0, "gallop": 2.5, "roar": 2.0, "sneak": 2.5,
    "look_at_other": 1.5, "point_at_audience": 1.5, "gripper_clap": 1.5, "idle_breathe": 3.0,
}
DUETS = {"cross_swords": 8.7, "cheers": 9.0, "poke": 8.8, "quick_draw": 7.7, "tip_hats": 5.6, "copycat": 7.3,
         "car_ride": 6.8}
ENSEMBLES = {"chomp": 5.6, "wings": 7.1, "this_big": 3.5, "shrug": 2.3, "rocket": 5.9, "waves": 3.7, "storm": 4.9,
             "gate_opening": 4.8, "explosion": 6.7, "row_boat": 4.8, "once_upon_a_time": 4.0, "the_end": 4.0,
             "clap": 8.2, "rub_hands": 10.0, "hug": 7.5, "pull_rope": 13.3, "heart": 7.7}
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
                state["props"][a] = bool(body.get("on", True))
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
