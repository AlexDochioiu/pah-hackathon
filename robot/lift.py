"""Lift both YAM arms ~38 cm, clap grippers, sway bases together, wave wrists, lower back to rest.

Same safety as wiggle.py: station lock, refuses unless at rest, always returns to the start pose before switching off.
Run: cd ~/physical-ai-hacks && .venv/bin/python ~/dial/lift.py
"""
import pathlib, sys, time
import numpy as np, yaml
from dream_hackathon import arms, config, station_lock

REST_TOL = 0.25  # ponytail: rad from dreamcli home that counts as "resting"; calibration knob
station = config.load("configs/station.yaml")
d = yaml.safe_load((pathlib.Path.home() / ".dreamlabs/station.yaml").read_text())
HOME = {"left": d["follower_l"]["home"], "right": d["follower_r"]["home"]}

lock = station_lock.acquire("dial-lift")
a = arms.open_arms(station, energize=True)
try:
    j0, g0 = a.read()
    far = {s: float(np.max(np.abs(j0[s] - np.asarray(HOME[s])))) for s in j0}
    if any(v > REST_TOL for v in far.values()):
        sys.exit(f"not at rest (max rad off home {far})")

    def go(delta, grip, seconds, hz=50):
        """Smoothstep from the current pose to start + delta (6 joints), gripper to `grip`."""
        js, gs = a.read()
        tj = {s: j0[s] + np.asarray(delta) for s in js}
        for k in range(1, int(seconds * hz) + 1):
            t = k / (seconds * hz); t = t * t * (3 - 2 * t)
            a.command({s: js[s] + t * (tj[s] - js[s]) for s in js}, {s: gs[s] + t * (grip - gs[s]) for s in gs})
            time.sleep(1 / hz)
        jr, _ = a.read()
        print(f"  reached, worst joint error {max(float(np.max(np.abs(jr[s] - tj[s]))) for s in jr):.3f} rad")

    UP = [0, 0.8, 1.2, 0, 0, 0]
    up = lambda **k: [UP[i] + k.get(f"j{i+1}", 0) for i in range(6)]
    print("lift");  go(UP, 1.0, 3.0)
    print("clap")
    for _ in range(2):
        go(UP, 0.0, 0.6); go(UP, 1.0, 0.6)
    print("sway")
    go(up(j1=0.35), 1.0, 1.5); go(up(j1=-0.35), 1.0, 2.5); go(UP, 1.0, 1.5)
    print("wave")
    for _ in range(2):
        go(up(j6=0.6), 1.0, 0.7); go(up(j6=-0.6), 1.0, 1.0)
    go(UP, 1.0, 0.7)
    print("lower"); go([0] * 6, g0["left"], 4.0)
    time.sleep(0.5)
    print("done, back at start")
finally:
    jr, _ = a.read()
    print("final off-start", {s: round(float(np.max(np.abs(jr[s] - j0[s]))), 3) for s in jr})
    a.close()
    lock.close()
