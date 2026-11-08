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
GRIP_AT = 0.3     # where along the pen (from the end nearer the arm) the first hand grips
HAND_UP = 0.05    # m along the pen above the first hand's grip where the second hand grips (fingers ~2 cm wide)
ROLL = 0.6        # rad of wrist roll each way: the pen sways left and right like a wand (owner, Nov 8)


def find_pen(img):
    """Centre (u, v), long-axis angle and length (px) of the biggest thin green blob: the pen, not Lars."""
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
            best = (st[i, 4], cu, cv_, np.deg2rad(ang if w >= h else ang + 90), max(w, h))
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


def handover(iks, a_side, b_side, present, now, top):
    """The other arm (b) takes the standing pen above a's claw from the side, a lets go and goes home, b lays it on the
    table in front of its own base and goes home. Our own clearance check between the arms (the stage's is off for
    raw moves): wrist to wrist > 10 cm, wrist to the other's fingers > 7 cm all along. None if anything fails."""
    sh = {"left": np.zeros(3), "right": np.r_[OFFSET[:2], 0.0]}
    ik_a, ik_b = iks[a_side], iks[b_side]
    Fa = ik_a.fk(present)
    grip_s = Fa[:3, 3] - sh[a_side] + [0, 0, HAND_UP]  # shared frame
    base_b = -sh[b_side]
    z = np.array([0.0, np.sign(grip_s[1] - base_b[1]), 0.0])  # b comes in sideways, from its own side
    G = grip_s + sh[b_side]
    q_b = None
    for y in ([1.0, 0, 0], [-1.0, 0, 0]):
        R = np.column_stack([np.cross(y, z), y, z])
        T = lambda pnt: np.block([[R, pnt[:, None]], [np.zeros((1, 3)), np.ones((1, 1))]])
        qs, q = [], now[b_side]
        for pnt in (G - 0.20 * z + [0, 0, 0.10], G - 0.08 * z, G, G + [0, 0, 0.10]):
            q = solve(ik_b, q, T(pnt), iters=1500)
            if q is None:
                break
            qs.append(q)
        if q is not None:
            q_b = qs
            break
    if q_b is None:
        print("handover: the other arm can't reach the pen", file=sys.stderr)
        return None
    Ta = Fa.copy()
    Ta[:3, 3] -= Fa[:3, 2] * 0.08  # a backs off along its claw
    q_back = solve(ik_a, present, Ta, iters=1500)
    top_b = TOP_ZS[b_side]
    q_above = solve(ik_b, q_b[3], top_down(np.array([0.30, 0.0, top_b + pick.HOVER]), np.array([0.0, 1.0, 0.0])), iters=1500)
    q_down = q_above is not None and solve(ik_b, q_above, top_down(np.array([0.30, 0.0, top_b - PEN_Z + 0.008]),
                                                                   np.array([0.0, 1.0, 0.0])), iters=1500)
    if q_back is None or q_above is None or q_down is None or q_down is False:
        print("handover: no pose to back off or to put it down", file=sys.stderr)
        return None
    steps = [(b_side, "b_stage", q_b[0], 1.0, 0.0), (b_side, "b_in", q_b[1], 1.0, 0.0), (b_side, "b_at", q_b[2], 1.0, 0.2),
             (b_side, "b_grip", q_b[2], 0.0, 0.5), (a_side, "a_open", present, 1.0, 0.3), (a_side, "a_back", q_back, 1.0, 0.0),
             (a_side, "a_home", now[a_side], 0.05, 0.0), (b_side, "b_up", q_b[3], 0.0, 0.0),
             (b_side, "b_above", q_above, 0.0, 0.0), (b_side, "b_down", q_down, 0.0, 0.2), (b_side, "b_open", q_down, 1.0, 0.3),
             (b_side, "b_lift", q_above, 1.0, 0.0), (b_side, "b_home", now[b_side], 0.05, 0.0)]

    def pts(side, q):
        F = iks[side].fk(q)
        return F[:3, 3] - sh[side], F[:3, 3] - 0.12 * F[:3, 2] - sh[side]  # fingers, wrist (shared frame)
    pose = {a_side: present, b_side: now[b_side]}
    floor_b = top_b - 0.02
    for arm, name, qj, _, _ in steps:
        o = a_side if arm == b_side else b_side
        og, ow = pts(o, pose[o])
        for t in np.linspace(0, 1, 30):
            q = pose[arm] + t * (qj - pose[arm])
            g, w = pts(arm, q)
            if np.linalg.norm(w - ow) < 0.10 or np.linalg.norm(w - og) < 0.07 or np.linalg.norm(g - ow) < 0.07:
                print(f"handover: '{name}' comes too close to the other arm", file=sys.stderr)
                return None
            if arm == b_side and name.startswith("b_") and name not in ("b_stage",) and iks[arm].fk(q)[2, 3] < floor_b - 0.003:
                print(f"handover: '{name}' dips below the table", file=sys.stderr)
                return None
        pose[arm] = qj
    return steps


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--q", required=True, help='JSON {"left": [6], "right": [6]}: where the arms are now')
    a = ap.parse_args()
    now = {s: np.asarray(v, float)[:6] for s, v in json.loads(a.q).items()}
    img = cv2.imread(str(DIR / "snaps/overhead.jpg"))
    u, v, axis, length = find_pen(img)
    p = to_left(u, v)
    side = "left" if abs(p[1]) <= abs(p[1] + OFFSET[1]) else "right"
    other = "left" if side == "right" else "right"
    shift = {"left": np.zeros(3), "right": np.r_[OFFSET[:2], 0.0]}  # arm frame = shared (left) frame + shift
    ends = [to_left(u + k * length / 2 * np.cos(axis), v + k * length / 2 * np.sin(axis)) + shift[side][:2] for k in (1, -1)]
    near, far = sorted(ends, key=np.linalg.norm)
    along = (far - near) / np.linalg.norm(far - near)
    xy = near + GRIP_AT * (far - near)  # off-centre (owner, Nov 8): the long end sticks up for the other hand
    close = closing_dir(u, v, axis)
    top = TOP_ZS[side]
    floor = top - 0.02
    iks = yam.teleop_ik(config.load("configs/station.yaml"))
    ik = iks[side]
    home = yaml.safe_load((pathlib.Path.home() / ".dreamlabs/station.yaml").read_text())
    q_up = np.asarray(home["follower_l" if side == "left" else "follower_r"]["home"]) + np.array([0, 0.8, 1.2, 0, 0, 0])
    plan, q = [("raise", q_up, 1.0, 0.0)], q_up
    for name, z, g, hold in [("above", top + pick.HOVER, 1.0, 0.0), ("down", top - PEN_Z, 1.0, 0.2),
                             ("grip", top - PEN_Z, 0.0, 0.5), ("lift", top + LIFT, 0.0, 0.0)]:
        # a far pen is at the edge of the reach (Nov 8: 'above' failed at px 297,118, solved at 297,117): lower hovers
        zs = {"lift": [z, top + 0.10, top + 0.06], "above": [z, top + 0.07, top + 0.05]}.get(name, [z])
        q = next((r for zz in zs for seed in (q, q_up)
                  if (r := solve(ik, seed, top_down(np.array([*xy, max(zz, floor)]), close), iters=1500)) is not None), None)
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
    # tool x is the pen's axis; at present it is -ysign * z: pick ysign so the long end is up
    up_sign = -1 if float(top_down(np.array([*xy, top]), close)[:2, 0] @ along) > 0 else 1
    mid_y = -OFFSET[1] / 2 + shift[side][1]  # halfway between the two bases, in this arm's frame: the handover spot
    # where: the handover spot, above the pen, else in front of the arm's own base (Nov 8: a far-left pen had no pose)
    for px, py, dz in [(0.30, mid_y, 0.0), (0.30, mid_y, -0.05), (0.25, mid_y, 0.0), (xy[0], xy[1], 0.0), (0.30, 0.0, 0.0),
                       (0.25, 0.0, 0.0), (0.30, 0.0, -0.05), (0.35, 0.0, 0.05), (0.25, 0.0, -0.08), (xy[0], xy[1], -0.08)]:
        for ysign in (up_sign, -up_sign):
            T = np.eye(4)
            T[:3, :3] = np.column_stack([np.cross([0, ysign, 0], [1, 0, 0]), [0, ysign, 0], [1, 0, 0]])
            T[:3, 3] = [px, py, top + PRESENT_Z + dz]
            for seed in (q_lift, q_up):
                qp = solve(ik, seed, T, iters=1500)
                if qp is not None and (present is None or np.abs(qp - q_lift).sum() < np.abs(present - q_lift).sum()):
                    present, pen_up, at_mid = qp, ysign == up_sign, py == mid_y
        if present is not None:
            break
    hand = handover(iks, side, other, present, now, top) if present is not None and pen_up and at_mid else None
    out, cur = [], {s: [now[s], 0.05] for s in now}

    def emit(arm, name, qj, g, hold=0.0, event="chunk"):
        Q, G = segment(cur[arm][0], qj, cur[arm][1], g, hold)
        o = "left" if arm == "right" else "right"
        for i in range(0, len(Q), ROWS):
            q, gg = Q[i:i + ROWS], G[i:i + ROWS]
            out.append({"event": event, "arm": arm, "name": name, "n": len(out) + 1, "latency_s": 0.0,
                        "t": [round(r / HZ, 4) for r in range(len(q))],
                        arm: {"q": np.round(q, 5).tolist(), "g": np.round(gg, 4).tolist()},
                        o: {"q": [np.round(cur[o][0], 5).tolist()] * len(q), "g": [cur[o][1]] * len(q)}})
        cur[arm] = [qj, g]

    for name, qj, g, hold in plan:
        emit(side, name, qj, g, hold)
    # "wave" events: dream_run plays them after the lift instead of its own wiggle, then the chunks backwards.
    # Present, roll the wrist (j6) left, right, left, back, then down to the lift pose again.
    if present is None:
        print("no pose with the claw toward the audience: dream_run wiggles at the lift instead", file=sys.stderr)
    else:
        emit(side, "present", present, 0.0, 0.3, "wave")
        for r in (ROLL, -ROLL, ROLL, 0.0):
            emit(side, "roll", present + np.r_[0, 0, 0, 0, 0, r], 0.0, 0.0, "wave")
        if hand:  # "hand" events: the other arm takes it, this one lets go and goes home, the other puts it down
            for arm, name, qj, g, hold in hand:
                emit(arm, name, qj, g, hold, "hand")
        else:
            print("no handover: the same arm puts it back", file=sys.stderr)
            emit(side, "lower", q_lift, 0.0, 0.0, "wave")
    print(f"pen at px ({u:.0f},{v:.0f}) -> {side} arm ({xy[0]:.3f},{xy[1]:.3f}); handover {'yes' if hand else 'no'}; {len(out)} chunks, "
          f"{sum(len(c['t']) for c in out) / HZ:.1f} s", file=sys.stderr)
    json.dump(out, sys.stdout)


if __name__ == "__main__":
    main()
