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


class Seq:
    """Builds a gesture as a function of time: a chain of pose moves, holds and added oscillations."""

    def __init__(self, start=None):
        self.segs, self.t, self.q = [], 0.0, np.zeros(6) if start is None else start
        self.osc, self.grip = [], []

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

    def gesture(self, name, about, arms="one", hold=False, loop=False, table=None):
        n = int(round(self.t / DT))
        ts = np.linspace(0, self.t, n + 1)
        keys = [{"t": round(float(t), 3), "q": [round(float(x), 4) for x in self.at(t)]} for t in ts]
        if self.grip:
            gt = np.array([a for a, _ in self.grip]); gv = np.array([b for _, b in self.grip])
            for k in keys:
                k["g"] = round(float(np.interp(k["t"], gt, gv)), 3)
        out = {"name": name, "about": about, "arms": arms, "hold": hold, "loop": loop, "keys": keys}
        if table:
            out["table"] = table  # the hand may touch the table (show.TABLE_PARTS), empty hands only
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
        .to(pose(j1=-0.12, j2=0.3, j3=-0.15, p=-0.45, j6=-0.3), 0.5, hold=0.35).to(np.zeros(6), 1.0),
        "slash", "Wind up high and outside, chop down and across toward the front.")

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

    # Death (Nov 7, round 3): the power cuts. A gasp, then everything drops at once like a falling body, a small
    # bounce, and a slow last sag until the head lies sideways on the table, a hair above it (table contact rules:
    # the final approach <= 5 cm/s, the links keep 4 cm). Empty hands only.
    dead = pose(j1=0.35, j2=0.2, j3=-0.48, p=-0.9, j5=-0.2, j6=0.7)
    s = Seq().to(pose(j2=-0.03, j3=0.12, p=0.35, j6=-0.1), 0.35).grips((0, 0.05), (0.3, 0.7))
    s.shake(0.35, 0.95, pose(j4=0.05, j6=0.05), 5.0, ramp=0.1).wait(0.6)  # the shudder
    s.fall(dead + pose(j3=0.32, p=-0.1), 1.0)  # stops ~6 cm up: the last 5 cm go at <= 5 cm/s
    s.to(dead + pose(j3=0.38, p=-0.14), 0.25).to(dead + pose(j3=0.32, p=-0.1), 0.25)  # bounce
    s.to(dead, 2.4, hold=0.5).grips((1.0, 0.7), (1.6, 0.35))  # the last sag, the jaw hangs slack
    add(s, "die_dramatically", "Gasp and shudder, then go limp: drops like a puppet with its strings cut, a small "
        "bounce, and lies dead with the head on the table, mouth slack (play revive or stage after).",
        hold=True, table=dict(min=0.005, impact=0.05))

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
    for h, rise in ((8, 6), (6, 8)):
        s.fall(fist(-0.4), 0.5 if h == 6 else 0.58).to(fist(rise) + pose(j1=0.03 if rise == 6 else 0.05, p=0.12), 0.32)
    t0 = s.t
    s.to(fist(8) + pose(j2=-0.03, p=0.3), 0.5).shake(t0, t0 + 0.9, pose(j5=0.12, j6=0.12), 3.0, ramp=0.3)
    s.wait(0.4).grips((t0, 0.0), (t0 + 0.25, 0.75), (t0 + 0.6, 0.75), (t0 + 0.85, 0.0))  # a snarl
    s.to(fist(8) + pose(p=0.15), 0.3).fall(fist(-0.4), 0.58).wait(0.35).to(np.zeros(6), 0.9)
    add(s, "bang_table", "Make a fist and bang it on the table, twice, snarl, and once more, hard: anger, "
        "impatience, demanding, a judge's verdict.", table=dict(min=-show.BANG_DEPTH, impact=0.3))

    # Stirring (round 3): a closed gripper points down like a spoon in a pot, ~24 cm over the table; j1 sways the
    # circle sideways, j2+j3 move it to and fro at the same height, j6 swirls the spoon; the eyes (the wrist camera)
    # watch the pot.
    spoon = pose(j2=0.3, j3=0.61, p=-1.2)  # ~24 cm up: a held spoon (prop) clears the table too
    side, fro = pose(j1=0.14, j6=0.12), pose(j2=0.07, j3=0.09, p=-0.03)
    s = Seq().to(spoon, 0.9).grips((0, 0.05), (0.5, 0.0))
    t0 = s.t
    s.wait(4.4).circle(t0, s.t, side, fro, 0.45, ramp=1.0).shake(t0, s.t, pose(j5=0.06), 0.225, ramp=1.0)
    s.to(pose(j2=0.05, j3=0.15, p=0.2), 0.6, hold=0.3).to(np.zeros(6), 0.6)  # a satisfied look up
    add(s, "stir_lazy", "Hold a spoon down in a pot and stir slow, lazy circles, then look up content: cooking, "
        "brewing a potion, mixing, waiting.")
    s = Seq().to(spoon + pose(j2=0.03), 0.7).grips((0, 0.05), (0.4, 0.0))
    t0 = s.t
    s.wait(3.2).circle(t0, s.t, pose(j1=0.09, j6=0.08), pose(j2=0.06, j3=0.08), 1.1, ramp=0.6)
    s.shake(t0, s.t, pose(j5=0.04, j6=0.03), 2.2, ramp=0.6)
    s.to(np.zeros(6), 0.8)
    add(s, "stir_frantic", "Stir a pot fast and frantically, head bobbing: whisking, cooking in a hurry, mixing "
        "a potion before it boils over.")

    # Italian hands (round 3): the closed gripper = pinched fingertips pointing up, pumped from the elbow and wrist,
    # with a body sway and a head tilt; plays well with the talking mouth right after.
    pinch = pose(j2=0.1, j3=0.35, p=1.2)
    s = Seq().to(pinch + pose(j1=-0.05, j6=0.15), 0.6).grips((0, 0.05), (0.3, 0.0))
    t0 = s.t
    s.to(pinch + pose(j1=0.08, j6=-0.15), 2.2)
    s.shake(t0, s.t, pose(j2=0.025, j3=0.07, p=0.05), 2.4, ramp=0.3).shake(t0, s.t, pose(j5=0.08), 0.9, ramp=0.4)
    s.to(np.zeros(6), 0.7).grips((s.t - 0.7, 0.0), (s.t, 0.05))
    add(s, "italian", "The pinched-fingers hand: fingertips together pointing up, pumping up and down with a head "
        "tilt: \"what do you want?!\", arguing, insisting, explaining, complaining.")

    s = Seq().to(pinch + pose(j2=0.04, j3=0.08, p=0.1), 0.6).grips((0, 0.05), (0.3, 0.0))
    t0 = s.t
    s.wait(2.14).shake(t0, s.t, pose(j2=0.035, j3=0.085, p=0.05), 1.4, ramp=0.5).shake(t0, s.t, pose(j1=0.06, j6=0.1), 0.8, ramp=0.3)
    s.to(pose(j1=0.15, j2=-0.02, j3=0.25, p=0.5, j5=-0.2, j6=0.3), 0.8).grips((s.t - 0.8, 0.0), (s.t - 0.3, 1.0))  # hands up
    s.wait(0.5).to(pose(j2=0.0, j3=-0.1, p=-0.3), 0.7).wait(0.3).to(np.zeros(6), 0.6).grips((s.t - 1.0, 1.0), (s.t, 0.05))
    add(s, "italian_exasperated", "Both hands pinched and pumping big, then thrown open to the sky and dropped: "
        "\"mamma mia!\", exasperated, fed up, giving up.", arms="both")

    s = Seq().to(pose(j2=0.0, j3=0.3, p=0.6, j1=-0.05), 0.6).grips((0, 0.05), (0.3, 0.0))
    s.to(pose(j2=0.06, j3=0.25, p=0.35), 0.35, hold=0.25)  # the kiss: the pinch pecks forward
    s.to(pose(j1=0.25, j2=0.02, j3=0.45, p=1.0, j5=-0.3, j6=0.4), 0.45, hold=0.6)  # ... and springs open
    s.grips((s.t - 1.05, 0.0), (s.t - 0.85, 1.0), (s.t, 1.0)).to(np.zeros(6), 0.7).grips((s.t, 0.05))
    add(s, "chef_kiss", "The chef's kiss: pinched fingertips peck forward, then spring open and fly up: "
        "perfect!, delicious!, magnificent, a masterpiece.")

    s = Seq().to(pose(j1=0.0, j2=0.05, j3=0.2, p=0.1, j5=0.1), 0.5).grips((0, 0.05), (0.3, 0.0))
    s.to(pose(j1=0.3, j2=0.0, j3=0.35, p=0.8, j5=-0.4, j6=0.3), 0.35).grips((s.t - 0.25, 0.0), (s.t, 1.0))
    s.wait(0.6).to(np.zeros(6), 0.8).grips((s.t - 0.8, 1.0), (s.t, 0.05))
    add(s, "flick", "A dismissive flick: the hand flips up and away, fingers springing open, head turned aside: "
        "bah!, whatever, go away, never mind.")

    # ---- face acting (round 3, step B): eyes (the wrist camera) lead, anticipation, the action, a held beat; the
    # gripper mouth plays the emotion. CLOSED/OPEN are mouth.py's shut and wide values.
    C, O = 0.05, 0.8
    flap = lambda t0, t1, hz, lo=C, hi=0.6: [(t0 + i / (2 * hz), hi if i % 2 else lo) for i in range(int((t1 - t0) * 2 * hz) + 1)]

    s = Seq().to(pose(j2=0.08, j3=0.25, p=0.0, j1=-0.05), 0.6, hold=0.25).grips((0, C))  # aim the barrel
    for _ in range(2):
        s.to(pose(j2=0.02, j3=0.38, p=0.45, j1=0.0, j6=0.15), 0.18).to(pose(j2=0.08, j3=0.25, p=0.0, j1=-0.05), 0.45, hold=0.3)
    s.to(pose(j2=0.0, j3=0.35, p=0.6, j6=-0.3), 0.6, hold=0.4).to(np.zeros(6), 0.7)  # blow the smoke off
    t = 0.85
    s.grips((t, C), (t + 0.12, 0.7), (t + 0.4, C), (t + 0.93, C), (t + 1.05, 0.7), (t + 1.33, C))
    add(s, "shoot", "Aim the gripper like a gun barrel and fire twice, each shot kicking the head up in recoil, "
        "then blow the smoke away: shoot, fire, blast, zap, a laser.")

    s = Seq().to(pose(j1=0.25, j2=0.03, j3=0.1, p=0.05, j5=-0.4), 0.9, hold=0.4)
    s.to(pose(j1=-0.08, j2=0.03, j3=0.1, p=0.05, j5=0.4), 1.3, hold=0.4).to(np.zeros(6), 0.8)
    add(s, "look_around", "Scan slowly to one side, then the other, searching: look around, search, where is it?, "
        "keep watch.")

    s = Seq().to(pose(j1=-0.05, j5=0.3, p=0.0), 0.5, hold=0.2).to(pose(j1=0.1, j5=-0.2), 0.5, hold=0.35)
    s.to(pose(j1=-0.05, j2=-0.02, j3=0.2, p=0.35, j5=0.35, j6=0.3), 0.3, hold=0.8).to(np.zeros(6), 0.8)
    s.grips((0, C), (1.55, C), (1.85, O), (2.65, O), (3.2, C))
    add(s, "double_take", "Glance at something, look away, then snap back, head jerking up and tilted, jaw dropped: "
        "a double take, spot, notice, wait, what?!")

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
        s.to(pose(j2=0.05, j3=0.3, p=0.25), 0.45, hold=0.1).to(pose(j2=0.12, j3=-0.25, p=-0.35), 0.3, hold=0.25)
    s.to(np.zeros(6), 0.6).grips((0, C), (1.6, C), (1.9, 0.5), (2.5, C))
    add(s, "stomp", "Lift high and stamp down hard, twice: stomp, march, land heavily, stamp in anger, a giant's step.")

    s = Seq().to(pose(j2=0.02, j3=0.2, p=0.3), 0.5, hold=0.1)
    for _ in range(2):
        s.to(pose(j1=-0.06, j2=0.32, j3=-0.05, p=-0.15), 0.4).to(pose(j2=0.15, j3=0.12, p=0.1), 0.4)
    s.shake(1.0, 2.2, pose(j5=0.12, j6=0.1), 2.5, ramp=0.3).to(np.zeros(6), 0.8)
    s.grips((0, C), (0.5, O), (0.85, O), (1.0, C), (1.25, O), (1.65, O), (1.8, C))
    add(s, "bite", "Rear back with the mouth wide, then lunge and snap it shut, twice, and shake the catch: bite, "
        "snap the jaws, chomp, grab with the teeth.")

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

    s = Seq().to(pose(j1=0.1, j2=0.05, j3=-0.1, p=-0.55, j6=0.35), 1.2)
    t0 = s.t
    s.wait(4.0).shake(t0, s.t, pose(j3=0.04, j4=0.08), 0.5, ramp=0.6).to(np.zeros(6), 1.0)
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
        s.to(pose(j1=-0.08 if i % 2 else 0.08, j2=0.14, j3=0.3, p=-0.1, j6=0.2 if i % 2 else -0.2), 0.25)
        s.to(pose(j2=0.12, j3=0.4, p=0.4), 0.25)
    s.wait(0.3).to(np.zeros(6), 0.8).grips((0, C), (0.5, O), (2.7, O), (3.1, C))
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
    s.to(pose(j1=-0.03, j2=0.3, j3=0.05, p=-0.1, j6=-0.2), 0.4, hold=0.4).to(np.zeros(6), 0.8)
    s.grips((0, C), (0.9, C), (1.1, O), (1.6, O), (2.0, C))
    add(s, "throw", "Wind back and up, then fling forward, letting go at the top: throw, toss, hurl, fling, "
        "cast a spell.")

    # ---- stand-up comedy (round 3b): punchline reactions for a comedian or a double act; the laugh track is the
    # agent's. Nothing is held: the mic in mic_drop is mimed.
    s = Seq().to(pose(j2=0.06, j3=0.38, p=0.2), 0.7, hold=0.6)  # the mic held up high
    s.to(pose(j2=0.1, j3=0.3, p=-0.55), 0.6, hold=0.5)  # look down where it fell
    s.to(pose(j2=0.03, j3=0.22, p=0.3, j5=-0.2, j6=0.3), 0.7, hold=1.0).to(np.zeros(6), 0.8)  # smug tilt
    s.grips((0, C), (1.15, C), (1.45, O), (s.t - 1.2, O), (s.t - 0.6, C))
    add(s, "mic_drop", "Hold the mic up high, let it drop (the mouth springs open), look down at it, then a smug "
        "head tilt: mic drop, nailed it, the punchline, boom.")

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
    s.to(pose(j1=0.15, j2=0.08, j3=0.38, p=0.4, j5=-0.25, j6=0.35), 0.45, hold=1.2).to(np.zeros(6), 0.8)  # flourish
    s.grips((0, C), (0.8, C), (1.1, O), (2.3, O), (2.9, C))
    add(s, "ta_da", "Gather in, then swing up and out to the audience with a flourish, mouth wide: ta-da, "
        "presenting, voila, here it is, a big reveal, a magic trick.")

    s = Seq().wait(0.6).to(pose(j1=0.1, j3=0.04, p=0.05, j5=-0.55), 2.6).wait(1.8).to(np.zeros(6), 1.0)
    s.grips((0, C))
    add(s, "slow_turn", "Freeze, then turn the head slowly to the audience and stare, deadpan, saying nothing: "
        "after a bad joke, deadpan, awkward silence, really?, the slow turn.")

    s = Seq().to(pose(j2=0.04, j3=0.32, p=0.6, j6=0.15), 0.5)
    t0 = s.t
    s.wait(2.0).shake(t0, s.t, pose(j2=0.025, j3=0.06, j4=0.05), 3.0, ramp=0.33)
    s.to(pose(j1=0.05, j2=0.2, j3=0.0, p=-0.4, j6=-0.1), 0.6, hold=0.2)  # doubled over, still going
    t1 = s.t
    s.wait(1.6).shake(t1, s.t, pose(j2=0.015, j4=0.03, j6=0.03), 2.5, ramp=0.4).to(np.zeros(6), 0.8)
    s.grips((0, C), *flap(t0, s.t - 0.9, 3.0, 0.15, 0.75), (s.t, C))
    add(s, "laugh_hard", "Crack up: head back, bouncing, the mouth flapping with laughter, then doubled over, still "
        "laughing: laugh hard, crack up, roar with laughter, can't stop laughing, LOL.")

    for g in prop_gestures():
        out.append(g)

    s = Seq().wait(3.0)
    s.shake(0, 3.0, pose(j2=0.04, j3=0.08, j4=-0.12), 1.5).shake(0, 3.0, pose(j1=0.08), 0.5, ramp=0.5)
    add(s, "gallop", "Bounce up and down like riding a horse.")

    s = Seq().to(pose(j2=0.18, j3=0.4, p=0.6), 0.8, hold=0.1).to(pose(j2=0.35, j3=-0.05, p=0.2), 0.6)
    s.shake(s.t, s.t + 1.0, pose(j5=0.08, j6=0.08), 2.2).wait(1.0).to(np.zeros(6), 0.9)
    add(s, "roar", "Rise up tall, lunge at the audience and shake the head.")

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


def car_frame(h):
    """Grasp site rotation holding the toy car wheels-down, its long axis (site y) along the horizontal heading h."""
    h = np.array([h[0], h[1], 0.0]) / np.hypot(h[0], h[1])
    z = np.array([0.0, 0.0, -1.0])
    return np.column_stack([np.cross(h, z), h, z])


def ik_path(pts, frames, seed):
    """Left-arm joint deltas (from STAGE_LEFT) along Cartesian grasp-site points and rotations, by IK."""
    from make_duets import ik
    out = []
    for p, R in zip(pts, frames):
        q, e, r = ik("left", np.asarray(p, float), R, seed)
        assert e < 2e-3 and r < 0.02, (p, e, r)
        out.append(q - show.STAGE_LEFT); seed = q
    return np.array(out)


def prop_gestures():
    """Gestures with a handed-in toy (round 3b): the left arm's keys; the right arm mirrors."""
    S, out = show.STAGE_LEFT, []
    # drive: the toy car (show.PROP_SHAPES["car"]) rolls on the table: touch down gently, drive inward with two little
    # bumps, a U-turn toward the audience, drive back out, lift off. The wheels press 2 mm into the table.
    z0 = show.TABLE_Z + 0.028 - 0.002  # grasp site height with the wheels 2 mm into the table
    seed = S + pose(j2=0.3, j3=-0.38, p=-1.25)
    line = lambda a, b, n: [np.asarray(a) + (np.asarray(b) - np.asarray(a)) * u for u in np.linspace(0, 1, n)[1:]]
    bump = lambda y: 0.004 * sum(np.exp(-((y - c) / 0.012) ** 2) for c in (0.05, -0.02))
    ax = (0, 1)  # the car's long axis (site y); it drives along it either way (j6 can't turn the car around)
    hover = ik_path([(0.28, 0.10, z0 + 0.06)], [car_frame(ax)], seed)
    down = ik_path(line((0.28, 0.10, z0 + 0.06), (0.28, 0.10, z0), 4), [car_frame(ax)] * 3, S + hover[-1])
    ys = np.linspace(0.10, -0.07, 18)[1:]
    a = np.linspace(np.pi, 1.5 * np.pi, 9)[1:]  # then a left turn toward the audience around (0.32, -0.07), r 4 cm
    xs = np.linspace(0.32, 0.40, 9)[1:]  # and on toward the audience
    legs = [([(0.28, y, z0 + bump(y)) for y in ys], [car_frame(ax)] * len(ys), 2.4),
            ([(0.32 + 0.04 * np.cos(t), -0.07 + 0.04 * np.sin(t), z0) for t in a],
             [car_frame((np.sin(t), -np.cos(t))) for t in a], 2.3),
            ([(x, -0.11, z0) for x in xs], [car_frame((-1, 0))] * len(xs), 1.3)]
    s = Seq().to(hover[-1], 1.2).path(down, 2.0).wait(0.3)
    p0 = (0.28, 0.10, z0)
    for pts, rots, T in legs:  # each leg its own smooth start and stop: slows into the turn, like a careful driver
        q = ik_path(pts, rots, S + s.q)
        s.path(q, T, pts=[p0] + pts); p0 = pts[-1]
    up = ik_path(line(p0, (p0[0], p0[1], z0 + 0.06), 4), [car_frame((-1, 0))] * 3, S + s.q)
    s.wait(0.4).path(up, 1.0).to(np.zeros(6), 1.2)
    g = s.gesture("drive", "Holding a toy car, put it down on the table and drive it: vroom along over two bumps, turn "
                  "toward the audience, brake, lift it off: drive, race, park, a road trip.",
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
        return ik_path(pts, rots, seed), [tuple(p) for p in pts]

    seed = S + np.array([0, 0.89, 0.9, -1.68, 0, 0])
    # fly: a wobbly figure-eight in front, the saucer rocking and spinning a little as it goes
    u = np.linspace(0, 2 * np.pi, 41)
    f8 = [ufo((0.30 + 0.035 * np.sin(2 * t), 0.09 * np.sin(t), 0.14 + 0.015 * np.sin(3 * t)),
              tilt=0.08 + 0.04 * np.sin(2 * t), toward=3 * t, spin=0.4 * np.sin(t))
          for t in u]
    q8, p8 = fly(f8, seed)
    s = Seq().to(q8[0], 1.2).path(q8[1:], 9.0, pts=p8).to(np.zeros(6), 1.2)
    out.append(dict(s.gesture("ufo_fly", "Holding the UFO, fly it in a wobbly figure-eight, rocking and spinning: fly, "
                              "zoom, an alien ship, invade, cruise through space."), prop="ufo"))
    # hover: stop in front and pulse, bobbing and spinning slowly, as if beaming something up
    qh, _ = fly([ufo((0.30, 0.0, 0.15))], seed)
    s = Seq().to(qh[0], 1.2)
    t0 = s.t
    s.wait(3.0).shake(t0, s.t, pose(j2=0.015, j3=0.04, p=-0.0), 1.0, ramp=0.5).shake(t0, s.t, pose(j6=0.5), 0.33, ramp=0.6)
    s.to(np.zeros(6), 1.2)
    out.append(dict(s.gesture("ufo_hover", "The UFO stops in mid-air and hovers, pulsing up and down and slowly "
                              "turning: beam up, abduct, scan, hover, the aliens watch."), prop="ufo"))
    # land: a slow, wobbling descent, touch down on the table, sit, take off again
    zl = show.TABLE_Z + 0.042  # site height with the saucer just on the table (facing each other: ~3 mm in)
    desc = [ufo((0.30, 0.02, zl + 0.13 * (1 - v) ** 1.5), tilt=0.1 * (1 - v), toward=6 * v, spin=0.3 * v, lean=0.12 * (1 - v))
            for v in np.linspace(0, 1, 17)]
    qd, pd = fly(desc, seed)
    s = Seq().to(qd[0], 1.3).path(qd[1:], 5.0, pts=pd).wait(1.2)
    s.path(qd[::-1][1:], 2.0, pts=pd[::-1]).to(np.zeros(6), 1.2)
    out.append(dict(s.gesture("ufo_land", "The UFO comes down slowly, wobbling, lands on the table, sits a moment, "
                              "then takes off again: land, arrive, touch down, visit Earth.",
                              table=dict(min=-0.004, impact=0.05, prop=True)), prop="ufo"))
    # crash: a widening wobble and a spiral down that stops, tilted, well above the table; then a sorry climb back
    sp = [ufo((0.30 + 0.06 * (1 - v) * np.cos(9 * v), 0.06 * (1 - v) * np.sin(9 * v), 0.15 - 0.06 * v ** 2),
              tilt=0.05 + 0.3 * v, toward=9 * v, spin=0.5 * v, lean=0.12 + 0.1 * v) for v in np.linspace(0, 1, 31)]
    qc, pc = fly(sp, seed)
    s = Seq().to(qc[0], 1.2).path(qc[1:], 5.5, pts=pc, law=lambda x: x * x * (3 - 2 * x))
    s.wait(1.0).to(np.zeros(6), 2.0)
    out.append(dict(s.gesture("ufo_crash", "The UFO loses control: it wobbles harder and harder, spirals down and "
                              "crashes, tilted (stopping well above the table): crash, shot down, spin out, "
                              "out of fuel."), prop="ufo"))
    return out


if __name__ == "__main__":
    show.GESTURE_DIR.mkdir(exist_ok=True)
    for f in show.GESTURE_DIR.glob("*.json"):
        if not json.loads(f.read_text()).get("recorded"):
            f.unlink()  # regenerate; recorded gestures stay
    for g in library():
        (show.GESTURE_DIR / f"{g['name']}.json").write_text(json.dumps(g, separators=(",", ":")) + "\n")
        print(f"{g['name']:18s} {g['keys'][-1]['t']:.1f} s")
