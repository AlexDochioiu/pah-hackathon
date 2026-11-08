"""Fit overhead pixel -> arm frame from the teach.py touches; writes ~/dial/calib.json for pick.py.

ponytail: one-off for the Nov 7 calibration; the block<->touch pairs below were matched by hand from the
snaps/overhead_cal3.jpg snapshot and teach_cal3_round*.npz logs. Redo the touches and edit PAIRS if the cameras or arms move.
"""
import json, pathlib
import cv2, numpy as np

DIR = pathlib.Path.home() / "dial"

# (snapshot, approx pixel, {arm: touch xyz in that arm's frame})
# Round 3 (Nov 7 evening, after the camera was moved): 5 blocks, both arms touched each. Matched to blocks by the
# best rigid fit (image v flipped). Older rounds: git history.
PAIRS = [
    ("snaps/overhead_cal3.jpg", (189, 151), {'left': (0.3045, -0.0613, 0.0219), 'right': (0.3123, 0.545, 0.0341)}),  # yellow
    ("snaps/overhead_cal3.jpg", (427, 152), {'left': (0.3021, -0.5152, 0.0309), 'right': (0.2902, 0.089, 0.0218)}),  # orange right
    ("snaps/overhead_cal3.jpg", (264, 99), {'left': (0.4133, -0.2005, 0.037), 'right': (0.415, 0.397, 0.0424)}),  # orange left
    ("snaps/overhead_cal3.jpg", (312, 198), {'left': (0.2142, -0.3003, 0.0151), 'right': (0.2025, 0.3148, 0.0162)}),  # green
    ("snaps/overhead_cal3.jpg", (361, 42), {'left': (0.5553, -0.3907, 0.0671), 'right': (0.5412, 0.1952, 0.0606)}),  # red
]


def centre(img, u, v):
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = ((hsv[..., 1] > 90) & (hsv[..., 2] > 70)).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    n, lab, stats, cen = cv2.connectedComponentsWithStats(mask)
    d = [np.hypot(*(cen[i] - (u, v))) if stats[i, cv2.CC_STAT_AREA] > 60 else 1e9 for i in range(1, n)]
    i = int(np.argmin(d)) + 1
    assert d[i - 1] < 20, f"no blob near {u},{v}"
    return cen[i]


shared = [p for p in PAIRS if len(p[2]) == 2]
offset = np.mean([np.subtract(t["right"][:2], t["left"][:2]) for _, _, t in shared], axis=0)
px, xy = [], []
for snap, (u, v), touch in PAIRS:
    c = centre(cv2.imread(str(DIR / snap)), u, v)
    pts = [np.asarray(t[:2]) - (offset if arm == "right" else 0) for arm, t in touch.items()]
    px.append(c); xy.append(np.mean(pts, axis=0))
px, xy = np.float32(px), np.float32(xy)
H, _ = cv2.findHomography(px, xy, 0)
proj = cv2.perspectiveTransform(px[None], H)[0]
res = np.linalg.norm(proj - xy, axis=1)
for (snap, uv, _), c, r in zip(PAIRS, px, res):
    print(f"px {np.round(c, 1).tolist()} residual {r * 1000:.1f} mm")
top = {arm: float(np.median([t[arm][2] for *_, t in PAIRS if arm in t and t[arm][2] < 0.03])) for arm in ("left", "right")}
print("right offset", np.round(offset, 4).tolist(), "block top z", top, f"max residual {res.max() * 1000:.1f} mm")
(DIR / "calib.json").write_text(json.dumps({"H": H.tolist(), "right_offset": offset.tolist(), "block_top_z": top}, indent=1))
