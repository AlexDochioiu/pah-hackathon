"""Fit overhead pixel -> arm frame from the teach.py touches; writes ~/dial/calib.json for pick.py.

ponytail: one-off. Nov 8 (cal4): flat stickers touched by both arms in teach mode, matched by hand from
snaps/overhead_cal4.jpg and teach_cal4.npz (Nov 7 cal3 blocks are in git history). Redo the touches and edit PAIRS if the cameras or arms move.
A pair with no pixel only feeds the right-arm offset (sticker the camera can't see).
"""
import json, pathlib
import cv2, numpy as np

DIR = pathlib.Path.home() / "dial"

# (snapshot, approx pixel, {arm: touch xyz in that arm's frame})
# Round 3 (Nov 7 evening, after the camera was moved): 5 blocks, both arms touched each. Matched to blocks by the
# best rigid fit (image v flipped). Older rounds: git history.
PAIRS = [
    ("snaps/overhead_cal4.jpg", (183, 79), {'left': (0.466, -0.023, 0.029), 'right': (0.471, 0.575, 0.041)}),  # pink
    ("snaps/overhead_cal4.jpg", (182, 195), {'left': (0.215, -0.057, -0.004), 'right': (0.225, 0.551, 0.014)}),
    ("snaps/overhead_cal4.jpg", (275, 218), {'left': (0.17, -0.236, -0.006), 'right': (0.174, 0.383, 0.001)}),
    ("snaps/overhead_cal4.jpg", (241, 143), {'left': (0.323, -0.169, 0.009), 'right': (0.322, 0.443, 0.017)}),  # right touched it twice
    ("snaps/overhead_cal4.jpg", (293, 101), {'left': (0.41, -0.262, 0.023), 'right': (0.41, 0.342, 0.026)}),
    ("snaps/overhead_cal4.jpg", (358, 104), {'left': (0.403, -0.389, 0.028)}),
    ("snaps/overhead_cal4.jpg", (377, 178), {'left': (0.248, -0.421, 0.006)}),
    ("snaps/overhead_cal4.jpg", None, {'left': (0.445, -0.532, 0.034), 'right': (0.431, 0.065, 0.025)}),  # by the right arm, not visible
]


def centre(img, u, v):
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    w = hsv[v - 8:v + 9, u - 8:u + 9, 2].astype(float)  # stickers are a few px: bright-spot centroid near the hand pick
    w = np.clip(w - np.median(w) - 0.5 * (w.max() - np.median(w)), 0, None)
    assert w.sum() > 0, f"no sticker near {u},{v}"
    ys, xs = np.mgrid[-8:9, -8:9]
    return np.array([u + (xs * w).sum() / w.sum(), v + (ys * w).sum() / w.sum()])


shared = [p for p in PAIRS if len(p[2]) == 2]
offset = np.mean([np.subtract(t["right"][:2], t["left"][:2]) for _, _, t in shared], axis=0)
px, xy = [], []
for snap, uv, touch in PAIRS:
    if uv is None:
        continue
    u, v = uv
    c = centre(cv2.imread(str(DIR / snap)), u, v)
    pts = [np.asarray(t[:2]) - (offset if arm == "right" else 0) for arm, t in touch.items()]
    px.append(c); xy.append(np.mean(pts, axis=0))
px, xy = np.float32(px), np.float32(xy)
H, _ = cv2.findHomography(px, xy, 0)
proj = cv2.perspectiveTransform(px[None], H)[0]
res = np.linalg.norm(proj - xy, axis=1)
for (snap, uv, _), c, r in zip([p for p in PAIRS if p[1]], px, res):
    print(f"px {np.round(c, 1).tolist()} residual {r * 1000:.1f} mm")
# ponytail: flat stickers give table z, not block-top z, and they read 1-2 cm above the cal3 block tops minus BLOCK_H
# (FK sag grows with reach); keep the cal3 block_top_z the gestures were checked against until a block round redoes it.
top = json.loads((DIR / "calib_cal3.json").read_text())["block_top_z"]
print("right offset", np.round(offset, 4).tolist(), "block top z", top, f"max residual {res.max() * 1000:.1f} mm")
(DIR / "calib.json").write_text(json.dumps({"H": H.tolist(), "right_offset": offset.tolist(), "block_top_z": top}, indent=1))
