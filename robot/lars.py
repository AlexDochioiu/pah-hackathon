"""Lars the frog (green plush doll, ~9 cm): find him in the overhead camera, pick him up, present him HEAD UP, put him
back down (or somewhere new).

  lars.py [--dry] [--image PATH] [--hint U,V] [--head U,V] [--place U,V | --random-place] [--face DEG] [--no-gif]
  lars.py --selftest
  lars.py --bake [--speed 0.25..1.0] [--dock-z M] [--face-up]
  lars.py --go (--estop-attended | --unattended) [--yes] [--sim] [--place U,V | --random-place] [--repeat N]

--bake: plans the pick once from his fixed dock (DOCK_RIGHT, right-arm frame; no camera) and writes
gestures/pick_lars.json and place_lars.json for the stage server (/play, checked by show.Checker): reach, grip, lift,
hold head up; and back. He lies face-down there and is held facing the audience; --face-up: on his back, in profile.
--speed scales the whole timing (default 0.3; 1.0 = ~5 s each); restart the stage server to load a new bake.
Live planning (--dry / --go) is the fallback.

--dry (default): snaps the overhead camera (or reads --image), detects Lars by colour and shape (outline, body axis,
head end), maps him to the arm frame with calib.json, plans every waypoint with the kit's IK in simulation and checks
the whole 50 Hz trajectory: joint limits/speed/accel (show.motion_problems), table clearance with the doll, the
lean-back COM rule (show.Checker), 15 cm from the other arm (at rest and at stage). Prints the plan and writes
lars/lars_annotated.jpg and previews/lars.gif. Nothing moves.
--selftest: detection on the real frame and on rotated/flipped synthetic variants (asserts), then a dry plan.
--go: plays exactly the checked trajectory on the arms. Only with a human at the e-stop (AGENTS.md): it refuses
without --estop-attended, while stage.py runs, or unless both arms are at rest. Each run gets an episode folder
lars_runs/<time>/ (log, before/after frames, cameras at ~10 Hz, joints+gripper command and measured at 50 Hz).
--sim runs the same executor on i2rt's MuJoCo sim (no station lock, no CAN).

Frames: each arm's own base frame (x toward the audience, z up); pixels are 640x360 overhead frames.
"""
import argparse, json, math, os, pathlib, sys, threading, time
os.environ.setdefault("MUJOCO_GL", "cgl" if sys.platform == "darwin" else "egl")
import cv2, mujoco, numpy as np
import show

DIR = pathlib.Path(__file__).resolve().parent
OUT_IMG = DIR / "lars" / "lars_annotated.jpg"
OUT_GIF = DIR / "previews" / "lars.gif"
RUNS = DIR / "lars_runs"

# ---- calibration knobs (ponytail: first guesses from one overhead frame, untested on the real doll) ----
DOLL_TOP = 0.035        # m, Lars lying on his back: belly top above the table
# m, Lars face-down (on his belly, the default dock): his back above the table. An estimate from the doll model
# below, not measured: lying on the belly bump and the eyes his axis sits ~2.1 cm up (1.8 on his back), plus the 1.8 cm
# body radius = 3.9 cm; rounded up because it only sets the hover height (higher is safer). Measure it and set it.
BACK_TOP = 0.040
BELLY_AXIS_Z = 0.021    # m, his body axis above the table face-down (model; the preview draws him there)
GRIP_Z = 0.016          # m, grasp-site height above the table when the fingers close (grip depth). Both ways up: face
                        # down the axis sits ~3 mm higher, so the fingers close ~5 mm under it instead of ~2 (a deeper wrap)
GRIP_FROM_HEAD = 0.40   # where to grip, as a fraction of his length from the head tip: just below the head
GRIP_CLOSE = 0.0        # gripper command closed on the plush (0 = fully closed: squeezes it, it is soft)
GRIP_MARGIN = 0.03      # m the fingers open wider than his body at the grip line when coming down
GRIP_FULL = 0.095       # m finger opening at command 1.0 (2 x 47.5 mm)
HELD_MIN = 0.05         # gripper reading below this after closing = closed on air (a miss)
HEAD_UP_ROLL = 0.0      # rad, extra wrist roll about the pointing axis at the presentation (if he looks tilted)
DOLL_R, HEAD_R = 0.018, 0.021  # m, body capsule / head radius of the doll model (checks and preview)

FACES = (90, 60, 45, 120, 30, 0)  # deg his face turns from the audience toward the other arm, in order of preference
# face-down at the dock his face looks out past the fingertips once lifted, so the audience comes first
FACES_BELLY = (0, 10, -10, 20, -20, 30, -30, 45, 60, 90)
TILTS = (0, 20, 35, 50)  # deg the approach may lean along his body when straight down is out of reach (first that works)
HOVER = 0.10            # m above his top for approach/retreat
CARRY_R = 0.36          # m, the carry pose is at most this far from the base (horizontally)
CARRY_Z = 0.25          # m, grasp site height of the top-down "carry" pose before turning him head up
PRESENT_HOLD = 2.5      # s at the presentation pose
V_FAR, V_NEAR = 0.6, 0.3  # rad/s peak joint speed: away from / near the table
A_PEAK = 2.0            # rad/s^2 peak joint acceleration while executing
NEAR_CLEAR = 0.003      # m every link stays above the table during the grasp/place moves (fingers included)
XY_DRIFT = 0.015        # m the grasp site may drift sideways while going straight down/up
POS_TOL, ROT_TOL = 0.006, 0.08  # IK acceptance, as pick.py
TRACK_MAX = 0.2         # rad, measured vs commanded (arm joints) before --go freezes and folds back
REST_TOL = 0.25
POSE_MARGIN = 0.1       # rad, a held presentation pose stays this much further inside the checker's joint limits
HZ = show.HZ
BLOCK_H = 0.025
# ---- --bake: pick_lars / place_lars from the dock, as stage-server gestures
APPROACH = 0.05         # m above the grip height where the slow zone starts (show's table rule: <= 0.3 rad/s there)
LIFTED = 0.085          # m above the grip height he is lifted straight up to before turning: his feet clear the slow zone
V_FAST, A_FAST = 1.1, 4.0  # rad/s, rad/s^2 peak away from the table (the gesture library's 75 % of VMAX)
GRIP_SECS = 0.3         # s to close / open the fingers (show's GRIP_VMAX turns a full stroke in ~0.3 s)
GRIP_SETTLE = 0.1       # s still after closing, before lifting
# The dock, fixed on the table in the RIGHT arm's frame (m), the arm that picks him: where the real pick took Lars from
# and put him back on Nov 7 evening (left frame (0.43599, -0.33469) + the 11:30 right_offset (0.005625, 0.602)). It is
# not derived from any camera frame or calibration: a recalibration must not move the arm's target. The 18:45
# recalibration moved right_offset by 1.1 cm (the per-block offsets in calib.py spread +-1.5 cm), so the left-frame
# dock DOCK_XY (and the dock check's pixel, to_px(DOCK_XY)) follow calib.json instead. To move the dock: change these,
# rebake.
DOCK_RIGHT = (0.441615, 0.26731)         # his centre (midway head tip - feet), right-arm frame
DOCK_AXIS = (0.95459, -0.29792)          # unit, toward his head (-17 deg from +x = toward the audience); both frames
DOCK_LEN, DOCK_WIDTH = 0.11989, 0.04236  # m, his length, and his width at the grip line (from the same frame)
DOCK_Z = 0.0            # m, height of his dock surface above the table (0 = on the table; set it for the stand)
LARS_R = 0.025          # m, radius of the capsule the stage checks him as (body, arms and legs inside it)

cal = json.loads((DIR / "calib.json").read_text())
H = np.asarray(cal["H"])                  # overhead pixel -> left-arm frame (x, y)
HI = np.linalg.inv(H)
OFFSET = np.array([*cal["right_offset"], 0.0])  # right-arm frame = left-arm frame + OFFSET
TABLE_Z = {s: cal["block_top_z"][s] - BLOCK_H for s in show.SIDES}  # table, in each arm's frame
CALIB_PX = (189, 427, 42, 198)            # u0, u1, v0, v1: the area the homography was fitted on (trust it there)
DOCK_XY = tuple(np.asarray(DOCK_RIGHT) - OFFSET[:2])  # the dock in the left arm's frame, through the current calib


def to_left(u, v):
    p = np.c_[np.atleast_1d(u), np.atleast_1d(v), np.ones(np.size(u))] @ H.T
    return p[:, :2] / p[:, 2:]


def to_px(xy):
    xy = np.atleast_2d(xy)
    p = np.c_[xy[:, :2], np.ones(len(xy))] @ HI.T
    return p[:, :2] / p[:, 2:]


def arm_frame(xy_left, side):
    return np.asarray(xy_left)[..., :2] + (OFFSET[:2] if side == "right" else 0)


# ===================================================================== detection

GREEN_LO, GREEN_HI = (24, 50, 38), (62, 255, 255)  # OpenCV HSV; Lars is yellow-green (hue 28-50), the yellow block ~22
# his crown next to the green blob: hue 19-26 on the Nov 7 frames (face down 66-101 px, face up 34 px, all at one end)
CROWN_H, CROWN_S, CROWN_V, CROWN_N = (14, 27), 90, 70, (8, 220)  # hue range, min S, min V, pixel count (more = not a crown)


def detect(img, hint=None, head_hint=None):
    """Find Lars. Returns a dict: contour (px), centre/head/feet/grip (px and left-arm xy), axis (unit, left frame,
    toward the head), length, width at the grip line, and how the head end was decided. None if not found."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    Hh, S, V = hsv[..., 0].astype(int), hsv[..., 1].astype(int), hsv[..., 2].astype(int)
    green = cv2.morphologyEx(cv2.inRange(hsv, GREEN_LO, GREEN_HI), cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, lab, st, cen = cv2.connectedComponentsWithStats(green)
    cands, notes = [], []
    for i in range(1, n):
        if st[i, cv2.CC_STAT_AREA] < 80:
            continue
        ys, xs = np.nonzero(lab == i)
        P = to_left(xs, ys)
        c = P.mean(0)
        ev, evec = np.linalg.eigh(np.cov((P - c).T))
        ax = evec[:, 1]
        L, W = np.ptp((P - c) @ ax), np.ptp((P - c) @ [-ax[1], ax[0]])
        (_, _), (rw, rh), _ = cv2.minAreaRect(np.c_[xs, ys].astype(np.float32))
        fill = len(xs) / max(rw * rh, 1.0)
        ok = 0.05 <= L <= 0.16 and 0.02 <= W <= 0.09 and fill < 0.88 and L / W >= 1.2
        notes.append(f"green blob at px ({cen[i][0]:.0f},{cen[i][1]:.0f}): {L*100:.1f} x {W*100:.1f} cm, "
                     f"fill {fill:.2f} -> {'doll-like' if ok else 'not Lars'}")
        if ok:
            cands.append((i, cen[i], len(xs)))
    if hint is not None:
        cands = [c for c in cands if np.hypot(*(c[1] - hint)) < 45]
        cands.sort(key=lambda c: np.hypot(*(c[1] - hint)))
    else:
        cands.sort(key=lambda c: -c[2])
    if not cands:
        return None
    i, c_px = cands[0][0], cands[0][1]
    comp = (lab == i).astype(np.uint8)
    near = cv2.dilate(comp, np.ones((9, 9), np.uint8)) > 0
    white = near & (V >= 100) & (S <= 90)                    # the eyes
    tan = near & (Hh >= 8) & (Hh < 24) & (S >= 80) & (V >= 45)  # yellowish rim of the eyes / mouth
    body = cv2.morphologyEx(((comp > 0) | white | tan).astype(np.uint8), cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    cs, _ = cv2.findContours(body, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    contour = max(cs, key=cv2.contourArea)
    body = cv2.drawContours(np.zeros_like(body), [contour], -1, 1, -1)
    ys, xs = np.nonzero(body)
    P = to_left(xs, ys)
    c = P.mean(0)
    ev, evec = np.linalg.eigh(np.cov((P - c).T))
    ax = evec[:, 1]
    t = (P - c) @ ax
    perp = np.array([-ax[1], ax[0]])
    w = (P - c) @ perp
    t0, t1 = np.percentile(t, 1), np.percentile(t, 99)
    L, mid = t1 - t0, (t0 + t1) / 2
    # Head end. Eyes: the white pixels sit at the head end (face up only). Crown: his yellow crown sits past the head
    # tip, seen either way up. Legs: face down his two legs show split apart at the feet end. Shape (the wider,
    # heavier end) is a weak cue only: face down it points at the feet (splayed legs; the crown is not green).
    def end_of(xs, ys):  # positions along the axis, in half-lengths from the middle (+1 = the +ax tip)
        return ((to_left(xs, ys) - c) @ ax - mid) / (L / 2)

    ey, ex = np.nonzero(white)
    eye = None
    if len(ex) >= 3:
        eye = float(end_of(ex, ey).mean())
    third = L / 3
    w_pos = np.ptp(w[t > t1 - third]) if np.any(t > t1 - third) else 0
    w_neg = np.ptp(w[t < t0 + third]) if np.any(t < t0 + third) else 0
    shape = 0.5 * (w_pos - w_neg) / max(w_pos + w_neg, 1e-6) + (float(t.mean()) - mid) / (L / 2)
    cy, cx = np.nonzero(near & (Hh >= CROWN_H[0]) & (Hh <= CROWN_H[1]) & (S >= CROWN_S) & (V >= CROWN_V))
    crown = None
    if CROWN_N[0] <= len(cx) <= CROWN_N[1]:
        tc = end_of(cx, cy)
        side = 1 if tc.mean() > 0 else -1
        if np.mean(tc * side > 0) >= 0.9 and abs(tc.mean()) >= 0.6:  # one compact cluster at one end
            crown = float(tc.mean())
    gy, gx = np.nonzero(comp)  # the legs: green only
    tg, wg = end_of(gx, gy), (to_left(gx, gy) - c) @ perp
    pitch = float(np.linalg.norm(np.diff(to_left([c_px[0], c_px[0] + 1], [c_px[1]] * 2), axis=0)))
    split = {}
    for e in (1, -1):
        n2 = 0
        for f0 in (0.5, 0.6, 0.7, 0.8):  # bands across the body near that end: two runs of green = two legs
            ws = np.sort(wg[np.abs(tg * e - f0) < 0.05])
            n2 += len(ws) > 3 and bool(np.any(np.diff(ws) > 1.5 * pitch))
        split[e] = n2
    legs = -1 if split[1] >= 2 and split[-1] == 0 else 1 if split[-1] >= 2 and split[1] == 0 else None  # head sign
    votes = [(nm, 1 if v > 0 else -1, txt) for nm, v, txt in (
        ("eyes", eye if eye is not None and abs(eye) > 0.25 else None, f"eyes (white pixels at {abs(eye or 0):.2f} of the half-length)"),
        ("crown", crown, f"crown (yellow at {abs(crown or 0):.2f})"),
        ("legs", legs, f"legs (split in {max(split.values())}/4 bands at the feet end)")) if v is not None]
    if head_hint is not None:
        hp = to_left(*head_hint)[0]
        sign, how = (1 if (hp - c) @ ax > 0 else -1), "given by --head"
        confident = True
    elif votes:
        sign = votes[0][1]
        agree = all(v[1] == sign for v in votes)
        confident = agree and votes[0][0] != "legs"  # eyes or crown, nothing against them
        how = ", ".join(v[2] for v in votes) + ("" if agree else " DISAGREE") + \
            ("" if votes[0][0] != "legs" else ": legs only, no crown or eyes seen") + \
            f"; shape {'agrees' if shape * sign > 0 else 'disagrees'} ({shape * sign:+.2f}, not counted)"
    elif abs(shape) > 0.05:
        sign, how, confident = (1 if shape > 0 else -1), f"shape only ({abs(shape):.2f}), no eyes, crown or legs seen", False
    else:
        sign, how, confident = 1, "UNDECIDED (no eyes, crown or legs; symmetric shape)", False
    ax = ax * sign
    t, w, mid = t * sign, w * sign, mid * sign
    centre = c + ax * mid
    head, feet = centre + ax * L / 2, centre - ax * L / 2
    grip = head - ax * GRIP_FROM_HEAD * L
    tg = (grip - c) @ ax
    band = np.abs(t - tg) < 0.006
    width = float(np.ptp(w[band])) if band.sum() > 3 else 0.04
    return dict(contour=contour[:, 0, :], centre_px=to_px(centre)[0], head_px=to_px(head)[0], feet_px=to_px(feet)[0],
                grip_px=to_px(grip)[0], centre=centre, head=head, feet=feet, grip=grip, axis=ax, length=float(L),
                width=width, eyes_px=np.c_[ex, ey], crown_px=np.c_[cx, cy], how=how, confident=confident, eye_score=eye,
                shape_score=shape, crown_score=None if crown is None else crown * sign,  # + = at the head end
                legs=None if legs is None else legs * sign, notes=notes)


# ===================================================================== planning

def grasp_pose(p, head_dir, s, tilt=0.0, lean=1.0):
    """Grasp-site pose to grip him lying on his back: fingers close horizontally across his body (site y), his body
    along site x (x = s * head direction when not tilted). tilt (rad): the approach axis (site z, straight down at 0)
    leans along his body toward lean * head direction, so far spots stay reachable."""
    hd = np.array([head_dir[0], head_dir[1], 0.0]); hd /= np.linalg.norm(hd)
    z0 = np.array([0.0, 0.0, -1.0])
    y = np.cross(z0, s * hd)
    z = math.cos(tilt) * z0 + lean * math.sin(tilt) * hd
    T = np.eye(4); T[:3, :3] = np.column_stack([np.cross(y, z), y, z]); T[:3, 3] = p
    return T


def doll_pose(p, head, face, roll=HEAD_UP_ROLL):
    """Pose of Lars's own frame (x toward his head, z out of his face) at p; roll turns him about his face axis."""
    x = np.asarray(head, float) / np.linalg.norm(head)
    z = np.asarray(face, float) - x * (x @ face); z /= np.linalg.norm(z)
    R = np.column_stack([x, np.cross(z, x), z])
    c, sn = math.cos(roll), math.sin(roll)
    T = np.eye(4); T[:3, :3] = R @ np.array([[c, -sn, 0], [sn, c, 0], [0, 0, 1]]); T[:3, 3] = p
    return T


def solve(ik, q, T, iters=600):
    """Joints reaching T from q by running the kit's IK in simulation; None unless within POS_TOL/ROT_TOL."""
    q = np.asarray(q, float)
    for _ in range(iters):
        q = ik.step(T, q, 0.02)
    F = ik.fk(q)
    pos = np.linalg.norm(F[:3, 3] - T[:3, 3])
    rot = np.arccos(np.clip((np.trace(F[:3, :3].T @ T[:3, :3]) - 1) / 2, -1, 1))
    return q if pos < POS_TOL and rot < ROT_TOL else None


def quintic_move(q0, q1, vpeak, g0=None, g1=None, amax=A_PEAK, gsecs=1.0):
    """50 Hz quintic joint move q0 -> q1 (q0 excluded) peaking at vpeak rad/s and amax rad/s^2."""
    d = np.abs(np.asarray(q1) - q0).max()
    T = max(0.3, 1.875 * d / vpeak, math.sqrt(5.78 * d / amax))
    if g0 is not None and g1 is not None:
        T = max(T, abs(g1 - g0) * gsecs)
    n = int(math.ceil(T * HZ))
    s = show.quintic(np.arange(1, n + 1) / n)[:, None]
    Q = q0 + s * (np.asarray(q1) - q0)
    G = np.full(n, g0) if g1 is None else g0 + s[:, 0] * (g1 - g0)
    return Q, G


def doll_local(det):
    """Doll model in its own frame (origin on his axis at the grip line, x toward the head, z out of his face):
    points with radii for the checks."""
    a = GRIP_FROM_HEAD * det["length"]
    b = det["length"] - a
    xs = np.linspace(-b + DOLL_R, a - HEAD_R, 10)
    pts = np.c_[xs, np.zeros(10), np.zeros(10)]
    rad = np.where(xs > a - 2 * HEAD_R, HEAD_R, DOLL_R)
    return pts, rad


class Plan:
    """The whole pick -> present -> place trajectory for one arm, checked."""

    def __init__(self, det, side, place=None, face_deg=None, ik=None, ck=None, sc=None, log=print, dz=0.0, belly=True):
        self.det, self.side, self.log = det, side, log
        self.dz = dz  # m, Lars lies this high above the table (the dock stand); 0 = on the table
        self.belly = belly  # he lies face-down (on his belly); False = on his back, face up
        self.other = "left" if side == "right" else "right"
        self.ik, self.ck = ik, ck
        self.sc = sc
        self.tz = TABLE_Z[side]
        self.floor = self.tz + dz + 0.005
        self.gz = max(self.tz + dz + GRIP_Z, self.floor)
        self.hz = self.tz + dz + (BACK_TOP if belly else DOLL_TOP) + HOVER
        self.pick = arm_frame(det["grip"], side)
        h = det["axis"]
        self.h = h / np.linalg.norm(h)  # head direction (left frame and arm frame have parallel axes)
        if place is None:
            place = (self.pick, self.h)
        self.place, self.h_place = np.asarray(place[0], float), np.asarray(place[1], float)
        self.toward = 1.0 if side == "right" else -1.0  # y sign of the other arm in this arm's frame
        self.face_deg = face_deg
        self.q_rest = show.HOME[side].copy()
        self.q_up = self.q_rest + np.array([0, 0.8, 1.2, 0, 0, 0])
        self.ok, self.problems = False, []
        self.g_open = float(np.clip((det["width"] + GRIP_MARGIN) / GRIP_FULL, 0.55, 1.0))
        self.g_vis = float(np.clip(det["width"] * 0.7 / GRIP_FULL, 0.15, 1.0))  # fingers on the squeezed plush

    # ---- waypoints
    SEEDS = [np.array(q, float) for q in ([0.5, 2.3, 1.8, -0.8, 0.4, 0.9], [0.5, 1.8, 1.4, -1.0, 0, 0],
                                          [0.6, 2.4, 2.0, -1.0, 0.3, -0.9], [0.3, 1.2, 1.0, -0.8, 0, 1.2])]

    def solve_any(self, T, first):
        for seed in [first] + [show.mirror(q, self.side) for q in self.SEEDS]:
            q = solve(self.ik, seed, T)
            if q is not None:
                return q
        return None

    def waypoints(self, s, tilt):
        tz = self.tz

        def pull(xy):  # the carry pose: lifted and pulled in toward the base, where turning him is easy
            return xy * min(1.0, CARRY_R / max(np.linalg.norm(xy), 1e-6))

        wp = {"rest": self.q_rest, "raise": self.q_up}
        # lean the approach outward (away from the base) along his body
        lean = 1.0 if self.h @ self.pick >= 0 else -1.0
        self.lean = lean
        for name, xy, hd, z, seed in (("above", self.pick, self.h, self.hz, "raise"), ("down", self.pick, self.h, self.gz, "above"),
                                     ("carry", pull(self.pick), self.h, tz + CARRY_Z, "above"),
                                     ("carry2", pull(self.place), self.h_place, tz + CARRY_Z, "carry"),
                                     ("above2", self.place, self.h_place, self.hz, "carry2"),
                                     ("down2", self.place, self.h_place, self.gz + 0.005, "above2"),
                                     ("approach", self.pick, self.h, self.gz + APPROACH, "down"),
                                     ("approach2", self.place, self.h_place, self.gz + 0.005 + APPROACH, "down2"),
                                     ("lifted", self.pick, self.h, self.gz + LIFTED, "approach"),
                                     ("lifted2", self.place, self.h_place, self.gz + 0.005 + LIFTED, "approach2")):
            q = self.solve_any(grasp_pose(np.array([*xy, z]), hd, s, tilt, lean), wp[seed])
            if q is None:
                return None, f"cannot reach '{name}' at {np.round([*xy, z], 3).tolist()} (head sign {s:+d}, tilt {math.degrees(tilt):.0f} deg)"
            wp[name] = q
        # Lars relative to the grasp site once gripped (rigid: the plush squeezed between the fingers)
        Ts = self.site_pose(wp["down"])
        Td = self.doll_on_table(self.pick, self.h); Td[:3, 3] = Ts[:3, 3]
        self.T_sd = np.linalg.inv(Ts) @ Td
        self.doll_pts = doll_local(self.det)
        best = self.present(wp)
        if best is None:
            return None, f"no head-up presentation pose passes the checks (head sign {s:+d}, tilt {math.degrees(tilt):.0f} deg)"
        wp["present"], self.face_used = best
        return wp, ""

    def present(self, wp):
        """Search the head-up presentation pose. On his back his face looks back at the gripper (it grips him from the
        front), so "facing the other arm" means the hand reaches out to the far side and points the fingers outward;
        on his belly his face looks out past the fingertips, so facing the audience is the fingers pointing at it. Tries
        face directions in FACES / FACES_BELLY order (deg from the audience toward the other arm), positions on a polar
        grid around the base on the outer side; keeps the pose nearest the stage pose that passes the static checks."""
        stage = show.stage_pose(self.side)
        faces = [self.face_deg] if self.face_deg is not None else FACES_BELLY if self.belly else FACES
        inv = np.linalg.inv(self.T_sd)
        out = -self.toward  # y sign of the arm's outer side
        for fd in faces:
            ang = math.radians(fd)
            face = np.array([math.cos(ang), self.toward * math.sin(ang), 0.0])
            best = None
            for az in (0, 20, 40, 55):
                a = math.radians(az) * out
                seeds = [wp["carry"]] + [stage + show.mirror([-math.radians(min(az, 60)), 0, 0, 0, w, 0], self.side)
                                         for w in (0.0, 0.9, -0.9)]
                for r in (0.24, 0.30, 0.36, 0.42):
                    for z in (0.36, 0.42, 0.48):
                        p = np.array([r * math.cos(a), r * math.sin(a), self.tz + z])
                        T = doll_pose(p, [0, 0, 1.0], face) @ inv
                        for seed in seeds:
                            q = solve(self.ik, seed, T, iters=500)
                            if q is None or self.static_problems(q, held=True):
                                continue
                            # near the stage pose, and in front of the arm rather than beside it
                            # and quick to reach from straight above him (the move time goes with the largest joint step)
                            cost = (0.5 * float(np.linalg.norm((q - stage)[:3])) + float(np.abs(q - wp["lifted"]).max())
                                    + 3.0 * max(0.0, 0.22 - p[0]))
                            if best is None or cost < best[0]:
                                best = (cost, q)
                            break
            if best is not None:
                return best[1], fd
        return None

    # ---- doll pose bookkeeping
    def doll_on_table(self, xy, hd):
        """His frame lying at xy, head toward hd: x to the head, z out of his face (up on his back, down on his belly)."""
        if self.belly:
            R = np.column_stack([[hd[0], hd[1], 0], [hd[1], -hd[0], 0], [0, 0, -1]])
        else:
            R = np.column_stack([[hd[0], hd[1], 0], [-hd[1], hd[0], 0], [0, 0, 1]])
        T = np.eye(4); T[:3, :3] = R; T[:3, 3] = [xy[0], xy[1], self.tz + self.dz + (BELLY_AXIS_Z if self.belly else DOLL_R)]
        return T

    def site_pose(self, q):
        return self.ik.fk(q)

    # ---- checks
    def static_problems(self, q, held):
        Q = np.asarray(q)[None]
        probs = show.motion_problems(Q) + self.ck.lean_problems(self.side, Q)
        if np.any(Q[0] < show.SAFE_LO + POSE_MARGIN) or np.any(Q[0] > show.SAFE_HI - POSE_MARGIN):
            probs.append("a joint within POSE_MARGIN of its limit")  # a held pose: keep room for sag and tracking
        low_l, low_d, *_ = self.clear(Q, held)
        if low_l < show.CLEAR or (held and low_d < show.CLEAR):
            probs.append("table")
        if not probs:
            sw = self.sweep(Q, held)
            if self.gap(sw) < show.GAP:
                probs.append("gap")
        return probs

    def cloud(self, q, held, grip=1.0):
        """Points + radii of the arm's moving parts (the 15 cm stick prop of show's scene left out) and of Lars if
        held, shared frame; and the grasp site position in the arm's own frame."""
        sc, side = self.sc, self.side
        sc.set(side, q, grip); sc.forward()
        pts, rad = [], []
        for g, v, r in sc.geoms[side]:
            if g == sc.prop[side]:
                continue
            pts.append(sc.d.geom_xpos[g] + v @ sc.d.geom_xmat[g].reshape(3, 3).T); rad.append(np.full(len(v), r))
        P, R = np.concatenate(pts), np.concatenate(rad)
        D = Dr = None
        if held:
            Ts = np.eye(4); Ts[:3, :3] = sc.d.site_xmat[sc.site[side]].reshape(3, 3); Ts[:3, 3] = sc.d.site_xpos[sc.site[side]]
            Td = Ts @ self.T_sd
            lp, Dr = self.doll_pts
            D = lp @ Td[:3, :3].T + Td[:3, 3]
        site = sc.d.site_xpos[sc.site[side]] + (OFFSET if side == "right" else 0)
        return P, R, D, Dr, site

    def clear(self, Q, held):
        """(lowest link point, lowest doll point, lowest site z, max site xy) above the table (m), arm frame."""
        low_l = low_d = low_s = np.inf
        sites = []
        for q in Q:
            P, R, D, Dr, site = self.cloud(q, held)
            low_l = min(low_l, float(np.min(P[:, 2] - R)) - self.tz)
            if D is not None:
                low_d = min(low_d, float(np.min(D[:, 2] - Dr)) - self.tz)
            low_s = min(low_s, float(site[2]))
            sites.append(site)
        return low_l, low_d, low_s, np.array(sites)

    def sweep(self, Q, held):
        pts, rad = [], []
        for q in Q:
            P, R, D, Dr, _ = self.cloud(q, held)
            pts.append(P); rad.append(R)
            if D is not None:
                pts.append(D); rad.append(Dr)
        p, r = np.concatenate(pts), np.concatenate(rad)
        op, orr = [], []
        for rv in np.unique(r):
            k = np.unique(np.round(p[r == rv] / show.VOX), axis=0) * show.VOX
            op.append(k); orr.append(np.full(len(k), rv))
        return np.concatenate(op), np.concatenate(orr), 0.0

    def gap(self, sw):
        if not hasattr(self, "_others"):
            o = self.other
            # at rest and at the audience stage pose; the stage server works out the other facings (load_library) and
            # refuses the pick/place in those it fails
            self._others = [self.ck.pose_sweep(o, show.HOME[o]), self.ck.pose_sweep(o, show.stage_pose(o))]
        return min(show.gap(sw, o) for o in self._others)

    def check_segment(self, seg):
        Q, held, near = seg["Q"], seg["held"], seg["near"]
        probs = show.motion_problems(np.vstack([seg["q0"][None], Q])) if len(Q) else []
        probs += self.ck.lean_problems(self.side, Q, fold=seg["fold"])
        low_l, low_d, low_s, sites = self.clear(Q, held and not near)
        if near:
            if low_s < self.floor - 1e-6:
                probs.append(f"grasp site down to z={low_s:.3f}, below the floor {self.floor:.3f}")
            if low_l < self.dz + NEAR_CLEAR:
                probs.append(f"a link comes {low_l*100:.1f} cm from the table (needs {NEAR_CLEAR*100:.1f})")
            drift = float(np.max(np.linalg.norm(sites[:, :2] - seg["xy"], axis=1)))
            if drift > XY_DRIFT:
                probs.append(f"grasp site drifts {drift*100:.1f} cm sideways while going down/up")
            seg["drift"] = drift
        else:
            if low_l < show.CLEAR:
                probs.append(f"a link comes {low_l*100:.1f} cm from the table (needs {show.CLEAR*100:.0f})")
            if held and low_d < show.CLEAR:
                probs.append(f"Lars comes {low_d*100:.1f} cm from the table (needs {show.CLEAR*100:.0f})")
        g = self.gap(self.sweep(Q[::2] if len(Q) > 2 else Q, held and not near))
        if g < show.GAP:
            probs.append(f"comes {g*100:.1f} cm from the other arm (needs {show.GAP*100:.0f})")
        com = self.ck.com(self.side, Q[::5] if len(Q) > 5 else Q)[:, 0]
        seg.update(low_links=low_l, low_doll=low_d, low_site=low_s, gap=g, com_min=float(com.min()),
                   vmax=float(np.abs(np.diff(np.vstack([seg["q0"][None], Q]), axis=0)).max() * HZ) if len(Q) else 0.0)
        return probs

    # ---- build
    def build(self):
        tried = []
        for tilt_deg, s in [(t, s) for t in TILTS for s in (1, -1)]:
            wp, why = self.waypoints(s, math.radians(tilt_deg))
            if wp is None:
                tried.append(why); continue
            self.s, self.tilt, self.wp = s, tilt_deg, wp
            g_open = self.g_open
            segs = []

            def move(name, a, b, held, near=False, fold=False, v=V_FAR, g=None, xy=None):
                g0 = segs[-1]["G"][-1] if segs else 1.0
                Q, G = quintic_move(wp[a], wp[b], v, g0, g)
                segs.append(dict(name=name, frm=a, to=b, q0=wp[a], Q=Q, G=G, held=held, near=near, fold=fold, xy=xy))

            def grip(name, at, g, held, secs=1.4):
                g0 = segs[-1]["G"][-1]
                n = int(secs * HZ)
                G = np.r_[g0 + show.quintic(np.arange(1, int(HZ) + 1) / HZ) * (g - g0), np.full(n - int(HZ), g)]
                segs.append(dict(name=name, frm=at, to=at, q0=wp[at], Q=np.repeat(wp[at][None], n, 0), G=G, held=held,
                                 near=True, fold=False, xy=self.pick if at == "down" else self.place))

            move("raise", "rest", "raise", False, fold=True, g=g_open)
            move("over Lars", "raise", "above", False)
            move("down", "above", "down", False, near=True, v=V_NEAR, xy=self.pick)
            grip("grip", "down", GRIP_CLOSE, True)
            move("lift", "down", "above", True, near=True, v=V_NEAR, xy=self.pick)
            move("carry up", "above", "carry", True)
            move("turn head up", "carry", "present", True)
            n = int(PRESENT_HOLD * HZ)
            segs.append(dict(name="present", frm="present", to="present", q0=wp["present"], Q=np.repeat(wp["present"][None], n, 0),
                             G=np.full(n, GRIP_CLOSE), held=True, near=False, fold=False, xy=None))
            move("turn back", "present", "carry2", True)
            move("over the spot", "carry2", "above2", True)
            move("set down", "above2", "down2", True, near=True, v=V_NEAR, xy=self.place)
            grip("release", "down2", g_open, False)
            move("up", "down2", "above2", False, near=True, v=V_NEAR, xy=self.place)
            move("raise", "above2", "raise", False)
            move("rest", "raise", "rest", False, fold=True, g=1.0)
            # exit used by --go if he is not in the gripper after the lift: carry -> raise -> rest (checked too)
            Q, G = quintic_move(wp["carry"], wp["raise"], V_FAR, GRIP_CLOSE, g_open)
            self.exit = [dict(name="exit: carry -> raise", frm="carry", to="raise", q0=wp["carry"], Q=Q, G=G, held=False,
                              near=False, fold=False, xy=None)]
            Q, G = quintic_move(wp["raise"], wp["rest"], V_FAR, g_open, 1.0)
            self.exit.append(dict(name="exit: raise -> rest", frm="raise", to="rest", q0=wp["raise"], Q=Q, G=G, held=False,
                                  near=False, fold=True, xy=None))
            probs = []
            for seg in segs + self.exit:
                p = self.check_segment(seg)
                seg["problems"] = p
                probs += [f"{seg['name']}: {x}" for x in p]
            self.segs = segs
            # head-up check at the presentation pose
            Tp = self.site_pose(wp["present"]) @ self.T_sd
            self.head_dir, self.face_dir = Tp[:3, 0], Tp[:3, 2]
            self.head_tilt = math.degrees(math.acos(np.clip(self.head_dir[2], -1, 1)))
            if self.head_tilt > 10 + abs(math.degrees(HEAD_UP_ROLL)):
                probs.append(f"present: head is {self.head_tilt:.0f} deg off vertical")
            if probs:
                tried.append("; ".join(probs)); continue
            self.ok, self.problems = True, []
            return self
        self.ok, self.problems = False, tried
        return self

    def trajectory(self):
        """(Q (N,6), G (N,), held (N,), segment index (N,)) of the whole plan at 50 Hz."""
        Q = np.concatenate([s["Q"] for s in self.segs]); G = np.concatenate([s["G"] for s in self.segs])
        held = np.concatenate([np.full(len(s["Q"]), s["held"]) for s in self.segs])
        idx = np.concatenate([np.full(len(s["Q"]), i) for i, s in enumerate(self.segs)])
        return Q, G, held, idx

    def report(self):
        log, d = self.log, self.det
        lf = d["centre"]; af = arm_frame(lf, self.side)
        hd = math.degrees(math.atan2(self.h[1], self.h[0]))
        log(f"Lars at px ({d['centre_px'][0]:.0f},{d['centre_px'][1]:.0f}), {d['length']*100:.1f} cm long; head toward px "
            f"({d['head_px'][0]:.0f},{d['head_px'][1]:.0f}); decided by {d['how']}")
        log(f"  left-arm frame centre ({lf[0]:.3f},{lf[1]:.3f}); {self.side}-arm frame ({af[0]:.3f},{af[1]:.3f}); head points "
            f"{hd:+.0f} deg from +x (toward the audience = 0, toward the left arm's left = +90)")
        log(f"  {self.side} arm picks (nearer base). Grip across the body {GRIP_FROM_HEAD:.0%} of his length below the head tip at "
            f"({self.pick[0]:.3f},{self.pick[1]:.3f}, z {self.gz:.3f}), body {d['width']*100:.1f} cm wide there, open {self.g_open:.2f}, close {GRIP_CLOSE}")
        if not self.ok:
            log("PLAN FAILED:"); [log("  " + p) for p in self.problems]; return
        log(f"  grasp: fingers close across his body; approach leans {self.tilt} deg from vertical toward his "
            f"{'head' if self.lean > 0 else 'feet'}; head sign {self.s:+d} (his head along {'+' if self.s > 0 else '-'}x of the grasp site)")
        Tp = self.site_pose(self.wp["present"])
        log(f"  present: grasp site at ({Tp[0,3]:.3f},{Tp[1,3]:.3f},{Tp[2,3]:.3f}), head {self.head_tilt:.1f} deg off vertical, face "
            f"{self.face_used} deg from the audience toward the other arm, joints {np.round(self.wp['present'], 2).tolist()}")
        if (self.place != self.pick).any():
            log(f"  place at ({self.place[0]:.3f},{self.place[1]:.3f}), head {math.degrees(math.atan2(self.h_place[1], self.h_place[0])):+.0f} deg")
        log(f"  {'segment':16s} {'s':>5s} {'vmax':>5s} {'links':>6s} {'Lars':>6s} {'site z':>6s} {'gap':>5s} {'COM x':>6s}  (cm, rad/s)")
        for s in self.segs + self.exit:
            log(f"  {s['name']:16s} {len(s['Q'])/HZ:5.1f} {s['vmax']:5.2f} {s['low_links']*100:6.1f} "
                f"{(s['low_doll']*100 if np.isfinite(s['low_doll']) else float('nan')):6.1f} {s['low_site']*100:6.1f} "
                f"{min(s['gap'], 9.99)*100:5.0f} {s['com_min']*100:6.1f}")
        log(f"  total {sum(len(s['Q']) for s in self.segs)/HZ:.1f} s; checks: limits/speed/accel, table (links {show.CLEAR*100:.0f} cm away, "
            f"site >= floor {self.floor:.3f} and links >= {NEAR_CLEAR*100:.1f} cm near the table), lean-back, "
            f">= {show.GAP*100:.0f} cm from the other arm at rest and stage: all pass")


class LarsScene(show.Scene):
    """show's two-arm scene plus Lars (a mocap body) for the checks and the preview."""

    def __init__(self, det):
        orig = show.scene_spec
        show.scene_spec = lambda: add_doll(orig(), det)
        try:
            super().__init__()
        finally:
            show.scene_spec = orig
        m = self.m
        self.prop = {s: m.geom(p + "prop").id for s, p in (("left", "l_"), ("right", "r_"))}
        for g in self.prop.values():
            m.geom_rgba[g] = 0  # no stick props in this act
        self.doll_mocap = m.body_mocapid[m.body("lars").id]

    def set_doll(self, T_shared):
        q = np.zeros(4); mujoco.mju_mat2Quat(q, T_shared[:3, :3].flatten())
        self.d.mocap_pos[self.doll_mocap] = T_shared[:3, 3]; self.d.mocap_quat[self.doll_mocap] = q


def add_doll(spec, det):
    a = GRIP_FROM_HEAD * det["length"]; b = det["length"] - a
    green, belly, white, black = [0.33, 0.62, 0.18, 1], [0.72, 0.78, 0.3, 1], [0.97, 0.97, 0.95, 1], [0.05, 0.05, 0.05, 1]
    body = spec.worldbody.add_body(name="lars", mocap=True)
    kw = dict(contype=0, conaffinity=0)
    C, S = mujoco.mjtGeom.mjGEOM_CAPSULE, mujoco.mjtGeom.mjGEOM_SPHERE
    body.add_geom(type=C, fromto=[-b + DOLL_R, 0, 0, a - HEAD_R * 1.5, 0, 0], size=[DOLL_R, 0, 0], rgba=green, **kw)
    body.add_geom(type=S, pos=[-(b - a) / 2 - 0.004, 0, 0.009], size=[0.012, 0, 0], rgba=belly, **kw)
    body.add_geom(type=S, pos=[a - HEAD_R, 0, 0.002], size=[HEAD_R, 0, 0], rgba=green, **kw)
    for sy in (-1, 1):
        body.add_geom(type=S, pos=[a - 0.010, sy * 0.010, 0.014], size=[0.0075, 0, 0], rgba=white, **kw)
        body.add_geom(type=S, pos=[a - 0.008, sy * 0.010, 0.021], size=[0.0035, 0, 0], rgba=black, **kw)
        body.add_geom(type=C, fromto=[-b + 0.022, sy * 0.008, 0, -b + 0.004, sy * 0.020, 0], size=[0.007, 0, 0], rgba=green, **kw)
        body.add_geom(type=C, fromto=[a - 0.045, sy * 0.014, 0, a - 0.060, sy * 0.030, 0.004], size=[0.006, 0, 0], rgba=green, **kw)
    return spec


# ===================================================================== images

def annotate(img, det, plan=None, side=None):
    out = img.copy()
    if det is None:
        cv2.putText(out, "Lars not found", (10, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1, cv2.LINE_AA)
        return out
    cv2.polylines(out, [det["contour"].astype(np.int32)], True, (255, 255, 0), 1, cv2.LINE_AA)
    c, h, f, g = (tuple(int(round(x)) for x in det[k]) for k in ("centre_px", "head_px", "feet_px", "grip_px"))
    cv2.line(out, f, h, (200, 200, 200), 1, cv2.LINE_AA)
    cv2.arrowedLine(out, c, h, (0, 0, 255), 2, cv2.LINE_AA, tipLength=0.35)
    cv2.circle(out, h, 4, (0, 0, 255), -1, cv2.LINE_AA)
    for (u, v) in det["eyes_px"]:
        out[v, u] = (255, 0, 255)
    # grasp line: where the fingers close, across the body, as wide as they open
    ax = det["axis"]; perp = np.array([-ax[1], ax[0]])
    half = (plan.g_open * GRIP_FULL / 2) if plan is not None and plan.ok else det["width"] / 2 + GRIP_MARGIN / 2
    p0, p1 = to_px(det["grip"] - perp * half)[0], to_px(det["grip"] + perp * half)[0]
    cv2.line(out, tuple(np.int32(p0)), tuple(np.int32(p1)), (0, 165, 255), 2, cv2.LINE_AA)
    cv2.circle(out, g, 2, (0, 165, 255), -1)
    if plan is not None and plan.ok and (plan.place != plan.pick).any():
        lp = plan.place - (OFFSET[:2] if plan.side == "right" else 0)
        pp = to_px(lp)[0]; hp = to_px(lp + plan.h_place * GRIP_FROM_HEAD * det["length"])[0]
        cv2.circle(out, tuple(np.int32(pp)), 5, (255, 0, 255), 1, cv2.LINE_AA)
        cv2.arrowedLine(out, tuple(np.int32(pp)), tuple(np.int32(hp)), (255, 0, 255), 1, cv2.LINE_AA)
        cv2.putText(out, "place", tuple(np.int32(pp) + [6, -4]), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 0, 255), 1, cv2.LINE_AA)
    # zoomed inset of Lars, top right
    z = 5
    cu, cv_ = det["centre_px"]
    x0, y0 = int(np.clip(cu - 30, 0, out.shape[1] - 60)), int(np.clip(cv_ - 30, 0, out.shape[0] - 60))
    inset = cv2.resize(out[y0:y0 + 60, x0:x0 + 60], None, fx=z, fy=z, interpolation=cv2.INTER_NEAREST)
    # draw the head label on the inset so it reads at a glance
    hx, hy = (det["head_px"] - [x0, y0]) * z
    cv2.putText(inset, "HEAD", (int(np.clip(hx + 8, 0, 240)), int(np.clip(hy - 8, 14, 290))), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                (0, 0, 255), 2, cv2.LINE_AA)
    W = out.shape[1]
    big = np.zeros((max(out.shape[0], 300 + 70), W + 300, 3), np.uint8)
    big[:out.shape[0], :W] = out
    big[:300, W:] = inset
    cv2.rectangle(big, (W, 0), (W + 299, 299), (255, 255, 255), 1)
    lines = [f"Lars: {det['length']*100:.1f} cm, head by {det['how'][:40]}",
             "cyan outline, red arrow = head, magenta = eyes",
             "orange = grasp line (fingers close here)"]
    if plan is not None:
        lines.append(f"{plan.side} arm picks; plan {'OK' if plan.ok else 'FAILED'}")
    for i, t in enumerate(lines):
        cv2.putText(big, t, (W + 4, 316 + 14 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (255, 255, 255), 1, cv2.LINE_AA)
    return big


def render_gif(plan, path, every=15, fps=10):
    """Audience-view GIF of the whole plan (every 15th 50 Hz sample at 10 fps = 3x speed) with a close-up of Lars
    seen from the audience and the other arm's side."""
    from PIL import Image, ImageDraw
    sc = plan.sc
    m, d = sc.m, sc.d
    W, Hh, IW, IH = 360, 240, 150, 120
    m.vis.global_.offwidth, m.vis.global_.offheight = W, Hh
    big, small = mujoco.Renderer(m, Hh, W), mujoco.Renderer(m, IH, IW)
    Q, G, held, idx = plan.trajectory()
    shift = np.eye(4); shift[:3, 3] = -OFFSET if plan.side == "right" else 0  # arm frame -> shared frame
    doll = shift @ plan.doll_on_table(plan.pick, plan.h)
    frames = []
    picks = list(range(0, len(Q), every)) + [len(Q) - 1] * 5
    released = False
    for i in picks:
        seg = plan.segs[idx[i]]
        gv = G[i] if not held[i] else max(G[i], plan.g_vis)
        sc.set(plan.side, Q[i], gv); sc.set(plan.other, show.HOME[plan.other], 1.0)
        mujoco.mj_kinematics(m, d)
        Ts = np.eye(4); Ts[:3, :3] = d.site_xmat[sc.site[plan.side]].reshape(3, 3); Ts[:3, 3] = d.site_xpos[sc.site[plan.side]]
        if held[i]:
            doll = Ts @ plan.T_sd
        elif held[:i].any():  # set down
            doll = shift @ plan.doll_on_table(plan.place, plan.h_place)
        sc.set_doll(doll)
        mujoco.mj_forward(m, d)
        big.update_scene(d, camera="audience")
        im = Image.fromarray(big.render())
        cam = mujoco.MjvCamera(); cam.lookat[:] = doll[:3, 3]; cam.distance, cam.azimuth, cam.elevation = 0.30, 180 + 35 * (1 if plan.side == "right" else -1), -12
        small.update_scene(d, camera=cam)
        im.paste(Image.fromarray(small.render()), (W - IW - 4, Hh - IH - 18))
        dr = ImageDraw.Draw(im)
        dr.rectangle([W - IW - 5, Hh - IH - 19, W - 4, Hh - 18], outline=(255, 255, 255))
        dr.text((W - IW, Hh - 16), "close-up: Lars", fill=(220, 220, 220))
        dr.text((6, 4), f"Lars: {seg['name']}", fill=(255, 255, 255))
        dr.text((W - 70, 4), f"{i / HZ:4.1f} s  {every * fps // HZ}x", fill=(200, 200, 200))
        if held[i]:
            tilt = math.degrees(math.acos(np.clip(doll[2, 0], -1, 1)))
            dr.text((6, 18), f"head {tilt:3.0f} deg from up", fill=(120, 255, 120) if tilt < 15 else (230, 230, 230))
        dr.text((6, Hh - 14), "right arm", fill=(200, 200, 200)); dr.text((W - IW - 60, Hh - 14), "left arm", fill=(200, 200, 200))
        frames.append(im.quantize(64, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE))
    path.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=int(1000 / fps), loop=0, optimize=True)
    return len(frames)


# ===================================================================== the pipeline

def kit():
    from dream_hackathon import config
    from dream_hackathon.arms import yam
    station = config.load("configs/station.yaml")
    return station, yam.teleop_ik(station)


def snap(roles, station, out_dir):
    """Grab one frame per camera role, one camera at a time (as snap.py). {role: path}; failures are logged, skipped."""
    from dream_hackathon import cameras
    got = {}
    for role in roles:
        cfg = tuple(c for c in station.cameras if c.role == role)
        try:
            cams = cameras.open_cameras(cfg, 640, 360)
        except Exception as e:
            print(f"camera {role}: cannot open ({e}); skipped")
            continue
        try:
            time.sleep(2.0)
            f = cams[role].latest()
            p = out_dir / f"{role}.jpg"; cv2.imwrite(str(p), f); got[role] = p
        finally:
            for c in cams.values():
                c.close()
    return got


def choose_side(det):
    c = det["grip"]
    return "left" if abs(c[1]) <= abs(c[1] + OFFSET[1]) else "right"


def in_calib(px):
    u0, u1, v0, v1 = CALIB_PX
    return u0 <= px[0] <= u1 and v0 <= px[1] <= v1


def random_place(img, det, side, rng, n=200):
    """Random clear spots (grip point xy in the arm frame, head direction) in the arm's comfortable reach."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    busy = ((hsv[..., 2] >= 45) | ((hsv[..., 1] >= 120) & (hsv[..., 2] >= 35))).astype(np.uint8)
    me = cv2.drawContours(np.zeros_like(busy), [det["contour"].astype(np.int32)], -1, 1, -1)
    busy[cv2.dilate(me, np.ones((7, 7), np.uint8)) > 0] = 0  # Lars himself moves away
    busy = cv2.dilate(busy, np.ones((13, 13), np.uint8))   # ~1.5 cm clearance to anything else
    a, b = GRIP_FROM_HEAD * det["length"], (1 - GRIP_FROM_HEAD) * det["length"]
    base = np.zeros(2) if side == "left" else -OFFSET[:2]  # the arm's base, left frame
    toward = 1.0 if side == "right" else -1.0
    for _ in range(n):
        r, th = rng.uniform(0.30, 0.46), math.radians(rng.uniform(-30, 30))
        xy_arm = np.array([r * math.cos(th), r * math.sin(th)])
        if xy_arm[0] < 0.26 or toward * xy_arm[1] > 0.28:  # in front, and not past the middle of the table
            continue
        hd_ang = rng.uniform(-math.pi, math.pi); hd = np.array([math.cos(hd_ang), math.sin(hd_ang)])
        lp = xy_arm + base
        foot = np.array([lp + hd * t + np.array([-hd[1], hd[0]]) * w for t in np.linspace(-b - 0.01, a + 0.01, 9)
                         for w in (-0.03, 0, 0.03)])
        px = to_px(foot)
        if not all(in_calib(p) for p in px):
            continue
        if any(busy[int(p[1]), int(p[0])] for p in px):
            continue
        if np.linalg.norm(lp - det["grip"]) < 0.06:  # somewhere new
            continue
        yield xy_arm, hd


def make_plan(det, img, args, ik, ck, log=print, rng=None):
    side = choose_side(det)
    sc = LarsScene(det)
    face = args.face
    belly = not args.face_up
    if args.place:
        u, v = map(float, args.place.split(","))
        xy = arm_frame(to_left(u, v)[0], side)
        if not in_calib((u, v)):
            log(f"warning: --place {u:.0f},{v:.0f} is outside the calibrated area {CALIB_PX}")
        return Plan(det, side, (xy, det["axis"]), face, ik[side], ck, sc, log, belly=belly).build()
    if args.random_place:
        rng = rng or np.random.default_rng()
        last = None
        for k, (xy, hd) in enumerate(random_place(img, det, side, rng)):
            p = Plan(det, side, (xy, hd), face, ik[side], ck, sc, log, belly=belly).build()
            if p.ok:
                return p
            last = p
            if k >= 12:
                break
        if last is None:
            log("no clear spot found in reach"); return Plan(det, side, None, face, ik[side], ck, sc, log, belly=belly).build()
        return last
    return Plan(det, side, None, face, ik[side], ck, sc, log, belly=belly).build()


def dry(args, img_path):
    img = cv2.imread(str(img_path))
    assert img is not None, f"cannot read {img_path}"
    hint = np.array(list(map(float, args.hint.split(",")))) if args.hint else None
    head = tuple(map(float, args.head.split(","))) if args.head else None
    det = detect(img, hint, head)
    if det is None:
        cv2.imwrite(str(OUT_IMG), annotate(img, None))
        sys.exit("Lars not found (no green doll-shaped blob); nothing planned")
    if not in_calib(det["centre_px"]):
        print(f"warning: Lars is outside the calibrated area {CALIB_PX}; expect > 1 cm error")
    station, ik = kit()
    ck = show.Checker()
    t = time.time()
    plan = make_plan(det, img, args, ik, ck, rng=np.random.default_rng(args.seed))
    plan.report()
    print(f"  (planned and checked in {time.time() - t:.1f} s)")
    OUT_IMG.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(OUT_IMG), annotate(img, det, plan))
    print(f"annotated image: {OUT_IMG}")
    if plan.ok and not args.no_gif:
        n = render_gif(plan, OUT_GIF)
        print(f"preview: {OUT_GIF}  {n} frames  {OUT_GIF.stat().st_size // 1024} KB")
    return det, plan


# ===================================================================== --bake: the dock pick and place as gestures

def via_path(points, step=0.004):
    """Dense joint-space path through via points [(q, blend)]: straight lines, each inner corner rounded by a
    quadratic Bezier over `blend` (fraction) of its two neighbouring lines, so the arm need not stop there."""
    P = [np.asarray(q, float) for q, _ in points]

    def line(a, b):
        n = max(2, int(np.abs(b - a).max() / step) + 1)
        return a + np.linspace(0, 1, n)[1:, None] * (b - a)

    out, cur = [P[0][None]], P[0]
    for k in range(1, len(P)):
        if k == len(P) - 1:
            out.append(line(cur, P[k])); break
        f = min(points[k][1], 0.45)
        p0, p2 = P[k] + (P[k - 1] - P[k]) * f, P[k] + (P[k + 1] - P[k]) * f
        out.append(line(cur, p0))
        n = max(4, int((np.abs(p0 - P[k]).max() + np.abs(p2 - P[k]).max()) / step))
        u = np.linspace(0, 1, n)[1:, None]
        out.append((1 - u) ** 2 * p0 + 2 * u * (1 - u) * P[k] + u ** 2 * p2)
        cur = p2
    X = np.vstack(out)
    c = np.r_[0, np.cumsum(np.linalg.norm(np.diff(X, axis=0), axis=1))]  # even spacing along the path
    cq = np.linspace(0, c[-1], max(3, int(c[-1] / step) + 1))
    return np.array([np.interp(cq, c, X[:, j]) for j in range(6)]).T


def retime(X, vlim, amax, hz=HZ):
    """Fastest rest-to-rest timing of path X (M,6) with |dq/dt| <= vlim[i] per path sample and |d2q/dt2| <= amax
    per joint (forward/backward passes on the path speed). Returns 50 Hz samples and their path fraction."""
    d1 = np.gradient(X, axis=0); d2 = np.gradient(d1, axis=0)
    a1 = np.maximum(np.abs(d1), 1e-9)
    sd = np.min(vlim[:, None] / a1, axis=1)  # path samples per second
    sd = np.minimum(sd, np.min(np.sqrt(amax / np.maximum(np.abs(d2), 1e-12)), axis=1))  # corners: |d2| sd^2 <= amax
    sd[0] = sd[-1] = 0.0

    def acc(i, v):  # path acceleration at sample i, speed v, that keeps every joint inside amax
        return max(float(((amax - np.abs(d2[i]) * v * v) / a1[i]).min()), 0.0)

    for i in range(len(X) - 1):
        sd[i + 1] = min(sd[i + 1], math.sqrt(sd[i] ** 2 + 2 * acc(i, sd[i])))
    for i in range(len(X) - 1, 0, -1):
        sd[i - 1] = min(sd[i - 1], math.sqrt(sd[i] ** 2 + 2 * acc(i, sd[i])))
    t = np.r_[0, np.cumsum(2.0 / np.maximum(sd[:-1] + sd[1:], 1e-9))]
    n = int(math.ceil(t[-1] * hz))
    u = np.interp(np.linspace(0, t[-1], n + 1), t, np.arange(len(X)))
    k = np.arange(len(X))
    return np.array([np.interp(u, k, X[:, j]) for j in range(6)]).T, u / (len(X) - 1)


V_FRAC = 0.9  # of show's VMAX / AMAX away from the table at --speed 1.0 (the library's gestures use 0.75; 1.0 fails
              # the checker's 50 Hz differences). Measured Nov 7: pick_lars 5.0 s at 0.9, 5.5 s at 0.75.


def timed(plan, points, held, g_from, g_to, g_frac=0.7):
    """Rest-to-rest move along via points at V_FRAC of show's limits, and at most 0.95 x TABLE_VMAX wherever any
    part of the arm (or the held Lars) is within the near zone (+5 mm) of the table. Gripper g_from -> g_to over the
    first g_frac of the way. Returns (Q, G, slow) at 50 Hz, the start pose excluded."""
    X = via_path(points)
    near_z = show.TABLE_NEAR + plan.dz
    low = np.array([min(r[0], r[1]) for r in (plan.clear(X[i:i + 1], held) for i in range(len(X)))])
    slow = np.convolve(low < near_z + 0.005, np.ones(5), "same") > 0
    vlim = np.where(slow, 0.95 * show.TABLE_VMAX, V_FRAC * show.VMAX)
    for frac in (V_FRAC, 0.6, 0.5, 0.4):  # the 50 Hz differences of a retimed corner overshoot a bit: back off
        Q, u = retime(X, vlim, frac * show.AMAX)
        if not show.motion_problems(np.round(np.vstack([X[:1], Q]), 4)):  # as written: keys are rounded to 1e-4 rad
            break
    G = g_from + (g_to - g_from) * np.clip(u / g_frac, 0, 1)
    sl = slow[np.round(u * (len(X) - 1)).astype(int)]
    return Q[1:], G[1:], sl[1:]


def bake_segments(plan):
    """pick_lars: stage -> over the dock -> down (one blended move, the gripper opening on the way) -> grip ->
    up -> holding pose. place_lars: the reverse. As Plan segments, split where the slow zone starts/ends."""
    wp, g_open = plan.wp, plan.g_open
    wp["stage"] = show.stage_pose(plan.side)
    out = {"pick": [], "place": []}

    def move(part, name, names, held, g_from, g_to, g_frac=0.7):
        xy = plan.pick if part == "pick" else plan.place
        Q, G, slow = timed(plan, [(wp[n], 0.35) for n in names], held, g_from, g_to, g_frac)
        q0 = wp[names[0]]
        cuts = [0] + [i for i in range(1, len(Q)) if slow[i] != slow[i - 1]] + [len(Q)]
        for a, b in zip(cuts[:-1], cuts[1:]):
            near = bool(slow[a])
            out[part].append(dict(name=f"{name} ({'slow' if near else 'fast'})", frm=names[0], to=names[-1], q0=q0,
                                  Q=Q[a:b], G=G[a:b], held=held,
                                  near=near, fold=False, xy=xy))
            q0 = Q[b - 1]

    def grip(part, name, at, g_from, g_to, held, settle):
        n, k = int(round((GRIP_SECS + settle) * HZ)), int(round(GRIP_SECS * HZ))
        G = np.r_[g_from + show.quintic(np.arange(1, k + 1) / k) * (g_to - g_from), np.full(n - k, g_to)]
        out[part].append(dict(name=name, frm=at, to=at, q0=wp[at], Q=np.repeat(wp[at][None], n, 0), G=G, held=held,
                              near=True, fold=False, xy=plan.pick if part == "pick" else plan.place))

    move("pick", "reach", ["stage", "approach", "down"], False, 1.0, g_open, 0.5)
    grip("pick", "grip", "down", g_open, GRIP_CLOSE, True, GRIP_SETTLE)
    move("pick", "lift", ["down", "lifted", "present"], True, GRIP_CLOSE, GRIP_CLOSE)
    move("place", "to the dock", ["present", "lifted2", "down2"], True, GRIP_CLOSE, GRIP_CLOSE)
    grip("place", "release", "down2", GRIP_CLOSE, g_open, False, 0.05)
    move("place", "back", ["down2", "approach2", "stage"], False, g_open, 1.0)
    return out


def dock_det():
    """The fixed dock (DOCK_*) as a detection: what the bake plans from. No camera involved (the pixels are only for
    the preview, through the current calib.json)."""
    ax = np.asarray(DOCK_AXIS, float); ax /= np.linalg.norm(ax)
    c = np.asarray(DOCK_XY, float)
    head, feet = c + ax * DOCK_LEN / 2, c - ax * DOCK_LEN / 2
    grip = head - ax * GRIP_FROM_HEAD * DOCK_LEN
    return dict(contour=np.zeros((0, 2)), centre_px=to_px(c)[0], head_px=to_px(head)[0], feet_px=to_px(feet)[0],
                grip_px=to_px(grip)[0], centre=c, head=head, feet=feet, grip=grip, axis=ax, length=DOCK_LEN,
                width=DOCK_WIDTH, eyes_px=np.zeros((0, 2), int), crown_px=np.zeros((0, 2), int),
                how="the fixed dock (DOCK_RIGHT, DOCK_AXIS)", confident=True, notes=[])


def bake_gesture(plan, name, segs, about, start, prop_from, prop_to, speed=1.0):
    """The stage server's gesture JSON; speed < 1 stretches the timing (same path, slower everywhere)."""
    Q = np.vstack([start[None]] + [s["Q"] for s in segs])
    G = np.r_[segs[0]["G"][0], np.concatenate([s["G"] for s in segs])]
    dur = round((len(Q) - 1) / HZ / speed, 4)  # = the last key's t
    o, x = plan.T_sd[:3, 3], plan.T_sd[:3, 0]
    a, b = GRIP_FROM_HEAD * plan.det["length"], (1 - GRIP_FROM_HEAD) * plan.det["length"]
    cap = np.r_[o + x * (-b + LARS_R), o + x * max(a - LARS_R, -b + LARS_R + 0.001)]
    return {"name": name, "about": about, "arms": "one", "side": plan.side, "hold": name == "pick_lars", "loop": False,
            "near": round(show.TABLE_NEAR + plan.dz, 4),
            "carry": {"name": "lars", "from": round(prop_from, 3), "to": round(min(prop_to, dur), 3),
                     "capsule": np.round(cap, 4).tolist(), "r": LARS_R},
            # table positions only (left arm's frame): the dock check maps them into the camera through the current
            # calib.json, so it follows a recalibration
            "dock": {"xy_left": np.round(plan.det["centre"], 5).tolist(), "head_xy_left": np.round(plan.det["head"], 5).tolist(),
                     "z": plan.dz, "lying": "belly" if plan.belly else "back"},
            "speed": speed,
            "keys": [{"t": round(i / HZ / speed, 4), "q": np.round(q, 4).tolist(), "g": round(float(g), 3)} for i, (q, g) in enumerate(zip(Q, G))]}


def bake(args):
    det = dock_det()
    side = choose_side(det)
    station, ik = kit()
    ck = show.Checker()
    t0 = time.time()
    # face-down he lies on the same spot, head the same way as on his back (the same outline from above)
    belly = not args.face_up
    plan = Plan(det, side, None, args.face, ik[side], ck, LarsScene(det), print, dz=args.dock_z, belly=belly)
    tried = []
    for tilt_deg, s in [(t, s) for t in TILTS for s in (1, -1)]:
        wp, why = plan.waypoints(s, math.radians(tilt_deg))
        if wp is None:
            tried.append(why); continue
        plan.s, plan.tilt, plan.wp = s, tilt_deg, wp
        parts = bake_segments(plan)
        probs = []
        for part in parts.values():
            for seg in part:
                probs += [f"{seg['name']}: {x}" for x in plan.check_segment(seg)]
        if probs:
            tried.append(f"tilt {tilt_deg} sign {s:+d}: " + "; ".join(probs)); continue
        break
    else:
        sys.exit("bake failed:\n  " + "\n  ".join(tried))
    Tp = plan.site_pose(wp["present"]) @ plan.T_sd
    plan.head_tilt = math.degrees(math.acos(np.clip(Tp[2, 0], -1, 1)))
    plan.face_dir = Tp[:3, 2]
    pick, place = parts["pick"], parts["place"]
    k = [sg["name"] for sg in pick].index("grip")
    t_grip = (sum(len(sg["Q"]) for sg in pick[:k]) / HZ + GRIP_SECS) / args.speed
    k = [sg["name"] for sg in place].index("release")
    t_release = sum(len(sg["Q"]) for sg in place[:k]) / HZ / args.speed
    fd = math.degrees(math.atan2(plan.face_dir[1], plan.face_dir[0]))
    shows = "facing the audience" if abs(fd) <= 20 else "in profile" if abs(fd) >= 60 else "turned toward the audience"
    gs = {"pick_lars": bake_gesture(plan, "pick_lars", pick, "Lars the frog joins: the right arm picks him up from his "
                                    f"dock and holds him head up, {shows}. Ends holding him: then any gesture plays with him.",
                                    wp["stage"], t_grip, 1e9, args.speed),
          "place_lars": bake_gesture(plan, "place_lars", place, "Lars goes home: the right arm puts him back on his dock, "
                                     "lets go and returns to the stage pose.", wp["present"], 0.0, t_release, args.speed)}
    probs = []
    for name, d in gs.items():
        g = show.Gesture(d)
        p = ck.check_side(g)
        probs += [f"{name}: {x}" for x in p]
        print(f"{name}: {g.duration:.1f} s, show.Checker {'OK' if not p else 'REJECTS: ' + '; '.join(p)}; holds Lars "
              f"{d['carry']['from']:.2f}-{d['carry']['to']:.2f} s")
    if probs:
        sys.exit("not written: the stage server's checker rejects them")
    for name, d in gs.items():
        (show.GESTURE_DIR / f"{name}.json").write_text(json.dumps(d, separators=(",", ":")) + "\n")
    print(f"baked in {time.time() - t0:.1f} s -> {show.GESTURE_DIR}/pick_lars.json, place_lars.json")
    for why in tried:
        print(f"  rejected: {why[:300]}")
    hd = math.degrees(math.atan2(plan.h[1], plan.h[0]))
    print(f"  dock: left-arm frame centre ({det['centre'][0]:.4f},{det['centre'][1]:.4f}) (px ({det['centre_px'][0]:.0f},"
          f"{det['centre_px'][1]:.0f}) through the current calib.json), {side}-arm frame grip ({plan.pick[0]:.3f},{plan.pick[1]:.3f}) "
          f"z {plan.gz:.3f}, head {hd:+.0f} deg, dock height {plan.dz:.3f} m; approach tilt {plan.tilt} deg, head sign {plan.s:+d}")
    print(f"  lying {'face-down (belly)' if belly else 'face-up (back)'}; holding pose {np.round(wp['present'], 2).tolist()}: "
          f"head {plan.head_tilt:.1f} deg off vertical, face toward {fd:+.0f} deg (0 = audience, +90 = toward the left arm); "
          f"searched {plan.face_used} deg first that passed")
    print(f"  {'segment':16s} {'s':>5s} {'vmax':>5s} {'links':>6s} {'Lars':>6s} {'site z':>6s} {'gap':>5s}  (cm, rad/s)")
    for sg in pick + place:
        print(f"  {sg['name']:16s} {len(sg['Q'])/HZ:5.2f} {sg['vmax']:5.2f} {sg['low_links']*100:6.1f} "
              f"{(sg['low_doll']*100 if np.isfinite(sg['low_doll']) else float('nan')):6.1f} {sg['low_site']*100:6.1f} {min(sg['gap'], 9.99)*100:5.0f}")
    if not args.no_gif:
        n = int(1.5 * HZ)
        hold = dict(name="holding (any gesture now)", frm="present", to="present", q0=wp["present"],
                    Q=np.repeat(wp["present"][None], n, 0), G=np.full(n, GRIP_CLOSE), held=True, near=False, fold=False, xy=None)
        plan.segs = pick + [hold] + place
        out = DIR / "previews" / "lars_baked.gif"
        k = render_gif(plan, out, every=5, fps=10)
        print(f"preview: {out}  {k} frames (real time)  {out.stat().st_size // 1024} KB")
    return plan


DOCK_TOL, DOCK_TOL_DEG = 0.03, 30.0  # m, deg: how far from his baked dock pose Lars may lie for pick_lars


def dock_problem(dock, img=None):
    """Why pick_lars must not run (None = Lars lies at his dock, head the right way). Grabs one overhead frame unless
    img is given. For the stage server's --dock-check."""
    if not dock:
        return "the gesture has no dock pose (re-bake it)"
    if img is None:
        from dream_hackathon import cameras, config
        station = config.load("configs/station.yaml")
        try:
            cams = cameras.open_cameras(tuple(c for c in station.cameras if c.role == "overhead"), 640, 360)
        except Exception as e:
            return f"overhead camera: cannot open ({e})"
        try:
            time.sleep(1.0)  # ponytail: auto-exposure settle; shorter if frames come out fine
            img = cams["overhead"].latest()
        finally:
            for c in cams.values():
                c.close()
    if "head_xy_left" not in dock:
        return "the gesture's dock has no head position (re-bake it with this lars.py)"
    # the dock (fixed on the table) seen through the current calib.json; his detection goes through the same one, so
    # this follows a recalibration and never changes where the arm goes
    px, hpx = to_px(dock["xy_left"])[0], to_px(dock["head_xy_left"])[0]
    det = detect(img, px)
    if det is None:
        return f"Lars is not at his dock (no doll-shaped green blob near px ({px[0]:.0f},{px[1]:.0f}))"
    if not det["confident"]:
        return f"not sure which end of Lars is his head ({det['how']})"
    d = float(np.linalg.norm(det["centre"] - np.asarray(dock["xy_left"])))
    want, got = hpx - px, det["head_px"] - det["centre_px"]
    ang = math.degrees(math.acos(np.clip(want @ got / np.linalg.norm(want) / np.linalg.norm(got), -1, 1)))
    if ang > DOCK_TOL_DEG:
        return f"Lars's head points {ang:.0f} deg off his dock direction ({det['how']})"
    if d > DOCK_TOL:
        return f"Lars lies {d*100:.1f} cm from his dock (max {DOCK_TOL*100:.0f})"
    return None


# ===================================================================== self-check

def variant(img, centre, angle_deg, flip=False, r=42):
    """Copy of img with a disc around Lars rotated (and mirrored): a synthetic other orientation."""
    cu, cv_ = map(int, np.round(centre))
    crop = img[cv_ - r:cv_ + r, cu - r:cu + r].copy()
    if flip:
        crop = crop[:, ::-1].copy()
    M = cv2.getRotationMatrix2D((r, r), angle_deg, 1.0)
    rot = cv2.warpAffine(crop, M, (2 * r, 2 * r), borderMode=cv2.BORDER_REFLECT)
    out = img.copy()
    mask = np.zeros((2 * r, 2 * r), bool); cv2.circle(mask.view(np.uint8), (r, r), r - 1, 1, -1)
    mask = cv2.circle(np.zeros((2 * r, 2 * r), np.uint8), (r, r), r - 1, 1, -1).astype(bool)
    out[cv_ - r:cv_ + r, cu - r:cu + r][mask] = rot[mask]
    return out


def variants_row(img, det, label, hint=False):
    """Rotated/mirrored synthetic copies of the frame around Lars: he must be found (without a hint unless hint), confidently, head
    direction within 25 deg of the real one turned the same way. Returns a row of zoomed annotated crops."""
    c, h = det["centre_px"], det["head_px"]
    v_img = (h - c) / np.linalg.norm(h - c)
    tiles = [annotate(img, det)[:, :640]]
    for ang, flip in ((90, False), (180, False), (-120, True), (35, True)):
        im2 = variant(img, c, ang, flip)
        d2 = detect(im2, c if hint else None)
        assert d2 is not None, f"{label} variant {ang} flip {flip}: not found"
        # expected head direction: rotate (and mirror) the real one about the disc centre (cv2 angle = CCW on screen)
        e = v_img * ([-1, 1] if flip else [1, 1])
        a = math.radians(ang)
        e = np.array([e[0] * math.cos(a) + e[1] * math.sin(a), -e[0] * math.sin(a) + e[1] * math.cos(a)])
        g = d2["head_px"] - d2["centre_px"]; g /= np.linalg.norm(g)
        err = math.degrees(math.acos(np.clip(g @ e, -1, 1)))
        assert np.hypot(*(d2["centre_px"] - c)) < 6, f"{label} variant {ang}/{flip}: centre {d2['centre_px']}"
        assert err < 25, f"{label} variant {ang} flip {flip}: head direction off by {err:.0f} deg ({d2['how']})"
        assert d2["confident"], (label, ang, flip, d2["how"])
        print(f"  {label} variant rot {ang:+4d} deg{' mirrored' if flip else ''}: head direction within {err:.0f} deg; {d2['how']}")
        tiles.append(annotate(im2, d2)[:, :640])
    y, x = int(np.clip(c[1], 45, img.shape[0] - 45)), int(np.clip(c[0], 45, img.shape[1] - 45))
    crops = [cv2.resize(t[y - 45:y + 45, x - 45:x + 45], (180, 180), interpolation=cv2.INTER_NEAREST) for t in tiles]
    for k, t in enumerate(crops):
        cv2.putText(t, f"{label}: " + ["real", "rot 90", "rot 180", "mirror -120", "mirror 35"][k], (4, 14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1, cv2.LINE_AA)
    return np.hstack(crops)


def selftest():
    img = cv2.imread(str(DIR / "lars" / "lars_overhead.jpg"))
    det = detect(img, np.array([377.0, 125.0]))
    assert det is not None, "Lars not found in lars_overhead.jpg"
    c, h = det["centre_px"], det["head_px"]
    assert np.hypot(*(c - [377, 125])) < 10, f"centre {c} not near (377,125)"
    assert h[1] < c[1] - 8, f"head {h} should be toward the top of the image (eyes there)"
    assert det["confident"] and "eyes" in det["how"], det["how"]
    assert 0.06 <= det["length"] <= 0.13, det["length"]
    print(f"real frame: centre ({c[0]:.0f},{c[1]:.0f}) head ({h[0]:.0f},{h[1]:.0f}) {det['length']*100:.1f} cm; {det['how']}")
    rows = [variants_row(img, det, "face up")]
    # face-down (on his belly, the default dock): no eyes; the crown decides, the split legs must not disagree. The
    # width/mass shape cue points at his feet here, so it is not counted.
    # (Nov 7 evening frames: at the dock after the real pick put him back, and elsewhere on the table)
    for name, hint, head_dir in (("facedown_dock_overhead.jpg", (317.0, 90.0), (1, -1)),
                                 ("facedown_overhead.jpg", (437.0, 122.0), (1, -1))):
        im = cv2.imread(str(DIR / "lars" / name))
        d = detect(im, np.array(hint))
        assert d is not None, f"Lars not found in {name}"
        g = d["head_px"] - d["centre_px"]
        assert d["confident"] and "crown" in d["how"] and d["legs"] != -1, (name, d["how"])
        assert np.all(np.sign(g) == head_dir), f"{name}: head toward {g}, expected the crown end {head_dir}"
        print(f"{name} (face down): centre ({d['centre_px'][0]:.0f},{d['centre_px'][1]:.0f}) head ({d['head_px'][0]:.0f},"
              f"{d['head_px'][1]:.0f}); {d['how']}")
        rows.append(variants_row(im, d, name[:-13], hint=True))  # today's frames hold other green blobs
    w = max(r.shape[1] for r in rows)
    cv2.imwrite(str(DIR / "lars" / "lars_variants.jpg"), np.vstack([np.pad(r, ((0, 0), (0, w - r.shape[1]), (0, 0))) for r in rows]))
    print(f"variants: {DIR / 'lars' / 'lars_variants.jpg'}")
    # the dock check, face-down, with the baked dock on a real frame from the camera calib.json was fitted for (Nov 7
    # 18:50, after the real place_lars put him back): at the dock passes; turned 180 deg (gripping 40 % from his feet)
    # and crown hidden refused. Recalibrate -> grab a new frame of him at the dock for this.
    im = cv2.imread(str(DIR / "lars" / "facedown_dock_cal3.jpg"))
    dd = dock_det()
    dock = {"xy_left": dd["centre"].tolist(), "head_xy_left": dd["head"].tolist()}
    assert dock_problem(dock, im) is None, dock_problem(dock, im)
    d = detect(im, to_px(DOCK_XY)[0])
    assert "crown" in d["how"] and d["confident"], d["how"]
    c = d["centre_px"]
    flipped = variant(im, c, 180)
    why = dock_problem(dock, flipped)
    assert why and "head points" in why, why
    print(f"dock check: at the dock OK; turned 180 deg refused: {why}")
    # he lies ~25 deg off DOCK_AXIS in this frame (where the real place left him): 15 deg one way passes, the other
    # way (~40 deg) is refused
    assert [dock_problem(dock, variant(im, c, ang)) is None for ang in (15, -15)].count(True) == 1
    for ang in (60, 120, -90):
        assert dock_problem(dock, variant(im, c, ang)), ang
    hidden = im.copy()
    d = detect(im, c)
    cx, cy = d["crown_px"].T
    m = np.zeros(im.shape[:2], np.uint8); m[cy, cx] = 1
    hidden[cv2.dilate(m, np.ones((5, 5), np.uint8)) > 0] = np.median(im[30:50, 360:400].reshape(-1, 3), axis=0)
    why = dock_problem(dock, hidden)
    assert why and not detect(hidden, c)["confident"], why
    print(f"dock check: crown hidden refused: {why}")
    why = dock_problem(dock, variant(hidden, c, 180))
    assert why, "crown hidden and turned 180 deg passed"
    # not Lars: the yellow block must not be taken for him
    d3 = detect(img, np.array([251.0, 125.0]))
    assert d3 is None, "the yellow block was taken for Lars"
    # a dry plan on the real frame passes, head up at the presentation
    station, ik = kit()
    ck = show.Checker()
    for belly in (False, True):
        plan = Plan(det, choose_side(det), None, None, ik[choose_side(det)], ck, LarsScene(det), lambda *_: None,
                    belly=belly).build()
        assert plan.ok, plan.problems
        assert plan.head_tilt < 10, plan.head_tilt
        assert all(s["vmax"] <= V_NEAR + 0.02 for s in plan.segs if s["near"]), "near-table speed above 0.3 rad/s"
        fd = math.degrees(math.atan2(plan.face_dir[1], plan.face_dir[0]))
        if belly:
            assert abs(fd) <= 30, f"face-down: held face {fd:.0f} deg from the audience"
        print(f"plan ({'face down' if belly else 'face up'}): {plan.side} arm, head {plan.head_tilt:.1f} deg off vertical at "
              f"the presentation, face {fd:+.0f} deg from the audience (searched {plan.face_used})")
    print("selftest OK")


# ===================================================================== --go (real arms only with a human at the e-stop)

def stage_running():
    me = os.getpid()
    for p in pathlib.Path("/proc").glob("[0-9]*/cmdline"):
        try:
            if int(p.parent.name) != me and b"stage.py" in p.read_bytes():
                return int(p.parent.name)
        except OSError:
            continue
    return None


class Recorder:
    """Episode folder: log.txt, plan.json, before/after frames, camera frames at ~10 Hz, joints at 50 Hz."""

    def __init__(self, root, station, sim):
        self.dir = root / time.strftime("%Y%m%d-%H%M%S"); self.dir.mkdir(parents=True, exist_ok=True)
        self.logf = open(self.dir / "log.txt", "a")
        self.rows = []  # t, cmd q (12), cmd g (2), meas q (12), meas g (2), segment
        self.station, self.sim, self.cams, self.stop = station, sim, {}, threading.Event()

    def log(self, *a):
        s = " ".join(str(x) for x in a)
        print(s, flush=True); self.logf.write(f"{time.strftime('%H:%M:%S')} {s}\n"); self.logf.flush()

    def start_cameras(self, roles):
        if self.sim:
            return
        from dream_hackathon import cameras
        for role in roles:
            try:
                self.cams.update(cameras.open_cameras(tuple(c for c in self.station.cameras if c.role == role), 640, 360))
            except Exception as e:
                self.log(f"camera {role}: cannot open ({e}); not recorded")
        (self.dir / "frames").mkdir(exist_ok=True)
        self.camlog = open(self.dir / "frames" / "frames.csv", "w"); self.camlog.write("t,role,file\n")
        self.thread = threading.Thread(target=self._cam_loop, daemon=True); self.thread.start()

    def _cam_loop(self):
        k = 0
        while not self.stop.is_set():
            t = time.time()
            for role, cam in self.cams.items():
                try:
                    f = cam.latest()
                    name = f"{role}_{k:05d}.jpg"; cv2.imwrite(str(self.dir / "frames" / name), f)
                    self.camlog.write(f"{t:.3f},{role},{name}\n")
                except Exception:
                    pass
            k += 1
            time.sleep(max(0.0, 0.1 - (time.time() - t)))

    def close(self):
        self.stop.set()
        if hasattr(self, "thread"):
            self.thread.join(2)
            self.camlog.close()
        for c in self.cams.values():
            c.close()
        if self.rows:
            a = np.array([r[:-1] for r in self.rows], float)
            np.savez(self.dir / "joints.npz", t=a[:, 0], cmd_q=a[:, 1:13].reshape(-1, 2, 6), cmd_g=a[:, 13:15],
                     meas_q=a[:, 15:27].reshape(-1, 2, 6), meas_g=a[:, 27:29], segment=np.array([r[-1] for r in self.rows]),
                     sides=np.array(show.SIDES))
        self.logf.close()


def safety_problems():
    """The run-time safety settings --go relies on; any change here makes it refuse on the real arms."""
    p = []
    if not show.LEAN_BACK_CHECK:
        p.append("show.LEAN_BACK_CHECK is off")
    if V_NEAR > 0.3 + 1e-9:
        p.append(f"V_NEAR {V_NEAR} rad/s > 0.3")
    if TRACK_MAX > 0.2 + 1e-9:
        p.append(f"TRACK_MAX {TRACK_MAX} rad > 0.2")
    if show.CLEAR < 0.04 or show.GAP < 0.15 or NEAR_CLEAR < 0.003:
        p.append("table/arm clearances lowered")
    if HELD_MIN <= 0:
        p.append("miss/drop detection off (HELD_MIN)")
    return p


def go(args):
    if not args.sim:
        if args.estop_attended == args.unattended:
            sys.exit("refused: real arms need --estop-attended (a human stands at the e-stop) or --unattended (the owner's "
                     "exception for lars.py in AGENTS.md); exactly one of them")
        if args.unattended and (args.image or not args.yes):
            sys.exit("refused: --unattended needs fresh snapshots (no --image) and --yes (nobody is there to type)")
        if safety_problems():
            sys.exit("refused: safety settings changed: " + "; ".join(safety_problems()))
        pid = stage_running()
        if pid:
            sys.exit(f"refused: the stage server (stage.py, pid {pid}) is running and owns the arms; stop it first")
    from dream_hackathon import arms
    station, ik = kit()
    ck = show.Checker()
    lock = None
    if not args.sim:
        from dream_hackathon import station_lock
        lock = station_lock.acquire("dial-lars")
    rng = np.random.default_rng(args.seed)
    a = None
    try:
        for ep in range(args.repeat):
            rec = Recorder(RUNS if not args.sim else RUNS / "sim", station, args.sim)
            try:
                rec.log(f"episode {ep + 1}/{args.repeat}  {'SIM' if args.sim else 'REAL ARMS'}  args {vars(args)}")
                if args.unattended and not args.sim:
                    rec.log("UNATTENDED: nobody at the e-stop; run under the owner's Nov 7 exception for lars.py (AGENTS.md)")
                if args.image:
                    img_path = pathlib.Path(args.image)
                else:
                    got = snap(["overhead", "wrist_r", "wrist_l"], station, rec.dir)
                    for role in ("overhead", "wrist_r", "wrist_l"):
                        if role in got:
                            got[role].rename(rec.dir / f"before_{role}.jpg")
                    rec.log("before frames:", sorted(got))
                    if "overhead" not in got:
                        rec.log("refused: no overhead frame"); return
                    img_path = rec.dir / "before_overhead.jpg"
                img = cv2.imread(str(img_path))
                hint = np.array(list(map(float, args.hint.split(",")))) if args.hint else None
                det = detect(img, hint, tuple(map(float, args.head.split(","))) if args.head else None)
                if det is None:
                    rec.log("refused: Lars not found"); return
                if not det["confident"]:
                    rec.log(f"refused: head end not sure ({det['how']}); give --head U,V"); return
                if not in_calib(det["centre_px"]):
                    rec.log(f"refused: Lars at px {np.round(det['centre_px'])} is outside the calibrated area"); return
                plan = make_plan(det, img, args, ik, ck, log=rec.log, rng=rng)
                plan.report()
                cv2.imwrite(str(rec.dir / "plan_overhead.jpg"), annotate(img, det, plan))
                (rec.dir / "plan.json").write_text(json.dumps(dict(
                    side=plan.side, ok=plan.ok, problems=plan.problems, centre_px=det["centre_px"].tolist(),
                    head_px=det["head_px"].tolist(), how=det["how"], length=det["length"], width=det["width"],
                    pick=plan.pick.tolist(), place=plan.place.tolist(), head_dir_place=plan.h_place.tolist(),
                    waypoints={k: v.tolist() for k, v in getattr(plan, "wp", {}).items()},
                    knobs=dict(GRIP_Z=GRIP_Z, GRIP_FROM_HEAD=GRIP_FROM_HEAD, GRIP_CLOSE=GRIP_CLOSE, HEAD_UP_ROLL=HEAD_UP_ROLL),
                ), indent=1))
                if not plan.ok:
                    rec.log("refused: the plan does not pass the checks; nothing moved"); return
                fast = [x["name"] for x in plan.segs if x["near"] and x["vmax"] > V_NEAR + 1e-3]
                if fast:
                    rec.log(f"refused: near-table segments faster than {V_NEAR} rad/s: {fast}"); return
                wrist = "wrist_r" if plan.side == "right" else "wrist_l"
                if args.unattended and not args.sim and not (rec.dir / f"before_{wrist}.jpg").exists():
                    rec.log(f"refused: --unattended needs the pre-move {wrist} snapshot and it could not be taken"); return
                if not args.sim and not args.yes and ep == 0:
                    if input(f"{plan.side} arm will pick Lars. Someone at the e-stop and the table clear? type yes: ").strip() != "yes":
                        rec.log("not confirmed; nothing moved"); return
                if a is None:
                    a = arms.open_arms(station, energize=True, sim=args.sim)
                result = execute(a, plan, rec, args.sim, attended=not args.unattended, fake_doll=args.sim and args.sim_doll)
                rec.log(f"episode result: {result}")
                if not args.image and not args.sim:
                    got = snap(["overhead"], station, rec.dir)
                    if "overhead" in got:
                        got["overhead"].rename(rec.dir / "after_overhead.jpg")
                if result != "ok":
                    return
            finally:
                rec.close()
    finally:
        if a is not None:
            a.close()
        if lock is not None:
            lock.close()


def execute(a, plan, rec, sim=False, attended=True, fake_doll=False):
    """Play the checked 50 Hz trajectory. Faults (tracking error > TRACK_MAX on either arm, a miss, a drop, Ctrl-C)
    freeze and fold back to rest along already-checked paths, logged. Returns "ok" or why it stopped."""
    import signal
    side, other = plan.side, plan.other
    js, gs = a.read()
    for s in show.SIDES:
        far = float(np.max(np.abs(js[s] - show.HOME[s])))
        if far > REST_TOL:
            rec.log(f"refused: the {s} arm is {far:.2f} rad from rest (fold it or run home.py first)")
            return "not at rest"
    hold_other, g_other = js[other].copy(), float(gs[other])
    rec.start_cameras(["overhead", "wrist_r" if side == "right" else "wrist_l"])
    Q0, G0 = quintic_move(js[side].copy(), plan.wp["rest"], V_NEAR, float(gs[side]), 1.0)  # measured -> planned rest
    segs = [dict(name="settle", Q=Q0, G=G0)] + plan.segs
    played_Q, played_G = [], []  # everything commanded so far, for retracing
    t0 = time.time()

    def play(Q, G, name, keep=True):
        """Command samples at 50 Hz; returns a fault string (and stops) on a tracking error, else None."""
        nxt = time.time()
        for i in range(len(Q)):
            a.command({side: Q[i], other: hold_other}, {side: float(G[i]), other: g_other})
            mq, mg = a.read()
            rec.rows.append([time.time() - t0, *(Q[i] if side == "left" else hold_other), *(hold_other if side == "left" else Q[i]),
                             *((G[i], g_other) if side == "left" else (g_other, G[i])), *mq["left"], *mq["right"],
                             mg["left"], mg["right"], name])
            if keep:
                played_Q.append(Q[i]); played_G.append(G[i])
            err, oerr = float(np.max(np.abs(mq[side] - Q[i]))), float(np.max(np.abs(mq[other] - hold_other)))
            if err > TRACK_MAX or oerr > TRACK_MAX:
                return f"tracking error {max(err, oerr):.2f} rad ({side if err >= oerr else other} arm) in '{name}'"
            nxt += 1 / HZ
            time.sleep(max(0.0, nxt - time.time()))
        return None

    def route_home(reason, k):
        """Fault: freeze 0.5 s, then back to rest on checked paths. Before the release: retrace everything played in
        reverse (Lars goes back down where he was picked and the fingers open there). After it: finish the plan."""
        signal.signal(signal.SIGINT, signal.SIG_IGN)  # a second Ctrl-C must not interrupt the fold
        rec.log(f"FAULT: {reason}; freezing, then folding to rest")
        time.sleep(0.5)
        names = [s["name"] for s in segs]
        if k > names.index("release"):
            route = [(s["Q"], s["G"], "fold:" + s["name"]) for s in segs[k + 1:]]
        elif reason.startswith("no Lars") and names[k] != "grip":  # missed after the lift: up and home, not the table
            route = []
            if names[k] == "present":
                b = segs[names.index("turn head up")]["Q"]; route.append((b[::-1], np.full(len(b), plan.g_open), "fold:turn back"))
            elif names[k] == "lift":
                c = segs[names.index("carry up")]["Q"]; route.append((c, np.full(len(c), plan.g_open), "fold:carry up"))
            route += [(e["Q"], e["G"], "fold:" + e["name"]) for e in plan.exit]
        else:
            route = [(np.array(played_Q[::-1]), np.array(played_G[::-1]), "fold:retrace")]
        for Q, G, nm in route:
            why = play(Q, G, nm, keep=False)
            if why:
                rec.log(f"SECOND FAULT while folding: {why}. Holding here; a human must take the arm.")
                if sim:
                    return
                try:
                    if not attended:
                        raise EOFError
                    input("Hold the arm, then press Enter: the motors switch off and it goes limp. ")
                except EOFError:  # nobody to answer: keep the motors holding it where it is until someone comes
                    rec.log("holding the arm with the motors on until a human stops this (Ctrl-C after holding the arm)")
                    signal.signal(signal.SIGINT, signal.default_int_handler)
                    try:
                        while True:
                            time.sleep(1)
                    except KeyboardInterrupt:
                        pass
                return
        rec.log("folded to rest")

    k = 0
    try:
        for k, s in enumerate(segs):
            rec.log(f"-> {s['name']}")
            why = play(s["Q"], s["G"], s["name"])
            if why:
                route_home(why, k); return why
            if s["name"] in ("grip", "lift", "present"):
                gr = float(a.read()[1][side])
                if fake_doll and s["name"] != "settle":
                    gr = max(gr, plan.g_vis)  # --sim-doll: the sim has no doll; pretend the fingers stopped on him
                rec.log(f"   gripper reads {gr:.2f} after '{s['name']}'")
                if gr < HELD_MIN:
                    why = f"no Lars in the gripper after '{s['name']}' (closed fully: {'a miss' if s['name'] != 'present' else 'dropped'})"
                    route_home(why, k); return why
        return "ok"
    except KeyboardInterrupt:
        route_home("Ctrl-C", k)
        return "Ctrl-C"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry", action="store_true", help="plan, check, image + GIF; nothing moves (default)")
    ap.add_argument("--go", action="store_true", help="run it (real arms: a human at the e-stop)")
    ap.add_argument("--sim", action="store_true", help="with --go: i2rt's MuJoCo sim instead of the real arms")
    ap.add_argument("--estop-attended", action="store_true", help="a human stands at the e-stop (required for real --go)")
    ap.add_argument("--unattended", action="store_true",
                    help="nobody at the e-stop: the owner's Nov 7 exception for lars.py (AGENTS.md); needs --yes and fresh snapshots")
    ap.add_argument("--yes", action="store_true", help="skip the typed confirmation (never skips a safety check)")
    ap.add_argument("--sim-doll", action="store_true", help="with --go --sim: pretend the fingers closed on Lars")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--image", help="overhead frame to use instead of snapping")
    ap.add_argument("--hint", help="U,V near Lars (else the most doll-like green blob)")
    ap.add_argument("--head", help="U,V near his head: overrides the head/feet call")
    ap.add_argument("--place", help="U,V where to put him down (default: where he was)")
    ap.add_argument("--random-place", action="store_true", help="a random clear spot in the arm's comfortable reach")
    ap.add_argument("--face", type=float, help="deg his face turns from the audience toward the other arm (default: FACES in order)")
    ap.add_argument("--repeat", type=int, default=1, help="with --go: episodes in a row (re-snaps and re-detects each)")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--no-gif", action="store_true")
    ap.add_argument("--bake", action="store_true", help="write gestures/pick_lars.json + place_lars.json from the dock")
    ap.add_argument("--dock-z", type=float, default=DOCK_Z, help="m, dock surface above the table (the stand)")
    ap.add_argument("--face-up", action="store_true",
                    help="Lars lies on his back (the old dock; held in profile); default: face-down, held facing the audience")
    ap.add_argument("--speed", type=float, default=0.3,
                    help="--bake: 0.25..1.0 scales the whole timing (1.0 = the 5 s target); ramp it up on the real arm")
    args = ap.parse_args()
    if args.bake:
        if not 0.25 <= args.speed <= 1.0:
            sys.exit("--speed must be 0.25 .. 1.0")
        return bake(args)
    if args.selftest:
        return selftest()
    if args.go:
        return go(args)
    if args.image:
        return dry(args, pathlib.Path(args.image))
    from dream_hackathon import config
    out = DIR / "snaps"; out.mkdir(exist_ok=True)
    got = snap(["overhead"], config.load("configs/station.yaml"), out)
    if "overhead" not in got:
        sys.exit("no overhead frame (camera busy?); pass --image PATH")
    return dry(args, got["overhead"])


if __name__ == "__main__":
    main()
