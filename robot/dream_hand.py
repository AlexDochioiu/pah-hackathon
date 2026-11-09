"""After Dream-1 lifts the green pen (dream_run.py --handover): our own IK shows it to the audience, rolls it like a
wand and hands it to the other arm, which lays it on the table. cam_pick.py's present, roll and handover() (the
plan the owner saw run without collisions, Nov 8), seeded from where Dream-1 left the arm. Runs on the station, in
~/dial next to cam_pick.py and pick.py:

  cd ~/physical-ai-hacks && .venv/bin/python ~/dial/snap.py > /dev/null && .venv/bin/python ~/dial/dream_hand.py locate
      before Dream-1 starts: the pen's two ends on the table, from a fresh overhead snap (JSON on stdout)
  cd ~/physical-ai-hacks && .venv/bin/python ~/dial/dream_hand.py plan --q '{...}'
      after the lift: "wave" and "hand" chunk events on stdout (cam_pick.py's format) for dream_run to play

plan's --q: {"arm": the arm Dream-1 used, "now": {"left": [6], "right": [6]} where both arms started (each goes home
there), "q": [6] and "g": that arm's joints and gripper now, "grasp": [6] its joints when its gripper closed, "pen":
locate's output, or null}.

Where the pen sits in the claw comes from the grasp: the pen lay flat along its two ends, and from the close on it
turns with the claw, so its axis in the claw's frame is the grasp rotation's transpose times that direction. The
longer end is the one farther from the grasp point. The handover is planned only if Dream-1 closed on the pen (the
grasp point within GRASP_TOL of the pen in the snap), the pen lies along the claw's x (the fingers closed across it,
as in cam_pick's own grasp) and its longer end leaves room for the second hand. Otherwise the same arm rolls it, comes
back to where Dream-1 left it, and dream_run puts it down the way Dream-1 came (checked).

Every move is solved and floor-checked here, and this arm's moves before the handover are checked against the other
arm with handover()'s distances (handover() checks its own). Exit 1 with the reason on stderr when there's no
plan at all: dream_run then wiggles the wand where it is and goes back the way Dream-1 came.
"""
import argparse, json, sys
import cv2, numpy as np
import cam_pick  # ~/dial/cam_pick.py: find_pen, segment, handover() and their constants
from cam_pick import (DIR, HZ, LIFT, OFFSET, PRESENT_Z, ROLL, ROWS, TOP_ZS, config, find_pen, handover, pathlib, segment,
                      solve, to_left, yam, yaml)

GRASP_TOL = 0.025   # m: Dream-1's grasp point at most this far from the pen in the snap: it closed on the pen. ponytail: knob
AXIS_COS = 0.94     # the pen within ~20 degrees of the claw's x axis (the fingers closed across it): handover() takes
                    # it as along x, so the second hand's grip is off by at most ~2 cm. ponytail: knob
FINGER_ROOM = 0.015  # m of pen left past the second hand's grip
HAND_UP_MIN = 0.04  # m: the second hand closer than this to the first one's claw clashes with its fingers (~2 cm wide)
SH = {"left": np.zeros(3), "right": np.r_[OFFSET[:2], 0.0]}  # arm frame = shared (left) frame + SH


def locate():
    img = cv2.imread(str(DIR / "snaps/overhead.jpg"))
    if img is None:
        sys.exit("no overhead snap")
    u, v, axis, length = find_pen(img)  # exits when there is no green pen
    ends = [to_left(u + k * length / 2 * np.cos(axis), v + k * length / 2 * np.sin(axis)) for k in (1, -1)]
    print(f"pen at px ({u:.0f},{v:.0f})", file=sys.stderr)
    json.dump({"px": [round(float(u), 1), round(float(v), 1)], "ends": [np.round(e, 4).tolist() for e in ends]}, sys.stdout)


def pen_in_hand(ik, side, grasp, pen):
    """(long_sign, long_len): the pen's longer end is along long_sign * the claw's x, long_len m from the grip. A
    string instead: why there's no handover."""
    F = ik.fk(np.asarray(grasp, float)[:6])
    p = (F[:3, 3] - SH[side])[:2]  # the grasp point, shared frame
    e0, e1 = (np.asarray(e, float)[:2] for e in pen["ends"])
    L = float(np.linalg.norm(e1 - e0))
    if L < 0.03:
        return f"the pen is only {L * 100:.1f} cm long in the snap"
    d = (e1 - e0) / L
    s = float(np.clip((p - e0) @ d, 0.0, L))
    off = float(np.linalg.norm(p - (e0 + s * d)))
    if off > GRASP_TOL:
        return f"Dream-1 closed {off * 100:.1f} cm from the pen in the snap (it moved, or that's something else)"
    long_dir, long_len = (d, L - s) if L - s >= s else (-d, s)
    x = F[:3, :3].T @ np.r_[long_dir, 0.0]  # in the claw's frame (the arm frames only differ by a shift)
    if abs(x[0]) < AXIS_COS:
        return f"the pen isn't along the claw (cos {x[0]:.2f}): Dream-1 didn't close across it"
    return (1.0 if x[0] > 0 else -1.0), long_len


def pts(iks, side, q):
    F = iks[side].fk(q)
    return F[:3, 3] - SH[side], F[:3, 3] - 0.12 * F[:3, 2] - SH[side]  # fingers, wrist (shared frame), as handover()


def path_problem(iks, side, other, q0, q1, q_other, floor):
    """None if the joint-space move q0 -> q1 stays above the floor and clear of the other arm (handover()'s distances)."""
    og, ow = pts(iks, other, q_other)
    for t in np.linspace(0, 1, 30):
        q = q0 + t * (q1 - q0)
        if iks[side].fk(q)[2, 3] < floor - 0.003:
            return "dips below the table"
        g, w = pts(iks, side, q)
        if np.linalg.norm(w - ow) < 0.10 or np.linalg.norm(w - og) < 0.07 or np.linalg.norm(g - ow) < 0.07:
            return "comes too close to the other arm"
    return None


def plan(a):
    side = a["arm"]
    other = "left" if side == "right" else "right"
    now = {s: np.asarray(a["now"][s], float)[:6] for s in ("left", "right")}
    q_cur = np.asarray(a["q"], float)[:6]
    iks = yam.teleop_ik(config.load("configs/station.yaml"))
    ik = iks[side]
    top = TOP_ZS[side]
    floor = top - 0.02
    home = yaml.safe_load((pathlib.Path.home() / ".dreamlabs/station.yaml").read_text())
    q_up = np.asarray(home["follower_l" if side == "left" else "follower_r"]["home"]) + np.array([0, 0.8, 1.2, 0, 0, 0])

    got = pen_in_hand(ik, side, a["grasp"], a["pen"]) if a.get("grasp") and a.get("pen") else "no grasp or no pen snap"
    if isinstance(got, str):
        print(f"no handover: {got}", file=sys.stderr)
        long_sign, long_len, hand_ok = 1.0, 0.0, False  # the long end's side unknown: it may point down at present
    else:
        (long_sign, long_len), hand_ok = got, True

    # rise straight up (Dream-1 stops 3 cm above where it closed), the claw as Dream-1 left it
    F = ik.fk(q_cur)
    q_rise = q_cur
    if F[2, 3] < top + LIFT:
        for z in (top + LIFT, top + 0.10, top + 0.07):
            T = F.copy()
            T[2, 3] = z
            if (r := solve(ik, q_cur, T, iters=1500)) is not None:
                q_rise = r
                break

    # present: the claw toward the audience (tool z = +x), the pen standing up in it. As cam_pick.main: tool x is
    # -ysign * z there, so the long end (long_sign * tool x) is up with ysign = -long_sign
    up_sign = -long_sign
    mid_y = -OFFSET[1] / 2 + SH[side][1]  # halfway between the bases, in this arm's frame: the handover spot
    cx, cy = F[0, 3], F[1, 3]
    present = None
    pen_up = at_mid = False
    for px, py, dz in [(0.30, mid_y, 0.0), (0.30, mid_y, -0.05), (0.25, mid_y, 0.0), (cx, cy, 0.0), (0.30, 0.0, 0.0),
                       (0.25, 0.0, 0.0), (0.30, 0.0, -0.05), (0.35, 0.0, 0.05), (0.25, 0.0, -0.08), (cx, cy, -0.08)]:
        for ysign in (up_sign, -up_sign):
            T = np.eye(4)
            T[:3, :3] = np.column_stack([np.cross([0, ysign, 0], [1, 0, 0]), [0, ysign, 0], [1, 0, 0]])
            T[:3, 3] = [px, py, top + PRESENT_Z + dz]
            for seed in (q_rise, q_up):
                qp = solve(ik, seed, T, iters=1500)
                if qp is not None and (present is None or np.abs(qp - q_rise).sum() < np.abs(present - q_rise).sum()):
                    present, pen_up, at_mid = qp, ysign == up_sign, py == mid_y
        if present is not None:
            break
    if present is None:
        sys.exit("no pose with the claw toward the audience")

    moves = [(q_cur, q_rise, "rise"), (q_rise, present, "present")]
    rolls = [present + np.r_[0, 0, 0, 0, 0, r] for r in (ROLL, -ROLL, ROLL, 0.0)]
    moves += [(q0, q1, "roll") for q0, q1 in zip([present, *rolls[:-1]], rolls)]
    for q0, q1, name in moves:  # the way down again (no handover) is these, reversed
        if (why := path_problem(iks, side, other, q0, q1, now[other], floor)):
            sys.exit(f"'{name}' {why}")

    hand = None
    if hand_ok and pen_up and at_mid:
        cam_pick.HAND_UP = min(cam_pick.HAND_UP, long_len - FINGER_ROOM)  # ponytail: module global; handover() reads it
        if cam_pick.HAND_UP < HAND_UP_MIN:
            print(f"no handover: only {long_len * 100:.1f} cm of pen past the claw", file=sys.stderr)
        else:
            hand = handover(iks, side, other, present, {side: now[side], other: now[other]}, top)
    elif hand_ok:
        print("no handover: no pose at the handover spot with the pen standing up", file=sys.stderr)

    out, cur = [], {side: [q_cur, float(a.get("g", 0.0))], other: [now[other], 0.05]}

    def emit(arm, name, qj, g, hold=0.0, event="wave"):  # cam_pick.main's emit
        Q, G = segment(cur[arm][0], qj, cur[arm][1], g, hold)
        o = "left" if arm == "right" else "right"
        for i in range(0, len(Q), ROWS):
            q, gg = Q[i:i + ROWS], G[i:i + ROWS]
            out.append({"event": event, "arm": arm, "name": name, "n": len(out) + 1, "latency_s": 0.0,
                        "t": [round(r / HZ, 4) for r in range(len(q))],
                        arm: {"q": np.round(q, 5).tolist(), "g": np.round(gg, 4).tolist()},
                        o: {"q": [np.round(cur[o][0], 5).tolist()] * len(q), "g": [cur[o][1]] * len(q)}})
        cur[arm] = [qj, g]

    if np.abs(q_rise - q_cur).max() > 1e-3:
        emit(side, "rise", q_rise, 0.0)
    emit(side, "present", present, 0.0, 0.3)
    for q in rolls:
        emit(side, "roll", q, 0.0)
    if hand:
        for arm, name, qj, g, hold in hand:
            emit(arm, name, qj, g, hold, "hand")
    else:  # back to where Dream-1 left it: dream_run's way back starts there
        emit(side, "lower", q_rise, 0.0)
        if np.abs(q_rise - q_cur).max() > 1e-3:
            emit(side, "lower", q_cur, 0.0)
    print(f"{side} arm: handover {'yes' if hand else 'no'}; {len(out)} chunks, "
          f"{sum(len(c['t']) for c in out) / HZ:.1f} s", file=sys.stderr)
    json.dump(out, sys.stdout)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["locate", "plan"])
    ap.add_argument("--q", help="plan: JSON, see the docstring")
    a = ap.parse_args()
    if a.what == "locate":
        locate()
    elif not a.q:
        sys.exit("plan needs --q")
    else:
        plan(json.loads(a.q))


if __name__ == "__main__":
    main()
