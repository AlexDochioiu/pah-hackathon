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

    def to(self, q, want=0.6, hold=0.0):
        T = move_time(self.q, q, want)
        self.segs.append((self.t, T, self.q.copy(), q.copy()))
        self.t += T; self.q = q.copy()
        if hold:
            self.segs.append((self.t, hold, q.copy(), q.copy())); self.t += hold
        return self

    def wait(self, s):
        return self.to(self.q, s)

    def shake(self, t0, t1, amp, hz, ramp=None):
        """Adds amp (6,) * sin(2 pi hz t) over [t0, t1], faded in/out over `ramp` s (default one period, 0 = none).
        Amplitudes are capped so the shake alone uses at most ~60 % of the speed and acceleration limits."""
        w = 2 * np.pi * hz
        cap = 0.6 * np.minimum(show.VMAX / w, show.AMAX / w**2)
        self.osc.append((t0, t1, np.clip(np.asarray(amp, float), -cap, cap), hz, 1 / hz if ramp is None else ramp))
        return self

    def grips(self, *tg):
        self.grip += list(tg)
        return self

    def at(self, t):
        q = self.q.copy()
        for t0, T, a, b in self.segs:
            if t0 <= t <= t0 + T:
                q = a + quintic((t - t0) / T) * (b - a); break
        for t0, t1, amp, hz, ramp in self.osc:
            if t0 <= t <= t1:
                env = min(1, (t - t0) / ramp, (t1 - t) / ramp) if ramp else 1
                q = q + amp * np.sin(2 * np.pi * hz * (t - t0)) * quintic(env)
        return q

    def gesture(self, name, about, arms="one", hold=False, loop=False):
        n = int(round(self.t / DT))
        ts = np.linspace(0, self.t, n + 1)
        keys = [{"t": round(float(t), 3), "q": [round(float(x), 4) for x in self.at(t)]} for t in ts]
        if self.grip:
            gt = np.array([a for a, _ in self.grip]); gv = np.array([b for _, b in self.grip])
            for k in keys:
                k["g"] = round(float(np.interp(k["t"], gt, gv)), 3)
        return {"name": name, "about": about, "arms": arms, "hold": hold, "loop": loop, "keys": keys}


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

    up = pose(j2=-0.15, j3=0.35, p=0.35)
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

    add(Seq().to(pose(j1=0.3, j2=-0.1, j3=0.35, p=0.9, j6=0.4), 0.8, hold=0.15)
        .to(pose(j1=-0.12, j2=0.3, j3=-0.15, p=-0.45, j6=-0.3), 0.5, hold=0.35).to(np.zeros(6), 1.0),
        "slash", "Wind up high and outside, chop down and across toward the front.")

    add(Seq().to(pose(j2=-0.2, j3=0.15, p=0.1), 0.6, hold=0.15)
        .to(pose(j1=-0.08, j2=0.4, j3=-0.1, p=0.0), 0.5, hold=0.4).to(np.zeros(6), 0.9),
        "thrust", "Draw back, then stab straight forward.")

    add(Seq().to(pose(j1=-0.1, j2=-0.05, j3=0.25, p=1.1, j6=0.8), 0.6, hold=1.2).to(np.zeros(6), 0.8),
        "block", "Raise the prop upright in front like a guard, hold.")

    hi = pose(j2=-0.15, j3=0.45, p=1.0)
    s = Seq().to(hi, 0.7)
    s.shake(s.t, s.t + 2.4, pose(j3=0.07, j4=-0.08, j6=0.3), 0.75).wait(2.4).to(np.zeros(6), 0.8)
    add(s, "celebrate", "Thrust the prop up high and wiggle it in triumph.", arms="both")

    low = pose(j1=0.2, j2=-0.3, j3=-0.35, p=-0.6)
    s = Seq().to(low, 0.8)
    s.shake(s.t, s.t + 2.0, pose(j5=0.03, j6=0.03), 3.0).wait(2.0).to(np.zeros(6), 1.0)
    add(s, "cower", "Shrink back and away from the other arm, head down, trembling.")

    s = Seq().wait(2.5)
    s.shake(0, 2.5, pose(j1=0.12, j4=0.15, j6=0.2), 0.8)
    s.to(pose(j1=0.25, j2=-0.4, j3=-0.5, p=-0.8, j6=0.6), 1.4)
    add(s, "die_dramatically", "Stagger, then slump backwards and stay slumped (play stage or rest after).", hold=True)

    s = Seq().wait(3.0)
    s.shake(0, 3.0, pose(j2=0.04, j3=0.08, j4=-0.12), 1.5).shake(0, 3.0, pose(j1=0.08), 0.5, ramp=0.5)
    add(s, "gallop", "Bounce up and down like riding a horse.")

    s = Seq().to(pose(j2=-0.2, j3=0.3, p=0.6), 0.8, hold=0.1).to(pose(j2=0.35, j3=-0.05, p=0.2), 0.6)
    s.shake(s.t, s.t + 1.0, pose(j5=0.08, j6=0.08), 2.2).wait(1.0).to(np.zeros(6), 0.9)
    add(s, "roar", "Rear back, lunge at the audience and shake the head.")

    crouch = pose(j2=-0.25, j3=-0.35, p=-0.2)
    s = Seq().to(crouch + pose(j1=0.25), 1.0)
    t0 = s.t
    s.to(crouch + pose(j1=-0.12), 2.6)
    s.shake(t0, s.t, pose(j4=0.06), 1.2).to(np.zeros(6), 1.0)
    add(s, "sneak", "Crouch low and creep across, head bobbing.")

    add(Seq().to(pose(j1=-0.25, j2=-0.2, j3=0.1, p=0.0, j5=0.35, j6=0.3), 0.8, hold=1.2).to(np.zeros(6), 0.8),
        "look_at_other", "Turn the head toward the other arm, tilt, hold.", arms="both")

    add(Seq().to(pose(j2=0.35, j3=0.05, p=0.0), 0.8, hold=1.2).to(np.zeros(6), 0.8),
        "point_at_audience", "Reach forward and point the prop at the audience.")

    s = Seq().wait(2.4)
    s.shake(0, 2.4, pose(j4=0.06), 1.25)
    s.grips(*[(0.2 + 0.33 * i, 1.0 if i % 2 == 0 else 0.0) for i in range(7)], (2.4, 1.0))
    add(s, "gripper_clap", "Snap the gripper open and shut three times (drops a held prop!).")
    return out


if __name__ == "__main__":
    show.GESTURE_DIR.mkdir(exist_ok=True)
    for f in show.GESTURE_DIR.glob("*.json"):
        if not json.loads(f.read_text()).get("recorded"):
            f.unlink()  # regenerate; recorded gestures stay
    for g in library():
        (show.GESTURE_DIR / f"{g['name']}.json").write_text(json.dumps(g, separators=(",", ":")) + "\n")
        print(f"{g['name']:18s} {g['keys'][-1]['t']:.1f} s")
