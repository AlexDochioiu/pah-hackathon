"""Stage server: one process owns both arms, plays safety-checked gestures, and serves a local HTTP API.

  stage.py [--real] [--backend i2rt|kin] [--port 8770] [--warp N]     serve 127.0.0.1:8770
  stage.py --bringup [--real] ...                                       one-pass test of every gesture, then exit

Default is simulation: i2rt's MuJoCo sim if the kit is importable (run from ~/physical-ai-hacks), else a pure
kinematic stand-in (--backend kin). --real drives the real arms: takes the station lock, refuses unless both arms are
at rest, and on Ctrl-C / SIGTERM folds both arms back to rest before switching the motors off.
Run on the station: cd ~/physical-ai-hacks && .venv/bin/python ~/dial/stage.py [--real]

API (JSON in and out; "arm" is left | right | both, default both):
  GET  /gestures                       [{"name","duration_s","arms","about","hold","loop","max_speed","uses_gripper"}]
  GET  /state                          per arm: what it plays, progress, queue, joints, tracking error, last error;
                                       plus rejected gestures and gesture pairs refused on the two arms at once
  POST /play {"arm","gesture","speed":1.0,"mode":"now"|"queue"}   now = newest wins (default), queue = after current
  POST /freeze {"arm"}                 hold the current pose at once, drop queues
  POST /idle {"arm"}                   go to stage and breathe; gestures return to idle afterwards
  POST /stage {"arm","speed"}          go to the stage pose and hold
  GET  /duets                          [{"name","duration_s","about","max_speed","closest_contact_cm"}]
  POST /duet {"name","speed"}          a two-arm duet (the arms touch); needs both arms free, and while it plays
                                       everything but /freeze is refused. /freeze stops both arms on the duet's
                                       path; the next request then first finishes the duet at half speed (409)
  POST /stance {"stance","speed"}      "audience" (default) or "each_other": both arms turn to the stance pose and
                                       every gesture, idle and /stage play relative to it until the next /stance
  POST /rest {"arm","speed"}           fold back to rest and hold (motors stay on)
  POST /grip {"arm","value"}           set the gripper 0 closed .. 1 open (close it on a prop)
Every motion is checked before it starts (limits, table clearance with the prop, 15 cm from the other arm's committed
motion); a refused request returns 409 with the reason and nothing moves.
"""
import argparse, collections, json, signal, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import numpy as np
import duets, show

SIDES = show.SIDES
REST_TOL = 0.25      # rad from home that counts as resting (same as lift.py)
FREEZE_S = 0.15      # s to brake to a stop on /freeze
TRACK_STOP = 0.35    # rad: tracking error above this freezes everything (something is blocking an arm)


class StageError(Exception):
    pass


class KinArms:
    """Kinematic stand-in for dream_hackathon's Arms: the measured pose follows the command with a short lag."""

    def __init__(self):
        self.q = {s: show.HOME[s].copy() for s in SIDES}
        self.g = {s: 1.0 for s in SIDES}
        self.start_joints = {s: v.copy() for s, v in self.q.items()}
        self.energized = True

    def read(self):
        return {s: v.copy() for s, v in self.q.items()}, dict(self.g)

    def command(self, joints, grips):
        for s in SIDES:
            self.q[s] += 0.5 * (np.asarray(joints[s]) - self.q[s])
            self.g[s] += 0.5 * (float(grips[s]) - self.g[s])

    def close(self):
        pass


def open_backend(real, backend):
    """(arms, lock or None, name)."""
    if real or backend == "i2rt":
        try:
            from dream_hackathon import arms, config
            station = config.load("configs/station.yaml")
        except Exception as e:
            if real:
                sys.exit(f"cannot load the kit ({e}): run from ~/physical-ai-hacks with its .venv")
            print(f"i2rt sim unavailable ({e}); using the kinematic sim")
            return KinArms(), None, "kin"
        if real:
            from dream_hackathon import station_lock
            lock = station_lock.acquire("dial-stage")
            try:
                a = arms.open_arms(station, energize=True)
            except BaseException:
                lock.close(); raise
            return a, lock, "real"
        return arms.open_arms(station, energize=True, sim=True), None, "i2rt-sim"
    return KinArms(), None, "kin"


def along(Q, u):
    """Rows of Q at fractional indices u (linear in between): a path re-timed."""
    i = np.clip(np.floor(u).astype(int), 0, len(Q) - 2)
    f = (np.asarray(u) - i)[:, None]
    return Q[i] * (1 - f) + Q[i + 1] * f


class Plan:
    def __init__(self, name, Q, G, sweep, gesture=None, loop=False, hold=False, duet=None):
        self.name, self.Q, self.G, self.sweep, self.gesture, self.loop, self.hold = name, Q, G, sweep, gesture, loop, hold
        self.duet = duet  # duet name: both arms' plans run in lockstep
        self.i, self.err = 0, 0.0

    @property
    def done(self):
        return self.i >= len(self.Q) - 1


class Stage:
    def __init__(self, arms, backend="kin", gesture_dir=show.GESTURE_DIR, warp=1.0, track_stop=TRACK_STOP):
        self.arms, self.backend, self.warp, self.track_stop = arms, backend, warp, track_stop
        self.ck = show.Checker()
        self.lib, self.rejected, self.unsafe = show.load_library(self.ck, gesture_dir)
        if "idle_breathe" not in self.lib:
            raise SystemExit("idle_breathe is missing or rejected: the stage server needs it")
        self.duets, self.duets_rejected = duets.load_duets(duets.DuetChecker(self.ck), verbose=False)
        self.duet_left = None  # a duet frozen half-way: {"name", "Q": {side: path}, "G": {side: grips}}
        self.stance = "audience"
        self.lock = threading.RLock()
        q, g = arms.read()
        self.cmd_q = {s: np.asarray(q[s], float).copy() for s in SIDES}
        self.cmd_g = {s: float(g[s]) for s in SIDES}
        self.meas = {s: self.cmd_q[s].copy() for s in SIDES}
        self.plan = {s: self._hold_plan(s, "start") for s in SIDES}
        self.queue = {s: collections.deque() for s in SIDES}
        self.after = {s: "hold" for s in SIDES}
        self.last_error = {s: None for s in SIDES}
        self.stop = threading.Event()
        self.fault = None
        self.thread = threading.Thread(target=self._loop, daemon=True)

    # ---------------------------------------------------------------- planning (call with self.lock held)

    def _hold_plan(self, side, name="hold"):
        q = self.cmd_q[side]
        return Plan(name, q[None].copy(), np.array([self.cmd_g[side]]), self.ck.pose_sweep(side, q), hold=True)

    def _build(self, side, name, Q, G, gesture=None, speed=1.0, base_sweep=None, loop=False, hold=False):
        """Plan = blend from the current command into (Q, G) then (Q, G). Checks limits, table clearance and lean-back.
        The blend and pose targets (stage, rest) are folds: they may lean back as far as rest or their start pose."""
        q0, g0 = self.cmd_q[side], self.cmd_g[side]
        G = np.full(len(Q), g0) if G is None else G
        BQ, BG = show.blend(q0, Q[0], speed, g0, G[0])
        probs, bsweep = self.ck.check_samples(side, np.vstack([q0[None], BQ, Q[:1]]), fold=True)
        if base_sweep is None:
            p2, base_sweep = self.ck.check_samples(side, Q, fold=gesture is None)
            probs += p2
        else:
            probs += show.motion_problems(Q) + self.ck.lean_problems(side, Q)  # at the requested speed
        if probs:
            raise StageError(f"{side} {name}: " + "; ".join(probs))
        return Plan(name, np.vstack([q0[None], BQ, Q]), np.concatenate([[g0], BG, G]),
                    show.join(bsweep, base_sweep), gesture, loop, hold)

    def _gesture_plan(self, side, name, speed):
        g = self.lib.get(name)
        if g is None:
            raise StageError(f"unknown gesture {name!r}" + (f" (rejected: {self.rejected[name]})" if name in self.rejected else ""))
        st = self.stance if name not in show.FOLD else "audience"  # stage/rest always fold via the audience pose
        if st not in g.stances:
            raise StageError(f"{name} is not available in the {st} stance: {self.ck.stance_rejected.get((name, st), '')}")
        speed = float(np.clip(speed, 0.1, g.max_speed))
        Q, G = g.samples(side, speed, stance=st)
        return self._build(side, name, Q, G, g, speed, self.ck.swept[show.key(g.name, side, st)],
                           loop=g.loop, hold=g.hold), speed

    def _pose_plan(self, side, name, q, speed):
        q = np.asarray(q, float)
        return self._build(side, name, q[None], None, speed=speed, hold=True)

    def _rest_plan(self, side, speed):
        try:
            return self._pose_plan(side, "rest", show.HOME[side], speed)
        except StageError:  # straight down is blocked: go through the stage pose
            p1 = self._pose_plan(side, "rest", show.stage_pose(side), speed)
            BQ, BG = show.blend(p1.Q[-1], show.HOME[side], speed, p1.G[-1], p1.G[-1])
            probs, sw = self.ck.check_samples(side, BQ, fold=True)
            if probs:
                raise
            return Plan("rest", np.vstack([p1.Q, BQ]), np.concatenate([p1.G, BG]), show.join(p1.sweep, sw), hold=True)

    def _commit(self, plans):
        """Start {side: Plan} at once if each clears the other arm's motion (new or committed) by GAP."""
        for side, p in plans.items():
            other = SIDES[1 - SIDES.index(side)]
            osweep = plans[other].sweep if other in plans else self.plan[other].sweep
            d = show.gap(p.sweep, osweep)
            if d < show.GAP:
                raise StageError(f"{side} {p.name} would come {d*100:.0f} cm from the {other} arm "
                                 f"({self.plan[other].name if other not in plans else plans[other].name}); "
                                 f"needs {show.GAP*100:.0f} cm")
        for side, p in plans.items():
            self.plan[side] = p
            self.last_error[side] = None
        self.fault = None

    # ---------------------------------------------------------------- API

    @staticmethod
    def sides(arm):
        if arm in (None, "both"):
            return list(SIDES)
        if arm not in SIDES:
            raise StageError(f"arm must be left, right or both, not {arm!r}")
        return [arm]

    def _guard(self):
        """Nothing else moves while a duet plays. A duet frozen half-way is finished first, slowly, on its path."""
        p = self.plan["left"]
        if p.duet and not p.done:
            raise StageError(f"duet {p.duet} is playing ({(len(p.Q) - 1 - p.i) / show.HZ:.1f} s left); /freeze stops it")
        if self.duet_left:
            name = self.duet_left["name"]
            self._finish_duet()
            raise StageError(f"duet {name} was frozen half-way: finishing it at half speed first; try again after it")

    def _finish_duet(self, rate=0.5):
        left, self.duet_left = self.duet_left, None
        n = len(left["Q"]["left"])
        r = rate * np.minimum(1.0, np.arange(1, 4 * n) / (0.3 * show.HZ))  # samples per tick, eased in over 0.3 s
        u = np.concatenate([[0.0], np.cumsum(r)])
        u = np.append(u[u < n - 1], n - 1)
        for s in SIDES:
            Q = along(left["Q"][s], u)
            self.plan[s] = Plan("duet", Q, np.interp(u, np.arange(n), left["G"][s]), self.ck.pose_sweep(s, Q[-1]),
                                hold=True, duet=left["name"])

    def play_duet(self, name, speed=1.0):
        """Both arms: blend to the stage pose, the duet, blend back to the stance pose, in lockstep."""
        with self.lock:
            du = self.duets.get(name)
            if du is None:
                raise StageError(f"unknown duet {name!r}" + (f" (rejected: {self.duets_rejected[name]})"
                                                              if name in self.duets_rejected else ""))
            self._guard()
            busy = [s for s in SIDES if not (self.plan[s].done or self.plan[s].loop) or self.queue[s]]
            if busy:
                raise StageError(f"duet {name} needs both arms free; {busy[0]} is playing {self.plan[busy[0]].name}")
            speed = float(np.clip(speed, 0.1, du.max_speed))
            smp = du.samples(speed)
            parts = {}
            for s in SIDES:
                Q, G = smp[s]
                G = np.full(len(Q), self.cmd_g[s]) if G is None else G
                BQ, BG = show.blend(self.cmd_q[s], Q[0], speed, self.cmd_g[s], G[0])
                OQ, OG = show.blend(Q[-1], show.stage_pose(s, self.stance), speed, G[-1], G[-1])
                parts[s] = [(np.vstack([self.cmd_q[s][None], BQ]), np.concatenate([[self.cmd_g[s]], BG])), (Q[1:], G[1:]),
                            (OQ, OG)]
            # The duet itself passed DuetChecker; the blends in and out are checked like any move, both arms at once.
            for k, before in ((0, None), (2, 1)):
                sw = {}
                for s in SIDES:
                    Qk = parts[s][k][0] if before is None else np.vstack([smp[s][0][-1:], parts[s][k][0]])
                    probs, sw[s] = self.ck.check_samples(s, Qk, fold=True)
                    if probs:
                        raise StageError(f"duet {name}: {s} {'into' if k == 0 else 'out of'} it: " + "; ".join(probs))
                if show.gap(sw["left"], sw["right"]) < show.GAP:
                    raise StageError(f"duet {name}: the arms would come within {show.GAP*100:.0f} cm "
                                     f"{'on the way in' if k == 0 else 'on the way back'}")
                n = max(len(parts[s][k][0]) for s in SIDES)  # the shorter blend waits: both start the duet together
                for s in SIDES:
                    Qk, Gk = parts[s][k]
                    if len(Qk) < n:
                        last = Qk[-1:] if len(Qk) else smp[s][0][-1:]
                        lastg = Gk[-1:] if len(Gk) else smp[s][1][-1:] if smp[s][1] is not None else [self.cmd_g[s]]
                        parts[s][k] = (np.vstack([Qk.reshape(-1, 6), np.repeat(last, n - len(Qk), 0)]),
                                       np.concatenate([Gk, np.repeat(lastg, n - len(Gk))]))
            for s in SIDES:
                Q = np.vstack([q for q, _ in parts[s]]); G = np.concatenate([g for _, g in parts[s]])
                # its sweep, for whatever the other arm does next, is where it ends: nothing else moves meanwhile
                self.plan[s] = Plan(f"duet:{name}", Q, G, self.ck.pose_sweep(s, Q[-1]),
                                    hold=self.after[s] != "idle", duet=name)
                self.queue[s].clear(); self.last_error[s] = None
            self.fault = None
            return {"ok": True, "duet": name, "speed": round(speed, 2), "duration_s": round(len(Q) / show.HZ, 2)}

    def play(self, arm, gesture, speed=1.0, mode="now"):
        with self.lock:
            self._guard()
            sides = self.sides(arm)
            if mode == "queue":
                started = {}
                for s in sides:
                    if self.plan[s].done or self.plan[s].loop:
                        started[s] = self._gesture_plan(s, gesture, speed)
                    else:
                        self._gesture_plan(s, gesture, speed)  # validate the name and speed now
                        self.queue[s].append((gesture, speed))
                if started:
                    self._commit({s: p for s, (p, _) in started.items()})
                return {"ok": True, "started": sorted(started), "queued": sorted(set(sides) - set(started))}
            plans = {s: self._gesture_plan(s, gesture, speed) for s in sides}
            self._commit({s: p for s, (p, _) in plans.items()})
            for s in sides:
                self.queue[s].clear()
            return {"ok": True, "started": sides, "speed": round(next(iter(plans.values()))[1], 2)}

    def freeze(self, arm=None):
        with self.lock:
            p = self.plan["left"]
            if p.duet and not p.done:  # both arms brake together on the duet's own (checked) path
                k = np.arange(1, int(FREEZE_S * show.HZ) + 1)
                u = np.minimum(p.i + np.cumsum(1 - k / (len(k) + 1)), len(p.Q) - 1)
                self.duet_left = {"name": p.duet, "Q": {}, "G": {}}
                for s in SIDES:
                    ps = self.plan[s]
                    Q = along(ps.Q, u)
                    self.duet_left["Q"][s] = np.vstack([Q[-1:], ps.Q[int(np.ceil(u[-1])):]])
                    self.duet_left["G"][s] = np.concatenate([[ps.G[int(u[-1])]], ps.G[int(np.ceil(u[-1])):]])
                    self.plan[s] = Plan("freeze", np.vstack([self.cmd_q[s][None], Q]), np.full(len(Q) + 1, self.cmd_g[s]),
                                        self.ck.pose_sweep(s, Q[-1]), hold=True, duet=p.duet)
                    self.queue[s].clear(); self.after[s] = "hold"
                return {"ok": True}
            for s in self.sides(arm):
                self.queue[s].clear()
                self.after[s] = "hold"
                p = self.plan[s]
                v = (p.Q[p.i] - p.Q[max(p.i - 1, 0)]) * show.HZ if not p.done else np.zeros(6)
                t = np.arange(1, int(FREEZE_S * show.HZ) + 1) / show.HZ
                Q = np.clip(self.cmd_q[s] + np.outer(t - t * t / (2 * FREEZE_S), v), show.SAFE_LO, show.SAFE_HI)
                probs, sw = self.ck.check_samples(s, Q, fold=True)
                probs = [x for x in probs if "speed" not in x and "acceleration" not in x]  # braking may decelerate hard
                if probs or show.gap(sw, self.plan[SIDES[1 - SIDES.index(s)]].sweep) < show.GAP:
                    Q = self.cmd_q[s][None].copy(); sw = self.ck.pose_sweep(s, Q[0])  # stop dead instead
                self.plan[s] = Plan("freeze", np.vstack([self.cmd_q[s][None], Q]), np.full(len(Q) + 1, self.cmd_g[s]),
                                    show.join(sw, self.ck.pose_sweep(s, self.cmd_q[s])), hold=True)
            return {"ok": True}

    def idle(self, arm=None):
        with self.lock:
            self._guard()
            sides = self.sides(arm)
            plans = {s: self._gesture_plan(s, "idle_breathe", 1.0)[0] for s in sides}
            self._commit(plans)
            for s in sides:
                self.after[s] = "idle"; self.queue[s].clear()
            return {"ok": True}

    def go_stage(self, arm=None, speed=1.0):
        with self.lock:
            self._guard()
            sides = self.sides(arm)
            self._commit({s: self._pose_plan(s, "stage", show.stage_pose(s, self.stance), speed) for s in sides})
            for s in sides:
                self.after[s] = "hold"; self.queue[s].clear()
            return {"ok": True}

    def set_stance(self, stance, speed=1.0):
        """Both arms turn to the stance pose; gestures, idle and /stage then play relative to it."""
        with self.lock:
            self._guard()
            if stance not in show.STANCES:
                raise StageError(f"stance must be one of {', '.join(show.STANCES)}, not {stance!r}")
            plans = {s: self._pose_plan(s, "stance", show.stage_pose(s, stance), speed) for s in SIDES}
            for s, p in plans.items():
                p.hold = self.after[s] != "idle"  # an idle arm resumes breathing in the new stance
            self._commit(plans)
            self.stance = stance
            for s in SIDES:
                self.queue[s].clear()
            return {"ok": True, "stance": stance}

    def go_rest(self, arm=None, speed=1.0):
        with self.lock:
            self._guard()
            sides = self.sides(arm)
            self._commit({s: self._rest_plan(s, speed) for s in sides})
            for s in sides:
                self.after[s] = "hold"; self.queue[s].clear()
            return {"ok": True}

    def grip(self, arm=None, value=0.0):
        with self.lock:
            self._guard()
            v = float(np.clip(value, 0, 1))
            plans = {}
            for s in self.sides(arm):
                p = self.plan[s]
                if not p.done and not p.loop:
                    raise StageError(f"{s} arm is busy with {p.name}; grip after it")
                BQ, BG = show.blend(self.cmd_q[s], self.cmd_q[s], 1.0, self.cmd_g[s], v)
                Q = np.vstack([self.cmd_q[s][None], BQ])
                plans[s] = Plan("grip", Q, np.concatenate([[self.cmd_g[s]], BG]), self.ck.pose_sweep(s, self.cmd_q[s]), hold=True)
            self._commit(plans)
            return {"ok": True}

    def busy(self, sides=SIDES):
        with self.lock:
            return any(not self.plan[s].done or self.queue[s] for s in sides) or bool(self.duet_left)

    def wait(self, sides=SIDES, timeout=120):
        t0 = time.monotonic()
        while self.busy(sides):
            if self.fault:
                raise StageError(self.fault)
            if time.monotonic() - t0 > timeout:
                raise StageError("timed out waiting for the arms")
            time.sleep(0.02)

    def state(self):
        with self.lock:
            out = {"backend": self.backend, "fault": self.fault, "rejected": self.rejected, "stance": self.stance,
                   "duet": self.plan["left"].duet if not self.plan["left"].done else None,
                   "duet_frozen": self.duet_left["name"] if self.duet_left else None, "rejected_duets": self.duets_rejected,
                   "not_in_stance": {f"{n} ({st})": why for (n, st), why in self.ck.stance_rejected.items()},
                   "unsafe_pairs": sorted([list(p) for p in self.unsafe]), "arms": {}}
            for s in SIDES:
                p = self.plan[s]
                out["arms"][s] = {
                    "playing": None if p.done and not p.loop else p.name, "pose": p.name,
                    "progress": round(p.i / max(len(p.Q) - 1, 1), 2), "after": self.after[s],
                    "queue": [g for g, _ in self.queue[s]], "grip": round(self.cmd_g[s], 2),
                    "joints": np.round(self.cmd_q[s], 3).tolist(), "measured": np.round(self.meas[s], 3).tolist(),
                    "tracking_error": round(p.err, 3), "last_error": self.last_error[s]}
            return out

    # ---------------------------------------------------------------- 50 Hz loop

    def _advance(self, s):
        p = self.plan[s]
        if not p.done:
            p.i += 1
            return
        if self.queue[s]:
            name, speed = self.queue[s].popleft()
            try:
                self._commit({s: self._gesture_plan(s, name, speed)[0]})
                self.plan[s].i = 1  # the plan's first sample is where we are: keep moving
            except StageError as e:
                self.last_error[s] = str(e); print("refused:", e, flush=True)
            return
        if p.loop or (self.after[s] == "idle" and not p.hold and p.name != "freeze"):
            try:
                self._commit({s: self._gesture_plan(s, "idle_breathe", 1.0)[0]})
                self.plan[s].i = 1
            except StageError as e:
                self.after[s] = "hold"; self.last_error[s] = f"idle refused: {e}"

    def _loop(self):
        period = 1.0 / show.HZ / self.warp
        nxt = time.monotonic()
        while not self.stop.is_set():
            with self.lock:
                for s in SIDES:
                    self._advance(s)
                    p = self.plan[s]
                    self.cmd_q[s] = p.Q[p.i].copy()
                    self.cmd_g[s] = float(p.G[p.i])
                try:
                    self.arms.command(dict(self.cmd_q), dict(self.cmd_g))
                    q, _ = self.arms.read()
                except Exception as e:
                    self.fault = f"arm I/O failed: {e}"; print(self.fault, flush=True); self.stop.set(); break
                for s in SIDES:
                    self.meas[s] = np.asarray(q[s], float)
                    p = self.plan[s]
                    err = float(np.max(np.abs(self.meas[s] - p.Q[max(p.i - 1, 0)])))  # vs last tick's target
                    p.err = max(p.err, err)
                    if err > self.track_stop and not self.fault:
                        self.fault = f"{s} arm tracking error {err:.2f} rad (blocked?): froze both arms"
                        print(self.fault, flush=True)
                        threading.Thread(target=self.freeze, daemon=True).start()
            nxt += period
            time.sleep(max(0.0, nxt - time.monotonic()))
            if time.monotonic() - nxt > 0.2:
                nxt = time.monotonic()  # fell behind (debugger, load): don't race to catch up


def shutdown(st, close, lock):
    """Fold both arms to rest, wait, switch off. A second Ctrl-C is ignored: switching off mid-air drops the arms."""
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: print("still returning to rest, please wait", flush=True))
    try:
        if not st.thread.is_alive():
            raise StageError("control loop is not running: cannot return to rest; fold the arms by hand")
        st.fault, st.track_stop = None, float("inf")  # like home.py: keep driving to rest, never freeze mid-air
        t0 = time.monotonic()
        while st.thread.is_alive() and time.monotonic() - t0 < 30:  # a duet ends on its own path first: the arms
            with st.lock:                                             # may be touching, so no straight homing
                if st.duet_left:
                    st._finish_duet()
                if not st.plan["left"].duet or st.plan["left"].done:
                    break
            time.sleep(0.05)
        try:
            st.go_rest(speed=0.5)
        except StageError as e:  # ponytail: last resort, the same straight slow homing as home.py
            print("rest plan refused, homing straight:", e, flush=True)
            with st.lock:
                for s in SIDES:
                    BQ, BG = show.blend(st.cmd_q[s], show.HOME[s], 0.4, st.cmd_g[s], st.cmd_g[s])
                    st.plan[s] = Plan("rest", np.vstack([st.cmd_q[s][None], BQ]), np.concatenate([[st.cmd_g[s]], BG]),
                                      show.join(), hold=True)
        st.wait(timeout=60)
        time.sleep(0.5)
    except StageError as e:
        print(f"COULD NOT RETURN TO REST: {e}", flush=True)
    finally:
        st.stop.set(); st.thread.join(timeout=2)
        close()
        if lock:
            lock.close()


def bringup(st, track_max):
    """home -> stage at 30 % -> every gesture once at 50 % on one arm at a time -> rest. Stops on any error."""
    report = []

    def step(label, fn, sides=SIDES, secs=None):
        fn()
        with st.lock:
            plans = [st.plan[s] for s in sides]
        if secs:  # a looping gesture never finishes: watch it for one cycle
            time.sleep(secs / st.warp)
            if st.fault:
                raise StageError(st.fault)
        else:
            st.wait(sides)
        worst = max(p.err for p in plans)
        report.append((label, worst))
        print(f"  {label:32s} max tracking error {worst:.3f} rad", flush=True)
        if worst > track_max:
            raise StageError(f"{label}: tracking error {worst:.3f} rad > {track_max}")

    print("bring-up: home, stage at 30 %, each gesture at 50 % one arm at a time, rest", flush=True)
    step("home (settle at rest)", lambda: st.go_rest(speed=0.3))
    step("stage (30 %)", lambda: st.go_stage(speed=0.3))
    for name, g in st.lib.items():
        if name in ("stage", "rest"):
            continue
        for s in SIDES:
            step(f"{s:5s} {name}", lambda: st.play(s, name, 0.5), [s], g.duration / 0.5 + 1 if g.loop else None)
            if g.hold or g.loop or np.abs(st.cmd_q[s] - show.stage_pose(s)).max() > 1e-3:
                step(f"{s:5s} back to stage", lambda: st.go_stage(s, 0.5), [s])
    step("face each other (30 %)", lambda: st.set_stance("each_other", 0.3))
    step("face the audience (30 %)", lambda: st.set_stance("audience", 0.3))
    step("rest (30 %)", lambda: st.go_rest(speed=0.3))
    print(f"bring-up OK: {len(report)} steps, worst tracking error {max(e for _, e in report):.3f} rad", flush=True)


def serve(st, port):
    class H(BaseHTTPRequestHandler):
        def _send(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *a):
            pass

        def do_GET(self):
            if self.path == "/gestures":
                self._send(200, [
                    {"name": g.name, "duration_s": round(g.duration, 2), "arms": g.arms, "about": g.about,
                     "hold": g.hold, "loop": g.loop, "max_speed": round(g.max_speed, 2), "uses_gripper": g.gt is not None,
                     "stances": sorted(g.stances)}
                    for g in st.lib.values()])
            elif self.path == "/duets":
                self._send(200, [{"name": d.name, "duration_s": round(d.duration, 2), "about": d.about,
                                  "max_speed": round(d.max_speed, 2), "closest_contact_cm": d.stats["closest_contact_cm"]}
                                 for d in st.duets.values()])
            elif self.path == "/state":
                self._send(200, st.state())
            else:
                self._send(404, {"ok": False, "error": "unknown path"})

        def do_POST(self):
            try:
                n = int(self.headers.get("Content-Length") or 0)
                b = json.loads(self.rfile.read(n) or b"{}") if n else {}
                arm = b.get("arm")
                routes = {
                    "/play": lambda: st.play(arm, b["gesture"], float(b.get("speed", 1.0)), b.get("mode", "now")),
                    "/freeze": lambda: st.freeze(arm),
                    "/idle": lambda: st.idle(arm),
                    "/stage": lambda: st.go_stage(arm, float(b.get("speed", 1.0))),
                    "/duet": lambda: st.play_duet(b["name"], float(b.get("speed", 1.0))),
                    "/stance": lambda: st.set_stance(b["stance"], float(b.get("speed", 1.0))),
                    "/rest": lambda: st.go_rest(arm, float(b.get("speed", 1.0))),
                    "/grip": lambda: st.grip(arm, float(b["value"])),
                }
                if self.path not in routes:
                    return self._send(404, {"ok": False, "error": "unknown path"})
                self._send(200, routes[self.path]())
            except StageError as e:
                self._send(409, {"ok": False, "error": str(e)})
            except (KeyError, ValueError, TypeError) as e:
                self._send(400, {"ok": False, "error": f"bad request: {e!r}"})

    httpd = ThreadingHTTPServer(("127.0.0.1", port), H)
    httpd.daemon_threads = True
    return httpd


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--real", action="store_true", help="drive the real arms (default: simulation)")
    ap.add_argument("--sim", action="store_true", help="simulation (the default; for clarity)")
    ap.add_argument("--backend", choices=["i2rt", "kin"], default="i2rt", help="simulator (ignored with --real)")
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--bringup", action="store_true", help="test every gesture once, then exit")
    ap.add_argument("--track-max", type=float, default=0.2, help="bring-up stops above this tracking error (rad)")
    ap.add_argument("--warp", type=float, default=1.0, help="kinematic sim only: run N x faster than real time")
    args = ap.parse_args(argv)
    if args.real and args.sim:
        sys.exit("--real or --sim, not both")

    a, lock, backend = open_backend(args.real, args.backend)
    if backend != "kin":
        args.warp = 1.0
    print(f"backend: {backend}", flush=True)
    try:
        st = Stage(a, backend, warp=args.warp)
        q, _ = a.read()
        far = {s: round(float(np.max(np.abs(q[s] - show.HOME[s]))), 3) for s in SIDES}
        if args.real and max(far.values()) > REST_TOL:
            raise SystemExit(f"not at rest (max rad off home {far}): fold the arms or run home.py first")
    except BaseException:
        a.close()
        if lock:
            lock.close()
        raise
    print(f"{len(st.lib)} gestures ok: {', '.join(st.lib)}", flush=True)
    if st.unsafe:
        print(f"{len(st.unsafe)} gesture pairs refused when played together", flush=True)
    st.thread.start()

    def on_signal(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)  # also when started in the background (SIGINT ignored there)
    code = 0
    httpd = None
    try:
        if args.bringup:
            bringup(st, args.track_max)
        else:
            httpd = serve(st, args.port)
            print(f"stage server on http://127.0.0.1:{args.port}  (Ctrl-C: back to rest and off)", flush=True)
            httpd.serve_forever()
    except KeyboardInterrupt:
        print("stopping: back to rest", flush=True)
    except StageError as e:
        print(f"STOPPED: {e}", flush=True); code = 1
    finally:
        if httpd:
            httpd.server_close()
        shutdown(st, a.close, lock)
        print("arms at rest, off" if backend == "real" else "done", flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
