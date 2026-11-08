"""A hand-taught pick (robot/teach.py's ~/dial/teach.npz) as Dream-1-style chunks for `dream_run.py --replay`:
the show's Dream-1 act without Dream Labs (owner, Nov 8). The arm that moved most is the picker. Its gripper
is made up: open from the start, closed over 0.5 s once the fingertips are at their lowest, closed afterwards.
The motion ends at its highest point after the grasp (the lift); dream_run waves the wand there and plays it
back to put it down.
Run: python3 robot/taught_pick.py teach.npz robot/lars/pen_pick.json
"""
import json
import sys

import numpy as np

HZ = 20.0  # dream_preview's rows per second (HZ_OUT)
ROWS = 48  # one Dream-1 chunk
OPEN, SHUT, REST_G = 1.0, 0.0, 0.05


def chunks(npz):
    d = np.load(npz)
    moved = {s: float(np.abs(np.diff(d[f"joints_{s}"][:, :6], axis=0)).sum()) for s in ("left", "right")}
    arm = max(moved, key=moved.get)
    other = "left" if arm == "right" else "right"
    J, P = d[f"joints_{arm}"][:, :6], d[f"raw_{arm}"]  # P: [t, x, y, z] of the tip, same rows as J
    t = P[:, 0]
    tt = np.arange(t[0], t[-1], 1.0 / HZ)  # resample to 20 Hz
    Q = np.stack([np.interp(tt, t, J[:, j]) for j in range(6)], 1)
    z = np.interp(tt, t, P[:, 3])
    step = np.r_[0.0, np.abs(np.diff(Q, axis=0)).max(1)]
    first = int(np.argmax(step > 0.002))  # the first motion
    low = first + int(np.argmin(z[first:]))  # the grasp: fingertips at their lowest
    top = low + int(np.argmax(z[low:]))  # the lift: highest after it
    Q, z = Q[first:top + 1], z[first:top + 1]
    low -= first
    n = len(Q)
    i = np.arange(n)
    G = np.where(i < low, OPEN, np.clip(OPEN - (i - low) / (0.5 * HZ), SHUT, OPEN))
    G[: min(n, int(0.6 * HZ))] = np.linspace(REST_G, OPEN, min(n, int(0.6 * HZ)))  # open on the way, not in a snap
    Qo = d[f"joints_{other}"][0, :6]
    out = []
    for k, a in enumerate(range(0, n, ROWS)):
        q, g = Q[a:a + ROWS], G[a:a + ROWS]
        out.append({"event": "chunk", "n": k + 1, "latency_s": 0.0, "t": [round(r / HZ, 4) for r in range(len(q))],
                    arm: {"q": np.round(q, 5).tolist(), "g": np.round(g, 4).tolist()},
                    other: {"q": [np.round(Qo, 5).tolist()] * len(q), "g": [REST_G] * len(q)}})
    info = dict(arm=arm, seconds=round(n / HZ, 1), grasp_s=round(low / HZ, 1), lift_mm=round(float(z[-1] - z[low]) * 1000),
                low_mm=round(float(z[low]) * 1000), chunks=len(out))
    return out, info


if __name__ == "__main__":
    out, info = chunks(sys.argv[1])
    assert info["lift_mm"] >= 30, f"the lift is only {info['lift_mm']} mm: lift it higher after the grasp ({info})"
    json.dump(out, open(sys.argv[2], "w"))
    print(info)
