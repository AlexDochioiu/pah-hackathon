"""Pick a block seen in the overhead camera and place it somewhere else, with direct arm control (no Dream-1).

  pick.py --from U,V --to U,V [--level N] [--dry]

U,V are overhead-image pixels (640x360, ~/dial/snaps/overhead.jpg). The block under --from is found by colour to
get its centre and rotation; it goes down at --to, on top of N blocks (--level, 0 = on the table). The arm whose base
is nearer does it. --dry plans and checks every waypoint without moving anything.

Every pose is solved in simulation first (the kit's mink IK on i2rt's model) and checked before any motor moves; the
gripper never goes below the table floor in calib.json. Needs calib.json from calib.py.
"""
import argparse, json, pathlib, sys, time
import cv2, numpy as np, yaml
from dream_hackathon import arms, config, station_lock
from dream_hackathon.arms import yam

DIR = pathlib.Path.home() / "dial"
HOVER = 0.10      # m above the block top for approach/retreat
GRIP_DEPTH = 0.012  # ponytail: m below the block top the fingers close at; calibration knob
BLOCK_H = 0.025   # m, height of one block for stacking; measure if stacks land high or low
MAX_RAD_S = 0.6   # joint speed while executing
POS_TOL, ROT_TOL = 0.006, 0.08  # m, rad: a solved waypoint further off than this aborts the move

cal = json.loads((DIR / "calib.json").read_text())
H = np.asarray(cal["H"])            # overhead pixel -> left-arm frame (x, y)
OFFSET = np.asarray(cal["right_offset"])  # right-arm frame = left-arm frame + OFFSET
TOP_ZS = cal["block_top_z"]         # per arm: block top on the table, in that arm's frame


def to_left(u, v):
    p = H @ [u, v, 1.0]
    return p[:2] / p[2]


def block_at(img, u, v):
    """Centre (u, v) and long-axis angle (rad, image) of the coloured blob nearest the pixel."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = ((hsv[..., 1] > 110) & (hsv[..., 2] > 90)).astype(np.uint8)
    n, lab = cv2.connectedComponents(cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8)))
    best = None
    for i in range(1, n):
        ys, xs = np.nonzero(lab == i)
        if len(xs) < 60:
            continue
        d = np.hypot(xs.mean() - u, ys.mean() - v)
        if d < 25 and (best is None or d < best[0]):
            best = (d, np.stack([xs, ys], 1).astype(np.float32))
    if best is None:
        sys.exit(f"no block found near pixel {u},{v}")
    (cu, cv_), (w, h), ang = cv2.minAreaRect(best[1])
    long_axis = np.deg2rad(ang if w >= h else ang + 90)
    return cu, cv_, long_axis


def closing_dir(u, v, long_axis_img):
    """Horizontal finger-closing direction in the left-arm frame: across the block's long side."""
    a = to_left(u, v)
    b = to_left(u + 10 * np.cos(long_axis_img), v + 10 * np.sin(long_axis_img))
    along = (b - a) / np.linalg.norm(b - a)
    return np.array([-along[1], along[0], 0.0])


def top_down(p, close):
    """4x4 grasp-site pose: approach straight down, fingers closing along `close`."""
    z = np.array([0.0, 0.0, -1.0])
    y = close / np.linalg.norm(close)
    if y[1] < 0:  # ponytail: of the two equivalent finger directions, keep the one with less wrist roll
        y = -y
    T = np.eye(4)
    T[:3, :3] = np.column_stack([np.cross(y, z), y, z])
    T[:3, 3] = p
    return T


def solve(ik, q, T, iters=600):
    """Joints reaching pose T from q, by running the IK in simulation; None if it cannot get close."""
    q = np.asarray(q, float)
    for _ in range(iters):
        q = ik.step(T, q, 0.02)
    F = ik.fk(q)
    pos_err = np.linalg.norm(F[:3, 3] - T[:3, 3])
    rot_err = np.arccos(np.clip((np.trace(F[:3, :3].T @ T[:3, :3]) - 1) / 2, -1, 1))
    return q if pos_err < POS_TOL and rot_err < ROT_TOL else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="src", required=True)
    ap.add_argument("--to", dest="dst", required=True)
    ap.add_argument("--level", type=int, default=0)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--probe", type=float, default=0, help="only hover 3 cm above the block for this many s")
    args = ap.parse_args()
    su, sv = map(float, args.src.split(","))
    du, dv = map(float, args.dst.split(","))

    img = cv2.imread(str(DIR / "snaps/overhead.jpg"))
    bu, bv, axis = block_at(img, su, sv)
    src, dst = to_left(bu, bv), to_left(du, dv)
    close = closing_dir(bu, bv, axis)
    side = "left" if abs(src[1]) <= abs(src[1] + OFFSET[1]) else "right"
    shift = np.zeros(2) if side == "left" else OFFSET[:2]
    src_a, dst_a = src + shift, dst + shift
    TOP_Z = TOP_ZS[side]
    FLOOR = TOP_Z - 0.02  # never put the grasp point lower than this
    print(f"block at px ({bu:.0f},{bv:.0f}) -> {side} arm ({src_a[0]:.3f},{src_a[1]:.3f}); place ({dst_a[0]:.3f},{dst_a[1]:.3f}) level {args.level}")

    pick_z = max(TOP_Z - GRIP_DEPTH, FLOOR)
    place_z = max(TOP_Z - GRIP_DEPTH + args.level * BLOCK_H + 0.005, FLOOR)
    plan = [  # (name, xyz, gripper 0 closed .. 1 open)
        ("above block", [*src_a, TOP_Z + HOVER], 1.0),
        ("down", [*src_a, pick_z], 1.0),
        ("grip", [*src_a, pick_z], 0.0),
        ("lift", [*src_a, TOP_Z + HOVER + args.level * BLOCK_H], 0.0),
        ("above target", [*dst_a, TOP_Z + HOVER + args.level * BLOCK_H], 0.0),
        ("down", [*dst_a, place_z], 0.0),
        ("release", [*dst_a, place_z], 1.0),
        ("up", [*dst_a, TOP_Z + HOVER + args.level * BLOCK_H], 1.0),
    ]
    if args.probe:
        plan = [plan[0], ("probe", [*src_a, TOP_Z + 0.03], 1.0), plan[0]]

    station = config.load("configs/station.yaml")
    ik = yam.teleop_ik(station)[side]
    home = yaml.safe_load((pathlib.Path.home() / ".dreamlabs/station.yaml").read_text())
    q_rest = np.asarray(home["follower_l" if side == "left" else "follower_r"]["home"])

    q_up = q_rest + np.array([0, 0.8, 1.2, 0, 0, 0])  # lift.py's raised pose: start and end through it
    joints, q = [("raise", q_up, 1.0)], q_up
    for name, p, g in plan:
        assert p[2] >= FLOOR - 1e-9, f"{name}: below the table floor"
        q = solve(ik, q, top_down(np.asarray(p), close))
        if q is None:
            sys.exit(f"cannot reach '{name}' at {np.round(p, 3).tolist()}: move the block closer, nothing moved")
        joints.append((name, q, g))
        print(f"  ok {name:12s} z={p[2]:.3f}")
    joints.append(("raise", q_up, 1.0))
    # The motors interpolate in joint space: check the grasp point stays above the floor along every segment.
    prev = q_rest
    for name, qj, _ in joints:
        low = min(ik.fk(prev + t * (qj - prev))[2, 3] for t in np.linspace(0, 1, 25))
        if low < FLOOR - 0.003:
            sys.exit(f"path into '{name}' dips to z={low:.3f}, below the floor {FLOOR:.3f}: nothing moved")
        prev = qj
    if args.dry:
        print("dry run: plan ok, nothing moved")
        return

    lock = station_lock.acquire("dial-pick")
    a = arms.open_arms(station, energize=True)
    try:
        js, gs = a.read()
        if float(np.max(np.abs(js[side] - q_rest))) > 0.25:
            sys.exit(f"the {side} arm is not at rest: fold it (or run home.py) first")
        start = {s: js[s].copy() for s in js}

        def go(target_q, grip, hold=0.0, hz=50):
            cur, cg = a.read()
            secs = max(0.6, float(np.max(np.abs(target_q - cur[side]))) / MAX_RAD_S)
            for k in range(1, int(secs * hz) + 1):
                t = k / (secs * hz); t = t * t * (3 - 2 * t)
                cmd = {s: start[s] for s in cur}
                cmd[side] = cur[side] + t * (target_q - cur[side])
                grips = dict(cg); grips[side] = cg[side] + t * (grip - cg[side])
                a.command(cmd, grips)
                time.sleep(1 / hz)
            time.sleep(hold)

        for i, (name, qj, g) in enumerate(joints):
            print("->", name, flush=True)
            go(qj, g, hold=args.probe if name == "probe" else 0.4 if name in ("grip", "release") else 0.0)
            if name == "grip":
                held = a.read()[1][side]
                print(f"   gripper at {held:.2f} after closing", flush=True)
                if held < 0.05:  # ponytail: fully closed means it closed on air; threshold is a knob
                    print("MISSED: nothing between the fingers; backing off", flush=True)
                    go(joints[i - 1][1], 1.0, hold=0.3)  # open where it is
                    go(joints[1][1], 1.0)  # straight back up to 'above block'
                    go(q_up, 1.0)
                    break
        print("-> rest")
        go(start[side], 1.0)
        time.sleep(0.5)
    finally:
        a.close()
        lock.close()


if __name__ == "__main__":
    main()
