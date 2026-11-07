"""Fit overhead pixel -> arm frame from the teach.py touches; writes ~/dial/calib.json for pick.py.

ponytail: one-off for the Nov 7 calibration; the block<->touch pairs below were matched by hand from the
teach_*.jpg snapshots and teach_round*.npz logs. Redo the touches and edit PAIRS if the cameras or arms move.
"""
import json, pathlib
import cv2, numpy as np

DIR = pathlib.Path.home() / "dial"

# (snapshot, approx pixel, {arm: touch xyz in that arm's frame})
PAIRS = [
    ("teach_overhead.jpg", (249, 148), {"left": (0.3525, -0.059, 0.006)}),                                # orange
    ("teach_overhead.jpg", (289, 157), {"left": (0.341, -0.151, 0.012), "right": (0.369, 0.451, 0.020)}),  # yellow
    ("teach_overhead.jpg", (341, 151), {"left": (0.3665, -0.256, 0.018), "right": (0.370, 0.345, 0.025)}),  # cactus
    ("teach_overhead.jpg", (396, 156), {"left": (0.372, -0.375, 0.019), "right": (0.370, 0.228, 0.020)}),  # pink
    ("teach_overhead2.jpg", (443, 185), {"left": (0.316, -0.467, 0.014), "right": (0.309, 0.135, 0.011)}),  # green, knocked
    ("teach2_overhead.jpg", (282, 210), {"left": (0.2215, -0.159, 0.008)}),                               # yellow moved
    ("teach2_overhead.jpg", (398, 96), {"right": (0.516, 0.223, 0.048)}),                                 # pink moved
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
