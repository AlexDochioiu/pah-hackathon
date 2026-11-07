"""Talking mouth: each arm is a puppet head (the wrist camera's two lenses are its eyes), so the gripper is its mouth.

Mouth.step() runs once per 50 Hz tick of the stage server and returns the gripper to command plus small head offsets
(j4 lifts a little as the mouth opens, j6 drifts into slow tilts), added on top of whatever the arm plays or idles.
Syllables are irregular, 3-5.5 Hz with varied openings and short breaths between phrases. The gripper never moves
faster than show.GRIP_VMAX, so the real motor can follow; at 4 Hz that caps a syllable at ~45 % open.
Run it to print a few seconds of mouth and check the rates.
"""
import numpy as np
import show

HZ = show.HZ
CLOSED, OPEN = 0.05, 0.8      # gripper 0..1: mouth shut, widest syllable target
BOB_J4, TILT_J6 = 0.05, 0.06  # rad: head lift at a full syllable, head tilt amplitude
BOB_TAU = 0.08                # s: the head bob follows the mouth through this low-pass (keeps j4 accel low)
FADE = 0.25                   # s to fade the head motion in and out


class Mouth:
    def __init__(self, seed=None):
        self.rng = np.random.default_rng(seed)
        self.on = False
        self.g = None          # commanded gripper while the mouth owns it (None: the plan's grip goes through)
        self.level = 0.0       # 0..1 fade of the head motion
        self.bob, self.tilt, self.tilt_to = 0.0, 0.0, 0.0
        self.todo = []         # remaining target openings of the current phrase

    def _phrase(self):
        out = []
        for _ in range(int(self.rng.integers(3, 9))):
            n = max(2, int(self.rng.uniform(0.18, 0.32) * HZ))  # one syllable: 3-5.5 Hz
            a = self.rng.uniform(0.3, 1.0)
            out += list(CLOSED + a * (OPEN - CLOSED) * np.sin(np.pi * np.arange(n) / n))
        return out + [CLOSED] * int(self.rng.uniform(0.08, 0.3) * HZ)  # a breath between phrases

    def step(self, g_plan):
        """One tick: (gripper to command, joint offsets (6,))."""
        dl = 1.0 / (FADE * HZ)
        self.level = min(1.0, self.level + dl) if self.on else max(0.0, self.level - dl)
        if self.g is None:
            if not self.on:
                return g_plan, np.zeros(6)
            self.g = g_plan
        if self.on:
            if not self.todo:
                self.todo, self.tilt_to = self._phrase(), self.rng.uniform(-1, 1)
            want = self.todo.pop(0)
        else:
            want, self.todo = g_plan, []
        self.g += float(np.clip(want - self.g, -show.GRIP_VMAX / HZ, show.GRIP_VMAX / HZ))
        self.bob += (max(0.0, self.g - CLOSED) / (OPEN - CLOSED) - self.bob) / (BOB_TAU * HZ)
        self.tilt += float(np.clip(self.tilt_to - self.tilt, -0.5 / HZ, 0.5 / HZ))
        dq = np.zeros(6)
        dq[3], dq[5] = BOB_J4 * self.bob * self.level, TILT_J6 * self.tilt * self.level
        if not self.on and self.level == 0 and abs(self.g - g_plan) < 1e-3:
            self.g = None  # back to the plan's grip
        return (g_plan if self.g is None else self.g), dq


if __name__ == "__main__":
    m = Mouth(seed=1)
    m.on = True
    G, D = zip(*[m.step(0.0) for _ in range(5 * HZ)])
    m.on = False
    G2, D2 = zip(*[m.step(0.0) for _ in range(HZ)])
    G, D = np.array(G + G2), np.array(D + D2)
    v, a = show.peak_rates(D)
    print("grip range", G.min().round(2), G.max().round(2), "peak grip rate", (np.abs(np.diff(G)).max() * HZ).round(2),
          "head v", v[[3, 5]].round(2), "a", a[[3, 5]].round(1))
    assert np.abs(np.diff(G)).max() * HZ <= show.GRIP_VMAX + 1e-9 and G.max() - G.min() > 0.3
    assert np.all(v <= 0.5) and np.all(a <= show.AMAX / 2) and abs(G[-1]) < 1e-3 and np.abs(D[-1]).max() < 1e-3
    syll = (np.diff(np.sign(np.diff(G[: 5 * HZ]))) < 0).sum() / 5
    print(f"~{syll:.1f} syllables/s")
