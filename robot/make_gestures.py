"""Writes the starter gesture library to gestures/*.json, procedurally (no recording).

Every gesture is joint deltas for the LEFT arm relative to its stage pose (show.STAGE_LEFT); the right arm plays the
mirror image. "Inward" (toward the other arm) is j1 < 0 and j5 > 0 for the left arm. Poses are joined by quintic
moves stretched to stay under ~90 % of the speed/acceleration limits; oscillations are sines chosen within them.
Run: python robot/make_gestures.py  (then check_gestures.py and preview.py)
"""
import json
import numpy as np
import show

J = {f"j{i+1}": i for i in range(6)}
GRIP_STEP = 0.7 * show.GRIP_VMAX * 0.04  # per key: the player's cubic overshoots a straight ramp a little
DT = 0.04  # key spacing (25 Hz, fine enough for the 3 Hz tremble); the player runs a monotone cubic through the keys


def pose(p=None, **k):
    """Joint deltas; p = head pitch (rad, + up) relative to stage, which sets j4 to cancel the pitch j2/j3 add
    (j2 + pitches the head down, j3 + and j4 + pitch it up)."""
    q = np.zeros(6)
    for n, v in k.items():
        q[J[n]] = v
    if p is not None:
        q[3] = q[1] - q[2] + p
    return q


quintic, move_time = show.quintic, show.move_time


def cruise(x, r=0.2):
    """Time law at a steady speed (1/(1-r), vs the quintic's 1.875 peak) with smooth (smoothstep-accel) ramps over the
    first and last r of the time: for long paths whose sharp bends must not come at top speed."""
    x = np.clip(np.asarray(x, float), 0, 1); v = 1 / (1 - r)
    ramp = lambda u: v * r * (u ** 3 - u ** 4 / 2)  # distance covered over a ramp, u = time / r in [0, 1]
    return np.where(x < r, ramp(x / r), np.where(x > 1 - r, 1 - ramp((1 - x) / r), v * r / 2 + v * (x - r)))


class Seq:
    """Builds a gesture as a function of time: a chain of pose moves, holds and added oscillations."""

    def __init__(self, start=None):
        self.segs, self.t, self.q = [], 0.0, np.zeros(6) if start is None else start
        self.osc, self.grip, self.hits = [], [], []

    def to(self, q, want=0.6, hold=0.0, ease=quintic):
        T = move_time(self.q, q, want)
        a, b = self.q.copy(), np.asarray(q, float).copy()
        self.segs.append((self.t, T, lambda x: a + ease(x) * (b - a)))
        self.t += T; self.q = b
        return self.wait(hold) if hold else self

    def path(self, qs, T, law=quintic, pts=None):
        """Through the joint waypoints qs (from the current pose, e.g. IK along a line) in T s, one time law along
        their arc length (joint space, or mostly Cartesian along pts (one per waypoint, the first one = where it
        starts from)): a smooth glide, no stop at each waypoint."""
        qs = np.vstack([self.q, qs])
        d = qs if pts is None else np.hstack([pts, 0.06 * qs])  # 6 cm of path per rad: turns in place take time too
        s = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(d, axis=0), axis=1))]); s /= s[-1]
        self.segs.append((self.t, T, lambda x: show.pchip(s, qs, [law(x)])[0]))  # C1 through the waypoints
        self.t += T; self.q = qs[-1].copy()
        return self

    def fall(self, q, T):
        """A limp drop to q in T s: speeds up like falling, then a short hard stop (keep T >= ~0.6 s per rad)."""
        return self.to(q, T, ease=lambda x: (lambda x: x**3 * (4 - 3 * x))(np.clip(x, 0, 1)))

    def kick(self, q, T):
        """A sharp start that coasts to q in T s (a recoil, a flick): the mirror image of fall()."""
        return self.to(q, T, ease=lambda x: 1 - (lambda u: u**3 * (4 - 3 * u))(1 - np.clip(x, 0, 1)))

    def drop(self, q, ease, T):
        """To q in exactly T s along a given time law (e.g. collapse_law's), not stretched."""
        a, b = self.q.copy(), np.asarray(q, float).copy()
        self.segs.append((self.t, T, lambda x: a + ease(x) * (b - a)))
        self.t += T; self.q = b
        return self

    def hit(self, frac=1.0):
        """Marks a hit (impact keyframe, for the gesture's sound): frac of the way through the last move."""
        t0, T, _ = self.segs[-1]
        self.hits.append(round(t0 + frac * T, 3))
        return self

    def wait(self, s):
        return self.to(self.q, s)

    def shake(self, t0, t1, amp, hz, ramp=None, phase=0.0):
        """Adds amp (6,) * sin(2 pi hz t) over [t0, t1], faded in/out over `ramp` s (default one period, 0 = none).
        Amplitudes are capped so the shake alone uses at most ~60 % of the speed and acceleration limits."""
        w = 2 * np.pi * hz
        cap = 0.6 * np.minimum(show.VMAX / w, show.AMAX / w**2)
        self.osc.append((t0, t1, np.clip(np.asarray(amp, float), -cap, cap), hz, 1 / hz if ramp is None else ramp, phase))
        return self

    def circle(self, t0, t1, a, b, hz, ramp=None):
        """a * sin + b * cos: a joint-space circle (stirring) over [t0, t1]."""
        return self.shake(t0, t1, a, hz, ramp).shake(t0, t1, b, hz, ramp, phase=np.pi / 2)

    def grips(self, *tg):
        self.grip += list(tg)
        return self

    def at(self, t):
        q = self.q.copy()
        for t0, T, f in self.segs:
            if t0 <= t <= t0 + T:
                q = f((t - t0) / T); break
        for t0, t1, amp, hz, ramp, ph in self.osc:
            if t0 <= t <= t1:
                env = min(1, (t - t0) / ramp, (t1 - t) / ramp) if ramp else 1
                q = q + amp * np.sin(2 * np.pi * hz * (t - t0) + ph) * quintic(env)
        return q

    def gesture(self, name, about, arms="one", hold=False, loop=False, table=None, sound=False):
        n = int(round(self.t / DT))
        ts = np.linspace(0, self.t, n + 1)
        keys = [{"t": round(float(t), 3), "q": [round(float(x), 4) for x in self.at(t)]} for t in ts]
        if self.grip:
            gt = np.array([a for a, _ in self.grip]); gv = np.array([b for _, b in self.grip])
            g = np.interp([k["t"] for k in keys], gt, gv)
            for i in range(1, len(g)):  # the gripper's own speed limit, so the gesture plays at full speed
                g[i] = g[i - 1] + np.clip(g[i] - g[i - 1], -GRIP_STEP, GRIP_STEP)
            for k, v in zip(keys, g):
                k["g"] = round(float(v), 3)
        out = {"name": name, "about": about, "arms": arms, "hold": hold, "loop": loop, "keys": keys}
        if table:
            out["table"] = table  # the hand may touch the table (show.TABLE_PARTS), empty hands only
        if sound is not False:  # the agent plays it on the hits (sfx name, or None: hit times for an explicit [sfx])
            hits = self.hits
            assert hits, f"{name}: a sound needs hit marks (Seq.hit)"
            hits = sorted(round(float(h), 3) for h in hits)
            out["sound"] = {"name": sound, "at": hits[0] if len(hits) == 1 else hits}
        return out


def library():
    S = show.STAGE_LEFT
    rest = -S  # nominal rest (all zeros); the stage server uses each arm's real home
    out = []
    add = lambda s, *a, **k: out.append(s.gesture(*a, **k))

    add(Seq(rest).to(np.zeros(6), 3.0), "stage", "Rise from rest into the stage pose.", arms="both", hold=True)
    add(Seq().to(rest, 3.0), "rest", "Fold back down to rest.", arms="both", hold=True)

    s = Seq().wait(4.0)
    s.shake(0, 4.0, pose(j2=0.03, j3=-0.05, j4=0.05, j6=0.04), 0.25, ramp=0)
    add(s, "idle_breathe", "Slow breathing sway; loops while idle.", arms="both", loop=True)

    # Don't lean back (show.COM_BACK_MARGIN): the arm's COM x moves ~+24 cm/rad with j2, -10 with j3, -1 with j4 at
    # stage, so "up" pairs j2+ with j3+ (~1:2.4 keeps the COM in place) instead of j2- (which tips the table).
    up = pose(j2=0.1, j3=0.3, p=0.35)
    s = Seq().to(up, 0.7)
    s.shake(s.t, s.t + 2 / 0.6, pose(j1=0.1, j5=0.25, j6=0.25), 0.6).wait(2 / 0.6).to(np.zeros(6), 0.7)
    add(s, "wave", "Raise the head and wave it side to side, three times.")

    add(Seq().to(pose(j2=0.3, j3=-0.1, p=-0.7), 1.1, hold=0.7).to(np.zeros(6), 1.1),
        "bow", "Lean toward the audience, head down, hold, straighten.", arms="both")

    s = Seq()
    for _ in range(2):
        s.to(pose(j4=-0.3), 0.45).to(pose(j4=0.05), 0.45)
    add(s.to(np.zeros(6), 0.3), "nod", "Nod twice (yes).")

    s = Seq().wait(0.2)
    s.shake(0.2, 0.2 + 2 / 0.8, pose(j5=0.2, j6=0.05), 0.8).wait(2 / 0.8)
    add(s.wait(0.2), "shake_head", "Shake the head side to side (no).")

    add(Seq().to(pose(j1=0.3, j2=0.12, j3=0.3, p=0.9, j6=0.4), 0.8, hold=0.15)
        .to(pose(j1=-0.12, j2=0.3, j3=-0.15, p=-0.45, j6=-0.3), 0.5).hit(0.5).wait(0.35).to(np.zeros(6), 1.0),
        "slash", "Wind up high and outside, chop down and across toward the front.", sound="sword_swing")

    add(Seq().to(pose(j2=0.05, j3=0.2, p=0.1), 0.6, hold=0.15)
        .to(pose(j1=-0.06, j2=0.4, j3=-0.1, p=0.0), 0.5, hold=0.4).to(np.zeros(6), 0.9),
        "thrust", "Raise the prop, then stab straight forward.")

    add(Seq().to(pose(j1=-0.1, j2=0.15, j3=0.25, p=1.1, j6=0.8), 0.6, hold=1.2).to(np.zeros(6), 0.8),
        "block", "Raise the prop upright in front like a guard, hold.")

    hi = pose(j2=0.22, j3=0.5, p=1.0)
    s = Seq().to(hi, 0.7)
    s.shake(s.t, s.t + 2.4, pose(j2=0.03, j3=0.07, j4=-0.04, j6=0.3), 0.75).wait(2.4).to(np.zeros(6), 0.8)
    add(s, "celebrate", "Thrust the prop up high and wiggle it in triumph.", arms="both")

    low = pose(j1=0.3, j2=-0.15, j3=-0.37, p=-0.5, j5=-0.4)  # down (COM in place), head away
    s = Seq().to(low, 0.8)
    s.shake(s.t, s.t + 2.0, pose(j5=0.03, j6=0.03), 3.0).wait(2.0).to(np.zeros(6), 1.0)
    add(s, "cower", "Shrink down, turn the head away from the other arm and down, trembling.")

    # Death (round 3b rework): the power cuts. A short gasp, then every joint gives at once along one straight joint
    # line, as fast as the accel limits allow (no easing, no arc); only the last 5 cm slow to the table impact rule,
    # a small rebound, then dead still: the head flat on the table, the jaw hanging slack. Empty hands only.
    dead = pose(j1=0.35, j2=0.2, j3=-0.48, p=-0.9, j5=-0.2, j6=0.7)
    gasp = pose(j2=-0.03, j3=0.12, p=0.35, j6=-0.1)
    s = Seq().to(gasp, 0.35, hold=0.15).grips((0, 0.05), (0.3, 0.75))
    s.drop(dead, *collapse_law(gasp, dead, vz=0.2)).hit()
    s.to(dead + pose(j3=0.05, p=0.03), 0.16).to(dead, 0.2).wait(1.0)  # the rebound, then nothing
    s.grips((s.t - 1.45, 0.75), (s.t - 1.2, 0.35))
    add(s, "die_dramatically", "The power cuts: a short gasp, then the whole arm collapses at once onto the table, "
        "a small bounce, and lies dead still with the head on the table, mouth slack (play revive or stage after).",
        hold=True, table=dict(min=0.005, impact=0.3), sound="body_fall")

    s = Seq(dead).wait(0.6).shake(0.6, 0.9, pose(j4=0.05, j6=0.06), 5.0).wait(0.3)  # a twitch
    s.wait(0.7).shake(1.6, 1.9, pose(j4=0.05, j5=0.05), 5.0).wait(0.3)  # another
    groggy = pose(j1=0.2, j2=0.08, j3=-0.15, p=-0.35, j5=-0.1, j6=0.3)
    s.to(groggy, 1.6)  # the head comes up slowly, heavy
    t0 = s.t
    s.to(groggy + pose(j1=-0.05, j3=0.05), 1.2).shake(t0, s.t, pose(j5=0.2, j6=0.2), 2.5, ramp=0.3)  # shake it off
    s.to(np.zeros(6), 0.8)
    s.grips((0, 0.35), (2.2, 0.35), (2.6, 0.05), (t0, 0.05), (t0 + 0.3, 0.6), (t0 + 0.6, 0.05))
    add(s, "revive", "Back from the dead: a twitch, another, the head lifts groggily, shakes it off and stands "
        "ready again (after die_dramatically).", table=dict(min=0.005, impact=0.05))

    # Bang the table (Nov 7, round 3): a closed fist (the gripper) knocks the table, aiming show.BANG_DEPTH into it at
    # <= 30 cm/s from a 6-8 cm wind-up; only the gripper touches, the links keep 4 cm. Empty hands only.
    # hand h cm above the table; the wind-up mostly cocks the wrist (j4 may accelerate twice as hard as the elbow)
    fist = lambda h: pose(j2=0.3, **{-0.4: dict(j3=-0.381, p=-1.25), 6: dict(j3=-0.3, p=-0.909), 8: dict(j3=-0.3, p=-0.798)}[h])
    s = Seq().to(fist(8) + pose(j1=0.04, p=0.1, j6=-0.15), 0.7).grips((0, 0.05), (0.4, 0.0))
    for h, rise in ((8, 8),):  # one bang, the snarl, then the big one
        s.fall(fist(-0.4), 0.5 if h == 6 else 0.58).hit().to(fist(rise) + pose(j1=0.03 if rise == 6 else 0.05, p=0.12), 0.32)
    t0 = s.t
    s.to(fist(8) + pose(j2=-0.03, p=0.3), 0.5).shake(t0, t0 + 0.9, pose(j5=0.12, j6=0.12), 3.0, ramp=0.3)
    s.wait(0.15).grips((t0, 0.0), (t0 + 0.28, 0.75), (t0 + 0.45, 0.75), (t0 + 0.75, 0.0))  # a snarl
    s.to(fist(8) + pose(p=0.15), 0.3).fall(fist(-0.4), 0.58).hit().wait(0.2).to(np.zeros(6), 0.75)
    add(s, "bang_table", "Make a fist and bang it on the table, snarl, and bang once more, hard: anger, "
        "impatience, demanding, a judge's verdict.", table=dict(min=-show.BANG_DEPTH, impact=0.3), sound="bonk")

    # Stirring (round 3): a closed gripper points down like a spoon in a pot, ~24 cm over the table; j1 sways the
    # circle sideways, j2+j3 move it to and fro at the same height, j6 swirls the spoon; the eyes (the wrist camera)
    # watch the pot.
    spoon = pose(j2=0.3, j3=0.61, p=-1.2)  # ~24 cm up: a held spoon (prop) clears the table too
    side, fro = pose(j1=0.14, j6=0.12), pose(j2=0.07, j3=0.09, p=-0.03)
    # Both loop (the stage repeats them until that arm's next gesture): they start and end at the pot, the stage's
    # blend brings the spoon there; every cycle is whole periods that ease in and out, so a repeat joins smoothly.
    T = 2 / 0.45
    s = Seq(spoon).wait(T).circle(0, T, side, fro, 0.45, ramp=1.0).shake(0, T, pose(j5=0.06), 0.225, ramp=1.0)
    add(s.grips((0, 0.0)), "stir_lazy", "Hold a spoon down in a pot and stir slow, lazy circles, on and on until the "
        "next move: cooking, brewing a potion, mixing, waiting.", loop=True)
    T = 4 / 1.1
    s = Seq(spoon + pose(j2=0.03)).wait(T).circle(0, T, pose(j1=0.09, j6=0.08), pose(j2=0.06, j3=0.08), 1.1, ramp=0.6)
    s.shake(0, T, pose(j5=0.04, j6=0.03), 2.2, ramp=0.6)
    add(s.grips((0, 0.0)), "stir_frantic", "Stir a pot fast and frantically, head bobbing, on and on until the next "
        "move: whisking, cooking in a hurry, mixing a potion before it boils over.", loop=True)

    # Italian hands (round 3): the closed gripper = pinched fingertips pointing up, pumped from the elbow and wrist,
    # with a body sway and a head tilt; plays well with the talking mouth right after.
    pinch = pose(j2=0.1, j3=0.35, p=1.2)
    # round 3b: the pinch pumps toward and away from the body along the arm's reach (j2:j3 ~2:1 keeps its height,
    # the tip stays up), each push stopping dead on the accent; the head rolls with it, never sideways.
    up = pose(j2=0.1, j3=0.35, p=1.0)  # the pinch, a touch less upright than `pinch`: quicker to raise
    reach = lambda k: up + pose(j2=0.11 * k, j3=0.056 * k)  # k = 1: ~4 cm out
    s = Seq().to(up + pose(j6=0.12), 0.5).grips((0, 0.05), (0.3, 0.0))
    for i, k in enumerate((0.9, 0.9, 0.75, 1.1)):
        s.fall(reach(k) + pose(j6=0.12 * (-1) ** i), 0.42 if k < 1 else 0.5).hit().wait(0.08)
        s.to(up + pose(j3=-0.03, j6=0.06 * (-1) ** i), 0.3)
    s.wait(0.1).to(np.zeros(6), 0.7).grips((s.t - 0.7, 0.0), (s.t, 0.05))
    add(s, "italian", "The pinched-fingers hand: fingertips together pointing up, jabbing hard toward the listener "
        "and back, again and again: \"what do you want?!\", arguing, insisting, explaining, complaining.")

    s = Seq().to(pinch + pose(j2=0.04, j3=0.08, p=0.1), 0.5).grips((0, 0.05), (0.3, 0.0))
    t0 = s.t
    s.wait(1.43).shake(t0, s.t, pose(j2=0.035, j3=0.085, p=0.05), 1.4, ramp=0.5).shake(t0, s.t, pose(j1=0.06, j6=0.1), 0.8, ramp=0.3)
    s.to(pose(j1=0.15, j2=-0.02, j3=0.25, p=0.5, j5=-0.2, j6=0.3), 0.8).grips((s.t - 0.8, 0.0), (s.t - 0.3, 1.0))  # hands up
    s.wait(0.2).to(pose(j2=0.0, j3=-0.1, p=-0.3), 0.7).wait(0.1).to(np.zeros(6), 0.6).grips((s.t - 1.0, 1.0), (s.t, 0.05))
    add(s, "italian_exasperated", "Both hands pinched and pumping big, then thrown open to the sky and dropped: "
        "\"mamma mia!\", exasperated, fed up, giving up.", arms="both")

    # round 3b: bigger, slower setups, then one crisp pop (the gripper springs open as fast as it can, ~0.3 s).
    s = Seq().to(pose(j1=-0.04, j2=0.12, j3=0.36, p=-0.25, j6=0.15), 0.9).grips((0, 0.05), (0.4, 0.0))
    t0 = s.t  # the pinch drawn to the lips, the head bowed over it: savour it...
    s.wait(0.6).shake(t0, s.t, pose(j4=0.04, j6=0.04), 1.7, ramp=0.25)
    s.to(pose(j1=0.1, j2=0.2, j3=0.42, p=0.45, j5=-0.1, j6=0.45), 0.5).hit(0.6)  # ... mwah! thrown up and out
    s.grips((s.t - 0.4, 0.0), (s.t - 0.04, 1.0)).wait(0.7).to(np.zeros(6), 0.8).grips((s.t - 0.8, 1.0), (s.t, 0.05))
    add(s, "chef_kiss", "The chef's kiss: the pinched fingertips drawn slowly to the lips, savoured, then thrown up "
        "and out, bursting open: perfect!, delicious!, magnificent, a masterpiece.", sound="kiss")

    haughty = pose(j1=0.1, j2=0.05, j3=0.15, p=0.3, j5=-0.35, j6=-0.15)  # nose in the air, the head turned aside
    s = Seq().to(haughty, 0.9, hold=0.4).grips((0, 0.05), (0.4, 0.0))
    s.kick(haughty + pose(j1=0.15, j3=0.05, j4=0.25, j5=-0.4, j6=0.5), 0.38).hit(0.3)  # flick: out and away
    s.grips((s.t - 0.42, 0.0), (s.t - 0.06, 1.0)).wait(0.7).to(np.zeros(6), 0.9).grips((s.t - 0.9, 1.0), (s.t, 0.05))
    add(s, "flick", "A dismissive flick: nose in the air, the head turned away, then the hand flicks out and away, "
        "fingers springing open: bah!, whatever, go away, never mind.", sound="whoosh")

    # ---- face acting (round 3, step B): eyes (the wrist camera) lead, anticipation, the action, a held beat; the
    # gripper mouth plays the emotion. CLOSED/OPEN are mouth.py's shut and wide values.
    C, O = 0.05, 0.8
    flap = lambda t0, t1, hz, lo=C, hi=0.6: [(t0 + i / (2 * hz), hi if i % 2 else lo) for i in range(int((t1 - t0) * 2 * hz) + 1)]

    aim = pose(j1=-0.04, j2=0.24, j3=-0.02, p=0.0)  # the arm reaching out level, the head sighting along it
    s = Seq().to(aim, 0.9, hold=0.7).grips((0, C))  # a beat of stillness
    s.kick(aim + pose(j2=-0.05, j4=0.22, j6=0.08), 0.37).hit(0.0)  # BANG: the recoil kicks the barrel up
    s.wait(0.5).to(np.zeros(6), 0.9)  # the barrel left raised, smoking, then lowered
    t = s.hits[0]  # the muzzle flash: the mouth snaps open on the shot
    s.grips((t, C), (t + 0.2, 0.6), (t + 0.4, 0.6), (t + 0.62, C))
    add(s, "shoot", "Aim the gripper like a gun barrel, the arm out straight, sight along it, hold still, fire: the "
        "recoil kicks the barrel up: shoot, fire, blast, zap, a laser.", sound="gunshot")

    s = Seq().to(pose(j1=0.25, j2=0.03, j3=0.1, p=0.05, j5=-0.4), 0.9, hold=0.4)
    s.to(pose(j1=-0.08, j2=0.03, j3=0.1, p=0.05, j5=0.4), 1.3, hold=0.4).to(np.zeros(6), 0.8)
    add(s, "look_around", "Scan slowly to one side, then the other, searching: look around, search, where is it?, "
        "keep watch.")

    s = Seq().to(pose(j1=-0.05, j5=0.3, p=0.0), 0.5, hold=0.2).to(pose(j1=0.1, j5=-0.2), 0.5, hold=0.35)
    s.to(pose(j1=-0.05, j2=-0.02, j3=0.2, p=0.35, j5=0.35, j6=0.3), 0.3).hit(0.5)
    t, T, _ = s.segs[-1]  # the jaw drops with the snap back, not before it
    s.wait(0.8).to(np.zeros(6), 0.8)
    s.grips((0, C), (t, C), (t + T, O), (t + T + 0.8, O), (t + T + 1.35, C))
    add(s, "double_take", "Glance at something, look away, then snap back, head jerking up and tilted, jaw dropped: "
        "a double take, spot, notice, wait, what?!", sound="record_scratch")

    s = Seq().to(pose(j2=0.1, j3=0.45, p=0.6, j6=0.2), 0.3, hold=0.2)
    s.shake(0.3, 1.5, pose(j4=0.04, j6=0.04), 4.0, ramp=0.2).wait(1.0).to(np.zeros(6), 1.0)
    s.grips((0, C), (0.3, O), (1.4, O), (2.0, C))
    add(s, "startle", "Jump up and back in fright, mouth wide, trembling, then calm down: a scare, startle, jump "
        "back, gasp, eek!")

    crouch2 = pose(j2=-0.06, j3=-0.25, p=-0.15)
    s = Seq().to(crouch2 + pose(j1=0.05), 0.8, hold=0.2)
    s.shake(0.8, 1.47, pose(j4=0.02, j6=0.02), 3.0, ramp=0.25).wait(0.47)  # the wiggle before the leap
    s.to(pose(j1=-0.08, j2=0.4, j3=0.0, p=-0.2), 0.45, hold=0.4).to(np.zeros(6), 0.9)
    s.grips((0, C), (1.3, C), (1.6, O), (1.75, O), (1.95, C))
    add(s, "pounce", "Crouch low, wiggle, then leap forward with the mouth open and snap it shut on the prey: "
        "pounce, leap out, lunge, attack, catch.")

    s = Seq().to(pose(j2=-0.1, j3=-0.38, p=-0.45, j6=0.1), 0.35, hold=0.6)
    s.to(pose(j2=-0.06, j3=-0.25, p=0.15, j5=0.15), 0.5, hold=0.4).to(np.zeros(6), 0.8)  # peek
    s.grips((0, C), (0.3, 0.3), (1.0, C))
    add(s, "duck", "Duck down fast, stay low, then peek up carefully: duck, hide, take cover, an arrow flies over.")

    s = Seq().to(pose(j1=0.22, j2=0.0, j3=-0.1, p=-0.05, j5=-0.2, j6=0.2), 0.4, hold=0.15)
    s.to(pose(j1=-0.03, j2=0.0, j3=-0.18, p=-0.05, j5=0.15, j6=-0.2), 0.5, hold=0.15)
    s.to(pose(j1=0.15, j2=-0.02, j3=-0.05, p=0.0, j5=-0.1), 0.45, hold=0.2).to(np.zeros(6), 0.6)
    add(s, "dodge", "Weave fast to one side, then the other, then back: dodge, swerve, weave, escape a blow.")

    s = Seq()
    for _ in range(2):
        s.to(pose(j2=0.05, j3=0.3, p=0.25), 0.45, hold=0.1).to(pose(j2=0.12, j3=-0.25, p=-0.35), 0.3).hit().wait(0.25)
    s.to(np.zeros(6), 0.6).grips((0, C), (1.6, C), (1.9, 0.5), (2.5, C))
    add(s, "stomp", "Lift high and stamp down hard, twice: stomp, march, land heavily, stamp in anger, a giant's step.",
        sound=None)  # the stomp sfx is a run of footsteps: it can't land on two stamps

    s = Seq().to(pose(j2=0.02, j3=0.2, p=0.3), 0.5, hold=0.1).grips((0, C), (0.3, O))  # rear back, mouth wide
    for i in range(2):  # lunge, the jaws snapping shut as it lands (a full close takes the gripper ~0.3 s)
        s.to(pose(j1=-0.06, j2=0.32, j3=-0.05, p=-0.15), 0.4).hit().grips((s.t - 0.34, O), (s.t, C))
        s.to(pose(j2=0.15, j3=0.12, p=0.1), 0.4).grips(*([(s.t - 0.36, C), (s.t, O)] if i == 0 else []))
    s.shake(1.0, 2.2, pose(j5=0.12, j6=0.1), 2.5, ramp=0.3).to(np.zeros(6), 0.8)  # shake the catch, jaws shut
    add(s, "bite", "Rear back with the mouth wide, then lunge and snap it shut, twice, and shake the catch: bite, "
        "snap the jaws, chomp, grab with the teeth.", sound="chomp")

    s = Seq().to(pose(j2=0.1, j3=0.05, p=-0.25), 0.6)
    t0 = s.t
    s.wait(2.4).shake(t0, s.t, pose(j4=0.06, j6=0.05), 2.0, ramp=0.3).to(pose(j3=0.12, p=0.25), 0.5, hold=0.3)
    s.to(np.zeros(6), 0.6).grips((0, C), *flap(t0, t0 + 2.4, 2.0, C, 0.45), (s.t, C))
    add(s, "eat", "Bend down and munch, chewing with the head bobbing, then look up satisfied: eat, munch, chew, "
        "gobble, taste.")

    s = Seq().to(pose(j2=0.04, j3=0.3, p=0.55, j6=0.15), 0.5)
    t0 = s.t
    s.wait(2.0).shake(t0, s.t, pose(j2=0.02, j3=0.05, j4=0.05), 3.0, ramp=0.3)
    s.to(pose(j2=0.2, j3=0.0, p=-0.4), 0.6, hold=0.2).to(np.zeros(6), 0.6)  # doubled over
    s.grips((0, C), *flap(t0, s.t - 1.4, 3.0, 0.15, 0.7), (s.t - 1.2, 0.5), (s.t, C))
    add(s, "laugh", "Throw the head back and laugh, ha-ha-ha, bouncing, then double over: laugh, giggle, find it "
        "funny, chuckle.")

    s = Seq().to(pose(j2=0.04, j3=0.18, p=0.3), 1.2, hold=0.2).to(pose(j2=0.06, j3=-0.2, p=-0.5, j6=0.15), 1.6, hold=0.5)
    s.to(np.zeros(6), 0.9).grips((0, C), (1.4, C), (1.7, 0.35), (3.0, 0.2), (3.4, C))
    add(s, "sigh", "Breathe in, rising, then let it all out and sag, head drooping: sigh, tired, bored, sad, oh well.")

    s = Seq().to(pose(j1=0.1, j2=0.05, j3=-0.1, p=-0.55, j6=0.35), 1.0)
    t0 = s.t
    s.wait(4.0).shake(t0, s.t, pose(j3=0.04, j4=0.08), 0.5, ramp=0.6).to(np.zeros(6), 0.8)
    s.grips((0, C), *[(t0 + 0.5 + 2 * i + d, g) for i in range(2) for d, g in ((0, C), (0.5, 0.45), (1.0, 0.45), (1.6, C))])
    add(s, "snore", "Nod off, head drooping to one side, and snore, the mouth opening on every slow breath: sleep, "
        "snore, doze, yawn, bored to sleep.")

    s = Seq().to(pose(j2=0.02, j3=0.15, p=0.35), 0.6, hold=0.15).to(pose(j2=0.03, j3=0.25, p=0.55), 0.5, hold=0.3)
    s.to(pose(j2=0.28, j3=-0.1, p=-0.5), 0.3, hold=0.4).shake(s.t - 0.4, s.t, pose(j5=0.04), 6.0, ramp=0.1)
    s.to(np.zeros(6), 0.8).grips((0, C), (0.6, 0.35), (1.25, 0.55), (1.6, 0.65), (1.85, C), (2.6, C))
    add(s, "sneeze", "Ah... ah... head tilting back twice, mouth open, then ACHOO, a sharp snap forward: sneeze, "
        "cough, an allergy, a cold.")

    s = Seq().to(pose(j1=0.08, j2=0.05, j3=-0.12, p=-0.5, j5=-0.15), 0.9)
    t0 = s.t
    s.wait(2.5).shake(t0, s.t, pose(j4=0.07, j6=0.05), 1.6, ramp=0.4).shake(t0, s.t, pose(j5=0.02), 5.0, ramp=0.3)
    s.to(np.zeros(6), 1.0).grips((0, C), *flap(t0, t0 + 2.5, 1.6, 0.15, 0.4), (s.t, C))
    add(s, "cry", "Hang the head and sob, shoulders shaking, the mouth quivering: cry, weep, sob, sad, heartbroken.")

    s = Seq().to(pose(j2=0.12, j3=0.4, p=0.4), 0.7)
    for i in range(4):
        s.to(pose(j1=-0.06 if i % 2 else 0.06, j2=0.14, j3=0.32, p=0.22, j6=0.15 if i % 2 else -0.15), 0.3)
        s.to(pose(j2=0.12, j3=0.4, p=0.4), 0.3)
    s.wait(0.2).to(np.zeros(6), 0.8).grips((0, C), (0.5, O), (2.7, O), (3.1, C))
    add(s, "beat_chest", "Rise tall and pound, left-right-left-right, mouth roaring open: beat the chest like a "
        "gorilla, show off, I am the strongest!", arms="both")

    s = Seq()
    for _ in range(3):
        s.to(pose(j2=-0.05, j3=-0.15, p=-0.15), 0.25).to(pose(j2=0.08, j3=0.3, p=0.25), 0.3)
    s.to(np.zeros(6), 0.5).grips((0, C), (0.8, 0.4), (1.5, 0.4), (2.0, C))
    add(s, "hop", "Bounce up and down three times like hopping: hop, jump, skip, a bunny, a frog.")

    s = Seq().to(pose(j2=0.1, j3=0.35, p=0.5), 0.5)
    t0 = s.t
    s.wait(1.8).shake(t0, s.t, pose(j4=0.03, j5=0.04, j6=0.05), 6.0, ramp=0.2).to(np.zeros(6), 0.7)
    s.grips((0, C), *flap(t0, t0 + 1.8, 3.5, 0.3, 0.75), (s.t, C))
    add(s, "tremble_excited", "Pop up and quiver all over with excitement, mouth chattering: so excited!, can't "
        "wait, oh oh oh!, yes yes yes.")

    s = Seq().to(pose(j2=0.03, j3=0.15, p=0.15), 0.4).to(pose(j1=-0.05, j2=0.35, j3=-0.05, p=-0.2), 0.5)
    s.to(pose(j2=0.0, j3=0.1, p=0.1), 0.6, hold=0.3).to(np.zeros(6), 0.6)
    s.grips((0, C), (0.4, O), (0.85, O), (1.0, C))
    add(s, "grab", "Reach out with the mouth open, snatch, and pull it back: grab, take, snatch, steal, catch.")

    s = Seq().to(pose(j2=0.3, j3=-0.05, p=-0.15), 0.6, hold=0.1)
    for _ in range(2):
        s.to(pose(j2=0.1, j3=0.12, p=0.25, j6=0.15), 0.4, hold=0.15).to(pose(j2=0.2, j3=0.02, p=0.05), 0.35)
    s.to(np.zeros(6), 0.7).grips((0, O), (0.5, O), (0.65, 0.0))
    add(s, "pull", "Bite onto something ahead and tug it back, hard, twice: pull, drag, tug, heave a rope.")

    s = Seq().to(pose(j2=0.05, j3=0.0, p=-0.1), 0.5)
    for _ in range(2):
        s.to(pose(j2=0.32, j3=-0.12, p=-0.2), 0.55, hold=0.15).to(pose(j2=0.2, j3=-0.06, p=-0.15), 0.35)
    s.to(np.zeros(6), 0.8).grips((0, C))
    add(s, "push", "Lower the head and shove forward, twice, straining: push, shove, ram, butt, move a boulder.")

    low2 = pose(j2=0.2, j3=-0.3, p=-0.6)
    s = Seq().to(low2, 0.8).grips((0, O), (0.6, O), (0.9, 0.0))
    t0 = s.t + 0.3
    s.wait(0.3).to(pose(j2=0.1, j3=0.3, p=0.2), 2.4).shake(t0, s.t, pose(j4=0.03, j6=0.04), 4.0, ramp=0.3)
    s.wait(0.3).to(np.zeros(6), 0.8)
    add(s, "lift_heavy", "Bend down, grip something with the mouth and heave it up, slowly, trembling with the "
        "strain: lift, carry something heavy, pick up, struggle.")

    s = Seq().to(pose(j1=0.15, j2=0.06, j3=0.4, p=0.7, j6=0.3), 0.7, hold=0.2)  # wind back and up
    s.to(pose(j1=-0.03, j2=0.3, j3=0.05, p=-0.1, j6=-0.2), 0.4).hit(0.5).wait(0.4).to(np.zeros(6), 0.8)
    s.grips((0, C), (0.9, C), (1.1, O), (1.6, O), (2.0, C))
    add(s, "throw", "Wind back and up, then fling forward, letting go at the top: throw, toss, hurl, fling, "
        "cast a spell.", sound="whoosh")

    # ---- mime (round 3b, Marceau-style): an invisible object shows through hard stops at constant contact points.
    # s.fall() runs into a pose and stops dead (the hardest stop the acceleration limits allow); quintic moves are
    # for the free motion between contacts. The open gripper is the flat palm.
    W = pose(j2=0.16, j3=0.22, p=0.0)  # the palm flat on a wall in front
    s = Seq().to(W + pose(j2=-0.05), 0.6).fall(W, 0.3).wait(0.3)  # reach, touch: stop dead
    s.to(W + pose(j1=0.18, j5=-0.18), 0.9).wait(0.2)  # slide along it, the palm stays flat
    s.to(W + pose(j1=-0.1, j5=0.1), 1.1)
    s.fall(W + pose(j1=-0.14, j2=0.06, j5=0.14), 0.25).wait(0.15)  # ... the wall ends: it slips past the edge
    s.to(W + pose(j1=-0.1, j2=-0.04, j3=0.06, p=0.25, j5=0.2), 0.35, hold=0.4).to(np.zeros(6), 0.7)  # look: huh
    s.grips((0, C), (0.5, 1.0), (s.t - 1.3, 1.0), (s.t - 1.1, O), (s.t - 0.2, C))
    add(s, "invisible_wall", "Mime: the flat hand meets an invisible wall and stops dead, slides along it, then slips "
        "past its edge: a wall, a window, a barrier, stuck, feel the way.")

    s = Seq().to(W + pose(j2=-0.04), 0.5).fall(W, 0.3).wait(0.15)  # the front wall
    side = W + pose(j1=0.16, j5=-0.4)
    s.to(side + pose(j2=-0.04), 0.55).fall(side, 0.3).wait(0.15)  # the side
    top = pose(j2=0.05, j3=0.38, p=0.75)
    s.to(top + pose(j3=-0.05), 0.6).fall(top, 0.32).wait(0.15)  # the ceiling: all closed in
    t0 = s.t
    s.to(pose(j2=0.04, j3=0.2, p=0.3), 0.4).wait(1.0)  # panic: patting all around, mouth flapping
    s.shake(t0 + 0.4, s.t, pose(j1=0.06, j5=0.2, j6=0.15), 2.5, ramp=0.4).to(np.zeros(6), 0.8)
    s.grips((0, C), (0.4, 1.0), (t0, 1.0), *flap(t0 + 0.2, t0 + 1.4, 3.0, 0.2, 0.8), (s.t, C))
    add(s, "trapped_in_box", "Mime: the flat hand finds a wall in front, then one at the side, then the ceiling, "
        "each a dead stop; then panic, patting all around: trapped, stuck, locked in, a box, a cage.")

    s = Seq()
    for i in range(3):  # hand over hand, each rung higher: reach up open, grab (dead stop), pull down
        rung = pose(j2=0.04 + 0.03 * i, j3=0.3 + 0.12 * i, p=0.35 + 0.12 * i, j1=0.05 * (-1) ** i)
        s.to(rung + pose(j3=0.06, p=0.05), 0.45).fall(rung, 0.18).wait(0.12)
        s.to(rung + pose(j3=-0.14, p=-0.05), 0.4)
    s.wait(0.2).to(np.zeros(6), 0.8)
    s.grips(*[(t, g) for i in range(3) for t, g in ((0.05 + 1.15 * i, 1.0), (0.6 + 1.15 * i, 0.0))], (s.t - 0.6, C))
    add(s, "climb_ladder", "Mime: reach up, grab a rung (a dead stop), pull, and again, higher and higher: climb, "
        "a ladder, a rope, a tree, go up.")

    lean = pose(j2=0.18, j3=0.05, p=-0.35)  # leaning into the wind, head down
    s = Seq().to(lean, 0.7)
    t0 = s.t
    for k in range(3):  # fight forward a little, a gust shoves it back
        s.to(lean + pose(j2=0.05 * (k + 1), j3=0.02), 0.5).to(lean + pose(j2=0.05 * k - 0.04, p=0.1), 0.35)
    s.wait(0.2).shake(t0, s.t, pose(j5=0.05, j6=0.08), 3.0, ramp=0.3).to(np.zeros(6), 0.8)
    s.grips((0, C), (0.5, 0.25), (s.t - 0.8, 0.25), (s.t, C))
    add(s, "against_the_wind", "Mime: lean hard into a storm and struggle forward, pushed back by every gust: wind, "
        "a storm, walk against it, a blizzard, struggle.")

    post = pose(j1=0.2, j2=0.06, j3=0.05, p=-0.1, j5=-0.25, j6=0.35)  # leaning on a post, head tilted, at ease
    s = Seq().to(post + pose(j2=-0.03), 0.8).fall(post, 0.3).wait(1.2)
    s.fall(post + pose(j1=0.12, j2=0.12, j3=-0.12, p=-0.3, j6=0.2), 0.52).hit()  # the post is gone: drop
    s.to(pose(j2=0.0, j3=0.25, p=0.45), 0.5, hold=0.4).to(np.zeros(6), 0.8)  # catch itself, mouth open
    s.grips((0, C), (s.t - 1.7, C), (s.t - 1.4, O), (s.t - 0.6, O), (s.t, C))
    add(s, "lean_on_nothing", "Mime: lean casually on an invisible post, relaxed, then it gives way and it drops, "
        "catching itself: lean, relax, slip, oops.", sound=None)

    s = Seq().to(pose(j2=0.04, j3=0.22, p=0.3), 0.6)
    t0 = s.t
    s.wait(10 / 3).shake(t0, s.t, pose(j5=0.25, j1=0.04), 0.6, ramp=0.5)  # the eyes follow a three-ball cascade
    s.shake(t0, s.t, pose(j4=0.12, j3=0.03), 1.2, ramp=0.5).to(np.zeros(6), 0.8)
    s.grips((0, C), *[(t0 + 0.35 + i / 2.4 + d, g) for i in range(7) for d, g in ((0, C), (0.12, 0.6))], (s.t, C))
    add(s, "juggle", "Mime: juggle three invisible balls, the head following them up and across, the hand "
        "flicking each one: juggle, a circus, show off a trick.")

    s = Seq().to(pose(j2=0.3, j3=-0.1, p=-0.5), 0.8, hold=0.15).grips((0, C), (0.4, O), (0.95, O), (1.25, C))
    s.fall(pose(j2=0.27, j3=-0.06, p=-0.38), 0.3)  # pluck: a tug, dead stop
    s.to(pose(j2=0.04, j3=0.2, p=0.25), 0.7)
    t0 = s.t
    s.wait(1.0).shake(t0, s.t, pose(j3=0.025, p=0.05), 2.0, ramp=0.4)  # sniff, sniff
    s.to(pose(j2=0.03, j3=0.25, p=0.55, j6=0.35), 0.8, hold=0.6).to(np.zeros(6), 0.8)  # bliss
    s.grips((t0 + 1.0, C), (t0 + 1.4, 0.3), (s.t - 0.8, 0.3), (s.t, C))
    add(s, "smell_flower", "Mime: pluck a flower, sniff it, sniff again, and melt into a blissful head tilt: smell, "
        "a flower, a perfume, love, spring.")

    s = Seq().to(pose(j2=0.12, j3=0.1, p=-0.2), 0.5).to(pose(j2=0.06, j3=0.15, p=0.15), 0.4)  # a sip
    s.wait(0.6)  # freeze...
    t0 = s.t
    s.fall(pose(j2=0.02, j3=0.23, p=0.4, j6=0.15), 0.4).hit().wait(0.8)  # ... SOUR: jerk back, pucker, shudder
    s.shake(t0 + 0.4, s.t, pose(j5=0.06, j6=0.06), 6.0, ramp=0.2).to(np.zeros(6), 0.9)
    s.grips((0, C), (0.3, 0.45), (0.65, 0.45), (0.95, C), (t0, C), (t0 + 0.2, 0.0), (s.t - 0.9, 0.0), (s.t, C))
    add(s, "taste_sour", "Mime: take a sip, freeze, then jerk back, the mouth puckered shut, shuddering: sour, a "
        "lemon, yuck, disgusting, bitter medicine.")

    door = pose(j2=0.16, j3=0.16, p=-0.05)
    off = pose(j2=-0.025, j4=-0.07)  # a knock is mostly the wrist: it may stop twice as hard as the elbow
    s = Seq().to(door + off, 0.6)
    for k in range(3):  # knock, knock, knock: each tap stops dead on the door
        s.fall(door, 0.27).hit().to(door + off, 0.2)
    s.to(door + off + pose(j6=0.3, p=0.1), 0.4, hold=0.6)  # listen...
    s.fall(door + pose(j1=-0.04), 0.35).to(door + pose(j1=0.22, j2=-0.05, j5=-0.3), 0.9)  # the handle, swing it open
    s.wait(0.3).to(np.zeros(6), 0.8)
    s.grips((0, C), (s.t - 2.3, C), (s.t - 2.05, 0.6), (s.t - 1.8, 0.0), (s.t - 1.0, 0.0), (s.t - 0.75, C))
    add(s, "knock", "Mime: knock-knock-knock on an invisible door, wait, listen, then grab the handle and pull it "
        "open: knock, a door, open the door, visit, who's there?", sound=None)

    s = Seq().to(pose(j2=0.02, j3=0.25, p=0.2), 0.6)
    t0 = s.t
    s.wait(3.0).shake(t0, s.t, pose(j1=0.05, j6=0.3), 0.66, ramp=1.0)  # balancing sways, arms out
    s.fall(pose(j1=-0.1, j2=0.06, j3=0.2, p=0.05, j6=-0.45), 0.35)  # a slip...
    s.to(pose(j2=0.02, j3=0.25, p=0.2), 0.6, hold=0.2).to(np.zeros(6), 0.7)  # ... and recovers
    s.grips((0, 1.0), (s.t - 0.7, 1.0), (s.t, C))
    add(s, "tightrope", "Mime: balance on a tightrope, swaying wider and wider, a slip, and it saves itself: "
        "balance, a tightrope, wobble, careful steps, a circus.")

    down = pose(j2=-0.05, j3=-0.38, p=0.0)  # sinks straight down, the head level
    s = Seq().to(pose(p=0.05), 0.4).to(down, 1.6, hold=0.6).to(np.zeros(6), 1.4).wait(0.2)
    s.grips((0, C))
    add(s, "elevator", "Mime: sink straight down, head level, as if riding an elevator (or stairs) behind a counter, "
        "then come back up: go down, an elevator, downstairs, sink, hide behind something.")

    # ---- stand-up comedy (round 3b): punchline reactions for a comedian or a double act; the laugh track is the
    # agent's. Nothing is held. (mic_drop was cut in round 3b.)
    s = Seq().to(pose(j2=0.12, j3=-0.12, p=-0.65, j5=-0.25), 0.45, hold=0.2)  # the head drops and turns away
    t0 = s.t
    s.wait(2.5).shake(t0, s.t, pose(j5=0.12, j6=0.06), 0.8, ramp=0.6).to(np.zeros(6), 1.0)
    s.grips((0, C))
    add(s, "facepalm", "The head drops into the hand and turns away, shaking slowly: facepalm, groan, a terrible "
        "pun, oh no, embarrassed, I can't believe it.")

    up3 = pose(j2=0.04, j3=0.32, p=0.5)
    s = Seq().to(up3, 0.45)
    t0 = s.t
    for _ in range(3):  # slap the knee, well above the table
        s.to(pose(j1=0.04, j2=0.14, j3=-0.12, p=-0.3), 0.28).to(up3, 0.4)
    s.wait(0.2).to(np.zeros(6), 0.7).grips((0, C), *flap(t0, s.t - 0.7, 3.0, 0.15, 0.7), (s.t, C))
    add(s, "knee_slap", "Laugh, head thrown back, and slap the knee, three times: knee slap, that's hilarious, "
        "the joke lands, roaring with laughter.")

    s = Seq().to(pose(j3=0.1, p=0.15, j5=0.12), 0.5)
    s.path([pose(j3=0.18, p=0.5, j5=0.0), pose(j3=0.15, p=0.4, j5=-0.25, j6=0.15),
            pose(j1=0.05, j3=0.05, p=0.0, j5=-0.4, j6=0.25)], 2.0).wait(0.6)  # up, over and away
    s.to(pose(j1=0.05, j2=0.04, j3=-0.05, p=-0.2, j5=-0.3, j6=0.15), 0.6, hold=0.3).to(np.zeros(6), 0.8)
    s.grips((0, C), (s.t - 1.6, 0.25), (s.t - 1.2, C))
    add(s, "eye_roll", "The head rolls up and around and away, then sags: an eye roll, ugh, so boring, whatever, "
        "really?, roll eyes.")

    s = Seq().to(pose(j1=-0.05, j2=0.05, j3=-0.1, p=-0.3, j6=-0.4), 0.6, hold=0.2)  # gather in
    s.to(pose(j1=0.15, j2=0.08, j3=0.38, p=0.4, j5=-0.25, j6=0.35), 0.45).hit(0.9).wait(1.2).to(np.zeros(6), 0.8)  # flourish
    s.grips((0, C), (0.8, C), (1.1, O), (2.3, O), (2.9, C))
    add(s, "ta_da", "Gather in, then swing up and out to the audience with a flourish, mouth wide: ta-da, "
        "presenting, voila, here it is, a big reveal, a magic trick.", sound="tada")

    # slow_turn knows where the audience is: from the audience facing it first side-eyes the other arm, from
    # each_other it is already looking at it (stance_keys, set after the library is built).
    def slow_turn(look, back, deadpan=1.4):
        s = Seq().wait(0.3)
        if look is not None:
            s.to(look, 0.9, hold=0.6)  # side-eye at the other one
        return s.to(back, 2.2, hold=deadpan).to(np.zeros(6), 0.9).grips((0, C))
    s = slow_turn(pose(j1=-0.06, j3=0.03, p=0.05, j5=0.5, j6=0.12), pose(j3=0.03, p=0.08), 1.0)
    add(s, "slow_turn", "Freeze, side-eye the other one, then turn the head slowly to the audience and stare, "
        "deadpan, saying nothing: after a bad joke, deadpan, awkward silence, really?, the slow turn.")

    s = Seq().to(pose(j2=0.04, j3=0.32, p=0.6, j6=0.15), 0.5)
    t0 = s.t
    s.wait(5 / 3).shake(t0, s.t, pose(j2=0.025, j3=0.06, j4=0.05), 3.0, ramp=0.33)
    s.to(pose(j1=0.05, j2=0.2, j3=0.0, p=-0.4, j6=-0.1), 0.6, hold=0.2)  # doubled over, still going
    t1 = s.t
    s.wait(1.6).shake(t1, s.t, pose(j2=0.015, j4=0.03, j6=0.03), 2.5, ramp=0.4).to(np.zeros(6), 0.8)
    s.grips((0, C), *flap(t0, s.t - 0.9, 3.0, 0.15, 0.75), (s.t, C))
    add(s, "laugh_hard", "Crack up: head back, bouncing, the mouth flapping with laughter, then doubled over, still "
        "laughing: laugh hard, crack up, roar with laughter, can't stop laughing, LOL.")

    for g, e in zip(prop_gestures(), prop_gestures("each_other_props")):  # the toys play in the props stances
        out.append(dict(g, stance_keys={"each_other_props": e["keys"]}))
    by = {g["name"]: g for g in out}
    for n in ("die_dramatically", "bang_table"):  # table contact from each_other too (the heads turned in)
        by[n]["stance_keys"] = {"each_other": table_variant(by[n])[0]}
    by["slow_turn"]["stance_keys"] = {  # already facing the other one: just the slow turn out to the audience
        "each_other": slow_turn(None, pose(j1=0.08, j3=0.03, p=0.05, j5=-0.85)).gesture("", "")["keys"],
        "each_other_props": slow_turn(None, pose(j1=0.12, j3=0.03, p=0.05, j5=-0.4)).gesture("", "")["keys"]}
    c = table_variant(by["die_dramatically"])[1]
    by["revive"]["stance_keys"] = {"each_other": table_variant(by["revive"], fix=c)[0]}  # gets up from where die lay

    s = Seq().wait(3.0)
    s.shake(0, 3.0, pose(j2=0.04, j3=0.08, j4=-0.12), 1.5).shake(0, 3.0, pose(j1=0.08), 0.5, ramp=0.5)
    add(s, "gallop", "Bounce up and down like riding a horse.")

    s = Seq().to(pose(j2=0.18, j3=0.4, p=0.6), 0.8, hold=0.1).to(pose(j2=0.35, j3=-0.05, p=0.2), 0.6).hit(0.0)
    s.shake(s.t, s.t + 1.0, pose(j5=0.08, j6=0.08), 2.2).wait(1.0).to(np.zeros(6), 0.9)
    add(s, "roar", "Rise up tall, lunge at the audience and shake the head.", sound="roar")

    crouch = pose(j2=-0.12, j3=-0.3, p=-0.2)
    s = Seq().to(crouch + pose(j1=0.25), 1.0)
    t0 = s.t
    s.to(crouch + pose(j1=-0.12), 2.6)
    s.shake(t0, s.t, pose(j4=0.06), 1.2).to(np.zeros(6), 1.0)
    add(s, "sneak", "Crouch low and creep across, head bobbing.")

    add(Seq().to(pose(j1=-0.22, j2=-0.05, j3=0.0, p=0.0, j5=0.35, j6=0.3), 0.8, hold=1.2).to(np.zeros(6), 0.8),
        "look_at_other", "Turn the head toward the other arm, tilt, hold.", arms="both")

    add(Seq().to(pose(j2=0.35, j3=0.05, p=0.0), 0.8, hold=1.2).to(np.zeros(6), 0.8),
        "point_at_audience", "Reach forward and point the prop at the audience.")

    s = Seq().wait(2.4)
    s.shake(0, 2.4, pose(j4=0.06), 1.25)
    s.grips(*[(0.2 + 0.33 * i, 1.0 if i % 2 == 0 else 0.0) for i in range(7)], (2.4, 1.0))
    add(s, "gripper_clap", "Snap the gripper open and shut three times (drops a held prop!).")
    return out


_SC = []


def collapse_law(q0, q1, vz=0.26, A=0.7, zone=0.055):
    """Time law along the joint line q0 -> q1 (left-arm deltas, audience) that ends on the table: as fast as A of the
    accel limits allow (bang-bang, no easing), but at <= vz m/s of hand descent once the hand is within `zone` of the
    table (the checker's impact rule). Returns (ease, T) for Seq.drop."""
    if not _SC:
        _SC.append(show.Scene())
    s = np.linspace(0, 1, 401); d = np.asarray(q1, float) - q0
    h = _SC[0].lows("left", show.STAGE_LEFT + q0 + s[:, None] * d)[0]
    dh = np.abs(np.gradient(h, s))
    a = A * np.min(show.AMAX / np.maximum(np.abs(d), 1e-6))  # in s per s^2
    vmax = 0.7 * np.min(show.VMAX / np.maximum(np.abs(d), 1e-6))
    cap = np.where(h < zone, vz / np.maximum(dh, 1e-6), np.inf)
    v = np.minimum.reduce([np.sqrt(2 * a * s), np.sqrt(2 * a * (1 - s)), np.full_like(s, vmax), cap])
    for i in range(len(s) - 2, -1, -1):  # brake in time for a cap ahead
        v[i] = min(v[i], np.sqrt(v[i + 1] ** 2 + 2 * a * (s[i + 1] - s[i])))
    for i in range(1, len(s)):
        v[i] = min(v[i], np.sqrt(v[i - 1] ** 2 + 2 * a * (s[i] - s[i - 1])))
    t = np.concatenate([[0], np.cumsum(np.diff(s) / np.maximum((v[1:] + v[:-1]) / 2, 1e-6))])
    T = float(t[-1])
    return (lambda x: np.interp(np.clip(x, 0, 1) * T, t, s)), T


def table_variant(g, stance="each_other", fix=None, ramp=0.6):
    """g re-solved for a stance so its table contact still lands (the head turned in leaves a slump or a bang a few
    cm above the table): a constant shoulder/elbow/wrist correction c, faded in before the first near-table key and
    out after the last one (kept at the start/end if the gesture starts/ends there, e.g. die's hold, revive), solved
    so the hand's lowest point equals the audience version's. Returns (keys, c); fix=c reuses a correction."""
    sc, S = show.Scene(), show.STANCES[stance]
    t = np.array([k["t"] for k in g["keys"]]); dq = np.array([k["q"] for k in g["keys"]])
    low = lambda Q: sc.lows("left", Q)[0]
    ha = low(show.STAGE_LEFT + dq)
    near = np.flatnonzero(ha < ha.min() + 0.08)
    t0, t1 = t[near[0]], t[near[-1]]
    r0 = min(ramp, t0) if np.abs(dq[0]).max() < 1e-6 else ramp
    r1 = min(ramp, t[-1] - t1) if np.abs(dq[-1]).max() < 1e-6 else ramp
    w = np.minimum(quintic(np.clip((t - t0 + r0) / max(r0, 1e-9), 0, 1)), quintic(np.clip((t1 + r1 - t) / max(r1, 1e-9), 0, 1)))
    J = [1, 2, 3]
    c = np.zeros(6) if fix is None else np.asarray(fix, float)
    for _ in range(0 if fix is not None else 12):
        hs = low(S + dq + w[:, None] * c)
        k = int(hs.argmin()); err = hs[k] - ha.min()
        if abs(err) < 5e-4:
            break
        base = S + dq[k] + w[k] * c
        grad = np.array([(low([base + 1e-3 * np.eye(6)[j]])[0] - hs[k]) / 1e-3 for j in J])
        c[J] -= err * grad / (grad @ grad)
    keys = [dict(kk, q=[round(float(x), 4) for x in dq[i] + w[i] * c]) for i, kk in enumerate(g["keys"])]
    return keys, c


def car_frame(h):
    """Grasp site rotation holding the toy car wheels-down, its long axis (site y) along the horizontal heading h."""
    h = np.array([h[0], h[1], 0.0]) / np.hypot(h[0], h[1])
    z = np.array([0.0, 0.0, -1.0])
    return np.column_stack([np.cross(h, z), h, z])


def ik_path(pts, frames, seed, base=None):
    """Left-arm joint deltas (from the stance pose `base`, default STAGE_LEFT) along Cartesian grasp-site points and
    rotations, by IK."""
    from make_duets import ik
    base = show.STAGE_LEFT if base is None else base
    out = []
    for p, R in zip(pts, frames):
        q, e, r = ik("left", np.asarray(p, float), R, seed)
        assert e < 2e-3 and r < 0.02, (p, e, r)
        out.append(q - base); seed = q
    return np.array(out)


def prop_gestures(stance="audience"):
    """Gestures with a handed-in toy (round 3b): the left arm's keys from that stance's pose; the right arm mirrors.
    Solved by IK per stance, so the toy follows the same path on the table in every stance (library() stores the
    each_other_props solution as stance_keys). Each toy gesture starts and ends at the toy's ready pose (the car just
    above the table, the saucer hanging in the air) and holds there, so a story's second toy move needs no approach;
    the stage's blend brings the arm there the first time."""
    S, out = show.STANCES[stance], []
    ik = lambda pts, rots, seed: ik_path(pts, rots, seed, S)
    # drive: the toy car (show.PROP_SHAPES["car"]) glides down onto the table while rolling, drives inward over two
    # little bumps, turns left toward the audience, lifts it off and holds it there. The wheels press
    # 2 mm into the table.
    z0 = show.TABLE_Z + 0.028 - 0.002  # grasp site height with the wheels 2 mm into the table
    seed = S + pose(j2=0.3, j3=-0.38, p=-1.25)
    line = lambda a, b, n: [np.asarray(a) + (np.asarray(b) - np.asarray(a)) * u for u in np.linspace(0, 1, n)[1:]]
    bump = lambda y: 0.004 * sum(np.exp(-((y - c) / 0.012) ** 2) for c in (0.03, -0.03))
    ax = (0, 1)  # the car's long axis (site y); it drives along it either way (j6 can't turn the car around)
    home_p = (0.28, 0.12, z0 + 0.025)
    home = ik([home_p], [car_frame(ax)], seed)[0]
    ys = np.linspace(0.12, -0.07, 20)[1:]
    a = np.linspace(np.pi, 4 / 3 * np.pi, 7)[1:]  # then a 60 deg left turn toward the audience around (0.32, -0.07)
    legs = [([(0.28, 0.12, z0)], [car_frame(ax)], 0.9),  # touch down (<= 6 cm/s near the table)
            ([(0.28, y, z0 + bump(y)) for y in ys], [car_frame(ax)] * len(ys), 2.0),
            ([(0.32 + 0.04 * np.cos(t), -0.07 + 0.04 * np.sin(t), z0) for t in a],
             [car_frame((np.sin(t), -np.cos(t))) for t in a], 1.6)]
    s, p0 = Seq(home), home_p
    for pts, rots, T in legs:  # each leg its own smooth start and stop: slows into the turn, like a careful driver
        q = ik(pts, rots, S + s.q)
        s.path(q, T, pts=[p0] + pts); p0 = pts[-1]
    h = (np.sin(a[-1]), -np.cos(a[-1]))
    up = ik(line(p0, (p0[0], p0[1], z0 + 0.03), 3), [car_frame(h)] * 2, S + s.q)
    s.path(up, 0.5).wait(0.3)  # and holds it up there
    g = s.gesture("drive", "Holding a toy car, roll it down onto the table and drive it: vroom along over two bumps, "
                  "turn toward the audience, lift it off: drive, race, park, a road trip.", hold=True,
                  table=dict(min=-0.003, impact=0.06, prop=True))
    out.append(dict(g, prop="car"))

    # The UFO (show.PROP_SHAPES["ufo"]): a 10 cm saucer hanging from its gripped dome, the gripper pointing down.
    def ufo(p, tilt=0.0, toward=0.0, spin=0.0, lean=0.12):
        """(point, rotation): the saucer at p, its axis tilted `tilt` rad toward azimuth `toward` on top of a `lean`
        toward the audience (pointing straight down, the wrist pitch limit keeps the grasp site below ~16 cm), spun
        `spin` rad."""
        z = np.array([np.tan(tilt) * np.cos(toward) + np.tan(lean), np.tan(tilt) * np.sin(toward), -1.0])
        z /= np.linalg.norm(z)
        x = np.array([-1.0, 0, 0]); x -= x @ z * z; x /= np.linalg.norm(x)
        R = np.column_stack([x, np.cross(z, x), z])
        c, s_ = np.cos(spin), np.sin(spin)
        return p, R @ np.array([[c, -s_, 0], [s_, c, 0], [0, 0, 1]])

    def fly(samples, seed):
        pts, rots = zip(*samples)
        return ik(pts, rots, seed), [tuple(p) for p in pts]

    seed = S + np.array([0, 0.89, 0.9, -1.68, 0, 0])
    hp = (0.30, 0.0, 0.14)
    uhome = fly([ufo(hp)], seed)[0][0]  # the saucer hanging in front: every UFO move starts and ends here
    # fly: a wobbly figure-eight through the ready point, rocking and spinning a little as it goes
    u = np.linspace(0, 2 * np.pi, 41)
    f8 = [ufo((0.30 + 0.025 * np.sin(2 * t), 0.06 * np.sin(t), 0.14 + 0.012 * np.sin(3 * t)),
              tilt=0.06 * np.sin(t / 2) ** 2 + 0.03 * np.sin(2 * t) * np.sin(t / 2), toward=2 * t,
              spin=0.4 * np.sin(t)) for t in u]
    q8, p8 = fly(f8, S + uhome)
    s = Seq(uhome).path(q8[1:], 5.6, law=cruise, pts=p8).to(uhome, 0.3)
    out.append(dict(s.gesture("ufo_fly", "Holding the UFO, fly it in a wobbly figure-eight, rocking and spinning: fly, "
                              "zoom, an alien ship, invade, cruise through space.", hold=True), prop="ufo"))
    # hover: stop in mid-air and pulse, bobbing and spinning slowly, as if beaming something up
    s = Seq(uhome).wait(4.0)
    s.shake(0, 4.0, pose(j2=0.015, j3=0.04), 1.0, ramp=0.5).shake(0, 4.0, pose(j6=0.5), 0.5, ramp=1.0)
    out.append(dict(s.gesture("ufo_hover", "The UFO stops in mid-air and hovers, pulsing up and down and slowly "
                              "turning: beam up, abduct, scan, hover, the aliens watch.", hold=True), prop="ufo"))
    # land: a slow, wobbling descent, touch down on the table, sit, take off again
    zl = show.TABLE_Z + 0.042  # site height with the saucer just on the table (facing each other: ~3 mm in)
    desc = [ufo((0.30, 0.02 * v, zl + (hp[2] - zl) * (1 - v) ** 2), tilt=0.1 * np.sin(np.pi * v), toward=6 * v,
                spin=0.3 * v, lean=0.12 * (1 - v)) for v in np.linspace(0, 1, 17)]
    qd, pd = fly(desc, S + uhome)
    k = int(np.argmax([p[2] < zl + 0.045 for p in pd]))  # the last 4.5 cm on its own, slowly (<= 5 cm/s)
    s = Seq(uhome).path(qd[1:k + 1], 1.2, pts=pd[:k + 1]).path(qd[k + 1:], 2.2, pts=pd[k:]).wait(0.3)
    s.path(qd[::-1][1:], 2.0, pts=pd[::-1])
    out.append(dict(s.gesture("ufo_land", "The UFO comes down slowly, wobbling, lands on the table, sits a moment, "
                              "then takes off again: land, arrive, touch down, visit Earth.", hold=True,
                              table=dict(min=-0.004, impact=0.06, prop=True)), prop="ufo"))
    # crash: a widening wobble and a spiral down that stops, tilted, well above the table, and stays there
    r = lambda v: 0.05 * np.sin(np.pi * v / 2)  # the spiral widens as it falls
    sp = [ufo((0.30 + r(v) * (1 - np.cos(9 * v)), -r(v) * np.sin(9 * v), hp[2] - 0.06 * v ** 2),
              tilt=0.3 * v, toward=9 * v, spin=0.5 * v, lean=0.12 + 0.1 * v) for v in np.linspace(0, 1, 61)]
    qc, pc = fly(sp, S + uhome)
    s = Seq(uhome).path(qc[1:], 4.9, pts=pc, law=cruise).hit().wait(0.6)
    out.append(dict(s.gesture("ufo_crash", "The UFO loses control: it wobbles harder and harder, spirals down and "
                              "crashes, tilted (stopping well above the table), and lies there: crash, shot down, "
                              "spin out, out of fuel.", hold=True, sound="crash"), prop="ufo"))
    return out

if __name__ == "__main__":
    show.GESTURE_DIR.mkdir(exist_ok=True)
    for f in show.GESTURE_DIR.glob("*.json"):
        if not (json.loads(f.read_text()).get("recorded") or f.stem.startswith(("pick_", "place_"))):
            f.unlink()  # regenerate; recorded gestures and lars.py's baked picks stay
    for g in library():
        (show.GESTURE_DIR / f"{g['name']}.json").write_text(json.dumps(g, separators=(",", ":")) + "\n")
        print(f"{g['name']:18s} {g['keys'][-1]['t']:.1f} s")
