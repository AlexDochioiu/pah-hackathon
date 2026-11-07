"""Smallest visible move: grippers open and back, wrist roll +0.4 rad and back, on both YAM arms.

Refuses unless both arms rest at the station's home pose (they switch off and go limp at the end).
Run: cd ~/physical-ai-hacks && .venv/bin/python ~/dial/wiggle.py
"""
import sys, time
import numpy as np, pathlib, yaml
from dream_hackathon import arms, config, station_lock

REST_TOL = 0.25   # ponytail: rad from station home that still counts as "resting"; calibration knob
ROLL = 0.4        # rad of wrist roll (joint6)
STEP_S = 2.0      # seconds per leg, slow on purpose

station = config.load("configs/station.yaml")
d = yaml.safe_load((pathlib.Path.home() / ".dreamlabs/station.yaml").read_text())
HOME = {"left": d["follower_l"]["home"], "right": d["follower_r"]["home"]}
lock = station_lock.acquire("dial-wiggle")
a = arms.open_arms(station, energize=True)
try:
    j0, g0 = a.read()
    print("start joints", {s: np.round(v, 3).tolist() for s, v in j0.items()}, "grips", g0)
    far = {s: float(np.max(np.abs(j0[s] - np.asarray(HOME[s])))) for s in j0}
    if any(d > REST_TOL for d in far.values()):
        sys.exit(f"not at rest (max rad off home {far}); put the arms in their rest pose first")

    def go(joints, grips, seconds=STEP_S, hz=50):
        js, gs = a.read()
        for k in range(1, int(seconds * hz) + 1):
            t = k / (seconds * hz)
            a.command({s: js[s] + t * (joints[s] - js[s]) for s in js},
                      {s: gs[s] + t * (grips[s] - gs[s]) for s in gs})
            time.sleep(1 / hz)

    go(j0, {s: 1.0 for s in g0})                                       # open grippers
    go(j0, g0)                                                         # back
    go({s: j0[s] + np.eye(6)[5] * ROLL for s in j0}, g0)               # roll wrists
    go(j0, g0)                                                         # back
    time.sleep(0.5)
    print("done, back at start")
finally:
    a.close()
    lock.close()
