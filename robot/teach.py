"""Float both arms (gravity compensation) and log each gripper tip's position, to calibrate camera pixels to arm space.

You hold an arm by its gripper, touch the top centre of a block, hold still ~2 s, move to the next.
Stationary touches are printed per arm and saved to ~/dial/teach.npz. Fold both arms to rest before it ends.
Run: cd ~/physical-ai-hacks && .venv/bin/python ~/dial/teach.py [seconds]
"""
import pathlib, sys, time
import numpy as np
from dream_hackathon import arms, config, station_lock
from dream_hackathon.arms import yam

SECS = float(sys.argv[1]) if len(sys.argv) > 1 else 90
station = config.load("configs/station.yaml")
iks = yam.teleop_ik(station)
lock = station_lock.acquire("dial-teach")
a = arms.open_arms(station, energize=False)  # floats: movable by hand
log = {"left": [], "right": []}
jlog = {"left": [], "right": []}  # joints too: a taught pose (e.g. beside the e-stop) becomes a gesture keyframe
try:
    t0 = time.time()
    while time.time() - t0 < SECS:
        js, gs = a.read()
        for s in js:
            log[s].append([time.time() - t0, *iks[s].fk(js[s])[:3, 3]])
            jlog[s].append([*js[s], gs[s]])
        time.sleep(0.05)
except KeyboardInterrupt:  # Ctrl-C / kill -INT ends it early and still saves (Nov 8: it lost a whole round)
    pass
finally:
    try:
        a.close()
    finally:
        lock.close()

out = {}
for s, rows in log.items():
    p = np.asarray(rows)
    still, touches, start = [], [], None
    for i in range(1, len(p)):
        v = np.linalg.norm(p[i, 1:] - p[i - 1, 1:]) / max(p[i, 0] - p[i - 1, 0], 1e-3)
        if v < 0.01:  # ponytail: <1 cm/s counts as holding still
            start = i if start is None else start
        else:
            if start is not None and p[i - 1, 0] - p[start, 0] > 1.5:
                touches.append(p[start:i, 1:].mean(axis=0))
            start = None
    rest = p[0, 1:]
    touches = [t for t in touches if np.linalg.norm(t - rest) > 0.05]  # drop holds at the rest pose
    out[s] = np.asarray(touches)
    print(s, "touches (m, arm frame):", np.round(out[s], 3).tolist())
np.savez(pathlib.Path.home() / "dial/teach.npz", **out, **{f"raw_{s}": np.asarray(r) for s, r in log.items()},
         **{f"joints_{s}": np.asarray(r) for s, r in jlog.items()})
