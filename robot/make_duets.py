"""Builds the duet library (duets/*.json): choreography as Cartesian targets for the grippers in the shared frame,
solved by IK on the sim model at build time and stored as absolute joints for both arms. Then run duets.py (checker),
check_gestures.py and preview_duets.py.  Run: python robot/make_duets.py [names]

Targets: the right arm's are the mirror image of the left's about the midplane y = MID_Y unless given apart.
Frame helpers: frame(point, up) = gripper pointing along `point`, its "up" (away from the fingers' underside) along
`up`; the stage pose is frame((1, 0, 0.1), (0, 0, 1)).
"""
import json, sys
import mujoco, numpy as np
import show, duets

SIDES = show.SIDES
DEBUG = False
DT = 0.04                      # key spacing; the player runs a monotone cubic through the keys
MID_Y = -show.RIGHT_OFFSET[1] / 2
MID_X = -show.RIGHT_OFFSET[0] / 2
M = np.diag([1.0, -1.0, 1.0])

SC = show.Scene()
_JID = {s: [SC.m.joint(f"{duets.PRE[s]}joint{i}").id for i in range(1, 7)] for s in SIDES}
_DOF = {s: SC.m.jnt_dofadr[_JID[s]] for s in SIDES}


def unit(v):
    v = np.asarray(v, float)
    return v / np.linalg.norm(v)


def aim(az, el):
    """Pointing direction (roll free) for the left arm: az rad from +x toward the other arm (-y), el rad up."""
    return np.array([np.cos(el) * np.cos(az), -np.cos(el) * np.sin(az), np.sin(el)])


def frame(point, up=(0, 0, 1)):
    """Rotation whose z (grasp axis) is `point` and whose -x is `up` (orthogonalised): the site's x points down."""
    z = unit(point)
    x = -np.asarray(up, float); x = unit(x - x @ z * z)
    return np.column_stack([x, np.cross(z, x), z])


def mirror_target(p, R):
    """The left target's mirror image for the right arm (about y = MID_Y, x shifted by the base offset)."""
    p = np.asarray(p, float)
    p2 = np.array([p[0] + 2 * MID_X, 2 * MID_Y - p[1], p[2]])
    R = np.asarray(R, float)
    return p2, (M @ R @ np.diag([1.0, -1.0, 1.0])) if R.shape == (3, 3) else M @ R


def fk(side, q):
    SC.set(side, q); mujoco.mj_kinematics(SC.m, SC.d)
    i = SC.site[side]
    return SC.d.site_xpos[i].copy(), SC.d.site_xmat[i].reshape(3, 3).copy()


def rot_err(Rt, Rc):
    q = np.zeros(4); mujoco.mju_mat2Quat(q, (Rt @ Rc.T).ravel())
    v = np.zeros(3); mujoco.mju_quat2Vel(v, q, 1.0)
    return v


def ik(side, p, R, seed, w_rot=0.3, iters=600, prefer=None):
    """Joints putting the side's grasp site at p with rotation R (damped least squares, joint limits clipped).
    Returns (q, position error m, rotation error rad)."""
    m, d = SC.m, SC.d
    q = np.array(seed, float)
    pref = q.copy() if prefer is None else np.asarray(prefer, float)
    jp, jr = np.zeros((3, m.nv)), np.zeros((3, m.nv))
    for it in range(iters):
        SC.set(side, q); mujoco.mj_kinematics(m, d); mujoco.mj_comPos(m, d)
        i = SC.site[side]
        ep = p - d.site_xpos[i]
        Rc = d.site_xmat[i].reshape(3, 3)
        er = rot_err(R, Rc) if R.shape == (3, 3) else np.cross(Rc[:, 2], R)  # R (3,) = pointing only, roll free
        if it > iters // 3 and np.linalg.norm(ep) < 2e-4 and np.linalg.norm(er) < 2e-3:
            break
        mujoco.mj_jacSite(m, d, jp, jr, i)
        J = np.vstack([jp[:, _DOF[side]], w_rot * jr[:, _DOF[side]]])
        e = np.concatenate([ep * min(1, 0.03 / max(np.linalg.norm(ep), 1e-9)),
                            w_rot * er * min(1, 0.2 / max(np.linalg.norm(er), 1e-9))])  # small steps: no divergence
        lam = 1e-3 if it < iters // 3 else 1e-6  # posture bias first, then converge exactly
        dq = np.linalg.solve(J.T @ J + (1e-3 + lam) * np.eye(6), J.T @ e + lam * (pref - q))
        q = np.clip(q + np.clip(dq, -0.2, 0.2), show.SAFE_LO, show.SAFE_HI)
    pc, Rc = fk(side, q)
    re = rot_err(R, Rc) if R.shape == (3, 3) else [np.arccos(np.clip(Rc[:, 2] @ R, -1, 1))]
    return q, float(np.linalg.norm(p - pc)), float(np.linalg.norm(re))


def slerp(R0, R1, u):
    """Rotation a fraction u of the way from R0 to R1 (or, for (3,) pointing vectors, the direction)."""
    if np.shape(R1) == (3,):
        a = R0[:, 2] if np.shape(R0) == (3, 3) else np.asarray(R0)
        ang = np.arccos(np.clip(a @ R1, -1, 1))
        if ang < 1e-6:
            return np.asarray(R1, float)
        return unit((np.sin((1 - u) * ang) * a + np.sin(u * ang) * np.asarray(R1)) / np.sin(ang))
    q0, q1, qd, qu = np.zeros(4), np.zeros(4), np.zeros(4), np.zeros(4)
    mujoco.mju_mat2Quat(q0, R0.ravel()); mujoco.mju_mat2Quat(q1, R1.ravel())
    mujoco.mju_negQuat(qd, q0); mujoco.mju_mulQuat(qd, qd, q1)
    v = np.zeros(3); mujoco.mju_quat2Vel(v, qd, 1.0)  # rotation vector R0 -> R1
    step = np.array([1.0, 0, 0, 0]); mujoco.mju_quatIntegrate(step, v, float(u))
    mujoco.mju_mulQuat(qu, q0, step)
    R = np.zeros(9); mujoco.mju_quat2Mat(R, qu)
    return R.reshape(3, 3)


def pose(p=None, **k):
    """Joint deltas from the stage pose (left-arm convention), as in make_gestures.pose: p = head pitch (+ up)."""
    q = np.zeros(6)
    for n, v in k.items():
        q[int(n[1]) - 1] = v
    if p is not None:
        q[3] = q[1] - q[2] + p
    return q


def approach_law(D, zone=0.04, vc=0.02, vmax=0.35, acc=0.6, taper=0.03):
    """Arc-length time law for a straight move of length D that ends in a slow creep: accelerate, cruise, slow to vc
    `zone` metres before the end, creep, stop gently. Returns u = s/D sampled every DT (ends at 1)."""
    s = np.linspace(0, D, 2000)
    v = np.minimum.reduce([np.sqrt(2 * acc * s) + 0.004, np.full_like(s, vmax),
                           np.sqrt(vc ** 2 + 2 * acc * np.maximum(0, D - zone - s)),
                           np.sqrt(2 * taper * np.maximum(D - s, 0)) + 1e-4])
    v = np.where(s >= D - zone, np.minimum(v, vc), v)
    t = np.concatenate([[0], np.cumsum(np.diff(s) / ((v[1:] + v[:-1]) / 2))])
    n = max(1, int(np.ceil(t[-1] / DT)))
    return np.interp(np.linspace(0, t[-1], n + 1)[1:], t, s) / max(D, 1e-9)


class Seq:
    """Both arms on one clock: moves (joint-space or straight Cartesian lines), holds and grips, sampled every DT."""

    def __init__(self):
        self.q = {s: show.stage_pose(s).copy() for s in SIDES}
        self.rows = [(0.0, self.q["left"].copy(), self.q["right"].copy())]
        self.t = 0.0
        self.grips = {s: [] for s in SIDES}
        self.marks = {}

    def _target(self, side, tgt):
        """None (stay), joint deltas from stage (6,), or a Cartesian (p, R) -> ("q", joints) or ("x", p, R)."""
        if tgt is None:
            return ("q", self.q[side].copy())
        if isinstance(tgt, tuple):
            return ("x", np.asarray(tgt[0], float), tgt[1])
        return ("q", show.stage_pose(side) + show.mirror(tgt, side))

    def _solve(self, side, p, R, seed):
        q, ep, er = ik(side, p, R, seed)
        if ep > 0.0015 or er > 0.01:  # keep the branch: retry longer from the same seed first
            q, ep, er = ik(side, p, R, seed, iters=3000)
        if ep > 0.0015 or er > 0.01:  # far from the seed: retry from the stage pose, keep the closest good one
            q2, ep2, er2 = ik(side, p, R, show.stage_pose(side))
            if ep2 + er2 / 10 < ep + er / 10:
                q, ep, er = q2, ep2, er2
        assert ep < 0.0015 and er < 0.01, f"IK failed for {side} at {np.round(p, 3)}: {ep*1000:.1f} mm, {er:.3f} rad"
        return q

    def move(self, left=None, right=None, T=0.8, line=False, approach=None, hold=0.0, mirror=True):
        """Move both arms at once. right defaults to the mirror image of left (Cartesian or joint deltas).
        line: straight Cartesian path (orientation slerp); approach=dict(zone, vc, ...): a line ending in a slow creep
        (the duration then follows from the law). Joint moves are quintic and stretched to the speed limits."""
        if right is None and mirror and left is not None:
            right = mirror_target(*left) if isinstance(left, tuple) else left
        tg = {"left": self._target("left", left), "right": self._target("right", right)}
        if approach is not None or line:
            start = {s: fk(s, self.q[s]) for s in SIDES}
            end = {s: (tg[s][1], tg[s][2]) if tg[s][0] == "x" else fk(s, tg[s][1]) for s in SIDES}
            D = max(np.linalg.norm(end[s][0] - start[s][0]) for s in SIDES)
            if approach is not None:
                U = approach_law(D, **approach)
                zone = approach.get("zone", 0.04)
                urot = np.clip(U * D / max(D - zone, 1e-6), 0, 1)
            else:  # quintic, stretched until the joint path it needs stays within ~80 % of the limits
                q = {s: self.q[s].copy() for s in SIDES}
                for s in SIDES:
                    path = [q[s]]
                    for u in np.linspace(0, 1, 31)[1:]:
                        path.append(self._solve(s, start[s][0] + u * (end[s][0] - start[s][0]),
                                                slerp(start[s][1], end[s][1], u), path[-1]))
                    T = max(T, show.move_time(np.zeros(6), np.abs(np.diff(path, axis=0)).max(0) * 30, 0, 0.8))
                n = max(1, int(np.ceil(T / DT)))
                U = show.quintic(np.arange(1, n + 1) / n); urot = U
            out = {s: [] for s in SIDES}
            for s in SIDES:
                q = self.q[s].copy()
                for u, ur in zip(U, urot):
                    q = self._solve(s, start[s][0] + u * (end[s][0] - start[s][0]), slerp(start[s][1], end[s][1], ur), q)
                    out[s].append(q)
            for k in range(len(U)):
                self.t += DT
                self.rows.append((self.t, out["left"][k], out["right"][k]))
        else:
            q1 = {s: self._solve(s, tg[s][1], tg[s][2], self.q[s]) if tg[s][0] == "x" else tg[s][1] for s in SIDES}
            T = max([T] + [show.move_time(self.q[s], q1[s], 0, 0.8) for s in SIDES])
            n = max(1, int(np.ceil(T / DT)))
            for k in range(1, n + 1):
                u = show.quintic(k / n)
                self.t += T / n
                self.rows.append((self.t, *(self.q[s] + u * (q1[s] - self.q[s]) for s in SIDES)))
        if DEBUG:
            print(f"  move -> t={self.t:5.2f}  {'approach' if approach else 'line' if line else 'joint'}  "
                  f"L {np.round(self.rows[-1][1], 2)}  R {np.round(self.rows[-1][2], 2)}")
        self.q = {"left": self.rows[-1][1].copy(), "right": self.rows[-1][2].copy()}
        return self.wait(hold) if hold else self

    def wait(self, T):
        n = max(1, int(round(T / DT)))
        for _ in range(n):
            self.t += T / n
            self.rows.append((self.t, self.q["left"].copy(), self.q["right"].copy()))
        return self

    def mark(self, name):
        self.marks[name] = self.t
        return self

    def grip(self, side, g, T=1.0):
        """Gripper side to g (0 closed .. 1 open) over T s from now (runs alongside the following moves)."""
        gs = self.grips[side]
        g0 = gs[-1][1] if gs else 1.0
        if not gs:
            gs.append((0.0, g0))
        gs += [(self.t, gs[-1][1]), (self.t + T, g)]
        return self

    def here(self, side):
        return fk(side, self.q[side])

    def home(self, T=1.0):
        return self.move(np.zeros(6), T=T)

    def duet(self, name, about, contact, prop=None):
        rows = self.rows
        keys = []
        for t, ql, qr in rows:
            k = {"t": round(float(t), 3), "left": [round(float(x), 4) for x in ql], "right": [round(float(x), 4) for x in qr]}
            for s in SIDES:
                gs = self.grips[s]
                if gs:
                    gt = [a for a, _ in gs]; gv = [b for _, b in gs]
                    k["g" + s[0]] = round(float(np.interp(t, gt, gv)), 3)
            keys.append(k)
        # dedupe equal times (holds of length 0)
        out = [keys[0]]
        for k in keys[1:]:
            if k["t"] > out[-1]["t"] + 1e-6:
                out.append(k)
        d = {"name": name, "about": about, "contact": contact, "keys": out}
        if prop:
            d["prop"] = prop
        return d


# ---------------------------------------------------------------- the library

SLOW = dict(zone=0.018, vc=0.024, vmax=0.5, acc=1.0, taper=0.06)  # per arm; two arms close at <= ~4.8 cm/s
HAND = ["fingers", "gripper", "wrist"]


def sword(y, z, az, el, x=0.33):
    """Left gripper at (x, y, z), its sword aimed az rad in toward the other arm and el rad up (roll free)."""
    return np.array([x, y, z]), aim(az, el)


def creep(D, vc=0.024):
    """Approach-law settings for a short final creep of D metres at vc (the whole move is the slow zone)."""
    return dict(zone=D, vc=vc, vmax=vc, acc=1.0, taper=0.06)


def cross_swords():
    """Blades cross in an X seen from the audience: the strike forms the X with the blades still apart in depth
    (along n, the common normal of the two blades), then a slow creep along n closes the last few cm."""
    s = Seq()
    gap = 0.015 + 2 * show.PROP_R  # blade centre lines at the clash

    def clash(z, az=0.85, el=0.5, x=0.31, along=0.12, sep=gap):
        """Both swords crossing the midplane `along` m out from the grippers, the crossing at height z."""
        aL = aim(az, el); aR = M @ aL
        n = unit(np.cross(aL, aR)); n = n if n[0] > 0 else -n  # the left blade passes in front
        pL = np.array([x, MID_Y - along * aL[1], z - along * aL[2]])
        pR, _ = mirror_target(pL, aL)
        return (pL + n * sep / 2, aL), (pR - n * sep / 2, aR)

    def out(z, dy, dz, az):
        """The left gripper dy further out and dz higher than at the clash, sword aimed az in."""
        p = clash(z)[0][0]
        return sword(p[1] + dy, p[2] + dz, az=az, el=0.5, x=p[0])

    z = 0.60
    s.move(out(z, 0.10, 0.0, 0.35), T=0.8, hold=0.1)                     # en garde
    s.move(out(z, 0.09, 0.06, 0.15), T=0.45)                             # wind up: out and up
    s.move(*clash(z, sep=gap + 0.07), T=0.4)                             # swing: X formed, still apart in depth
    s.move(*clash(z), approach=dict(zone=0.021, vc=0.021, vmax=0.15, acc=1.0, taper=0.06), hold=0.3)  # lock (slow)
    (pl, al), (pr, ar) = clash(z)
    for dy in (-0.04, 0.035, 0.0):                                       # struggle: the locked blades shove sideways
        s.move((pl + [0, dy, 0.01], al), (pr + [0, dy, 0.01], ar), T=0.5, line=True, mirror=False)
    s.move(*clash(z, sep=gap + 0.10), T=0.3, line=True)                  # unlock: straight back apart in depth
    s.move(out(z, 0.09, -0.04, 0.5), T=0.4, hold=0.15)                  # spring apart
    s.home(T=0.9)
    return s.duet("cross_swords", "En garde, wind up and clash: the swords lock in an X in the middle, shove "
                  "each other left and right, then spring apart.",
                  [{"left": ["prop"], "right": ["prop"], "min": 0.012},
                   {"left": ["prop"], "right": HAND, "min": 0.03}, {"left": HAND, "right": ["prop"], "min": 0.03},
                   {"left": HAND, "right": HAND, "min": 0.04}])


def _half_hand():
    """How far the hand (gripper, fingers closed on the prop, wrist) reaches sideways (site y) from the grasp site."""
    m, d = SC.m, SC.d
    SC.set("left", show.stage_pose("left"), duets.G_ON_PROP); mujoco.mj_kinematics(m, d)
    i = SC.site["left"]
    best = 0.0
    for g in range(m.ngeom):
        if m.body(m.geom_bodyid[g]).name in ("l_gripper", "l_tip_left", "l_tip_right", "l_link5") \
                and m.geom_type[g] == mujoco.mjtGeom.mjGEOM_MESH:
            mid = m.geom_dataid[g]
            v = m.mesh_vert[m.mesh_vertadr[mid]: m.mesh_vertadr[mid] + m.mesh_vertnum[mid]]
            w = d.geom_xpos[g] + v @ d.geom_xmat[g].reshape(3, 3).T
            best = max(best, float(np.abs((w - d.site_xpos[i]) @ d.site_xmat[i].reshape(3, 3)[:, 1]).max()))
    return best


HALF_HAND = _half_hand()


# Hand-to-hand contact (high five, handshake) is infeasible with the 15 cm props held: keeping link3 15 cm
# apart needs the hands angled in, and then the props cross. Prop-to-prop and prop-to-hand contacts only.


def tips(az, el, z, x=0.33, gap=0.015, sep=0.0):
    """Both props aimed (az, el) with their tips meeting over the midplane at height z, `gap` apart (plus `sep`)."""
    aL = aim(az, el)
    tipL = np.array([x, MID_Y + gap / 2 + sep / 2 + show.PROP_R, z])  # the capsule ends PROP_R past its endpoint
    pL = tipL - show.PROP_LEN * aL
    return (pL, aL), mirror_target(pL, aL)


def cheers():
    s = Seq()
    az, el, z = 0.9, 0.55, 0.66
    s.move(*tips(0.35, el, z - 0.04, sep=0.16), T=0.9, hold=0.1)         # raise the glasses
    s.move(*tips(0.15, el + 0.15, z + 0.02, sep=0.22), T=0.45)           # anticipation: lift and lean back out
    s.move(*tips(az, el, z, sep=0.05), T=0.5)                             # swing in, tips nearly touching
    s.move(*tips(az, el, z), approach=dict(zone=0.025, vc=0.021, vmax=0.12, acc=1.0, taper=0.06), hold=0.35)  # clink
    s.move(*tips(az, el, z, sep=0.06), T=0.3, line=True)                  # bounce off
    s.move(*tips(0.2, el + 0.2, z + 0.04, sep=0.24), T=0.5, hold=0.2)    # glasses up high: cheers!
    s.home(T=1.0)
    return s.duet("cheers", "Raise the glasses, lift, swing in and clink the tips together in the middle, then hold "
                  "them up high.", [{"left": ["prop"], "right": ["prop"], "min": 0.012}])


def poke():
    """The left arm pokes the right one on the side of its head; the right flinches away and shakes its head."""
    s = Seq()
    head = fk("right", show.stage_pose("right"))[0]
    def at(gap, az=1.15, el=0.05, dz=0.0):
        a = aim(az, el)
        # the right hand's inner face is HALF_HAND toward +y from its site; poke 4.5 cm behind the fingertips
        tip = head + np.array([-0.045, HALF_HAND + gap + show.PROP_R, dz])
        return tip - show.PROP_LEN * a, a
    s.grip("right", 0.0, 0.6)                                             # the right holds its prop (known width)
    s.move(at(0.14, az=0.8, el=0.35, dz=0.08), T=1.0, mirror=False)      # sneak up, prop raised: wind up
    s.move(at(0.015), approach=dict(zone=0.045, vc=0.04, vmax=0.3, acc=1.2, taper=0.08), mirror=False, hold=0.3)  # poke!
    s.move(at(0.05), T=0.2, line=True, mirror=False)                      # left snaps back
    flinch = pose(j1=0.45, j2=-0.05, j3=-0.1, p=-0.15, j5=-0.3)
    s.move(at(0.09, dz=0.02), flinch, T=0.4, mirror=False)               # right flinches away
    s.move(np.zeros(6), flinch + pose(j5=0.25), T=0.4, mirror=False)      # left goes back, giggling; right shakes its head
    s.move(None, flinch - pose(j5=0.25), T=0.35, mirror=False)
    s.move(None, np.zeros(6), T=0.8, mirror=False)
    return s.duet("poke", "The left arm sneaks up and pokes the right one on the side of its head with its prop; the "
                  "right flinches away and shakes its head.",
                  [{"left": ["prop"], "right": HAND, "min": 0.012}, {"left": HAND, "right": HAND, "min": 0.05},
                   {"left": ["prop"], "right": ["prop"], "min": 0.04}])


LIBRARY = [cross_swords, cheers]  # poke: its approach still breaks the accel limits


def main(names):
    duets.DUET_DIR.mkdir(exist_ok=True)
    dc = duets.DuetChecker()
    for fn in LIBRARY:
        if names and fn.__name__ not in names:
            continue
        d = fn()
        path = duets.DUET_DIR / f"{d['name']}.json"
        path.write_text(json.dumps(d, separators=(",", ":")) + "\n")
        du = duets.Duet(d)
        probs = dc.check(du)
        print(f"{d['name']:14s} {du.duration:4.1f} s  {'OK' if not probs else 'PROBLEMS'}  {du.stats}")
        for p in probs:
            print("   ", p)


if __name__ == "__main__" and sys.argv[1:2] != ["probe"]:
    main(sys.argv[1:])


if __name__ == "__main__" and sys.argv[1:2] == ["probe"]:
    np.set_printoptions(precision=3, suppress=True)
    for p, pt, up in [((0.33, -0.22, 0.55), (0.3, -1, 0.6), (0, 0, 1)),
                      ((0.35, -0.24, 0.62), (0, 0, 1), (0, 1, 0)),
                      ((0.35, -0.24, 0.62), (0, -0.4, 1), (0, 1, 0)),
                      ((0.38, -0.20, 0.45), (0.5, -1, 0.2), (0, 0, 1))]:
        q, e1, e2 = ik("left", np.array(p), frame(pt, up), show.stage_pose("left"))
        print(p, pt, q, round(e1 * 1000, 1), "mm", round(e2, 3))
