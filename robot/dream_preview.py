"""Ask Dream-1 for its next move, on the station, without moving anything (robot/dream_run.py drives it over ssh).

Runs with the Dream env from its own folder (not ~/dial):
    ~/dream-env/bin/python ~/dream-ghost/dream_preview.py --task "pick up the green frog"       # one chunk, then exit
    ~/dream-env/bin/python ~/dream-ghost/dream_preview.py --serve ...    # JSON lines on stdin: next / close

It opens the three cameras through dreamcli's own readers (~/.dreamlabs/station.yaml: top/left/right as
overhead/wrist_l/wrist_r, face blur on) and the Dream client through dreamcli's policy config, then feeds the client's
observation window at 5 Hz: each camera's latest frame and both arms' measured joints and grippers, read with
GET /state from the stage server. It never opens an arm, never takes ~/.dreamlabs/arms.lock, and never POSTs.

Each get_action is one billed call, logged to calls.jsonl beside this file. Output: JSON lines on stdout.
    {"event": "ready", ...}                          the window is full (9 observation frames)
    {"event": "chunk", "n", "latency_s", "t", "left": {"q", "g"}, "right": {"q", "g"}}
        48 rows: absolute joints (rad, each arm's own convention, as /state) and gripper 0 closed..1 open, at
        t = i / 20 s (dreamcli's pace; the checkpoint's rows are 30 Hz, so this is its two-thirds speed).
    {"event": "error", "error"}                      then it exits
--serve stdin lines: {"cmd": "next", "commanded": {"left": [6], "right": [6]}} (where the arms will be when this
chunk takes over: the seam), {"cmd": "close"}; EOF closes too. The cameras are released on every exit, so the stage's
dock check can open the overhead camera again.
--fake answers a small made-up reach of the right arm instead of calling Dream (no billing), to test the pipe.
"""

import argparse
import base64
import dataclasses
import json
import pathlib
import sys
import threading
import time
import types
import urllib.request

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
CALLS = HERE / "calls.jsonl"
HZ_OUT = 20.0     # dreamcli's control_hz: the pace it plays Dream-1's rows at
OBS_HZ = 5.0      # the client's window: 9 frames at 5 Hz
WINDOW = 9
STALE_S = 1.0     # a camera frame older than this is a drop: that observation tick is skipped
NO_FRAMES_S = 8.0 # no full set of fresh frames for this long: give up


def say(**kw):
    print(json.dumps(kw), flush=True)


def get_state(url):
    with urllib.request.urlopen(url + "/state", timeout=2) as r:
        return json.loads(r.read())


def arm_obs(s):
    """One arm of /state as the adapter reads it: measured joints, measured gripper."""
    grip = s.get("grip_measured", s.get("grip"))
    return types.SimpleNamespace(positions_rad=np.asarray(s["measured"], float), gripper=float(grip))


def log_call(**kw):
    with open(CALLS, "a") as f:
        f.write(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), **kw}) + "\n")


class Preview:
    def __init__(self, a):
        self.a, self.lock, self.stop = a, threading.Lock(), threading.Event()
        self.fed, self.skipped, self.last_fed = 0, 0, time.monotonic()
        self.readers, self.client, self.error, self.why = [], None, None, None
        self.out = pathlib.Path(a.out) if a.out else None
        if self.out:
            self.out.mkdir(parents=True, exist_ok=True)

    def open(self):
        t0 = time.monotonic()
        if not self.a.no_cameras:
            from robots import config, run
            app = run.with_action_space(config.load(pathlib.Path(self.a.config).expanduser()), self.a.head)
            self.app = app
            if not self.a.fake:
                from robots import adapter
                self.wire = adapter.YAM_POSE_WIRE if self.a.head == "eef" else adapter.YAM_VIDEO_WIRE
                dc = run.policy_config(app)
                dc["_record"] = False  # no background upload of this preview's frames (as dimos does)
                self.client = run.open_client(dc)
                if self.a.head == "eef":
                    self.client.wait_for_ik(60)
            for cam in app.cameras:
                try:
                    self.readers.append(run._open_camera(app, cam))
                except RuntimeError as e:  # a RealSense on a USB 2 link can't do 30 fps at 1280x720; 15 is plenty at 5 Hz
                    if "USB 2" not in str(e):
                        raise
                    say(event="warning", warning=f"camera {cam.name} is on USB 2: opening it at 15 fps")
                    self.readers.append(run._open_camera(app, dataclasses.replace(cam, fps=15)))
            for r in self.readers:
                r.start()
        self.last_fed = time.monotonic()
        self.feeder = threading.Thread(target=self.feed, daemon=True)
        self.feeder.start()
        while self.fed < WINDOW and not self.error:
            if time.monotonic() - self.last_fed > NO_FRAMES_S:
                raise RuntimeError(f"no full set of fresh camera frames for {NO_FRAMES_S:.0f} s "
                                   f"({self.skipped} ticks skipped; last problem: {self.why})")
            time.sleep(0.05)
        if self.error:
            raise RuntimeError(self.error)
        say(event="ready", open_s=round(time.monotonic() - t0, 2), fed=self.fed, skipped=self.skipped,
            head=self.a.head, task=self.a.task, fake=self.a.fake)

    def feed(self):
        """5 Hz: hand the client the newest frames and state. A camera that drops a frame skips the tick."""
        nxt = time.monotonic()
        while not self.stop.is_set():
            nxt += 1.0 / OBS_HZ
            try:
                st = get_state(self.a.stage)["arms"]
                self.state = st
                now = time.monotonic()
                frames = {r.name: r.frame for r in self.readers} if self.readers else {}
                late = [n for n, f in frames.items() if f is None or not f.jpeg or now - f.jpeg_timestamp_s > STALE_S]
                if late:
                    self.skipped += 1
                    self.why = f"no fresh frame from {late}"
                else:
                    if self.client:
                        obs = types.SimpleNamespace(
                            state=types.SimpleNamespace(left=arm_obs(st["left"]), right=arm_obs(st["right"])),
                            images=frames)
                        f, p = self.wire.to_dream(obs, self.a.task)
                        with self.lock:
                            self.client.set_observation(f, p, timestamp=now)
                    self.frames = frames
                    self.fed += 1
                    self.last_fed = now
            except Exception as e:  # a bad /state read or a camera hiccup: skip the tick, give up only when it lasts
                self.skipped += 1
                self.why = repr(e)[:300]
                if time.monotonic() - self.last_fed > NO_FRAMES_S:
                    self.error = f"observation feed stopped: {e!r}"
                    return
            time.sleep(max(0.0, nxt - time.monotonic()))

    def save_frames(self, n):
        if not self.out:
            return
        for name, f in getattr(self, "frames", {}).items():
            (self.out / f"chunk{n}_{name}.jpg").write_bytes(base64.b64decode(f.jpeg))

    def chunk(self, n, commanded=None):
        if self.error:
            raise RuntimeError(self.error)
        cmd = commanded or {s: self.state[s]["joints"] for s in ("left", "right")}
        t0 = time.monotonic()
        if self.a.fake:
            q0 = np.asarray(cmd["right"], float)
            dq = np.array([0.15, 0.35, 0.15, -0.1, 0.0, 0.0]) * np.linspace(0, 1, 48)[:, None]
            rows = {"q_r_chunk": np.c_[q0 + dq, np.linspace(1, 0.2, 48)],
                    "q_l_chunk": np.c_[np.tile(self.state["left"]["joints"], (48, 1)), np.full(48, 0.5)]}
            time.sleep(0.3)
        else:
            from dream.errors import DreamServerError
            com = {"q_r_chunk": np.asarray(cmd["right"], float), "q_l_chunk": np.asarray(cmd["left"], float)}
            deadline = t0 + self.a.max_wait
            while True:
                try:
                    with self.lock:
                        rows = self.client.get_action(commanded=com)
                    break
                except DreamServerError as e:  # pool warming or a busy seat: wait as told, capped
                    wait = min(e.retry_after_s or 2.0, 10.0)
                    log_call(task=self.a.task, head=self.a.head, n=n, ok=False, error=str(e)[:200],
                             retryable=bool(e.retryable or e.retry_after_s))
                    if not (e.retryable or e.retry_after_s) or time.monotonic() + wait > deadline:
                        raise
                    time.sleep(wait)
        lat = round(time.monotonic() - t0, 3)
        R, L = np.asarray(rows["q_r_chunk"], float), np.asarray(rows["q_l_chunk"], float)
        timing = None if self.a.fake else self.client.last_timing
        log_call(task=self.a.task, head=self.a.head, n=n, ok=True, latency_s=lat, rows=len(R), fake=self.a.fake,
                 timing={k: v for k, v in (timing or {}).items() if isinstance(v, (int, float, str))})
        self.save_frames(n)
        ch = {"event": "chunk", "n": n, "latency_s": lat, "t": [round(i / HZ_OUT, 4) for i in range(len(R))],
              "left": {"q": np.round(L[:, :6], 5).tolist(), "g": np.round(np.clip(L[:, 6], 0, 1), 4).tolist()},
              "right": {"q": np.round(R[:, :6], 5).tolist(), "g": np.round(np.clip(R[:, 6], 0, 1), 4).tolist()}}
        if self.out:
            (self.out / f"chunk{n}.json").write_text(json.dumps(ch))
        say(**ch)

    def close(self):
        self.stop.set()
        for r in self.readers:
            try:
                r.stop()
            except Exception:
                pass
        if self.client:
            try:
                self.client.close()
            except Exception:
                pass
        say(event="closed", fed=self.fed, skipped=self.skipped)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--task", default="pick up the green frog")
    ap.add_argument("--config", default="~/.dreamlabs/station.yaml")
    ap.add_argument("--head", choices=["joint", "eef"], default="joint")
    ap.add_argument("--stage", default="http://127.0.0.1:8770", help="read with GET /state only")
    ap.add_argument("--serve", action="store_true")
    ap.add_argument("--fake", action="store_true", help="no Dream call: a made-up reach")
    ap.add_argument("--no-cameras", action="store_true", help="with --fake: no cameras either")
    ap.add_argument("--max-wait", type=float, default=45.0, help="s to wait for a warming model per call")
    ap.add_argument("--out", default=str(HERE / "runs" / time.strftime("%H%M%S")), help="frames and chunks")
    a = ap.parse_args()
    if a.no_cameras and not a.fake:
        ap.error("--no-cameras needs --fake: Dream-1 needs the cameras")
    p = Preview(a)
    try:
        p.open()
        if not a.serve:
            p.chunk(1)
            return
        n = 0
        for line in sys.stdin:
            msg = json.loads(line or "{}")
            if msg.get("cmd") == "close":
                break
            if msg.get("cmd") == "next":
                n += 1
                p.chunk(n, msg.get("commanded"))
    except Exception as e:
        say(event="error", error=f"{type(e).__name__}: {e}"[:500])
    finally:
        p.close()


if __name__ == "__main__":
    main()
