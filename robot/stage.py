"""Stage server: one process owns both arms, plays safety-checked gestures, and serves a local HTTP API.

  stage.py [--real] [--backend i2rt|kin] [--port 8770] [--warp N]     serve 127.0.0.1:8770
  stage.py --bringup [--real] ...                                       one-pass test of every gesture, then exit

Default is simulation: i2rt's MuJoCo sim if the kit is importable (run from ~/physical-ai-hacks), else a pure
kinematic stand-in (--backend kin). --real drives the real arms: takes the station lock, refuses unless both arms are
at rest, and on Ctrl-C / SIGTERM folds both arms back to rest before switching the motors off.
Run on the station: cd ~/physical-ai-hacks && .venv/bin/python ~/dial/stage.py [--real]

API (JSON in and out; "arm" is left | right | both, default both):
  GET  /gestures                       [{"name","duration_s","arms","about","hold","loop","max_speed","uses_gripper",
                                        "stances","sound"}]  sound: {"name": sfx or null, "at": s or [s]} = its hits
  POST /play answers {"ok","started","speed","starts_in"}: starts_in = s until the gesture's t = 0 (the blend)
  GET  /state                          per arm: what it plays, progress, queue, joints (+ measured, grip_measured), tracking error, last error;
                                       plus rejected gestures and gesture pairs refused on the two arms at once
  POST /play {"arm","gesture","speed":1.0,"mode":"now"|"queue"}   now = newest wins (default), queue = after current
  POST /freeze {"arm"}                 hold the current pose at once, drop queues
  POST /idle {"arm"}                   go to stage and breathe; gestures return to idle afterwards
  POST /stage {"arm","speed"}          go to the stage pose and hold
  GET  /duets                          [{"name","kind","props","duration_s","about","max_speed","closest_contact_cm","sound"}]
                                       kind "duet": two characters together; "ensemble": both arms as ONE thing
                                       (wings, a storm, a castle gate). Both play through /duet, from either stance.
  POST /duet {"name","speed"}          a two-arm duet (the arms touch); needs both arms free, and while it plays
                                       everything but /freeze is refused. /freeze stops both arms on the duet's
                                       path; the next request then first finishes the duet at half speed (409)
  POST /talk {"arm","on"}             the arm's gripper flaps like a talking mouth (head bobbing a little) on top
                                       of whatever it plays or idles, until "on": false, /freeze or 20 s
  POST /stance {"stance","speed"}      "audience" (default) or "each_other": both arms turn to the stance pose and
                                       every gesture, idle and /stage play relative to it until the next /stance
  POST /props {"arm","on","speed","kind"}  the arm holds a prop (or not); kind "car"/"ufo" (show.PROP_SHAPES): a toy
                                       that plays only its own gestures (drive, ufo_*). Empty hands are the default: the gripper is the
                                       mouth and each_other turns deep (eye contact). While any arm holds a prop,
                                       everything is checked with both props, each_other turns shallow (the arms turn
                                       there now), /talk is refused on a holding arm, gestures keep its grip, and
                                       duets with "props": false (hand contact) are refused. A held toy plays only
                                       duets whose per-arm "props" ({"left": "ufo", "right": false}) match exactly
  POST /rest {"arm","speed"}           fold back to rest and hold (motors stay on)
  Pick/place gestures (lars.py --bake: pick_lars / place_lars) play through /play on their own arm only, facing the
                                       audience. pick_lars picks Lars up from his dock: the arm then holds him (props
                                       mode, checked with his shape) and plays any gesture with him, while /props, /grip,
                                       /talk and duets leave that arm alone until place_lars puts him back. Nothing else
                                       starts on that arm while either plays; /freeze stops it on its path and the next
                                       request finishes it at half speed first (409). With --dock-check, pick_lars first
                                       looks at the overhead camera and refuses (409) unless Lars lies at his dock
  POST /grip {"arm","value"}           set the gripper 0 closed .. 1 open (close it on a prop)
  POST /trajectory {"arm","t","q","g","speed","name","carry"}  an external joint trajectory on one arm (Dream-1's
                                       action chunk, robot/dream_run.py): absolute joints q (rows of 6, rad) at times t
                                       (s), gripper g (0..1, optional). It starts where the arm is, is retimed so no joint
                                       passes 0.3 rad/s x speed anywhere, and plays only if show.Checker passes it like a
                                       pick (limits, lean-back, 4 cm clearance, 0.3 rad/s and 3 mm within 5 cm of the
                                       table, 15 cm from the other arm). The arm must be free. "carry" (a carried prop's
                                       name: "lars"): a trajectory that ends with the gripper closed holds it from then on,
                                       like pick_lars (gestures keep the grip, /talk refused). While it holds one, a
                                       trajectory keeps the grip, unless it ends open: that puts it down. Answers
                                       {"ok","name","duration_s","peak_rad_s","slowed","props","clamped","tip_mm","tip_end_mm"}; /freeze
                                       stops it dead for good
Every motion is checked before it starts (limits, table clearance with any props, 15 cm from the other arm's committed
motion); a refused request returns 409 with the reason and nothing moves.
"""
import argparse, collections, json, signal, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import mujoco
import numpy as np
import duets, mouth, show

SIDES = show.SIDES
REST_TOL = 0.25      # rad from home that counts as resting (same as lift.py)
FREEZE_S = 0.15      # s to brake to a stop on /freeze
TALK_MAX_S = 20.0    # s: a mouth left talking stops by itself after this
TRACK_STOP = 0.35    # rad: tracking error above this freezes everything (something is blocking an arm)
# POST /trajectory (an external joint trajectory: Dream-1's action chunk). Retimed this slow everywhere, not only near
# the table: lars.py's 0.3 rad/s table cap (show.TABLE_VMAX), and gentle acceleration for a model's jittery rows.
TRAJ_VMAX = show.TABLE_VMAX  # rad/s, any joint, at speed 1
TRAJ_AMAX = 2.0              # rad/s^2, any joint
TRAJ_GRIP_VMAX = 1.0         # gripper (0..1) per s
TRAJ_HOLD_S = 0.2            # s of stillness added at both ends, so it starts and stops at zero speed
TRAJ_MAX_KEYS, TRAJ_MAX_S = 2000, 60.0
TRAJ_RAMP = np.linspace(4.0, 1.0, 8)  # the first and last keys' segments stretched this much: a gentle start and stop
TRAJ_CLOSED = 0.35           # a "carry" trajectory ending with the gripper at or below this holds the prop
TRAJ_OPEN = 0.6              # one ending at or above this, holding a prop, puts it down
TRAJ_TIP_FLOOR = -0.003      # m: the fingertips may brush the table this far in (Dream-1's real grasps: 2.3 and
                             # -0.2 mm in the model, harmless); every other part keeps show.TABLE_FLOOR (3 mm)
TRAJ_CLAMP = 0.05            # rad: a key this little past a joint's safe limit is clamped onto it, not refused (Dream-1
                             # jitters ~0.02 past the j2/j3 fold stops at rest); further past is still refused


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
        self.lead = 0  # ticks from the plan's start to the gesture's own t = 0 (the blend into it)

    @property
    def done(self):
        return self.i >= len(self.Q) - 1


class Stage:
    def __init__(self, arms, backend="kin", gesture_dir=show.GESTURE_DIR, warp=1.0, track_stop=TRACK_STOP, dock_check=None):
        self.arms, self.backend, self.warp, self.track_stop = arms, backend, warp, track_stop
        self.dock_check = dock_check  # callable(gesture) raising StageError when the prop is not at its dock
        self.ck = show.Checker()
        self.lib, self.rejected, self.unsafe = show.load_library(self.ck, gesture_dir)
        self.carried = {g.carry["name"] for g in self.lib.values() if g.carry}  # props picked up by a gesture (Lars)
        if "idle_breathe" not in self.lib:
            raise SystemExit("idle_breathe is missing or rejected: the stage server needs it")
        self.duets, self.duets_rejected = duets.load_duets(duets.DuetChecker(self.ck), verbose=False)
        self.mouth = {s: mouth.Mouth() for s in SIDES}
        self.talk_ticks = {s: 0 for s in SIDES}
        self.dq = {s: np.zeros(6) for s in SIDES}  # the mouth's head offsets, last tick
        self.duet_left = None  # a duet frozen half-way: {"name", "Q": {side: path}, "G": {side: grips}}
        self.table_left = {s: None for s in SIDES}  # a pick/place frozen half-way: {"gesture", "Q", "G"} still to go
        self.stance = "audience"  # the facing; _st() adds the props mode
        self.props = {s: False for s in SIDES}  # what the arm holds: False, True (POST /props), a toy kind, or Lars
        self.lock = threading.RLock()
        self.attach(arms)
        self.queue = {s: collections.deque() for s in SIDES}
        self.after = {s: "hold" for s in SIDES}
        self.last_error = {s: None for s in SIDES}
        self.stop = threading.Event()
        self.fault = None
        self.thread = threading.Thread(target=self._loop, daemon=True)

    def attach(self, arms):
        """Drive these arms from their current pose (main() checks the library first, then opens the real arms)."""
        self.arms = arms
        q, g = arms.read()
        self.cmd_q = {s: np.asarray(q[s], float).copy() for s in SIDES}
        self.cmd_g = {s: float(g[s]) for s in SIDES}
        self.meas = {s: self.cmd_q[s].copy() for s in SIDES}
        self.meas_g = dict(self.cmd_g)  # the gripper as read back (0 closed .. 1 open), mouth flaps included
        self.plan = {s: self._hold_plan(s, "start") for s in SIDES}

    # ---------------------------------------------------------------- planning (call with self.lock held)

    def _hold_plan(self, side, name="hold"):
        q = self.cmd_q[side]
        return Plan(name, q[None].copy(), np.array([self.cmd_g[side]]), self.ck.pose_sweep(side, q), hold=True)

    def _sweep(self, side):
        """What the side's committed plan may still occupy: all of its path while it runs or loops; once a one-shot
        plan has finished, only the pose it holds (Nov 7: a finished /stance move's whole path, each_other included,
        kept pick_lars refused at 13 cm). Whatever the arm starts next (idle, a queued gesture) is checked then."""
        p = self.plan[side]
        return self.ck.pose_sweep(side, p.Q[-1]) if p.done and not p.loop else p.sweep

    def _build(self, side, name, Q, G, gesture=None, speed=1.0, base_sweep=None, loop=False, hold=False):
        """Plan = blend from the current command into (Q, G) then (Q, G). Checks limits, table clearance and lean-back.
        The blend and pose targets (stage, rest) are folds: they may lean back as far as rest or their start pose."""
        q0, g0 = self.cmd_q[side], self.cmd_g[side]
        G = np.full(len(Q), g0) if G is None else G
        for k in range(4):  # a blend down onto the table (a toy's ready pose) slows until it lands softly enough
            BQ, BG = show.blend(q0, Q[0], speed / 2 ** k, g0, G[0])
            probs, bsweep = self.ck.check_samples(side, np.vstack([q0[None], BQ, Q[:1]]), fold=True,
                                                  table=self._table(side, gesture))
            if not any("near the table" in p for p in probs):
                break
        if base_sweep is None:
            p2, base_sweep = self.ck.check_samples(side, Q, fold=gesture is None)
            probs += p2
        else:
            probs += show.motion_problems(Q) + self.ck.lean_problems(side, Q)  # at the requested speed
        if probs:
            raise StageError(f"{side} {name}: " + "; ".join(probs))
        plan = Plan(name, np.vstack([q0[None], BQ, Q]), np.concatenate([[g0], BG, G]),
                    show.join(bsweep, base_sweep), gesture, loop, hold)
        plan.lead = len(BQ) + 1
        return plan

    def _table(self, side, gesture=None):
        """Table allowance of a blend: the gesture it leaves (a dead arm lies on the table) or the one it goes into."""
        prev = self.plan[side].gesture
        return (prev.table if prev is not None else None) or (gesture.table if gesture is not None else None)

    def _st(self):
        """The stance in use: the facing, in the props mode ("each_other_props" while any arm holds a prop)."""
        return show.with_props(self.stance, any(self.props.values()))

    def _gesture_plan(self, side, name, speed):
        g = self.lib.get(name)
        if g is None:
            raise StageError(f"unknown gesture {name!r}" + (f" (rejected: {self.rejected[name]})" if name in self.rejected else ""))
        if g.side:  # a pick/place: its own arm, its own hand, the same absolute path in every stance
            if side != g.side:
                raise StageError(f"{name} is for the {g.side} arm only")
            want = g.carry["name"] if g.carry and g.carry["from"] <= 0 else False
            if self.props[side] != want:
                raise StageError(f"{name} needs the {side} arm " + (f"holding {want}" if want else "with empty hands") +
                                 f", it holds {self.props[side] or 'nothing'}" + (" (POST /props off first)" if self.props[side]
                                                                                  and self.props[side] not in self.carried else ""))
            if self._st() not in g.stances:
                raise StageError(f"{name} is not available in the {self._st()} stance "
                                 f"({self.ck.stance_rejected.get((name, self._st()), '')}): face the audience first")
            speed = float(np.clip(speed, 0.1, g.max_speed))
            Q, G = g.samples(side, speed)
            return self._build(side, name, Q, G, g, speed, self.ck.swept[show.key(g.name, side)], hold=g.hold), speed
        st = self._st() if name not in show.FOLD else show.with_props("audience", any(self.props.values()))  # folds
        # always go via the audience pose
        if st not in g.stances:
            raise StageError(f"{name} is not available in the {st} stance: {self.ck.stance_rejected.get((name, st), '')}")
        held = self.props[side]
        if g.prop and held != g.prop:
            raise StageError(f"{name} needs the {g.prop} in the {side} hand (POST /props with kind {g.prop!r})")
        if held not in (False, True) and held not in self.carried and not g.prop and name not in show.FOLD:
            raise StageError(f"the {side} arm holds the {held}: only its own gestures play (or POST /props off)")
        speed = float(np.clip(speed, 0.1, g.max_speed))
        Q, G = g.samples(side, speed, stance=st)
        if self.props[side]:
            G = None  # holding a prop: keep the grip, never drop it
        # holding Lars: the library's sweeps were made with a stick, so check it all again with him
        base = None if held in self.carried else self.ck.swept[show.key(g.name, side, st)]
        return self._build(side, name, Q, G, g, speed, base, loop=g.loop, hold=g.hold), speed

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
        """Start {side: Plan} at once if each clears the other arm's motion (new or committed) by GAP. A pick/place
        runs to its end: nothing else starts on its arm meanwhile (/freeze still stops it)."""
        for side in plans:
            cur = self.plan[side]
            if cur.gesture is not None and cur.gesture.side and not cur.done:
                raise StageError(f"the {side} arm is busy with {cur.name} at the table ({(len(cur.Q) - 1 - cur.i) / show.HZ:.1f} s left)")
        for side, p in plans.items():
            other = SIDES[1 - SIDES.index(side)]
            osweep = plans[other].sweep if other in plans else self._sweep(other)
            d = show.gap(p.sweep, osweep)
            if d < show.GAP:
                raise StageError(f"{side} {p.name} would come {d*100:.0f} cm from the {other} arm "
                                 f"({self.plan[other].name if other not in plans else plans[other].name}); "
                                 f"needs {show.GAP*100:.0f} cm")
        for side, p in plans.items():
            self.plan[side] = p
            self.last_error[side] = None
            g = p.gesture
            if g is not None and g.carry and not g.d.get("external"):  # pick: holds it from now on; place: empty-
                # handed afterwards (a /trajectory sets its own props: it may carry it all the way)
                held = g.carry["from"] > 0  # a pick starts empty-handed and ends holding it
                self.props[side] = g.carry["name"] if held else False
                self.ck.set_props(self._ck_props(self.props))
                self.mouth[side].on = False
                print(f"{side} arm {'takes' if held else 'puts down'} {g.carry['name']} ({g.name})", flush=True)
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
        names = self._finish_table()
        if names:  # stopped at the table, nothing else would pass the checks from there (nor match the props state)
            raise StageError(f"{', '.join(names)} was frozen half-way: finishing it at half speed first; try again after it")

    def _finish_table(self, rate=0.5):
        """Finish the pick/place frozen half-way on its own (checked) path, eased in, at `rate` of its speed; not
        while the other arm is in the way. Returns the gesture names started."""
        started = []
        for s in SIDES:
            left = self.table_left[s]
            if not left:
                continue
            g, o = left["gesture"], SIDES[1 - SIDES.index(s)]
            sweep = self.ck.swept[show.key(g.name, s)]
            if show.gap(sweep, self._sweep(o)) < show.GAP:
                continue
            self.table_left[s] = None
            n = len(left["Q"])
            r = rate * np.minimum(1.0, np.arange(1, 4 * n) / (0.3 * show.HZ))
            u = np.concatenate([[0.0], np.cumsum(r)])
            u = np.append(u[u < n - 1], n - 1)
            self.plan[s] = Plan(g.name, along(left["Q"], u), np.interp(u, np.arange(n), left["G"]), sweep, g, hold=g.hold)
            started.append(g.name)
        return started

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
            if isinstance(du.props, dict):  # per-arm props (toys): exactly these in hand
                if any(self.props[s] != du.props[s] for s in SIDES):
                    raise StageError(f"duet {name} needs the props {du.props} in hand (now {self.props}); POST /props")
            elif not du.props and any(self.props.values()):
                raise StageError(f"duet {name} needs empty hands; POST /props off first")
            elif any(v not in (False, True) for v in self.props.values()):
                raise StageError(f"duet {name}: a toy is in hand; " + ("put it back first" if set(self.props.values()) & self.carried
                                                                      else "POST /props off first"))
            busy = [s for s in SIDES if not (self.plan[s].done or self.plan[s].loop) or self.queue[s]]
            if busy:
                raise StageError(f"duet {name} needs both arms free; {busy[0]} is playing {self.plan[busy[0]].name}")
            speed = float(np.clip(speed, 0.1, du.max_speed))
            smp = du.samples(speed)
            parts = {}
            for s in SIDES:
                Q, G = smp[s]
                if G is None or self.props[s] not in (False, True):  # a toy in hand: keep the grip, never drop it
                    G = np.full(len(Q), self.cmd_g[s])
                BQ, BG = show.blend(self.cmd_q[s], Q[0], speed, self.cmd_g[s], G[0])
                OQ, OG = show.blend(Q[-1], show.stage_pose(s, self._st()), speed, G[-1], G[-1])
                parts[s] = [(np.vstack([self.cmd_q[s][None], BQ]), np.concatenate([[self.cmd_g[s]], BG])), (Q[1:], G[1:]),
                            (OQ, OG)]
            # The duet itself passed DuetChecker; the blends in and out are checked like any move, both arms at once.
            for k, before in ((0, None), (2, 1)):
                sw = {}
                for s in SIDES:
                    Qk = parts[s][k][0] if before is None else np.vstack([smp[s][0][-1:], parts[s][k][0]])
                    probs, sw[s] = self.ck.check_samples(s, Qk, fold=True, table=self._table(s) if k == 0 else du.table)
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
                self.mouth[s].on = False  # a duet may carry props and touch: the mouths fade out on the way in
                Q = np.vstack([q for q, _ in parts[s]]); G = np.concatenate([g for _, g in parts[s]])
                # its sweep, for whatever the other arm does next, is where it ends: nothing else moves meanwhile
                self.plan[s] = Plan(f"duet:{name}", Q, G, self.ck.pose_sweep(s, Q[-1]),
                                    hold=self.after[s] != "idle", duet=name)
                self.queue[s].clear(); self.last_error[s] = None
            self.fault = None
            # starts_in: when the duet's own t=0 is reached (after the padded blend-in); its "sound" times are t / speed
            return {"ok": True, "duet": name, "speed": round(speed, 2), "duration_s": round(len(Q) / show.HZ, 2),
                    "starts_in": round((len(parts["left"][0][0]) - 1) / show.HZ, 2)}

    def talk(self, arm, on=True):
        with self.lock:
            p = self.plan["left"]
            if on and p.duet and not p.done:
                raise StageError(f"duet {p.duet} is playing: no talking during a duet")
            if on and (held := [s for s in self.sides(arm) if self.props[s]]):
                raise StageError(f"the {held[0]} arm holds a prop: its mouth is full (POST /props off first)")
            for s in self.sides(arm):
                self.mouth[s].on = bool(on)
                self.talk_ticks[s] = int(TALK_MAX_S * show.HZ) if on else 0
            return {"ok": True}

    def play(self, arm, gesture, speed=1.0, mode="now"):
        g = self.lib.get(gesture)
        if self.dock_check and g is not None and g.carry and g.carry["from"] > 0:  # a pick: is it there?
            try:
                self.dock_check(g)
            except StageError as e:
                print(f"refused {gesture}: {e}", flush=True); raise
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
                if self.lib[gesture].table:
                    self.mouth[s].on = False  # a fist on the table: the mouth shuts
            for s in sides:
                self.queue[s].clear()
            # starts_in: seconds until the gesture's own t = 0 (after the blend into it), for sounds on its hits
            return {"ok": True, "started": sides, "speed": round(next(iter(plans.values()))[1], 2),
                    "starts_in": round(max(p.lead for p, _ in plans.values()) / show.HZ, 3)}

    def freeze(self, arm=None):
        with self.lock:
            for s in self.sides(arm):
                self.mouth[s].on = False
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
                if p.gesture is not None and p.gesture.side and not p.done:  # brake on its own path, finish it later
                    k = np.arange(1, int(FREEZE_S * show.HZ) + 1)
                    u = np.minimum(p.i + np.cumsum(1 - k / (len(k) + 1)), len(p.Q) - 1)
                    Q, j = along(p.Q, u), int(np.ceil(u[-1]))
                    G = np.interp(u, np.arange(len(p.G)), p.G)
                    if not p.gesture.d.get("external"):  # an external trajectory (/trajectory) is never resumed
                        self.table_left[s] = {"gesture": p.gesture, "Q": np.vstack([Q[-1:], p.Q[j:]]), "G": np.r_[G[-1], p.G[j:]]}
                    self.plan[s] = Plan("freeze", np.vstack([self.cmd_q[s][None], Q]), np.r_[self.cmd_g[s], G],
                                        self.ck.pose_sweep(s, Q[-1]), hold=True)
                    continue
                v = (p.Q[p.i] - p.Q[max(p.i - 1, 0)]) * show.HZ if not p.done else np.zeros(6)
                t = np.arange(1, int(FREEZE_S * show.HZ) + 1) / show.HZ
                Q = np.clip(self.cmd_q[s] + np.outer(t - t * t / (2 * FREEZE_S), v), show.SAFE_LO, show.SAFE_HI)
                probs, sw = self.ck.check_samples(s, Q, fold=True)
                probs = [x for x in probs if "speed" not in x and "acceleration" not in x]  # braking may decelerate hard
                if probs or show.gap(sw, self._sweep(SIDES[1 - SIDES.index(s)])) < show.GAP:
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
            self._commit({s: self._pose_plan(s, "stage", show.stage_pose(s, self._st()), speed) for s in sides})
            for s in sides:
                self.after[s] = "hold"; self.queue[s].clear()
            return {"ok": True}

    def set_stance(self, stance, speed=1.0):
        """Both arms turn to the stance pose; gestures, idle and /stage then play relative to it."""
        with self.lock:
            self._guard()
            if stance not in show.FACINGS:
                raise StageError(f"stance must be one of {', '.join(show.FACINGS)}, not {stance!r}")
            st = show.with_props(stance, any(self.props.values()))
            plans = {s: self._pose_plan(s, "stance", show.stage_pose(s, st), speed) for s in SIDES}
            for s, p in plans.items():
                p.hold = self.after[s] != "idle"  # an idle arm resumes breathing in the new stance
            self._commit(plans)
            self.stance = stance
            for s in SIDES:
                self.queue[s].clear()
            return {"ok": True, "stance": stance}

    def _ck_props(self, props):
        """What the checker counts in each hand: a toy (car, ufo) where held, else a stick once any prop is out."""
        return {s: props[s] if props[s] else any(props.values()) for s in SIDES}

    def set_props(self, arm, on, speed=1.0, kind=None):
        """The arm holds a prop or not (kind: a show.PROP_SHAPES toy such as "car" or "ufo", which then plays only its
        own gestures; default a stick-like prop). If that changes the stance pose (each_other: deep <-> shallow),
        both arms turn there first, checked with empty hands: the prop goes in after the call and has come out
        before it."""
        if kind not in (None, "stick") and kind not in show.PROP_SHAPES:
            raise StageError(f"unknown prop kind {kind!r}; known: {sorted(show.PROP_SHAPES)}")
        with self.lock:
            self._guard()
            props = dict(self.props)
            sides = [s for s in self.sides(arm) if self.props[s] not in self.carried]
            if not sides:
                raise StageError(f"the {arm} arm holds {self.props[arm]}: put it back with its place gesture first")
            for s in sides:
                props[s] = (True if kind in (None, "stick") else kind) if on else False
            st = show.with_props(self.stance, any(props.values()))
            if np.any(show.STANCES[st] != show.STANCES[self._st()]):
                self.ck.set_props(False)
                try:
                    plans = {s: self._pose_plan(s, "props", show.stage_pose(s, st), speed) for s in SIDES}
                    for s, p in plans.items():
                        p.hold = self.after[s] != "idle"
                    self._commit(plans)
                finally:
                    self.ck.set_props(self._ck_props(self.props))
                for s in SIDES:
                    self.queue[s].clear()
            self.props = props
            self.ck.set_props(self._ck_props(props))
            for s in SIDES:
                if props[s]:
                    self.mouth[s].on = False
                # what each arm is committed to now counts its prop where it ends
                self.plan[s].sweep = show.join(self.plan[s].sweep, self.ck.pose_sweep(s, self.plan[s].Q[-1]))
            return {"ok": True, "props": props, "stance": st}

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
                if self.props[s] in self.carried:
                    raise StageError(f"the {s} arm holds {self.props[s]}: opening would drop it (use its place gesture)")
                if not p.done and not p.loop:
                    raise StageError(f"{s} arm is busy with {p.name}; grip after it")
                BQ, BG = show.blend(self.cmd_q[s], self.cmd_q[s], 1.0, self.cmd_g[s], v)
                Q = np.vstack([self.cmd_q[s][None], BQ])
                plans[s] = Plan("grip", Q, np.concatenate([[self.cmd_g[s]], BG]), self.ck.pose_sweep(s, self.cmd_q[s]), hold=True)
            self._commit(plans)
            return {"ok": True}

    def trajectory(self, arm, t, q, g=None, speed=1.0, name="dream", carry=None):
        """An external joint trajectory on one arm (Dream-1's action chunk, robot/dream_run.py): absolute joints q
        (N, 6) at times t (N,), gripper g (N,) 0..1 or None (keep the grip). It starts from the arm's current command
        (a key at t = 0, held TRAJ_HOLD_S, and held again at the end), each segment stretched to TRAJ_VMAX * speed and
        TRAJ_GRIP_VMAX, then the whole uniformly until the spline stays under those and TRAJ_AMAX. Then it must pass
        show.Checker as a one-arm absolute path with the TABLE_* rule (check_side: limits, speed, acceleration, CLEAR
        above TABLE_NEAR, TABLE_VMAX and TABLE_FLOOR below it), lean back no further than rest or its start (like a
        fold), and clear the other arm's committed motion by GAP (_commit). The arm must be free (holding or idling).
        Like a pick it runs to its end; /freeze brakes it on its path and it is dropped. Joint values up to TRAJ_CLAMP
        past show.SAFE_LO/SAFE_HI are clamped onto them (the reply counts them in "clamped").
        Props, like pick_lars/place_lars: with carry (a carried prop's name), one that closes the gripper from at
        or above TRAJ_OPEN to at or below TRAJ_CLOSED at its end takes the prop (props[arm], checked with it in hand from where the gripper last closes).
        Holding one, the gripper keeps its grip (g ignored) unless g ends at or above TRAJ_OPEN: that puts it down
        (checked with it in hand until the gripper last leaves TRAJ_CLOSED). Any other prop in hand: refused."""
        if arm not in SIDES:
            raise StageError(f"arm must be left or right, not {arm!r}")
        if not (isinstance(name, str) and 0 < len(name) <= 32) or name in self.lib or name in show.FOLD:
            raise StageError(f"name must be 1..32 characters and not a gesture's, not {name!r}")
        t, q = np.asarray(t, float), np.asarray(q, float)
        if t.ndim != 1 or not 1 <= len(t) <= TRAJ_MAX_KEYS or q.shape != (len(t), 6):
            raise StageError(f"t must be 1..{TRAJ_MAX_KEYS} times and q one row of 6 joints (rad) per time; "
                             f"got t {t.shape}, q {q.shape}")
        if not (np.isfinite(t).all() and np.isfinite(q).all()) or t[0] < 0 or np.any(np.diff(t) <= 0):
            raise StageError("t must be finite, from >= 0 and increasing, q finite")
        if g is not None:
            g = np.asarray(g, float)
            if g.shape != t.shape or not np.isfinite(g).all() or g.min() < 0 or g.max() > 1:
                raise StageError("g must be one gripper value in 0..1 per time")
        if carry is not None and carry not in self.carried:
            raise StageError(f"carry must be a carried prop ({sorted(self.carried)}), not {carry!r}")
        started = time.perf_counter()  # the check's CPU runs next to i2rt's motor threads (kb/arm-control.md#motor-dropouts-nov-7)
        lo, hi = show.SAFE_LO, show.SAFE_HI
        clamped = int(np.sum((q < lo) & (q >= lo - TRAJ_CLAMP)) + np.sum((q > hi) & (q <= hi + TRAJ_CLAMP)))
        q = np.where((q < lo) & (q >= lo - TRAJ_CLAMP), lo, np.where((q > hi) & (q <= hi + TRAJ_CLAMP), hi, q))
        speed = float(np.clip(speed, 0.1, 1.0))
        vcap = TRAJ_VMAX * speed
        with self.lock:
            self._guard()
            cur = self.plan[arm]
            if not (cur.done or cur.loop) or self.queue[arm]:
                raise StageError(f"the {arm} arm is busy with {cur.name}; send the trajectory after it")
            held = self.props[arm]
            if held and held not in self.carried:
                raise StageError(f"a trajectory needs the {arm} arm with empty hands, it holds {held} (POST /props off)")
            if held and carry not in (None, held):
                raise StageError(f"the {arm} arm holds {held}, not {carry}")
            q0, g0 = self.cmd_q[arm].copy(), self.cmd_g[arm]
            if held and (g is None or g[-1] < TRAJ_OPEN):
                g = None  # holding a prop: the gripper doesn't move, or it drops it
            Qk = np.vstack([q0, q0, q, q[-1:]])
            Gk = np.r_[g0, g0, g if g is not None else np.full(len(t), g0), g[-1] if g is not None else g0]
            prop = carry or held
            span = None  # the keys the prop is in hand for the checker: (first, last)
            if held:
                closed = np.nonzero(Gk <= TRAJ_CLOSED)[0]
                span = (0, len(Gk) - 1) if g is None else (0, int(closed[-1]) if len(closed) else 0)
            elif carry and Gk[-1] <= TRAJ_CLOSED and Gk.max() >= TRAJ_OPEN:  # it closes here, from open: a grasp
                opened = np.nonzero(Gk > TRAJ_CLOSED)[0]
                span = (int(opened[-1]) + 1, len(Gk) - 1)
            after = (False if g is not None else held) if held else (carry if span else False)
            shape = next((x.carry for x in self.lib.values() if prop and x.carry and x.carry["name"] == prop), None)
            tk = np.r_[0.0, TRAJ_HOLD_S, TRAJ_HOLD_S + t + (1.0 / show.HZ if t[0] <= 0 else 0.0)]
            tk = np.r_[tk, tk[-1] + TRAJ_HOLD_S]
            dt = np.maximum.reduce([np.diff(tk), np.abs(np.diff(Qk, axis=0)).max(1) / vcap,
                                    np.abs(np.diff(Gk)) / TRAJ_GRIP_VMAX])
            m = min(len(TRAJ_RAMP), (len(dt) - 2) // 2)  # ease in and out over several keys, not inside one segment
            if m:
                dt[1:1 + m] *= TRAJ_RAMP[:m]
                dt[-1 - m:-1] *= TRAJ_RAMP[:m][::-1]
            tk = np.r_[0.0, np.cumsum(dt)]
            asked = TRAJ_HOLD_S * 2 + t[-1]
            for _ in range(8):  # pchip scales with time, but its 50 Hz samples only roughly: a few stretches
                d = {"name": name, "side": arm, "near": show.TABLE_NEAR, "external": True,
                     "about": "an external trajectory (POST /trajectory)",
                     "keys": [{"t": float(a), "q": [float(x) for x in r], "g": float(b)} for a, r, b in zip(tk, Qk, Gk)]}
                if span:
                    d["carry"] = {**shape, "from": float(tk[span[0]]), "to": float(tk[span[1]])}
                gest = show.Gesture(d)
                Q, G = gest.samples(arm)
                v, a = show.peak_rates(Q)
                gv = float(np.abs(np.diff(G)).max()) * show.HZ if len(G) > 1 else 0.0
                k = max(v.max() / vcap, float(np.sqrt(a.max() / TRAJ_AMAX)), gv / TRAJ_GRIP_VMAX)
                if k <= 1.0 + 1e-6:
                    break
                tk = tk * k * 1.03
            else:
                raise StageError(f"{arm} {name}: could not retime it under {vcap:.2f} rad/s")
            if gest.duration > TRAJ_MAX_S:
                raise StageError(f"{arm} {name}: {gest.duration:.0f} s once slowed to {vcap:.2f} rad/s "
                                 f"(> {TRAJ_MAX_S:.0f} s): too much motion for one trajectory")
            floor = show.TABLE_FLOOR
            show.TABLE_FLOOR = TRAJ_TIP_FLOOR  # ponytail: module global swapped under self.lock (every check runs under
            try:                               # it); a floor argument on check_side if that ever changes
                probs = [p for p in self.ck.check_side(gest) if "leans back" not in p]
            finally:
                show.TABLE_FLOOR = floor
            tip, body = self._lowest(arm, Q)
            if body < floor:  # the tips alone get TRAJ_TIP_FLOOR: anything else this low is refused
                probs.append(f"{arm}: a part other than the fingertips is {body*1000:.0f} mm above the table "
                             f"(needs {floor*1000:.0f})")
            probs += self.ck.lean_problems(arm, Q, fold=True)  # as far back as rest or where it starts, like a fold
            check_ms = (time.perf_counter() - started) * 1000
            print(f"{arm} {name}: lowest fingertip {tip*1000:.1f} mm, other parts {body*1000:.1f} mm above the table, "
                  f"checked in {check_ms:.0f} ms" + (" (refused)" if probs else ""), flush=True)
            if probs:
                raise StageError(f"{arm} {name}: " + "; ".join(probs))
            plan = Plan(name, Q, G, self.ck.swept[show.key(name, arm)], gest, hold=True)
            self._commit({arm: plan})
            self.mouth[arm].on = False
            self.queue[arm].clear()
            self.after[arm] = "hold"
            if after != held:  # took it or put it down: from now on, as after pick_lars / place_lars
                self.props[arm] = after
                self.ck.set_props(self._ck_props(self.props))
                print(f"{arm} arm {'takes' if after else 'puts down'} {prop} ({name})", flush=True)
            return {"ok": True, "arm": arm, "name": name, "speed": round(speed, 2),
                    "duration_s": round((len(Q) - 1) / show.HZ, 2), "peak_rad_s": round(float(v.max()), 3),
                    "slowed": round(gest.duration / asked, 2), "props": dict(self.props), "clamped": clamped,
                    "tip_mm": round(tip * 1000, 1), "tip_end_mm": round(self._lowest(arm, Q[-1:])[0] * 1000, 1),
                    "check_ms": round(check_ms)}

    def _lowest(self, arm, Q):
        """(fingertips, every other moving part): the lowest point above the arm's table over Q, m."""
        sc, tip, body = self.ck.scene, np.inf, np.inf
        for q in Q:
            sc.set(arm, q); sc.forward()
            for k, pts, r in sc.geoms[arm]:
                z = float(np.min(sc.d.geom_xpos[k][2] + pts @ sc.d.geom_xmat[k].reshape(3, 3)[2] - r)) - show.TABLE_Z[arm]
                if "_tip_" in mujoco.mj_id2name(sc.m, mujoco.mjtObj.mjOBJ_BODY, sc.m.geom_bodyid[k]):
                    tip = min(tip, z)
                else:
                    body = min(body, z)
        return tip, body

    def busy(self, sides=SIDES):
        with self.lock:
            return (any(not self.plan[s].done or self.queue[s] or self.table_left[s] for s in sides)
                    or bool(self.duet_left))

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
            out = {"backend": self.backend, "fault": self.fault, "rejected": self.rejected, "stance": self.stance, "props": self.props,
                   "duet": self.plan["left"].duet if not self.plan["left"].done else None,
                   "duet_frozen": self.duet_left["name"] if self.duet_left else None,
                   "table_frozen": {s: t["gesture"].name for s, t in self.table_left.items() if t}, "rejected_duets": self.duets_rejected,
                   "not_in_stance": {f"{n} ({st})": why for (n, st), why in self.ck.stance_rejected.items()},
                   "unsafe_pairs": sorted([list(p) for p in self.unsafe]), "arms": {}}
            for s in SIDES:
                p = self.plan[s]
                out["arms"][s] = {
                    "playing": None if p.done and not p.loop else p.name, "pose": p.name,
                    "progress": round(p.i / max(len(p.Q) - 1, 1), 2), "after": self.after[s],
                    "queue": [g for g, _ in self.queue[s]], "grip": round(self.cmd_g[s], 2),
                    "joints": np.round(self.cmd_q[s], 3).tolist(), "measured": np.round(self.meas[s], 3).tolist(),
                    "grip_measured": round(self.meas_g[s], 2),
                    "tracking_error": round(p.err, 3), "last_error": self.last_error[s],
                    "talking": self.mouth[s].on}
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
            again = p.gesture.name if p.loop and p.gesture is not None else "idle_breathe"  # a loop repeats
            try:
                self._commit({s: self._gesture_plan(s, again, 1.0)[0]})
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
                    if self.talk_ticks[s] > 0:
                        self.talk_ticks[s] -= 1
                        if not self.talk_ticks[s]:
                            self.mouth[s].on = False
                out_q, out_g = {}, {}
                for s in SIDES:  # the mouth overlay: small head offsets (<= 0.06 rad) and the gripper
                    out_g[s], self.dq[s] = self.mouth[s].step(self.cmd_g[s])
                    if self.plan[s].gesture is not None and self.plan[s].gesture.table:
                        self.dq[s] = np.zeros(6)  # touching the table: no head bob on top
                    out_q[s] = self.cmd_q[s] + self.dq[s]
                try:
                    self.arms.command(out_q, out_g)
                    q, g = self.arms.read()
                except Exception as e:
                    self.fault = f"arm I/O failed: {e}"; print(self.fault, flush=True); self.stop.set(); break
                dead = dead_motors(self.arms)
                if dead:
                    self.fault = f"{'+'.join(dead)} arm motors stopped answering (e-stop, power or cable?): restart needed"
                    print(self.fault, flush=True); self.stop.set(); break
                for s in SIDES:
                    self.meas[s] = np.asarray(q[s], float)
                    self.meas_g[s] = float(g[s])
                    p = self.plan[s]
                    err = float(np.max(np.abs(self.meas[s] - p.Q[max(p.i - 1, 0)] - self.dq[s])))  # vs last tick's
                    p.err = max(p.err, err)
                    if err > self.track_stop and not self.fault:
                        self.fault = f"{s} arm tracking error {err:.2f} rad (blocked?): froze both arms"
                        print(self.fault, flush=True)
                        threading.Thread(target=self.freeze, daemon=True).start()
            nxt += period
            time.sleep(max(0.0, nxt - time.monotonic()))
            if time.monotonic() - nxt > 0.2:
                nxt = time.monotonic()  # fell behind (debugger, load): don't race to catch up


def dead_motors(a):
    """Sides whose i2rt motor chain has stopped (motor lost: e-stop, power, cable). read() keeps returning the last
    pose after that, so without this the server looks healthy while the arm is not listening."""
    # ponytail: reads i2rt's private YamArms._robots; sim robots have no motor_chain and count as alive
    return [s for s, r in getattr(a, "_robots", {}).items()
            if getattr(getattr(r, "motor_chain", None), "running", True) is False]


def shutdown(st, close, lock):
    """Fold both arms to rest, wait, switch off. A second Ctrl-C is ignored: switching off mid-air drops the arms."""
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: print("still returning to rest, please wait", flush=True))
    try:
        if not st.thread.is_alive():
            raise StageError("control loop is not running: cannot return to rest; fold the arms by hand")
        st.fault, st.track_stop = None, float("inf")  # like home.py: keep driving to rest, never freeze mid-air
        with st.lock:
            for s in SIDES:
                st.mouth[s].on, st.talk_ticks[s] = False, 0
        t0 = time.monotonic()
        while st.thread.is_alive() and time.monotonic() - t0 < 30:  # a duet ends on its own path first: the arms
            with st.lock:                                             # may be touching, so no straight homing
                if st.duet_left:
                    st._finish_duet()
                st._finish_table()  # a pick/place frozen at the table ends on its own path too
                if ((not st.plan["left"].duet or st.plan["left"].done) and not any(st.table_left.values())
                        and not any(st.plan[s].gesture is not None and st.plan[s].gesture.side and not st.plan[s].done
                                    for s in SIDES)):
                    break
            time.sleep(0.05)
        for s in SIDES:  # a carried prop goes back to its dock first (its place gesture), if that is still possible
            if st.props[s] in st.carried:
                name = next((n for n, g in st.lib.items() if g.side == s and g.carry and g.carry["from"] <= 0
                             and g.carry["name"] == st.props[s]), None)
                try:
                    if name:
                        st.play(s, name, 0.5); st.wait([s], timeout=60)
                except StageError as e:
                    print(f"could not put {st.props[s]} back ({e})", flush=True)
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
        if name in ("stage", "rest") or g.side:  # pick/place need the prop at its dock: not here
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
                     "stances": sorted(g.stances), "side": g.side, "carry": g.carry["name"] if g.carry else None,
                     "sound": g.sound}
                    for g in st.lib.values()])
            elif self.path == "/duets":
                self._send(200, [{"name": d.name, "kind": d.kind, "props": d.props, "duration_s": round(d.duration, 2), "about": d.about,
                                  "max_speed": round(d.max_speed, 2), "closest_contact_cm": d.stats["closest_contact_cm"], "sound": d.sound}
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
                    "/talk": lambda: st.talk(arm, bool(b.get("on", True))),
                    "/stance": lambda: st.set_stance(b["stance"], float(b.get("speed", 1.0))),
                    "/props": lambda: st.set_props(arm, bool(b.get("on", True)), float(b.get("speed", 1.0)), b.get("kind")),
                    "/rest": lambda: st.go_rest(arm, float(b.get("speed", 1.0))),
                    "/grip": lambda: st.grip(arm, float(b["value"])),
                    "/trajectory": lambda: st.trajectory(arm, b["t"], b["q"], b.get("g"), float(b.get("speed", 1.0)),
                                                         str(b.get("name") or "dream"), b.get("carry") or None),
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
    ap.add_argument("--dock-check", action="store_true",
                    help="pick gestures first check the overhead camera that the prop lies at its dock (adds ~1.5 s)")
    args = ap.parse_args(argv)
    if args.real and args.sim:
        sys.exit("--real or --sim, not both")

    if args.real:
        args.warp = 1.0
        # i2rt's motor threads give up the GIL on every CAN send and receive; a check running on another thread
        # (/trajectory, /play) then makes them wait up to the switch interval (5 ms) each time to get it back. A
        # motor that hears nothing for its CAN timeout (~400 ms) faults with "loss communication". 1 ms cuts that wait.
        sys.setswitchinterval(0.001)
        # The library check takes ~30 s of CPU. With the real arms already open it starves i2rt's motor thread,
        # motors miss replies and drop out ("loss communication", a different motor each start). Check first.
        a, lock, backend = KinArms(), None, "real"
    else:
        a, lock, backend = open_backend(False, args.backend)
        if backend != "kin":
            args.warp = 1.0
    print(f"backend: {backend}", flush=True)
    try:
        dock_check = None
        if args.dock_check:
            import lars

            def dock_check(g):
                why = lars.dock_problem(g.d.get("dock"))
                if why:
                    raise StageError(f"{g.name}: {why}")
        st = Stage(a, backend, warp=args.warp, dock_check=dock_check)
        if args.real:
            print("library checked; opening the arms", flush=True)
            a, lock, _ = open_backend(True, args.backend)
            st.attach(a)
        time.sleep(1.0)  # a motor that doesn't answer drops its chain within ~0.5 s of start
        if dead_motors(a):
            raise SystemExit(f"{'+'.join(dead_motors(a))} arm motors not answering: check the e-stop, power and cables")
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
