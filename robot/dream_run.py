"""Dream-1 picks up Lars, through our checker: ask Dream-1 (no motion), check, play slowly, else our own pick_lars.

    python3 robot/dream_run.py --task "pick up the green frog"             # real: the stage at :8770, Dream on nuck9
    python3 robot/dream_run.py --replay robot/lars/dream_chunks.json --stage http://127.0.0.1:8785   # kin, offline

Laptop side, stdlib only. Per chunk (up to --chunks, default 3):
1. robot/dream_preview.py runs on the station over ssh (--serve): cameras and the Dream client open, the observation
   window fed at 5 Hz. "next" asks Dream-1 for one 48-row chunk (~1 s round trip, one billed call). Nothing moves.
2. POST /trajectory plays that chunk's busier arm (the one --arm names, or the one that moves most in chunk 1).
   The stage retimes it to <= 0.3 rad/s and the gripper to 1.0 per s (a full close takes about 1 s), checks it with
   show.Checker (limits, table, the other arm) and refuses it with the reason, nothing moving.
3. While it plays, the next chunk is fetched (about 1.5 s before the end, so its ~1 s round trip is hidden), with
   the seam where this chunk ends.
It stops at a chunk that barely moves (STILL), the first refusal, a tracking error over TRACK_LIMIT, a stage fault, a Dream error or --timeout. Then
the cameras are released (the stage's dock check needs the overhead camera), the arm goes back along the chunks it
played, reversed (the same checks; it opens the gripper where it closed it, so Lars goes back down where he was;
   a chunk that ends closed on him makes the stage hold him (--carry), and the reversed one that opens puts him down),
and on a failure our own pick_lars and place_lars play instead (--no-fallback skips them).

Output: JSON lines on stdout, for agent/gestures.py ([dream:pick_lars]):
    {"event": "say", "text": "..."}      a line for the host, the "let me ask Dream-1... let's see" beat
    {"event": "info", ...}               what happened, for the log
    {"event": "done", "ok", "dream_chunks", "fallback", "reason", "latency_s"}
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
MAX_CHUNKS = 6
ARMS = ("left", "right")


def out(**kw):
    print(json.dumps(kw), flush=True)


def say(text):
    out(event="say", text=text)


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
               f"--task {shlex.quote(a.task)} --head {a.head}" + (" --fake" if a.fake else ""))
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


def play(stage, arm, name, t, q, g, speed, carry):
    """POST /trajectory (it starts; wait_done waits for its end). Returns (None, reply) or (reason, reply)."""
    code, r = stage.call("/trajectory", {"arm": arm, "t": t, "q": q, "g": g, "speed": speed, "name": name,
                                         "carry": carry})
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
    ap.add_argument("--task", default="pick up the green frog")
    ap.add_argument("--stage", default="http://127.0.0.1:8770")
    ap.add_argument("--station", default="nuck9")
    ap.add_argument("--remote-dir", default="~/dream-ghost")
    ap.add_argument("--head", choices=["joint", "eef"], default="joint")
    ap.add_argument("--arm", choices=["auto", *ARMS], default="auto")
    ap.add_argument("--chunks", type=int, default=3)
    ap.add_argument("--speed", type=float, default=1.0, help="x the stage's 0.3 rad/s cap (0.1..1)")
    ap.add_argument("--timeout", type=float, default=150.0, help="s for Dream's part, the way back excluded")
    ap.add_argument("--replay", help="a JSON list of chunk events (dream_preview.py output) instead of the station")
    ap.add_argument("--fake", action="store_true", help="the station preview makes up a reach (no Dream call)")
    ap.add_argument("--no-fallback", action="store_true")
    ap.add_argument("--carry", default="lars", help="the prop a chunk that ends closed holds (the stage then keeps "
                    "the grip: no mouth, no gesture grip changes, until a chunk that ends open puts it down); '' for none")
    a = ap.parse_args()
    a.chunks = max(1, min(a.chunks, MAX_CHUNKS))
    stage, t0 = Stage(a.stage), time.monotonic()
    end = t0 + a.timeout
    s = stage.state()
    busy = [side for side in ARMS if s["arms"][side].get("playing")]
    if s.get("fault") or busy or any(s["props"].values()):
        out(event="done", ok=False, dream_chunks=0, fallback=False,
            reason=f"stage not ready: fault {s.get('fault')}, busy {busy}, props {s['props']}")
        return
    say("Let me ask Dream-1 how to pick me up.")
    played, latency, reason, arm, pre, refused = [], [], None, None, None, False
    try:
        pre = Preview(a)
        pre.ready()
        cmd = {side: s["arms"][side]["joints"] for side in ARMS}
        pre.ask(cmd)
        ch = pre.chunk()
        for k in range(1, a.chunks + 1):
            latency.append(ch.get("latency_s"))
            if arm is None:
                arm = a.arm if a.arm != "auto" else max(ARMS, key=lambda side: moves(ch, side))
                out(event="info", arm=arm, moves={side: round(moves(ch, side), 3) for side in ARMS})
            name = f"dream{k}"
            c = {"t": ch["t"], **ch[arm]}
            if moves(ch, arm) < STILL and max(c["g"]) - min(c["g"]) < 0.3:
                reason = f"Dream-1 stays put (chunk {k}: the {arm} arm moves {moves(ch, arm):.2f} rad)"
                break
            if k == 1:
                say(f"Dream-1 says: the {arm} arm, like this. Let's see if our checker likes it.")
            reason, r = play(stage, arm, name, c["t"], c["q"], c["g"], a.speed, a.carry)
            out(event="info", chunk=k, reply=r)
            if reason:
                refused = code_refused(r)
                break
            played.append(c)
            if k == 1:
                say("Our checker says yes. Gently, please!")
            asked = [False]
            other = s["arms"]["left" if arm == "right" else "right"]["joints"]

            def prefetch(st, k=k, c=c, r=r, started=time.monotonic()):
                if k < a.chunks and not asked[0] and time.monotonic() - started > r["duration_s"] - PREFETCH_S:
                    asked[0] = True
                    pre.ask({arm: c["q"][-1], ("left" if arm == "right" else "right"): other})
            reason = wait_done(stage, arm, name, min(end, time.monotonic() + r["duration_s"] + 5), prefetch)
            if reason or k == a.chunks:
                break
            if not asked[0]:
                pre.ask({arm: c["q"][-1], ("left" if arm == "right" else "right"): other})
            ch = pre.chunk()
    except Exception as e:  # Dream down, no frames, ssh gone: our own pick then
        reason = f"{type(e).__name__}: {e}"
    finally:
        if pre:
            pre.close()  # the cameras free again before anything else moves
    out(event="info", dream_reason=reason, played=len(played), latency_s=latency)
    if played:
        say("That's where we stop Dream-1's idea. Back the way we came." if reason else
            "That was all Dream-1's own idea, checked by us. And back down I go.")
        back = None
        for i, c in enumerate(reversed(played)):
            back, r = play(stage, arm, f"dream_back{i + 1}", c["t"], c["q"][::-1], c["g"][::-1], a.speed,
                           None)  # the way back only puts down, never takes
            out(event="info", back=i + 1, reply=r)
            if not back:
                back = wait_done(stage, arm, f"dream_back{i + 1}", time.monotonic() + r["duration_s"] + 5)
            if back:
                break
        if back:
            say("I'm stuck halfway. Can someone at the robot help?")
            out(event="done", ok=False, dream_chunks=len(played), fallback=False,
                reason=f"{reason or 'ok'}; the way back stopped: {back}", latency_s=latency)
            return
    if not reason:
        out(event="done", ok=True, dream_chunks=len(played), fallback=False, reason=None, latency_s=latency)
        return
    if a.no_fallback:
        out(event="done", ok=False, dream_chunks=len(played), fallback=False, reason=reason, latency_s=latency)
        return
    if refused:
        why = next((w for k, w in (("table", "it would have bumped the table"), ("reaches", "it bends a joint too far"),
                                   ("from the", "it comes too close to my other arm"), ("rad/s", "it's too fast"))
                    if k in reason), "it doesn't fit our rules")
        say(f"Dream-1's plan didn't pass our safety check: {why}. Our own move, then!")
    else:
        say("Dream-1 is daydreaming today. Our own move, then!")
    fb = None
    for g in ("pick_lars", "place_lars"):
        code, r = stage.call("/play", {"arm": "right", "gesture": g})
        if code != 200:
            fb = f"{g}: {r.get('error')}"
            break
        time.sleep(0.3)
        fb = wait_done(stage, "right", g, time.monotonic() + 60)
        if fb:
            break
    out(event="done", ok=False, dream_chunks=len(played), fallback=not fb, reason=reason + (f"; fallback: {fb}" if fb else ""),
        latency_s=latency)


if __name__ == "__main__":
    main()
