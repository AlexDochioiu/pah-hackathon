"""Dream-1 picks up Lars, through our checker: ask Dream-1 (no motion), check, play slowly, else our own pick_lars.

    python3 robot/dream_run.py                    # real: the stage at :8770 (tunnel), Dream-1 on nuck9
    python3 robot/dream_run.py --replay robot/lars/dream_pick.json --stage http://127.0.0.1:8785   # kin, offline

Laptop side, stdlib only. Chunk after chunk, until Dream-1 has picked Lars up (--pick-s cap, --chunks cap):
1. robot/dream_preview.py runs on the station over ssh (--serve): cameras and the Dream client open, the observation
   window fed at 5 Hz. "next" asks Dream-1 for one 48-row chunk (~1 s round trip, one billed call). Nothing moves.
2. A chunk that barely moves (STILL: Dream-1 often idles a few chunks at rest before it reaches) is skipped, not
   played, and the next one asked for. The first one that moves picks the arm (--arm, or the one that moves most).
3. POST /trajectory plays it with carry=lars: the stage retimes it to <= 0.3 rad/s x --speed and the gripper to
   1.0 per s (a full close takes about 1 s), checks it with show.Checker (limits, table: fingertips may brush it to
   -3 mm, the other arm) and refuses it with the reason, nothing moving. A chunk that closes the gripper from open
   makes the stage hold Lars (props): from then on it keeps the grip, gestures too, and /talk is refused.
4. While it plays, the next chunk is fetched (about 1.5 s before the end, so its ~1 s round trip is hidden), with
   the seam where this chunk ends.
Done when the stage holds Lars and a chunk ends with the fingertips LIFT_MM above the table: no more Dream-1 calls,
the arm stays where it is, holding him, and is the stage's again (gestures play with him in hand; place_lars puts
him back). It stops early at a refusal, a tracking error over TRACK_LIMIT, a stage fault, a Dream error, --chunks or
--pick-s. Then the cameras are released (the stage's dock check needs the overhead camera), the arm goes back along
the chunks it played, reversed (the same checks; the reversed closing chunk opens the gripper, so Lars goes back
down where he was), and our own pick_lars and place_lars play instead (--no-fallback skips them).

A miss (owner, Nov 7 night) is Dream-1 messing up, not our checker saying no: the gripper closes almost fully
(EMPTY_GRIP measured: nothing in it), the hold is lost before the lift (an opening chunk let him go), or the chunks
or the time run out (held but never lifted, or never held). Then no more Dream-1 chunks; an "oops" line goes out at
once (the host's quip and a sad trombone), and the arm plays a short "ah, damn it" (REACTION, through the stage's
normal checked /play): right away when its hand is empty and the stage takes it from where the arm is, else after
the way back. Then our own pick, as above. While Lars is held the stage keeps the grip, gestures too.

--handover (act 2's wand, Nov 8): Dream-1 picks up the green pen through our checker (whole chunks, no --raw, no
Lars prop: the stage never thinks an arm holds the pen), then our own IK takes over from where Dream-1 left the arm:
present, wrist roll, the handover to the other arm and the other arm laying it down (robot/dream_hand.py on the
station, cam_pick.py's handover(); played raw like --cam's). A fresh overhead snap before Dream-1 starts gives the
pen's spot, so the claw's grasp says where the pen sits in it. A grip is judged by the measured gripper: the fingers
stopped GRIP_BLOCK short of the command (the pen is in there); under EMPTY_GRIP: closed on nothing; open again, or
lifted without that stop: a miss. A lift is RISE_MM. A miss, a refusal or a Dream error
all end in the oops line (kinds as below, plus refused), the way back and the facepalm. No handover plan: the same
arm rolls it and puts it back the way Dream-1 came (checked).

--patter S: while nothing has been said for S s (the preview opening, still chunks, a slow chunk playing), a filler
line goes out (PATTER), so a show never has dead air. --hold: the fallback plays pick_lars only, so it ends holding
Lars like a Dream-1 pick (the caller puts him back with place_lars).

Output: JSON lines on stdout, for agent/gestures.py ([dream:pick_lars]):
    {"event": "say", "text": "..."}      a line for the host, the "let me ask Dream-1... let's see" beat
    {"event": "oops", "kind", "reaction", "sfx", "text"}   Dream-1 missed (kind: empty_grip, dropped, not_lifted,
                                         timeout, no_plan; --handover: refused too); text is a default quip (the show picks its own from DREAM_OOPS)
    {"event": "info", ...}               what happened, for the log
    {"event": "done", "ok", "held", "dream_chunks", "fallback", "reason", "missed", "latency_s"}
"""

import argparse
import json
import pathlib
import queue
import shlex
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

TRACK_LIMIT = 0.2   # rad: stop Dream's run before the stage's own TRACK_STOP (0.35) freezes everything
PREFETCH_S = 1.5    # s before a chunk ends that the next one is asked for (a get_action takes ~1 s)
STILL = 0.15        # rad: a chunk whose arm moves less (and whose gripper changes < 0.3) is Dream-1 idling, not a
                    # move: seen on real frames Nov 7 (jitter of 0.02-0.06 rad at rest): our own pick instead
CLOSED_G = 0.35     # raw: Dream-1 commanding its gripper at or below this means it's holding (stage TRAJ_CLOSED)
RISE_MM = 30.0      # raw: lifted once the fingertips end this far above their lowest since the gripper closed. ponytail: knob
LIFT_MM = 80.0      # mm: the fingertips this high above the table at a chunk's end, holding Lars: he's picked up
MAX_CHUNKS = 60
ARMS = ("left", "right")
EMPTY_GRIP = 0.07   # measured gripper (0 closed .. 1 open) below this while "holding": it closed on nothing. Lars
                    # stops the fingers at 0.09-0.3 (the real picks, Nov 7). ponytail: knob, check on the real arms
GRIP_BLOCK = 0.015  # --handover: the measured gripper this far above the command: the fingers stopped on something
                    # (the pen, ~1 cm, stops them near 0.1 of the 95 mm stroke). ponytail: knob, check on the real arms
REACTION = "facepalm"   # the arm's "ah, damn it" after a miss: an existing gesture (head into the hand, shaking)
OOPS_SFX = "sad_trombone"   # agent/sounds/sfx/crowd, CC0
OOPS_LINES = {"empty_grip": "It grabbed thin air. Very magical.", "dropped": "And it dropped my wand. Typical.",
              "not_lifted": "Close. The wand stays put. It's shy.", "timeout": "Dream-1 ran out of time. Brave try.",
              "no_plan": "Dream-1 took one look at the table and called in sick. Honestly? Relatable.",
              "refused": "Dream-1 had an idea. My safety code had a better one: no."}
PATTER = ["Every move you see is Dream-1's own idea, worked out from the cameras, right now.",
          "It's looking for the green pen. Green, like me. Easy mistake.",
          "Robots move slowly when they're thinking. So do I, before coffee.",
          "This is the part where the founders stop breathing.",
          "Any moment now. Any. Moment.",
          "The back row can't see this, but trust me, it's very dramatic.",
          "Somewhere, a GPU is working very hard for one pen.",
          "Take your time, Dream-1. No pressure. Just the judges.",
          "Still thinking. Big brain, small table.",
          "If this works, we're calling it magic.",
          "Okay, Dream-1. The audience is getting hungry."]
OURS_PATTER = ["Dream-1 did the grab. Our own code takes it from here."]  # --handover, after the lift: no "Dream-1's
HAND_PATTER = ["Left hand, meet right hand. Be nice.",                   # own idea" now; HAND_PATTER while it hands over
               "Hands are complicated. I wouldn't know, I'm a frog.",
               "Almost there. Nobody sneeze."]
CAM_PATTER = ["Plan B: the overhead camera finds the pen, and plain old geometry does the rest.",  # --cam-fallback
              "No AI this time. Just maths. Very romantic.", *HAND_PATTER]
FALLBACK_OOPS = "Plan B didn't work either. Let's call it modern art."  # the --cam-fallback run missing too
FALLBACK_WAIT_S = 20.0  # s the --cam-fallback waits for both arms to be free (a facepalm still playing)
_lock, _said = threading.Lock(), [time.monotonic()]
_lines = [PATTER]  # the patter's lines: --handover swaps in HAND_PATTER once our own code takes over


def out(**kw):
    with _lock:
        print(json.dumps(kw), flush=True)


def say(text):
    _said[0] = time.monotonic()
    out(event="say", text=text)


def patter(every, stop):
    """A filler line whenever nothing has been said for `every` s, until stop is set."""
    k, lines = 0, _lines[0]
    while not stop.wait(0.25):
        if _lines[0] is not lines:
            k, lines = 0, _lines[0]
        if time.monotonic() - _said[0] >= every and k < len(lines):  # each line once: a repeat sounds broken (owner, Nov 8)
            say(lines[k])
            k += 1


def react(stage, arm):
    """The miss reaction through the normal checked /play; True once it has played (False: refused or stopped)."""
    code, r = stage.call("/play", {"arm": arm, "gesture": REACTION})
    out(event="info", reaction=REACTION, code=code, reply=r)
    if code != 200:
        return False
    time.sleep(0.3)
    return wait_done(stage, arm, REACTION, time.monotonic() + 20) is None


class Stage:
    def __init__(self, url):
        self.url = url.rstrip("/")

    def call(self, path, body=None):
        req = urllib.request.Request(self.url + path, data=None if body is None else json.dumps(body).encode(),
                                     method="GET" if body is None else "POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def state(self):
        return self.call("/state")[1]


class Preview:
    """The station's dream_preview.py --serve over ssh, or chunks replayed from a file (--replay)."""

    def __init__(self, a):
        self.lines, self.proc, self.replay = queue.Queue(), None, None
        if a.replay:
            data = json.loads(pathlib.Path(a.replay).read_text())
            self.replay = [c for c in (data if isinstance(data, list) else [data]) if c.get("event", "chunk") == "chunk"]
            return
        cmd = (f"cd {a.remote_dir} && exec nice -n 5 ~/dream-env/bin/python -u dream_preview.py --serve "
               f"--task {shlex.quote(a.task)} --head {a.head} --config ~/dream-test.yaml" + (" --fake" if a.fake else ""))
        self.proc = subprocess.Popen(["ssh", "-o", "ControlPath=none", "-o", "BatchMode=yes", a.station, cmd],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     text=True, bufsize=1)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.proc.stdout:
            try:
                self.lines.put(json.loads(line))
            except ValueError:
                pass
        self.lines.put({"event": "error", "error": "the station preview exited"})

    def wait(self, kind, timeout):
        """The next line of `kind` ("ready" or "chunk"); raises on an error line or a timeout."""
        end = time.monotonic() + timeout
        while True:
            try:
                msg = self.lines.get(timeout=max(0.01, end - time.monotonic()))
            except queue.Empty:
                raise RuntimeError(f"no {kind} from Dream-1 within {timeout:.0f} s") from None
            if msg.get("event") == kind:
                return msg
            if msg.get("event") == "error":
                raise RuntimeError(msg.get("error", "preview error"))
            out(event="info", preview=msg)

    def ready(self, timeout=40):
        return {"event": "ready", "replay": True} if self.replay is not None else self.wait("ready", timeout)

    def ask(self, commanded):
        if self.replay is not None:
            if not self.replay:
                self.lines.put({"event": "error", "error": "no more replayed chunks"})
            else:
                self.lines.put(self.replay.pop(0))
            return
        self.proc.stdin.write(json.dumps({"cmd": "next", "commanded": commanded}) + "\n")
        self.proc.stdin.flush()

    def chunk(self, timeout=25):
        return self.wait("chunk", timeout)

    def close(self):
        if not self.proc:
            return
        try:
            self.proc.stdin.write('{"cmd": "close"}\n')
            self.proc.stdin.close()
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()


def moves(ch, side):
    q = ch[side]["q"]
    return max(abs(a - b) for a, b in zip(q[-1], q[0])) + max(max(abs(x - y) for x, y in zip(r, q[0])) for r in q)


def grasp_row(g):
    """The row where the gripper closed: the first at or below CLOSED_G after its last open one (>= 0.6)."""
    last_open = max((i for i, x in enumerate(g) if x >= 0.6), default=-1)
    return next((i for i in range(last_open + 1, len(g)) if g[i] <= CLOSED_G), len(g) - 1)


def station_json(a, cmd, timeout):
    """`cmd` on the station in ~/physical-ai-hacks; its stdout as JSON, or None (the reason goes out as info)."""
    try:
        r = subprocess.run(["ssh", "-o", "ControlPath=none", "-o", "BatchMode=yes", a.station,
                            f"cd ~/physical-ai-hacks && {cmd}"], capture_output=True, text=True, timeout=timeout)
        out(event="info", station=r.stderr.strip()[-200:])
        return json.loads(r.stdout) if r.returncode == 0 else None
    except (subprocess.SubprocessError, OSError, ValueError) as e:
        out(event="info", station=f"{type(e).__name__}: {e}"[:200])
        return None


def hand_plan(a, stage, arm, start, grasp, pen):
    """--handover after the lift: dream_hand.py's present, roll and handover chunks from where the arm is now; []
    without a plan (then the wand wiggle and the way back)."""
    st = stage.state()["arms"][arm]
    q = shlex.quote(json.dumps({"arm": arm, "now": {x: list(start["arms"][x]["joints"])[:6] for x in ARMS},
                                "q": list(st["joints"])[:6], "g": st.get("grip", 0.0), "grasp": grasp, "pen": pen}))
    plan = station_json(a, f"nice -n 10 .venv/bin/python ~/dial/dream_hand.py plan --q {q}", 90)
    return plan if isinstance(plan, list) else []


def play(stage, arm, name, t, q, g, speed, carry, raw=False):
    """POST /trajectory (it starts; wait_done waits for its end). Returns (None, reply) or (reason, reply)."""
    code, r = stage.call("/trajectory", {"arm": arm, "t": t, "q": q, "g": g, "speed": speed, "name": name,
                                         "carry": carry, "raw": raw})
    if code != 200:
        return r.get("error", f"HTTP {code}"), r
    return None, r


def code_refused(reply):
    """A refusal by our checks (the stage's 409), not a stage that can't be reached."""
    return isinstance(reply, dict) and reply.get("ok") is False


def wait_done(stage, arm, name, end, on_tick=None):
    while True:
        s = stage.state()
        a = s["arms"][arm]
        if s.get("fault"):
            return f"stage fault: {s['fault']}"
        if a.get("last_error"):
            return f"stage: {a['last_error']}"
        if (a.get("tracking_error") or 0) > TRACK_LIMIT:
            return f"tracking error {a['tracking_error']:.2f} rad: something is in the way"
        if a.get("playing") != name:
            return None
        if time.monotonic() > end:
            return "timed out"
        if on_tick:
            on_tick(a)
        time.sleep(0.1)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--task", default="pick up the soft green plush toy frog and hold it up")
    ap.add_argument("--stage", default="http://127.0.0.1:8770")
    ap.add_argument("--station", default="nuck9")
    ap.add_argument("--cli", action="store_true", help="hand the arms to `dreamcli robot run` for --pick-s (stage server "
                    "stopped meanwhile, restarted after): Dream-1 raw, none of our code in the loop")
    ap.add_argument("--remote-dir", default="~/dream-ghost")
    ap.add_argument("--head", choices=["joint", "eef"], default="joint", help="joint: what dreamcli --action-space joint runs (all of today's eef runs failed)")
    ap.add_argument("--arm", choices=["auto", *ARMS], default="auto")
    ap.add_argument("--chunks", type=int, default=30, help="Dream-1 calls at most (still ones included)")
    ap.add_argument("--pick-s", type=float, default=60.0, help="s for Dream-1's part (asking and playing), the way "
                    "back excluded; our 0.3 rad/s cap plays its chunks ~1.5-2.5x slower than dreamcli")
    ap.add_argument("--raw", action="store_true", help="the stage plays Dream-1's motion unchecked (retimed only)")
    ap.add_argument("--speed", type=float, default=1.0, help="x the stage's 0.3 rad/s cap (0.1..4)")
    ap.add_argument("--exec", type=int, default=0, help="play only each chunk's first N steps (dreamcli ~16); 0: all")
    ap.add_argument("--cam", action="store_true", help="no Dream Labs: the station finds the green pen in a fresh overhead "
                    "snap and plans the pick with our IK (robot/cam_pick.py), played like Dream-1's chunks")
    ap.add_argument("--handover", action="store_true", help="Dream-1 picks the pen (checked chunks, no Lars prop), then "
                    "our IK presents it, rolls it and hands it to the other arm (robot/dream_hand.py on the station)")
    ap.add_argument("--cam-fallback", action="store_true", help="after a Dream-1 miss, refusal or error (the pen back "
                    "on the table, or never lifted): the oops line, then --cam's camera pick, present, handover and place")
    ap.add_argument("--replay", help="a JSON list of chunk events (dream_preview.py output) instead of the station")
    ap.add_argument("--fake", action="store_true", help="the station preview makes up a reach (no Dream call)")
    ap.add_argument("--no-fallback", action="store_true")
    ap.add_argument("--carry", default="lars", help="the prop a closing chunk makes the stage hold; '' for none")
    ap.add_argument("--patter", type=float, default=0.0, help="s of silence before a filler line (0: none)")
    ap.add_argument("--hold", action="store_true", help="the fallback only picks Lars up (ends held, like Dream-1)")
    a = ap.parse_args()
    if a.cam and (a.handover or a.cam_fallback):
        ap.error("--cam is the camera pick itself: no --handover or --cam-fallback with it")
    a.chunks = max(1, min(a.chunks, MAX_CHUNKS))
    stage, t0 = Stage(a.stage), time.monotonic()
    end = t0 + a.pick_s
    if a.cam or a.handover:  # an earlier run that never put the pen down left a stuck "holds lars": open that gripper where it is
        st = stage.state()
        for side in ARMS:
            if st["props"].get(side) == "lars" and not str(st["arms"][side].get("playing") or "").startswith("dream"):
                steps(stage, side, "dream_release", [0.6], [list(st["arms"][side]["joints"])[:6]], [1.0], a)
    # idle_breathe loops between gestures and /trajectory takes over from it; wait out a gesture still playing.
    while True:
        s = stage.state()
        busy = [side for side in ARMS if s["arms"][side].get("playing") not in (None, "idle_breathe")
                or s["arms"][side].get("queue")]
        if not busy or s.get("fault") or time.monotonic() - t0 > 10:
            break
        time.sleep(0.2)
    if s.get("fault") or busy or any(s["props"].values()):
        out(event="oops", kind="no_plan", reaction=REACTION, sfx=OOPS_SFX, text=OOPS_LINES["no_plan"])  # owner: say it
        out(event="done", ok=False, held=False, dream_chunks=0, fallback=False,
            reason=f"stage not ready: fault {s.get('fault')}, busy {busy}, props {s['props']}")
        return
    say("Dream-1 is looking for it now, with three cameras.")
    stop = threading.Event()
    if a.patter > 0:
        threading.Thread(target=patter, args=(a.patter, stop), daemon=True).start()
    try:
        (cli if a.cli else run)(a, stage, s, end)
    finally:
        stop.set()


STAGE_PAT = "[b]in/python -u /home/pah/dial/stage.py"
STAGE_STOP = (f'for p in $(pgrep -f "{STAGE_PAT}"); do kill -INT $p; done; '
              f'for i in $(seq 40); do pgrep -f "{STAGE_PAT}" >/dev/null || exit 0; sleep 0.5; done; exit 1')
STAGE_START = ('cd ~/physical-ai-hacks && setsid -f sh -c "exec .venv/bin/python -u ~/dial/stage.py --real '
               '> ~/dial/stage_after_dreamcli.out 2>&1 < /dev/null"')


def wave(stage, arm, c, secs=2.6):
    """The owner (Nov 8): wave the wand 2-3 s before putting it back. Raw (unchecked) wiggle of the wrist (j4 +-0.25,
    j6 +-0.4 rad, two cycles) around where the lift ended, the gripper kept where Dream-1 left it."""
    import math
    q0, g0 = c["q"][-1], c["g"][-1]
    t = [round(0.05 * (i + 1), 3) for i in range(int(secs * 20))]
    q = [[*q0[:3], q0[3] + 0.25 * math.sin(2 * math.pi * 2 * x / secs), q0[4], q0[5] + 0.4 * math.sin(2 * math.pi * 2 * x / secs)]
         for x in t]
    reason, r = play(stage, arm, "dream_wave", t, q, [g0] * len(t), 1.0, None, True)
    out(event="info", wave=reason or "ok")
    if not reason:
        wait_done(stage, arm, "dream_wave", time.monotonic() + r["duration_s"] + 5)


def steps(stage, arm, name, t, q, g, a, raw=None):
    """Play one piece once the arm is free (Nov 8: the way back was refused while the wave still finished); a reason
    if it was refused or stopped. raw: a.raw unless given."""
    t_free = time.monotonic() + 10
    while time.monotonic() < t_free and str(stage.state()["arms"][arm].get("playing") or "").startswith("dream"):
        time.sleep(0.1)
    reason, r = play(stage, arm, name, t, q, g, a.speed, None, a.raw if raw is None else raw)
    reason = reason or wait_done(stage, arm, name, time.monotonic() + 2 * r["duration_s"] + 10)  # Nov 8: a turn to the audience outlasted duration + 5 s
    if reason:
        out(event="info", piece=name, refused=str(reason)[:160])
    return reason


def cli(a, stage, s, end):
    """--cli (owner, Nov 8: raw dreamcli picks far better than our /trajectory path): stop the stage server (it folds
    the arms), run `dreamcli robot run` until --pick-s is up, Ctrl-C it (it homes the arms and switches them off),
    start the stage server again and wait for it. We can't see a grab from here: the person watching judges it."""
    ssh = ["ssh", "-o", "ControlPath=none", "-o", "BatchMode=yes", a.station]
    sh = lambda cmd, t=90: subprocess.run(ssh + [cmd], capture_output=True, text=True, timeout=t)
    say("Dream-1, the arms are yours. No script, no safety net.")
    if sh(STAGE_STOP).returncode:
        out(event="done", ok=False, held=False, dream_chunks=0, fallback=False, reason="the stage server didn't stop")
        return
    run_cmd = ("cd ~ && setsid -f sh -c 'echo $$ > /tmp/dreamcli_show.pid; exec ~/dream-env/bin/dreamcli robot run "
               f"--config ~/dream-test.yaml --action-space joint --task \"{''.join(ch for ch in a.task if ch not in '\'"$`\\')}\" "
               "> ~/dial/dreamcli_show.out 2>&1 < /dev/null'")
    sh(run_cmd)
    out(event="info", cli="dreamcli robot run started", task=a.task)
    while time.monotonic() < end:
        time.sleep(1)
    sh('kill -INT $(cat /tmp/dreamcli_show.pid); for i in $(seq 60); do kill -0 $(cat /tmp/dreamcli_show.pid) 2>/dev/null || exit 0; sleep 0.5; done; exit 1')
    say("Time's up, Dream-1. Arms back to me.")
    sh(STAGE_START)
    t0 = time.monotonic()
    while time.monotonic() - t0 < 90:
        try:
            stage.state()
            break
        except Exception:
            time.sleep(2)
    else:
        out(event="done", ok=False, held=False, dream_chunks=0, fallback=False, reason="the stage server didn't come back")
        return
    out(event="done", ok=True, held=False, arm=None, dream_chunks=0, fallback=False, reason=None, missed=None)


def cam_fallback(a, stage, reason, missed, n_played, latency):
    """--cam-fallback, after a Dream-1 miss whose way back finished (the pen on the table, nothing held): --cam's run
    (snap, cam_pick.py's plan, raw like the live demo's --cam --raw) does the act instead. Its done line is the last;
    a miss in it gets its own oops line (FALLBACK_OOPS) and the way back, and it never takes a stage prop (carry
    None). Arms not free, a fault or a prop held: no fallback, the done line says why."""
    t0 = time.monotonic()
    while True:  # the facepalm may still be playing
        st = stage.state()
        busy = [x for x in ARMS if st["arms"][x].get("playing") not in (None, "idle_breathe") or st["arms"][x].get("queue")]
        if not busy or st.get("fault") or time.monotonic() - t0 > FALLBACK_WAIT_S:
            break
        time.sleep(0.2)
    if st.get("fault") or busy or any(st["props"].values()):
        out(event="done", ok=False, held=any(st["props"].values()), dream_chunks=n_played, fallback=False,
            reason=f"{reason}; no camera fallback: fault {st.get('fault')}, busy {busy}, props {st['props']}",
            missed=missed, latency_s=latency)
        return
    b = argparse.Namespace(**vars(a))
    b.cam, b.raw, b.handover, b.cam_fallback, b.no_fallback = True, True, False, False, True  # as DREAM_ARGS' --cam line
    b.replay, b.exec, b.arm, b.carry = None, 0, "auto", None
    out(event="info", fallback="cam", dream_reason=reason, dream_missed=missed, dream_chunks=n_played)
    _lines[0] = list(CAM_PATTER)
    say("Plan B: our own code, same pen.")
    run(b, stage, st, time.monotonic() + b.pick_s, fb=True)


def run(a, stage, s, end, fb=False):
    """One pick (fb: the --cam-fallback run after a Dream-1 miss: our own lines, not Dream-1's)."""
    played, latency, reason, arm, pre, refused, held, lifted = [], [], None, None, None, False, False, False
    low = float("inf")  # raw: the lowest fingertip since the gripper closed
    opened = False
    missed = None
    pen = grasp = None  # --handover: the pen's spot before Dream-1 starts (dream_hand.py locate); the arm's joints at the close
    gripped = False     # --handover: the measured gripper showed something between the fingers
    cmd = {side: s["arms"][side]["joints"] for side in ARMS}
    try:
        if a.handover:
            a.carry = None  # as --cam: the pen isn't the stage's Lars prop, so no stuck "holds lars" after a run
            if not a.replay:  # the cameras free before the preview opens them; no pen seen: Dream-1 still tries, no handover
                pen = station_json(a, ".venv/bin/python ~/dial/snap.py > /dev/null && "
                                      ".venv/bin/python ~/dial/dream_hand.py locate", 60)
                out(event="info", pen=pen)
        if a.cam:  # the pen's spot changes between runs: snap and plan now (owner, Nov 8)
            a.carry = None  # the pen isn't the stage's Lars prop: a stuck "holds lars" refused the next run (Nov 8)
            q = shlex.quote(json.dumps({x: list(s["arms"][x]["joints"])[:6] for x in ARMS}))
            r = subprocess.run(["ssh", "-o", "ControlPath=none", "-o", "BatchMode=yes", a.station,
                                "cd ~/physical-ai-hacks && .venv/bin/python ~/dial/snap.py > /dev/null && "
                                f".venv/bin/python ~/dial/cam_pick.py --q {q}"], capture_output=True, text=True, timeout=60)
            out(event="info", cam=r.stderr.strip()[-200:])
            if r.returncode:
                raise RuntimeError(f"cam_pick: {r.stderr.strip()[-160:]}")
            a.replay = "/tmp/pah_cam_pick.json"
            pathlib.Path(a.replay).write_text(r.stdout)
        pre = Preview(a)
        pre.ready()
        pre.ask(cmd)
        restarts = 0
        for k in range(1, a.chunks + 1):
            try:
                ch = pre.chunk()
            except RuntimeError as e:  # Nov 8: "DreamSessionExpired ... the episode restarted server-side" mid-pick
                if "SessionExpired" not in str(e) or restarts >= 2:
                    raise
                restarts += 1
                out(event="info", restart=str(e)[:160])
                pre.close()
                pre = Preview(a)
                pre.ready()
                pre.ask(cmd)
                ch = pre.chunk()
            latency.append(ch.get("latency_s"))
            if time.monotonic() > end:
                reason = f"no pick within {a.pick_s:.0f} s"
                break
            side = arm or (a.arm if a.arm != "auto" else max(ARMS, key=lambda x: moves(ch, x)))
            c = {"t": ch["t"], **ch[side]}
            if a.exec:  # like dreamcli: play only the chunk's first steps, then look again (Nov 8: whole chunks, slowed, missed)
                c = {key: (v[:a.exec] if isinstance(v, list) else v) for key, v in c.items()}
            if moves(ch, side) < STILL and max(c["g"]) - min(c["g"]) < 0.3:
                out(event="info", chunk=k, still=round(moves(ch, side), 3))
                if k < a.chunks:
                    pre.ask(cmd)  # Dream-1 idles: ask again from where the arms are
                continue
            if arm is None:
                arm = side
                out(event="info", arm=arm, moves={x: round(moves(ch, x), 3) for x in ARMS})
                say(f"The {arm} arm, then." if fb else f"Dream-1 picks the {arm} arm.")
            name = f"dream{k}"
            reason, r = play(stage, arm, name, c["t"], c["q"], c["g"], a.speed, a.carry, a.raw)
            out(event="info", chunk=k, g=[round(min(c["g"]), 2), round(c["g"][-1], 2)], reply=r)  # Dream-1's gripper: lowest, end
            if reason:
                refused = code_refused(r)
                break
            played.append(c)
            if len(played) == 1:
                say("Here it comes. Gently, please!")
            if a.raw or a.handover:  # the stage's prop only notices a close inside one chunk (--handover: no prop at
                # all): go by Dream-1's own gripper and height
                opened = opened or max(c["g"]) >= 0.6  # the arms rest with the gripper shut: a grab opens first
                if opened and c["g"][-1] <= CLOSED_G:  # (Nov 8: it lifted Lars in the left arm and nothing noticed)
                    low = min(low, r.get("tip_mm", low))
                    if not held:
                        held = True
                        if a.handover:  # (the live --cam --raw path computes nothing new here)
                            grasp = c["q"][grasp_row(c["g"])]
                        say("Got it!")
                else:
                    held, low = False, float("inf")
                lifted = held and r.get("tip_end_mm", 0) - low >= RISE_MM
            else:
                if not held and r["props"][arm]:
                    held = True
                    say("Got it!")
                lifted = held and r.get("tip_end_mm", 0) >= LIFT_MM
            asked = [False]
            other = s["arms"]["left" if arm == "right" else "right"]["joints"]
            cmd = {arm: c["q"][-1], ("left" if arm == "right" else "right"): other}

            def prefetch(st, k=k, r=r, started=time.monotonic()):
                if not lifted and k < a.chunks and not asked[0] and time.monotonic() - started > r["duration_s"] - PREFETCH_S:
                    asked[0] = True
                    pre.ask(cmd)
            reason = wait_done(stage, arm, name, time.monotonic() + r["duration_s"] + 5, prefetch)  # --pick-s: next ask
            if reason:
                break
            st = stage.state()
            now_held, grip = bool(st["props"].get(arm)), st["arms"][arm].get("grip_measured")
            if now_held and grip is not None and grip < EMPTY_GRIP and not a.raw:  # a squeezed Lars reads 0.05
                missed, reason = "empty_grip", f"the gripper closed on nothing ({grip:.2f} measured)"
                break
            if held and not now_held and not a.raw and not a.handover:  # raw, --handover: the stage's prop isn't how we track it
                missed, reason, held, lifted = "dropped", "the hold was lost before the lift", False, False
                break
            if a.handover and held:  # no stage prop: the measured gripper tells (no reading: trust Dream-1's close)
                if grip is not None and grip < EMPTY_GRIP:
                    missed = "dropped" if gripped else "empty_grip"
                    reason, held, lifted = f"the gripper closed on nothing ({grip:.2f} measured)", False, False
                    break
                # the fingers stopped short of the command: something is between them
                gripped = gripped or grip is None or grip >= st["arms"][arm].get("grip", 0.0) + GRIP_BLOCK
            if a.handover and gripped and not held:
                missed, reason, lifted = "dropped", "Dream-1 opened its gripper before the lift", False
                break
            if a.handover and lifted and not gripped:  # e.g. closed to 0.3 (~28 mm): a 1 cm pen isn't in there
                missed, reason, held, lifted = "empty_grip", f"lifted, nothing between the fingers ({grip} measured)", False, False
                break
            if lifted:
                break
            if k == a.chunks:
                reason = f"no pick in {a.chunks} chunks"
                break
            if not asked[0]:
                pre.ask(cmd)
        else:
            reason = reason or f"no pick in {a.chunks} chunks"
    except Exception as e:  # Dream down, no frames, ssh gone: our own pick then
        reason = f"{type(e).__name__}: {e}"
    finally:
        if pre:
            pre.close()  # the cameras free again before anything else moves
    if missed is None and reason and ("cam_pick" in reason or "stage not ready" in reason):  # never even tried (owner: joke)
        missed = "no_plan"
    if missed is None and reason and reason.startswith("no pick"):  # Dream-1's own time or chunks ran out
        missed = "not_lifted" if held else "timeout"
    if (a.handover or a.cam_fallback or fb) and reason and missed is None:  # our checker said no, or Dream-1 broke off: a joke all the same
        missed = "refused" if refused else "timeout" if played else "no_plan"
    n_played = len(played)
    out(event="info", dream_reason=reason, played=len(played), held=held, lifted=lifted, missed=missed,
        latency_s=latency)
    if lifted and not reason and (a.raw or a.handover):  # nothing else can put him down from where Dream-1 lifted him
        say("Behold! The robot is now a wizard. I, sadly, am still a frog.")
        if a.cam:
            pieces = [c for c in json.loads(pathlib.Path(a.replay).read_text()) if c.get("event") in ("wave", "hand")]
        elif a.handover:  # planned now, from where Dream-1 left the arm; the patter stops crediting Dream-1
            _lines[0] = list(OURS_PATTER)
            pieces = [c for c in hand_plan(a, stage, arm, s, grasp, pen) if c.get("event") in ("wave", "hand")]
            if any(c["event"] == "hand" for c in pieces):
                _lines[0] = list(HAND_PATTER)
        else:
            pieces = []
        ours = a.raw or a.handover  # our own planned pieces play raw, as --cam's (handover() checks the arms' clearance)
        waves, hand = [c for c in pieces if c["event"] == "wave"], [c for c in pieces if c["event"] == "hand"]
        stopped = None
        if waves:  # cam_pick's: the claw turns to the audience, the wrist rolls the pen left and right
            for i, c in enumerate(waves):
                if (stopped := steps(stage, arm, f"dream_wave{i + 1}", c["t"], c[arm]["q"], c[arm]["g"], a, ours)):
                    break
        elif not a.handover:  # --handover stops 3 cm up: no raw (unchecked) wiggle that low, just the way back
            wave(stage, arm, played[-1])  # 2-3 s of a wizard's flourish with the wand, then back the way it came
        if hand and not stopped:  # cam_pick's handover (owner, Nov 8): the other hand takes it and puts it down
            say("Teamwork! Pass it to the other hand.")
            for i, c in enumerate(hand):
                side = c.get("arm", arm)
                if (stopped := steps(stage, side, f"dream_hand{i + 1}", c["t"], c[side]["q"], c[side]["g"], a, ours)):
                    say("I'm stuck halfway. Can someone at the robot help?")
                    break
            if not stopped:
                say("Back on the table, safe and sound." if fb else "Thank you, Dream-1. Back on the table, safe and sound.")
        else:
            say("Put it back gently." if fb else "Thank you, Dream-1. Put it back gently.")
            for i, c in enumerate(reversed(played)):
                if steps(stage, arm, f"dream_back{i + 1}", c["t"], c["q"][::-1], c["g"][::-1], a):
                    break
        out(event="done", ok=True, held=False, arm=arm, dream_chunks=n_played, fallback=False, reason=None,
            missed=None, latency_s=latency)
        return
    if lifted and not reason:
        say("That was all Dream-1's own idea.")
        out(event="done", ok=True, held=True, arm=arm, dream_chunks=n_played, fallback=False, reason=None,
            missed=None, latency_s=latency)
        return
    reacted = tried = False
    if missed:
        out(event="oops", kind=missed, reaction=REACTION, sfx=OOPS_SFX, text=FALLBACK_OOPS if fb else OOPS_LINES[missed],
            **({"fallback": True} if fb else {}))
        _said[0] = time.monotonic()  # the host says the quip: no filler line right on top of it
        react_arm = arm or "right"
        # empty hands: react from where the arm is, if the stage lets it (--handover holding the pen: back first)
        if not any(stage.state()["props"].values()) and not (a.handover and held):
            reacted, tried = react(stage, react_arm), True
            if reacted:
                played = []  # the reaction ends at the stage pose: no way back needed
    went_back = bool(played)
    t_busy = time.monotonic() + 20  # a stopped chunk may still be playing out: the way back waits for it (Nov 8: refused)
    while played and arm and time.monotonic() < t_busy and str(stage.state()["arms"][arm].get("playing") or "").startswith("dream"):
        time.sleep(0.3)
    if played:
        say("Back the way we came." if fb else "That's where we stop Dream-1's idea. Back the way we came.")
        back = None
        for i, c in enumerate(reversed(played)):
            back, r = play(stage, arm, f"dream_back{i + 1}", c["t"], c["q"][::-1], c["g"][::-1], a.speed,
                           None, a.raw)  # the way back only puts down, never takes
            out(event="info", back=i + 1, reply=r)
            if not back:
                back = wait_done(stage, arm, f"dream_back{i + 1}", time.monotonic() + r["duration_s"] + 5)
            if back:
                break
        if back or any(stage.state()["props"].values()):
            say("I'm stuck halfway. Can someone at the robot help?")
            out(event="done", ok=False, held=any(stage.state()["props"].values()), dream_chunks=n_played,
                fallback=False, reason=f"{reason}; the way back stopped: {back or 'still holding'}", missed=missed,
                latency_s=latency)
            return
    if missed and not reacted and (went_back or not tried):
        react(stage, arm or "right")  # back where it started, the hand empty: the "ah, damn it" now
    if a.cam_fallback and not fb:  # the pen is on the table again (or never left it): our camera pick does the act
        cam_fallback(a, stage, reason, missed, n_played, latency)
        return
    if a.no_fallback:
        out(event="done", ok=False, held=False, dream_chunks=n_played, fallback=False, reason=reason,
            missed=missed, latency_s=latency)
        return
    if missed:
        say("Our own move, then!")
    elif refused:
        say("Dream-1 got stuck. Our own move, then!")
    else:
        say("Dream-1 is daydreaming today. Our own move, then!")
    fb = None
    for g in ("pick_lars",) if a.hold else ("pick_lars", "place_lars"):
        code, r = stage.call("/play", {"arm": "right", "gesture": g})
        if code != 200:
            fb = f"{g}: {r.get('error')}"
            break
        time.sleep(0.3)
        fb = wait_done(stage, "right", g, time.monotonic() + 60)
        if fb:
            break
    out(event="done", ok=False, held=bool(a.hold and not fb), dream_chunks=n_played, fallback=not fb,
        reason=reason + (f"; fallback: {fb}" if fb else ""), missed=missed, latency_s=latency)


if __name__ == "__main__":
    main()
