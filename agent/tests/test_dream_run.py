"""robot/dream_run.py against a stub stage (laptop only, no Dream call): a Dream-1 miss reacts and says so."""

import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DREAM_RUN = Path(__file__).resolve().parents[2] / "robot" / "dream_run.py"
REST = [0.0, 0.8, 0.7, 0.2, 0.0, 0.0]


def stub_stage(grip_measured):
    """A stage that takes every move at once: a closing chunk holds Lars, the way back (carry None) lets go."""
    calls, st = [], {"held": False}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def reply(self, body):
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            arm = {"playing": None, "joints": REST, "grip": 0.0, "grip_measured": grip_measured,
                   "tracking_error": 0.0, "last_error": None}
            self.reply({"fault": None, "props": {"left": False, "right": st["held"]},
                        "arms": {"left": dict(arm, grip_measured=1.0), "right": arm}})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
            calls.append((self.path, body))
            if self.path == "/trajectory":
                st["held"] = bool(body.get("carry"))
                self.reply({"ok": True, "duration_s": 0.1, "tip_end_mm": 5.0,
                            "props": {"left": False, "right": st["held"]}})
            else:
                if body.get("gesture") == "pick_lars":
                    st["held"] = True
                self.reply({"ok": True, "started": [body.get("arm")]})

    server = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, calls


def run_miss(tmp_path, grip_measured):
    reach = [[REST[0] - 0.5 * i / 4, *REST[1:]] for i in range(5)]
    chunk = {"event": "chunk", "t": [0.1 * i for i in range(5)], "latency_s": 1.0,
             "right": {"q": reach, "g": [1.0, 0.8, 0.5, 0.2, 0.0]}, "left": {"q": [REST] * 5, "g": [1.0] * 5}}
    replay = tmp_path / "miss.json"
    replay.write_text(json.dumps([chunk]))
    server, calls = stub_stage(grip_measured)
    try:
        out = subprocess.run([sys.executable, str(DREAM_RUN), "--replay", str(replay), "--hold", "--stage",
                              f"http://127.0.0.1:{server.server_address[1]}"],
                             capture_output=True, text=True, timeout=60, check=True).stdout
    finally:
        server.shutdown()
    return [json.loads(line) for line in out.splitlines()], calls


def test_an_empty_grip_is_a_miss_the_arm_facepalms_and_our_pick_takes_over(tmp_path):
    events, calls = run_miss(tmp_path, grip_measured=0.02)
    oops = [e for e in events if e["event"] == "oops"]
    assert len(oops) == 1 and oops[0]["kind"] == "empty_grip", events
    assert oops[0]["reaction"] == "facepalm" and oops[0]["sfx"] == "sad_trombone" and oops[0]["text"]
    plays = [b["gesture"] for p, b in calls if p == "/play"]
    assert plays == ["facepalm", "pick_lars"], calls  # the reaction through /play, then our own pick (--hold)
    back = [b for p, b in calls if p == "/trajectory" and b["name"].startswith("dream_back")]
    assert back and calls.index(("/play", {"arm": "right", "gesture": "facepalm"})) > calls.index(
        ("/trajectory", back[-1]))  # the hand lets go on the way back before the facepalm
    done = events[-1]
    assert done["event"] == "done" and done["missed"] == "empty_grip" and done["held"] and done["fallback"]
    assert "Our own move, then!" in [e.get("text") for e in events if e["event"] == "say"]


def test_a_real_grip_is_no_miss(tmp_path):
    events, calls = run_miss(tmp_path, grip_measured=0.2)
    assert not [e for e in events if e["event"] == "oops"]
    assert "facepalm" not in [b.get("gesture") for p, b in calls if p == "/play"]
