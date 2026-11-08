"""The green pen, found fresh in the overhead camera every time, picked with our own IK: Dream-1-style chunks on
stdout for `dream_run.py --cam` (owner, Nov 8: the show's Dream-1 act without Dream Labs; the pen's spot changes,
the pen doesn't). Runs on the station after snap.py:

  cd ~/physical-ai-hacks && .venv/bin/python ~/dial/snap.py && .venv/bin/python ~/dial/cam_pick.py --q '{"left":[..6],"right":[..6]}'

--q: where the arms are now (the first move starts there). The plan: raise (pick.py's raised pose), above the pen,
down, close, lift. Each move is its own chunk(s), so dream_run sees the close end a chunk and the lift after it; it
waves the pen and plays the chunks backwards to put it down. Every pose is solved and floor-checked here (pick.py).
"""
import argparse, json, sys
import cv2, numpy as np
import pick  # ~/dial/pick.py: calibration, IK, grasp pose
from pick import DIR, OFFSET, TOP_ZS, closing_dir, solve, to_left, top_down, config, yam, yaml, pathlib

GREEN_LO, GREEN_HI = (35, 60, 40), (90, 255, 255)  # OpenCV HSV: the pen (hue ~55-65); Lars is yellower (28-50)
PEN_Z = 0.016     # ponytail: m below block_top_z the fingers close at (block top 25 mm, pen ~10 mm thick); knob
LIFT = 0.15       # m above block_top_z after the grasp
RAD_S = 1.0       # joint speed of the plan; dream_run's --speed 0.5 halves it
HZ, ROWS = 20.0, 48
PRESENT_Z = 0.25  # m above block_top_z: the claw points at the audience (+x) there, the pen standing up in it
ROLL = 0.6        # rad of wrist roll each way: the pen sways left and right like a wand (owner, Nov 8)


def find_pen(img):
    """Centre (u, v) and long-axis angle of the biggest thin green blob: the pen, not Lars."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    m = cv2.morphologyEx(cv2.inRange(hsv, GREEN_LO, GREEN_HI), cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats(m)
    best = None
    for i in range(1, n):
        if not 250 <= st[i, 4] <= 4000:
            continue
        ys, xs = np.nonzero(lab == i)
        (cu, cv_), (w, h), ang = cv2.minAreaRect(np.stack([xs, ys], 1).astype(np.float32))
        if max(w, h) / max(min(w, h), 1) >= 3 and (best is None or st[i, 4] > best[0]):
            best = (st[i, 4], cu, cv_, np.deg2rad(ang if w >= h else ang + 90))
    if best is None:
        sys.exit("no green pen in the overhead camera")
    return best[1:]


def segment(q0, q1, g0, g1, hold=0.0):
    secs = max(0.6, float(np.max(np.abs(q1 - q0))) / RAD_S)
    t = np.linspace(0, 1, int(secs * HZ) + 1)[1:]
    s = t * t * (3 - 2 * t)
    Q = q0 + s[:, None] * (q1 - q0)
    G = g0 + t * (g1 - g0)
    k = int(hold * HZ)
    return np.vstack([Q, np.repeat(Q[-1:], k, 0)]), np.r_[G, [g1] * k]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--q", required=True, help='JSON {"left": [6], "right": [6]}: where the arms are now')
    a = ap.parse_args()
    now = {s: np.asarray(v, float)[:6] for s, v in json.loads(a.q).items()}
    img = cv2.imread(str(DIR / "snaps/overhead.jpg"))
    u, v, axis = find_pen(img)
    p = to_left(u, v)
    side = "left" if abs(p[1]) <= abs(p[1] + OFFSET[1]) else "right"
    xy = p + (np.zeros(2) if side == "left" else OFFSET[:2])
    close = closing_dir(u, v, axis)
    top = TOP_ZS[side]
    floor = top - 0.02
    ik = yam.teleop_ik(config.load("configs/station.yaml"))[side]
    home = yaml.safe_load((pathlib.Path.home() / ".dreamlabs/station.yaml").read_text())
    q_up = np.asarray(home["follower_l" if side == "left" else "follower_r"]["home"]) + np.array([0, 0.8, 1.2, 0, 0, 0])
    plan, q = [("raise", q_up, 1.0, 0.0)], q_up
    for name, z, g, hold in [("above", top + pick.HOVER, 1.0, 0.0), ("down", top - PEN_Z, 1.0, 0.2),
                             ("grip", top - PEN_Z, 0.0, 0.5), ("lift", top + LIFT, 0.0, 0.0)]:
        zs = [z] if name != "lift" else [z, top + 0.10, top + 0.06]  # the lift goes as high as the arm reaches there
        q = next((r for zz in zs if (r := solve(ik, q, top_down(np.array([*xy, max(zz, floor)]), close))) is not None), None)
        if q is None:
            sys.exit(f"cannot reach '{name}' for the pen at px ({u:.0f},{v:.0f}): nothing planned")
        plan.append((name, q, g, hold))
    prev = now[side]
    for name, qj, _, _ in plan[1:]:  # the joint-space path stays above the floor (the raise starts from anywhere)
        if min(ik.fk(prev + t * (qj - prev))[2, 3] for t in np.linspace(0, 1, 25)) < floor - 0.003:
            sys.exit(f"the path into '{name}' dips below the table: nothing planned")
        prev = qj
    q_lift = q
    present = None  # the claw horizontal toward the audience (tool z = +x), fingers closing sideways: the pen stands up
    for dz in (0.0, -0.05, 0.05, -0.10):
        for ysign in (1, -1):
            T = np.eye(4)
            T[:3, :3] = np.column_stack([np.cross([0, ysign, 0], [1, 0, 0]), [0, ysign, 0], [1, 0, 0]])
            T[:3, 3] = [xy[0], xy[1], top + PRESENT_Z + dz]
            qp = solve(ik, q_lift, T)
            if qp is not None and (present is None or np.abs(qp - q_lift).sum() < np.abs(present - q_lift).sum()):
                present = qp
        if present is not None:
            break
    other = "left" if side == "right" else "right"
    out, q0, g0 = [], now[side], 0.05

    def emit(name, qj, g, hold=0.0, event="chunk"):
        nonlocal q0, g0
        Q, G = segment(q0, qj, g0, g, hold)
        for i in range(0, len(Q), ROWS):
            q, gg = Q[i:i + ROWS], G[i:i + ROWS]
            out.append({"event": event, "n": len(out) + 1, "latency_s": 0.0, "t": [round(r / HZ, 4) for r in range(len(q))],
                        side: {"q": np.round(q, 5).tolist(), "g": np.round(gg, 4).tolist()},
                        other: {"q": [now[other].round(5).tolist()] * len(q), "g": [0.05] * len(q)}})
        q0, g0 = qj, g

    for name, qj, g, hold in plan:
        emit(name, qj, g, hold)
    # "wave" events: dream_run plays them after the lift instead of its own wiggle, then the chunks backwards.
    # Present, roll the wrist (j6) left, right, left, back, then down to the lift pose again.
    if present is None:
        print("no pose with the claw toward the audience: dream_run wiggles at the lift instead", file=sys.stderr)
    else:
        emit("present", present, 0.0, 0.3, "wave")
        for r in (ROLL, -ROLL, ROLL, 0.0):
            emit("roll", present + np.r_[0, 0, 0, 0, 0, r], 0.0, 0.0, "wave")
        emit("lower", q_lift, 0.0, 0.0, "wave")
    print(f"pen at px ({u:.0f},{v:.0f}) -> {side} arm ({xy[0]:.3f},{xy[1]:.3f}); {len(out)} chunks, "
          f"{sum(len(c['t']) for c in out) / HZ:.1f} s", file=sys.stderr)
    json.dump(out, sys.stdout)


if __name__ == "__main__":
    main()
