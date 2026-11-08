"""Builds the duet library (duets/*.json): choreography as Cartesian targets for the grippers in the shared frame,
solved by IK on the sim model at build time and stored as absolute joints for both arms. Then run duets.py (checker),
check_gestures.py and preview_duets.py.  Run: python robot/make_duets.py [names]

Targets: the right arm's are the mirror image of the left's about the midplane y = MID_Y unless given apart.
Frame helpers: frame(point, up) = gripper pointing along `point`, its "up" (away from the fingers' underside) along
`up`; the stage pose is frame((1, 0, 0.1), (0, 0, 1)).
"""
import functools, json, sys
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
        self.hits = {}

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

    def move(self, left=None, right=None, T=0.8, line=False, approach=None, hold=0.0, mirror=True, frac=0.8):
        """Move both arms at once. right defaults to the mirror image of left (Cartesian or joint deltas).
        line: straight Cartesian path (orientation slerp); approach=dict(zone, vc, ...): a line ending in a slow creep
        (the duration then follows from the law). Joint moves are quintic and stretched until they peak at `frac` of
        the speed and acceleration limits (FAST: the shortened moves, Nov 8)."""
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
                urot = show.quintic(np.clip(U * D / max(D - zone, 1e-6), 0, 1))  # turn eases in and out
            else:  # quintic, stretched until the joint path it needs stays within ~80 % of the limits
                q = {s: self.q[s].copy() for s in SIDES}
                for s in SIDES:
                    path = [q[s]]
                    for u in np.linspace(0, 1, 31)[1:]:
                        path.append(self._solve(s, start[s][0] + u * (end[s][0] - start[s][0]),
                                                slerp(start[s][1], end[s][1], u), path[-1]))
                    T = max(T, show.move_time(np.zeros(6), np.abs(np.diff(path, axis=0)).max(0) * 30, 0, frac))
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
            T = max([T] + [show.move_time(self.q[s], q1[s], 0, frac) for s in SIDES])
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

    def path(self, fl, fr=None, T=1.0, warp=show.quintic):
        """Both arms along curves: fl(u) / fr(u) give targets as move() takes them (Cartesian (p, R) or joint deltas
        from stage; None = stay), u = 0..1 warped in time, sampled every DT with the IK seeded along the way (circles,
        loops). fr=None mirrors fl. The duration is NOT stretched to the speed limits: the checker has the last word."""
        n = max(1, int(np.ceil(T / DT)))
        for k in range(1, n + 1):
            u = warp(k / n) if warp else k / n
            tl = fl(u)
            tr = fr(u) if fr else (mirror_target(*tl) if isinstance(tl, tuple) else tl)
            q = {}
            for s, tgt in (("left", tl), ("right", tr)):
                tg = self._target(s, tgt)
                q[s] = self._solve(s, tg[1], tg[2], self.q[s]) if tg[0] == "x" else tg[1]
            self.t += T / n
            self.rows.append((self.t, q["left"], q["right"]))
            self.q = q
        return self

    def wait(self, T):
        n = max(1, int(round(T / DT)))
        for _ in range(n):
            self.t += T / n
            self.rows.append((self.t, self.q["left"].copy(), self.q["right"].copy()))
        return self

    def hit(self, sfx):
        """Now is a hit for the duet's "sound" (an agent/sounds/sfx name)."""
        self.hits.setdefault(sfx, []).append(round(self.t, 2))
        return self

    def grip(self, side, g, T=1.0, after=0.0):
        """Gripper side to g (0 closed .. 1 open) over T s from `after` s from now (runs alongside the following
        moves)."""
        gs = self.grips[side]
        g0 = gs[-1][1] if gs else 1.0
        if not gs:
            gs.append((0.0, g0))
        gs += [(self.t + after, gs[-1][1]), (self.t + after + T, g)]
        return self

    def here(self, side):
        return fk(side, self.q[side])

    def home(self, T=1.0, frac=0.8):
        return self.move(np.zeros(6), T=T, frac=frac)

    def duet(self, name, about, contact, prop=None, kind="duet", props=True, table=None):
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
        d = {"name": name, "kind": kind, "about": about, "contact": contact, "keys": out}
        if prop:
            d["prop"] = prop
        if props is not True:
            d["props"] = props  # False: empty hands, refused while a prop is held; {side: kind}: exactly these
        if table:
            d["table"] = table  # the hands may touch the table (show.TABLE_PARTS, empty hands only)
        for sfx, at in self.hits.items():  # (one sfx per duet)
            d["sound"] = {"name": sfx, "at": at[0] if len(at) == 1 else at}
        return d


# ---------------------------------------------------------------- the library

SLOW = dict(zone=0.018, vc=0.024, vmax=0.5, acc=1.0, taper=0.06)  # per arm; two arms close at <= ~4.8 cm/s
FAST = 0.95  # Nov 8: duets <= 8 s (gestures lagged the narration): big moves peak at 95 % of the limits, not 80 %
HAND = ["fingers", "gripper", "wrist"]


def sword(y, z, az, el, x=0.33):
    """Left gripper at (x, y, z), its sword aimed az rad in toward the other arm and el rad up (roll free)."""
    return np.array([x, y, z]), aim(az, el)


def creep(D, vc=0.024):
    """Approach-law settings for a short final creep of D metres at vc (the whole move is the slow zone)."""
    return dict(zone=D, vc=vc, vmax=vc, acc=1.0, taper=0.06)


def glide(e=0.2):
    """Time warp for Seq.path: smooth start and stop (sin^2 ramps over a fraction e at each end), steady between."""
    x = np.linspace(0, 1, 2001)
    v = np.sin(np.pi / 2 * np.clip(np.minimum(x, 1 - x) / e, 0, 1)) ** 2
    c = np.concatenate([[0], np.cumsum((v[1:] + v[:-1]) / 2)]); c /= c[-1]
    return lambda u: float(np.interp(u, x, c))


def mir(p):
    """The mirror image of a point (left side <-> right side, about the midplane)."""
    return np.array([p[0] + 2 * MID_X, 2 * MID_Y - p[1], p[2]])


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
        pR[0] = pL[0]  # the same x: the blades are then exactly `sep` apart along n (the bases' x offset would add to it)
        return (pL + n * sep / 2, aL), (pR - n * sep / 2, aR)

    def out(z, dy, dz, az):
        """The left gripper dy further out and dz higher than at the clash, sword aimed az in."""
        p = clash(z)[0][0]
        return sword(p[1] + dy, p[2] + dz, az=az, el=0.5, x=p[0])

    z = 0.50                                                             # the lowered stage (Nov 7): was 0.60
    s.move(out(z, 0.10, 0.0, 0.35), T=0.8, hold=0.1)                     # en garde
    s.move(out(z, 0.09, 0.06, 0.15), T=0.45)                             # wind up: out and up
    s.move(*clash(z, sep=gap + 0.07), T=0.4, frac=FAST)                  # swing: X formed, still apart in depth
    s.move(*clash(z), approach=dict(zone=0.021, vc=0.021, vmax=0.15, acc=1.0, taper=0.06), hold=0.15)  # lock (slow)
    (pl, al), (pr, ar) = clash(z)
    for dy in (-0.04, 0.0):                                              # struggle: the locked blades shove sideways
        s.move((pl + [0, dy, 0.01], al), (pr + [0, dy, 0.01], ar), T=0.4, line=True, mirror=False)
    s.move(*clash(z, sep=gap + 0.10), T=0.3, line=True)                  # unlock: straight back apart in depth
    s.move(out(z, 0.09, -0.04, 0.5), T=0.4, hold=0.1)                   # spring apart
    s.home(T=0.9, frac=FAST)
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
    az, el, z = 0.9, 0.55, 0.56                                           # lowered stage (Nov 7): was 0.66
    s.move(*tips(0.35, el, z - 0.04, sep=0.16), T=0.9, frac=FAST)        # raise the glasses
    s.move(*tips(0.25, el + 0.15, z + 0.02, sep=0.19), T=0.45)           # anticipation: lift and lean back out
    s.move(*tips(az, el, z, sep=0.05), T=0.5, frac=FAST)                  # swing in, tips nearly touching
    s.move(*tips(az, el, z), approach=dict(zone=0.025, vc=0.021, vmax=0.12, acc=1.0, taper=0.06), hold=0.2)  # clink
    s.move(*tips(az, el, z, sep=0.06), T=0.3, line=True)                  # bounce off
    s.move(*tips(0.35, el + 0.2, z + 0.04, sep=0.2), T=0.5, hold=0.15, frac=FAST)  # glasses up high: cheers!
    s.home(T=1.0, frac=FAST)
    return s.duet("cheers", "Raise the glasses, lift, swing in and clink the tips together in the middle, then hold "
                  "them up high.", [{"left": ["prop"], "right": ["prop"], "min": 0.012}])


POKE = -0.003  # target gap past the estimated inner face of the right hand: lands ~1.5 cm off it (the wrist sticks out)


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
    s.move(at(0.14, az=0.8, el=0.35, dz=0.08), T=1.0, mirror=False, frac=FAST)  # sneak up, prop raised: wind up
    s.move(at(0.075), T=0.5, mirror=False)                                # aim: the prop levels at the head
    s.move(at(POKE), approach=dict(zone=0.045, vc=0.04, vmax=0.3, acc=1.2, taper=0.08), mirror=False, hold=0.3)  # poke!
    s.move(at(0.05), T=0.2, line=True, mirror=False)                      # left snaps back
    flinch = pose(j1=0.45, j2=-0.05, j3=-0.1, p=-0.15, j5=-0.3)
    s.move(at(0.09, dz=0.02), flinch, T=0.4, mirror=False)               # right flinches away
    s.move(np.zeros(6), flinch + pose(j5=0.25), T=0.4, mirror=False, frac=FAST)  # left goes back, giggling; right
    s.move(None, np.zeros(6), T=0.6, mirror=False, frac=FAST)                     # shakes its head
    return s.duet("poke", "The left arm sneaks up and pokes the right one on the side of its head with its prop; the "
                  "right flinches away and shakes its head.",
                  [{"left": ["prop"], "right": HAND, "min": 0.012}, {"left": HAND, "right": HAND, "min": 0.05},
                   {"left": ["prop"], "right": ["prop"], "min": 0.04}])


def head_top(side, q, back=0.085, band=0.035):
    """Where a hat rests on the side's head at joints q: the point above the gripper body `back` m behind the grasp
    site, at the highest point of the hand (gripper, fingers on the prop) within `band` m of it in x."""
    m, d = SC.m, SC.d
    SC.set(side, q, duets.G_ON_PROP); mujoco.mj_kinematics(m, d)
    i = SC.site[side]
    c = d.site_xpos[i] - back * d.site_xmat[i].reshape(3, 3)[:, 2]
    top = -np.inf
    for g in range(m.ngeom):
        if m.body(m.geom_bodyid[g]).name in [duets.PRE[side] + b for b in ("gripper", "tip_left", "tip_right")] \
                and m.geom_type[g] == mujoco.mjtGeom.mjGEOM_MESH:
            mid = m.geom_dataid[g]
            v = m.mesh_vert[m.mesh_vertadr[mid]: m.mesh_vertadr[mid] + m.mesh_vertnum[mid]]
            w = d.geom_xpos[g] + v @ d.geom_xmat[g].reshape(3, 3).T
            w = w[np.abs(w[:, 0] - c[0]) < band]
            if len(w):
                top = max(top, float(w[:, 2].max()))
    return np.array([c[0], c[1], top])


def place_hat():
    """The left crowns the right: it raises its prop like a hat, brings it over the right's head and lays it gently
    across the top of the gripper; the right ducks its head to take it, then straightens up proudly."""
    s = Seq()
    duck = pose(j2=-0.02, j3=-0.1, p=-0.12)                                    # the right lowers its head a little
    proud = pose(j2=0.04, j3=0.05, p=0.12, j5=0.15)                            # chin up, a turn to the audience
    qd = show.stage_pose("right") + show.mirror(duck, "right")
    top = head_top("right", qd, back=0.055)                                    # over the finger roots

    def hat(gap, dy=0.0, el=-0.06):
        a = aim(1.75, el)                                                      # across the head, from the front-left
        end = top + [0.0, -0.03 + dy, show.PROP_R + gap]                       # the prop's far end, past the head centre
        return end - show.PROP_LEN * a, a

    s.move(hat(0.16, dy=0.08, el=0.15), pose(p=0.15, j5=0.2), T=1.0, mirror=False, hold=0.1, frac=FAST)  # hat held up high;
    # the right looks up at it
    s.move(hat(0.05), duck, T=0.6, mirror=False)                                # over the head; the right ducks
    s.move(hat(0.012), None, approach=dict(zone=0.04, vc=0.04, vmax=0.25, acc=0.8, taper=0.06),
           mirror=False, hold=0.3)                                              # lay it on, gently
    s.move(hat(0.07, dy=0.03), None, T=0.3, line=True, mirror=False)           # let go
    s.move(hat(0.16, dy=0.08, el=0.1), proud, T=0.5, mirror=False)             # the right, proud, chin up
    s.move(np.zeros(6), np.zeros(6), T=0.8, mirror=False, frac=FAST)
    return s.duet("place_hat", "The left raises its prop like a hat and lays it gently on the right's head; the "
                  "right ducks to take it, then straightens up proudly.",
                  [{"left": ["prop"], "right": HAND, "min": 0.008}, {"left": HAND, "right": HAND, "min": 0.05},
                   {"left": HAND, "right": ["prop"], "min": 0.05}, {"left": ["prop"], "right": ["prop"], "min": 0.04}])


FACE = pose(j1=-0.2, j5=0.25)  # the each_other stance (show.FACING_LEFT), as a delta


def quick_draw():
    """Western standoff: guns (props) low in the holsters, fingers twitch... DRAW! The left is faster: BANG, its gun
    kicks up with the recoil; the right, hit mid-draw, jerks back, drops its gun and slumps. The left raises its
    barrel: smoke."""
    s = Seq()
    st = show.stage_pose("left")

    def solve(p, a, seed=st):
        return Seq()._solve("left", np.array(p, float), frame(a), seed) - st
    gun = solve((0.38, -0.09, 0.40), aim(1.15, 0.03))                         # aimed in, at the other
    hip = gun + pose(j1=0.15, j2=-0.05, j3=-0.25, p=-0.6, j5=-0.35)          # low at the hip, barrel down
    s.move(hip, T=0.9, hold=0.15, frac=FAST)                                  # the standoff
    s.move(hip + pose(j6=0.25, p=0.06), hip, T=0.3, mirror=False)             # the left's fingers twitch...
    s.move(hip, hip + pose(j6=0.25, p=0.06), T=0.3, mirror=False)             # ...the right's
    s.move(hip, T=0.3, hold=0.25)                                             # ...wait for it
    s.move(gun, hip + 0.45 * (gun - hip), T=0.6, mirror=False)                # DRAW! the left is faster
    s.wait(0.08)
    s.hit("gunshot")
    dl, dr = deltas(s)
    s.move(dl + pose(j2=-0.03, j3=0.06, p=0.28), dr + pose(j1=0.15, j2=-0.04, p=0.3, j5=-0.2, j6=0.3), T=0.3,
           mirror=False)                                                       # BANG: recoil; the right is hit
    s.move(dl, dr + pose(j1=0.2, j2=0.1, j3=-0.15, p=-0.6, j5=-0.3, j6=0.6), T=0.85, mirror=False, hold=0.15)  # slumps
    s.move(dl + pose(j3=0.15, p=0.7, j5=-0.3), None, T=0.6, mirror=False, hold=0.25)  # barrel up: smoke
    s.home(T=1.0, frac=FAST)
    return s.duet("quick_draw", "Western standoff: guns low at the hips, fingers twitch... DRAW! The left is faster: "
                  "BANG, its gun kicks up; the right, hit mid-draw, jerks back, drops its gun and slumps; the left "
                  "raises its smoking barrel.", [])


def tip_hats():
    """Two gentlemen meet: the left turns and tips its hat, the right answers with a deeper one, both straighten."""
    s = Seq()
    up = FACE + pose(j2=0.05, j3=0.2, p=0.15)
    tip = FACE + pose(j2=0.12, j3=0.05, p=-0.55, j6=0.35)
    s.move(up, T=0.7, hold=0.3)                                            # turn to each other, chin up
    s.move(tip, up, T=0.5, mirror=False, hold=0.35)                        # left tips its hat
    s.move(up, tip + pose(p=-0.15), T=0.5, mirror=False, hold=0.45)        # the right answers, deeper
    s.move(up, T=0.5, hold=0.2)
    s.home(T=0.9)
    return s.duet("tip_hats", "Turn to each other; the left tips its hat, the right answers with a deeper tip.", [])


def copycat():
    """The right copies everything the left does, a beat late; the left catches it and the right looks innocent."""
    s = Seq()
    a = pose(j2=0.22, j3=0.5, p=1.0)                        # stretch up tall (celebrate's pose)
    b = pose(j1=0.2, j2=0.12, j3=0.15, p=0.0, j5=-0.35)     # lean out, look away
    s.move(a, None, T=0.6, mirror=False, hold=0.15)
    s.move(b, a, T=0.6, mirror=False, hold=0.15)
    s.move(None, b, T=0.75, mirror=False, hold=0.2)
    s.move(FACE + pose(j5=0.25), None, T=0.4, mirror=False, hold=0.2)     # the left whips round: caught!
    s.move(None, np.zeros(6) + pose(j5=-0.4, p=0.2), T=0.4, mirror=False, hold=0.5)  # the right: who, me?
    s.home(T=0.9)
    return s.duet("copycat", "The right copies every move of the left a beat late; the left whips round and "
                  "catches it; the right looks away, innocent.", [])


# ---------------------------------------------------------------- stand-up comedy: a double act (empty hands)
LOOK = show.FACING_LEFT  # the head turned ~66 deg toward the other arm (the each_other stance), as a delta


def elbow_nudge():
    """"Get it? Get it?": the left leans in beside the right and nudges it with the side of its head, three little
    pokes that really touch (Nov 8: they used to stop ~5 cm short): each darts in to just outside the 5 cm slow zone,
    creeps the last ~4 cm at the closing limit until the heads touch (~1.2 cm in sim), and snaps back out; mouth
    flapping; the right stays deadpan; the left grins (mouth wide open, head up)."""
    s = Seq()
    (pl, rl), (pr, rr) = meet(0.55, 0.1, 0.42, x=0.38, gap=0.13)              # side by side, heads ~13 cm apart
    st = show.stage_pose
    qr = ik("right", pr, rr, st("right"))[0]

    def toward(gap):                                                          # the left alone, `gap` from the right
        a, b = 0.0, 0.15
        for _ in range(12):
            c = (a + b) / 2
            a, b = (c, b) if hand_gap(ik("left", pl - [0, c, 0], rl, st("left"))[0], qr) > gap else (a, c)
        return pl - [0, a, 0]
    pout, pin = toward(0.056), toward(0.013)
    for k in range(4):                                                        # "get it? get it?": the mouth flaps
        s.grip("left", 0.3 if k % 2 == 0 else 1.0, 0.12, after=0.2 + 0.2 * k)  # on the way in (it stays open while
    s.move((pout, rl), (pr, rr), T=1.0, mirror=False)                         # touching: the fingers are the cheek)
    for k in range(3):                                                        # the left leans in, the right waits
        s.move((pin, rl), None, approach=dict(zone=0.042, vc=0.045, vmax=0.35, acc=1.5, taper=0.12),
               mirror=False)                                                  # nudge: touch
        s.move((pout, rl) if k < 2 else (pout + [0, 0.02, 0.02], rl), None, T=0.2, line=True, mirror=False)  # get it?
    dl, dr = deltas(s)
    s.move(dl + pose(p=0.3, j6=0.25), dr + pose(j5=-0.15), T=0.5, mirror=False, hold=0.4)  # grin; the right: no
    s.home(T=0.9, frac=FAST)
    return s.duet("elbow_nudge", "\"Get it? Get it?\": the left leans in beside the right and nudges it with the side "
                  "of its head, three little pokes that touch, mouth flapping; the right stays deadpan; the left grins "
                  "wide.", [{"left": HAND, "right": HAND, "min": 0.008}], props=False)


def straight_man():
    """The left does a silly bit (bouncing, wiggling, mouth flapping); the right slowly turns its head to stare at it,
    deadpan, and holds the stare; the left notices and freezes mid-wiggle; the right slowly turns back to the
    audience; the left droops."""
    s = Seq()
    P = 0.7                                                                   # one bounce, s

    def silly(u, T):
        t = u * T
        env = np.sin(np.pi * u) ** 2
        w = 2 * np.pi * t / P
        return env * pose(j2=0.03 * np.sin(w), j3=0.06 * np.sin(w), p=0.1 * np.sin(w), j6=0.25 * np.sin(w / 2),
                          j1=0.08 * np.sin(w / 4))

    T1 = 3.2
    for k in range(int(T1 / 0.25)):                                           # mouth flapping
        s.grip("left", 0.3 if k % 2 == 0 else 1.0, 0.12, after=0.25 * k)
    stare = LOOK + pose(p=-0.05)
    turn = glide(0.4)
    s.path(lambda u: silly(u, T1), lambda u: stare * turn(np.clip((u - 0.2) / 0.6, 0, 1)), T=T1, warp=None)
    s.wait(0.3)                                                               # the stare
    s.move(LOOK + pose(p=0.1), None, T=0.4, mirror=False, hold=0.4, frac=FAST)  # the left notices: caught, frozen
    s.move(pose(p=-0.3, j2=0.03), np.zeros(6), T=1.2, mirror=False, hold=0.15, frac=FAST)  # the right turns back;
    s.home(T=0.7, frac=FAST)                                                  # the left droops
    return s.duet("straight_man", "The left does a silly bit, bouncing and wiggling with its mouth flapping; the right "
                  "slowly turns to stare at it, deadpan; the left notices and freezes; the right turns back to the "
                  "audience and the left droops.", [], props=False)


def heckle():
    """The left points at the right and laughs at it (head thrown back, bouncing, mouth flapping); the right looks,
    then slowly droops, head hanging."""
    s = Seq()
    point = Seq()._solve("left", np.array([0.36, -0.08, 0.44]), frame(aim(1.1, 0.1)), show.stage_pose("left"))
    point = point - show.stage_pose("left")
    s.move(point, LOOK * 0.6, T=0.6, mirror=False, hold=0.15, frac=FAST)      # "HA!": point; the right looks over
    laugh = point + pose(p=0.25, j2=0.02, j3=0.06)
    droop = LOOK * 0.3 + pose(j2=0.06, j3=-0.1, p=-0.75, j6=0.2)
    for k in range(12):                                                       # ...HA HA HA: the mouth flaps
        s.grip("left", 0.3 if k % 2 == 0 else 1.0, 0.12, after=0.25 * k)
    w = glide(0.3)
    s.move(laugh, LOOK * 0.6, T=0.4, mirror=False)                            # head back...
    s.path(lambda u: laugh + np.sin(np.pi * u) ** 2 *
           pose(j3=0.045 * np.sin(2 * np.pi * u * 4), p=0.1 * np.sin(2 * np.pi * u * 4)),
           lambda u: LOOK * 0.6 + w(np.clip((u - 0.2) / 0.8, 0, 1)) * (droop - LOOK * 0.6), T=2.6, warp=None)
    s.grip("left", 1.0, 0.2)
    s.wait(0.4)                                                               # the right hangs its head
    s.home(T=0.9, frac=FAST)
    return s.duet("heckle", "The left points at the right and laughs at it, head back, bouncing, mouth flapping; the "
                  "right looks over, then slowly droops, head hanging.", [], props=False)


# ---------------------------------------------------------------- ensembles: both arms as ONE thing, action or mood
# Joint deltas, left-arm convention (j1 + = out, away from the other arm; p = head pitch, + up). Mirrored on the right
# unless both are given. Lean-back rule of thumb: COM x moves ~ 24*j2 - 11*j3 - 1.7*p cm and must stay > -2 cm,
# so raising the elbow (j3+) or the head (p+) needs j2 forward with it.

def ens(name, about, s, contact=(), props=True, table=None, frac=0.8):
    return s.home(T=0.9, frac=frac).duet(name, about, list(contact), kind="ensemble", props=props, table=table)


def car_ride(X=0.40, ZC=0.34, RW=0.15, A=0.65, T=4.6, BW=0.12, BD=0.015):
    """Both hands on one big steering wheel in the middle (a circle across the stage, at 10 and 2 o'clock, the
    grippers pointing forward through it, rolling with it): turn left, turn right, the wheel shifting into each
    turn, two bumps on the straight, then let go. The hands stay ~29 cm apart (the sticks point forward)."""
    s = Seq()

    def bump(u, at, w=BW):
        x = np.clip((u - at) / w, 0, 1)
        return np.sin(np.pi * x) ** 2

    def wheel(u):                                                   # u: 0..1 of the drive
        th = A * np.sin(2 * np.pi * u) * np.sin(np.pi * u)           # left, back, right, back (smooth ends)
        dy = 0.025 * th / A                                         # the wheel leans into the turn
        dz = -BD * (bump(u, 0.40) + bump(u, 0.52))               # two bumps on the straight
        c = np.array([X, MID_Y + dy, ZC + dz])
        fwd = aim(0.0, 0.0)
        a = np.radians(60)
        pl = c + RW * np.array([0, np.sin(a + th), np.cos(a + th)])
        cm = np.array([X, MID_Y - dy, ZC + dz])                     # the right hand, as its mirror image
        pr = cm + RW * np.array([0, np.sin(a - th), np.cos(a - th)])
        return ((pl, frame(fwd, (0, np.sin(th), np.cos(th)))),
                mirror_target(pr, frame(fwd, (0, -np.sin(th), np.cos(th)))))

    both_grip(s, 0.0, 0.6)
    s.move(*wheel(0.0), T=1.0, mirror=False, hold=0.1)                         # hands on the wheel
    s.path(lambda u: wheel(u)[0], lambda u: wheel(u)[1], T=T, warp=None)       # drive
    s.wait(0.1)
    both_grip(s, 1.0, 0.4)                                                     # we're here: let go
    s.move(pose(j2=0.05, j3=0.1, p=0.2), T=0.6, hold=0.1, frac=FAST)
    return ens("car_ride", "Both hands on one big steering wheel: turn left, turn right, leaning into the turns, "
               "bounce over two bumps, then let go: we're here.", s, frac=FAST)


def wings():
    s = Seq()
    spread = pose(j1=0.45, j2=0.18, j3=0.35, p=0.2)
    s.move(spread + pose(j2=0.08, j3=-0.1, p=-0.3), T=0.7).move(spread, T=0.4, hold=0.1)
    for _ in range(3):
        s.move(spread + pose(j2=0.12, j3=0.3, p=0.35), T=0.4).move(spread + pose(j2=0.15, j3=-0.15, p=-0.35), T=0.4)
    s.move(spread + pose(j3=0.1, p=0.1, j6=0.3), T=0.6, hold=0.4)           # glide
    return ens("wings", "Both arms are a great pair of wings: spread wide, flap three times, glide.", s)


def this_big():
    s = Seq()
    s.move(pose(j1=-0.1, j2=0.05, p=-0.25), T=0.4, hold=0.1)               # gather in
    s.move(pose(j1=0.6, j2=0.2, j3=0.3, p=0.25), T=0.6, hold=1.0)           # THIS big!
    return ens("this_big", "Arms thrown wide apart: \"it was THIS big!\"", s)


def shrug():
    """"Who knows?": a little dip with the hands curling, then a quick pop up with the hands thrown open and out (the
    grippers spring open, the wrists turn the palms up and out, the heads tilt), an "eh" bob, then they drop."""
    s = Seq()
    both_grip(s, 0.3, 0.35)
    s.move(pose(j2=0.03, j3=-0.08, p=-0.2), T=0.4, hold=0.1)                  # dip: shoulders sink, hands curl
    both_grip(s, 1.0, 0.3)
    up = pose(j1=0.3, j2=0.12, j3=0.25, p=0.0, j5=-0.4, j6=0.7)
    s.move(up, T=0.5, hold=0.3)                                               # POP: up, palms up and out, heads tilt
    s.move(up + pose(j2=-0.02, j3=-0.06, p=-0.05, j6=-0.1), T=0.3)            # eh...
    s.move(up, T=0.3, hold=0.4)
    s.move(pose(j2=-0.02, j3=-0.08, p=-0.25, j6=0.3), T=0.7, hold=0.2)        # drop: oh well
    return ens("shrug", "\"Who knows?\": a little dip, then both pop up with the hands thrown open, palms up and out, "
               "heads tilted, an \"eh\" bob, and they drop: oh well.", s)


def rocket():
    s = Seq()
    crouch = pose(j2=-0.1, j3=-0.28, p=-0.35)
    s.move(crouch, T=0.6, hold=0.3)                                         # crouch: the countdown
    for _ in range(2):                                                      # rumble
        s.move(crouch + pose(j6=0.12), T=0.15).move(crouch + pose(j6=-0.12), T=0.15)
    s.move(pose(j1=-0.15, j2=0.4, j3=0.6, p=1.1), T=0.6, hold=0.6)         # LIFT-OFF: up high, tips together
    return ens("rocket", "Crouch for the countdown, rumble, then blast up high together: a rocket lifting off.", s)


def waves():
    s = Seq()
    hi, lo = pose(j2=0.1, j3=0.35, p=0.35), pose(j2=0.2, j3=-0.12, p=-0.35)
    for k in range(4):
        s.move(hi if k % 2 == 0 else lo, lo if k % 2 == 0 else hi, T=0.6, mirror=False)
    return ens("waves", "The two arms rise and fall in turn like rolling ocean waves.", s)


def storm():
    s = Seq()
    for yaw, T in [(0.3, 0.5), (-0.25, 0.4), (0.35, 0.45), (-0.2, 0.35), (0.25, 0.4)]:
        bend = pose(j2=0.08, j3=0.05, p=-0.1, j6=0.25 * np.sign(yaw))
        # both bend the SAME way, like trees in the wind: the right arm's j1 delta is negated
        s.move(pose(j1=yaw) + bend, pose(j1=-yaw) + bend * [1, 1, 1, 1, 1, -1], T=T, mirror=False)
    return ens("storm", "Both arms bend and whip together like trees in a howling storm.", s)


def gate_opening():
    """A castle gate: the arms swing in level until the two sticks meet tip to tip in the middle (the closed gate),
    it rattles, then the doors creak open on their hinges (the bases: j1 only), stop-start, then swing wide."""
    s = Seq()
    az, el, z, x = 1.25, 0.0, 0.40, 0.42
    s.move(*tips(az, el, z, x, sep=0.035), T=1.1, frac=FAST)               # the doors swing shut...
    s.move(*tips(az, el, z, x), approach=dict(zone=0.03, vc=0.021, vmax=0.12, acc=1.0, taper=0.06), hold=0.15)  # ...clunk
    shut, _ = deltas(s)
    for a in (0.12, -0.12, 0.0):                                         # locked: rattle (j6 turns the stick in place)
        s.move(shut + pose(j6=a), T=0.2)
    s.wait(0.1)
    s.move(shut + pose(j1=0.14, j6=0.05), T=0.6)                          # creeeak...
    s.move(shut + pose(j1=0.14, j6=-0.05), T=0.26, hold=0.2)               # (stuck)
    s.move(shut + pose(j1=0.75, j5=-0.45, p=0.05), T=1.0, hold=0.3, frac=FAST)  # ...then they swing wide open
    return ens("gate_opening", "A castle gate: the arms swing shut until the sticks meet in the middle, it rattles, "
               "then the doors creak open on their hinges, stop and start, and swing wide.", s,
               contact=[{"left": ["prop"], "right": ["prop"], "min": 0.012}], frac=FAST)


def explosion():
    """The two fists huddle into one bomb in the middle, the fuse fizzes (a growing tremble), a held breath, then
    BOOM: both fling up and out, heads thrown back, the hands springing open; the debris flutters down. Empty hands:
    the bomb is the two fists ~5.5 cm apart (outside the slow zone, so the fling may be fast)."""
    s = Seq()
    both_grip(s, 0.0, 0.6)                                                      # fists
    (pl, rl), (pr, rr) = meet(1.2, -0.25, 0.37, gap=0.056, grip=0.0)
    s.move((pl, rl), (pr, rr), T=1.0, mirror=False, hold=0.1, frac=FAST)       # the bomb, heads bowed over it
    for k, a in enumerate((0.004, 0.006, 0.008, 0.010, 0.012)):                 # the fuse fizzes: a growing tremble
        dz = a * (-1) ** k
        s.move((pl + [0, 0, dz], rl), (pr + [0, 0, dz], rr), T=0.16, line=True, mirror=False)
    s.move((pl + [0, 0, -0.015], rl), (pr + [0, 0, -0.015], rr), T=0.3, line=True, mirror=False, hold=0.2)  # hush...
    dl, _ = deltas(s)
    boom = dl + pose(j1=0.45, j2=0.25, j3=0.5, p=0.75, j5=-0.45, j6=0.3)     # (mirrored: both arms the same)
    both_grip(s, 1.0, 0.25, after=0.1)
    s.hit("explosion")
    s.move(boom, T=0.5, hold=0.2)                                               # BOOM: up and out, hands burst open
    d = boom + pose(j1=0.1, j2=-0.05, j3=-0.35, p=-0.9, j6=-0.3)               # ...the debris drifts down
    def flutter(ph):                                                            # side to side, wrists fluttering
        return lambda u: boom + show.quintic(u) * (d - boom) + np.sin(np.pi * u) ** 2 * pose(j6=0.18 * np.sin(4 * np.pi * u + ph),
                                                                          j5=0.1 * np.sin(2 * np.pi * u + ph))
    s.path(flutter(0.0), flutter(1.5),
           T=2.05, warp=None)
    s.wait(0.1)
    return ens("explosion", "Both fists huddle into one bomb in the middle, the fuse fizzes and trembles, a held "
               "breath, then BOOM: both fling up and out, heads thrown back, hands bursting open, and the debris "
               "flutters down.", s, contact=[{"left": HAND, "right": HAND, "min": 0.03}], props=False, frac=FAST)


def row_boat():
    """Two oars (the sticks), rowing off-sync, the right half a stroke behind: each stroke dips in at the catch, pulls
    back low, lifts out and returns high with the wrist rolled flat (the feather). A smooth loop through four keys
    (a trig interpolant); the phase ramps up and down smoothly."""
    s = Seq()
    K = np.array([pose(j2=0.32, j3=-0.12, p=-0.4),                     # catch: forward, the blade dips in
                  pose(j2=0.02, j3=-0.2, p=-0.3),                      # the pull ends: back, still low
                  pose(j2=0.02, j3=0.1, p=0.05, j6=0.52),              # out of the water, feathered
                  pose(j2=0.3, j3=0.12, p=0.0, j6=0.52)])              # the return, high and flat
    F = np.fft.rfft(K, axis=0) / 4

    def oar(ph):
        k = np.arange(3)
        w = np.where(k == 0, 1, 2) * np.exp(1j * np.outer(ph, k))      # trig interpolant through the 4 keys
        return np.real(w @ F)[0]
    N, P = 3, 1.75                                                     # strokes, seconds per stroke
    s.move(oar(np.array([0.0])), oar(np.array([-np.pi])), T=0.9, mirror=False, hold=0.1)
    w = glide(0.12)
    s.path(lambda u: oar(np.array([2 * np.pi * N * w(u)])), lambda u: oar(np.array([2 * np.pi * N * w(u) - np.pi])),
           T=N * P * 1.1, warp=None)
    return ens("row_boat", "Both arms row a boat with the sticks as oars, off-sync: dip in, pull back, lift out and "
               "return with the wrist rolled flat, three strokes each.", s)


def once_upon_a_time():
    """The storybook: the hands press together in the middle, pointing up, a closed book; it opens (the wrists turn
    out, the tips part while the bottoms stay together: the spine), a beat to read it, then a slow, grand sweep up
    and out."""
    s = Seq()
    s.move(*meet(1.15, 0.6, 0.5, gap=0.05), T=0.9)
    s.move(*meet(1.15, 0.6, 0.5, gap=0.012), approach=TOUCH, hold=0.25)  # the closed book
    book, _ = deltas(s)
    s.move(book + pose(j5=-0.55, j6=0.35, p=-0.1), T=1.0, hold=0.3)          # it opens: the pages face the audience
    s.move(book + pose(j5=-0.6, j6=0.35, p=-0.2), T=0.4, hold=0.2)            # a beat: read it
    s.move(pose(j1=0.45, j2=0.15, j3=0.45, p=0.6, j6=0.3), T=1.3, hold=0.4)  # once upon a time...: the grand sweep
    return ens("once_upon_a_time", "The narrator's storybook: the hands press together in the middle like a closed "
               "book, it opens to the audience, a beat to read it, then a slow, grand sweep up and out.", s,
               contact=[{"left": HAND, "right": HAND, "min": 0.006}], props=False)


def the_end():
    s = Seq()
    s.move(pose(j1=0.5, j2=0.2, j3=0.35, p=0.4), T=0.7, hold=0.8)            # arms wide: THE END
    s.move(pose(j2=0.3, j3=-0.1, p=-0.7), T=0.9, hold=0.6)                  # and a deep bow
    return ens("the_end", "Arms wide for \"THE END!\", then a deep bow together.", s)


def chomp(AZ=1.0, C=-0.04, X=0.38, ZC=0.40, OPEN=(0.10, 0.12, 0.14), TH=(0.12, 0.15, 0.2), GAP=0.056, SH=-0.04):
    """One giant pair of jaws in the middle: the left hand is the upper jaw, the right the lower one, both pointing
    in, stacked. They hinge open wide (upper up, lower down, the tips spread most, the fingers bared like teeth) and
    SNAP shut. The snap stops ~5.5 cm short (outside the 5 cm slow zone), so it may be fast; the third bite shakes
    its prey. Empty hands: with the sticks pointing in, the arms couldn't reach the middle."""
    s = Seq()

    def jaw(h, th, lo, shake=0.0):                       # h: lift of the upper jaw, th: tilt of each, lo: lower z
        up = np.array([X, MID_Y + C + shake, ZC + h]), frame(aim(AZ, th))
        dn = mirror_target(np.array([X, MID_Y + C - shake, lo]), frame(aim(AZ, -th)))
        return up, dn

    a, b = ZC - 0.25, ZC                                 # shut: the lower jaw just GAP under the upper one
    st = show.stage_pose
    for _ in range(12):
        z = (a + b) / 2
        (pl, rl), (pr, rr) = jaw(0.0, SH, z)
        qL = ik("left", pl, rl, st("left"))[0]; qR = ik("right", pr, rr, st("right"))[0]
        a, b = (a, z) if hand_gap(qL, qR, 0.3) < GAP else (z, b)
    z0 = a
    shut = jaw(0.0, SH, z0)
    both_grip(s, 0.3, 0.5)
    s.move(*jaw(0.04, 0.1, z0 - 0.03), T=0.9, mirror=False, frac=FAST)  # the jaws rise into the middle
    s.move(*shut, T=0.4, mirror=False, hold=0.1)                       # shut, lurking
    for k, (o, th) in enumerate(zip(OPEN, TH)):
        both_grip(s, 1.0, 0.4)                                         # open wide: teeth bared
        s.move(*jaw(o, th, z0 - 0.6 * o), T=0.55, mirror=False, hold=0.15 if k == 2 else 0.04)
        both_grip(s, 0.3, 0.25)
        s.move(*shut, T=0.3, mirror=False)                             # SNAP!
        s.hit("chomp")
        s.wait(0.08)
    for sh in (0.025, -0.025, 0.0):                                    # got it: shake the prey
        s.move(*jaw(0.0, SH, z0, shake=sh), T=0.26, mirror=False)
    s.wait(0.1)
    both_grip(s, 1.0, 0.6)
    return ens("chomp", "The two arms are one giant pair of jaws in the middle, the left hand the upper jaw and the "
               "right the lower: they open wide, fingers bared like teeth, and SNAP shut, three times, then shake the "
               "prey.", s, contact=[{"left": HAND, "right": HAND, "min": 0.03}], props=False, frac=FAST)


POT = np.array([0.42, MID_Y, 0.27])  # the witches' cauldron: the spoon tips stir around here (on the midplane)


def cauldron():
    """Two witches share one cauldron: closed grippers angle down into it like spoons; one stirs (the tip circles the
    pot, the handle leaning out along the circle) while the other holds back and its head follows the spoon; swap.
    The wrist can't point much steeper than ~45 deg down (j4 limit), so the pot sits low and forward (27 cm up)."""
    s = Seq()

    def stir(th, r=0.035, tilt=0.12):
        tip = POT + [r * np.cos(th), r * np.sin(th), 0.0]
        return tip, frame(aim(0.6, -0.75) + tilt * np.array([np.cos(th), np.sin(th), 0]))

    turns, th0 = 1.25, 0.0                             # start in front, end on its own side (away from the other)
    over = Seq()._solve("left", *stir(th0), show.stage_pose("left") + pose(j1=-0.6, j2=0.9, j3=1.1, p=-0.6))
    WATCH = over - show.stage_pose("left") + pose(j1=0.6, j2=-0.35, j3=-0.3, p=0.1, j5=0.35)  # spoon up, out

    def watch(th):                                                              # the head follows the spoon
        return WATCH + pose(j5=0.12 * np.sin(th), j6=0.1 * np.sin(th))

    def turn(stirrer):
        def lt(u):
            th = th0 + 2 * np.pi * turns * u
            return stir(th) if stirrer == "left" else watch(th)

        def rt(u):
            th = th0 + 2 * np.pi * turns * u
            return mirror_target(*stir(th)) if stirrer == "right" else watch(th)
        return lt, rt

    both_grip(s, 0.0, 0.5)                                                      # grip the spoons
    s.move(*(f(0) for f in turn("left")), T=0.8, mirror=False, hold=0.05, frac=FAST)  # the left dips in, the right peers
    s.path(*turn("left"), T=1.5, warp=glide(0.25))                              # stir, the other watches
    s.move(*(f(0) for f in turn("right")), T=0.6, mirror=False, frac=FAST)      # swap
    s.path(*turn("right"), T=1.5, warp=glide(0.25))
    for k in range(2):                                                          # lift it out and cackle: hee-hee
        s.move(WATCH + pose(p=0.3 + 0.12 * (k % 2), j6=0.15 * (-1) ** k), T=0.22, frac=FAST)
    s.wait(0.1)
    return ens("cauldron", "Two witches at one cauldron: closed grippers angle down into the pot like spoons; one "
               "stirs round and round while the other's head follows the spoon, they swap, then both cackle.", s,
               props=False, frac=FAST)


# ---------------------------------------------------------------- hand-contact ensembles (empty hands only)
# Without the 15 cm props the hands can meet in the middle with the arms (link2/link3) still >= 15 cm apart: the
# grippers point in at az ~1.2 rad. meet() finds the targets where the hands are `gap` apart.

_HANDB = ("gripper", "tip_left", "tip_right", "link4", "link5")
_PARTS = {s: [(g, SC.m.body(SC.m.geom_bodyid[g]).name[len(duets.PRE[s]):] in _HANDB)
              for g, _, _ in SC.geoms[s] if g != SC.prop_geom[s]] for s in SIDES}


def hand_gap(qL, qR, grip=1.0):
    """Closest hand-to-hand distance of the two arms at these joints (fingers at grip)."""
    m, d, ft = SC.m, SC.d, np.zeros(6)
    SC.set("left", qL, grip); SC.set("right", qR, grip); mujoco.mj_kinematics(m, d)
    return min(mujoco.mj_geomDistance(m, d, a, b, 0.3, ft) for a, ha in _PARTS["left"] if ha
               for b, hb in _PARTS["right"] if hb)


def palm(point, normal):
    """Rotation with the grasp axis along `point` and the flat side of the hand (site y; the fingers open along
    site x) facing `normal`: palm(aim(...), (0, -1, 0)) = the left hand's flat side toward the other arm."""
    z = unit(point)
    y = np.asarray(normal, float); y = unit(y - y @ z * z)
    return np.column_stack([np.cross(y, z), y, z])


@functools.lru_cache(maxsize=None)
def meet(az, el, z, x=0.36, gap=0.012, up=(0, 0, 1), dx=0.0, grip=1.0, normal=None):
    """Left and right gripper targets (mirrored; the left dx further forward, the right dx back) where the hands
    (fingers at grip) are `gap` apart, pointing az in and el up (normal: palm() instead of frame(.., up))."""
    R = palm(aim(az, el), normal) if normal else frame(aim(az, el), up)
    lo, hi = 0.0, 0.25
    for _ in range(14):
        c = (lo + hi) / 2
        pL = np.array([x + dx, MID_Y + c, z])
        pR, RR = mirror_target(np.array([x - dx, MID_Y + c, z]), R)
        qL, _, _ = ik("left", pL, R, show.stage_pose("left"))
        qR, _, _ = ik("right", pR, RR, show.stage_pose("right"))
        lo, hi = (c, hi) if hand_gap(qL, qR, grip) < gap else (lo, c)
    pL = np.array([x + dx, MID_Y + hi, z])
    return (pL, R), mirror_target(np.array([x - dx, MID_Y + hi, z]), R)


TOUCH = dict(zone=0.03, vc=0.022, vmax=0.3, acc=1.0, taper=0.06)  # per arm: the hands close at <= 4.4 cm/s


def hands(name, about, s, mn=0.006, frac=0.8):
    return s.home(T=0.9, frac=frac).duet(name, about, [{"left": HAND, "right": HAND, "min": mn}], kind="ensemble", props=False)


def clap():
    s = Seq()
    for k in range(3):
        z = 0.46 + 0.03 * (k % 2)
        s.move(*meet(1.2, 0.1, z, gap=0.055), T=0.8 if k == 0 else 0.3)          # hands apart
        s.move(*meet(1.2, 0.1, z), approach=TOUCH, hold=0.1)                    # pat
    s.move(*meet(1.2, 0.3, 0.5, gap=0.1), T=0.4, line=True)
    return hands("clap", "Both arms are two hands clapping softly together in the middle, three times.", s)


def rub_hands():
    s = Seq()
    s.move(*meet(1.25, 0.0, 0.44, gap=0.07), T=0.9, frac=FAST)
    s.move(*meet(1.25, 0.0, 0.44, gap=0.025), approach=TOUCH, hold=0.1)
    dl = s.q["left"] - show.stage_pose("left")                               # where the hands met, as deltas
    dr = show.mirror(s.q["right"] - show.stage_pose("right"), "right")
    for k in range(3):                                                          # rub: the wrists roll against each other
        a = 0.3 * (1 if k % 2 == 0 else -1)
        s.move(dl + pose(j6=a, j5=0.04 * np.sign(a)), dr + pose(j6=-a, j5=-0.04 * np.sign(a)), T=0.3, mirror=False)
    s.move(dl, dr, T=0.7, mirror=False, hold=0.1)  # settle slowly: the rolled hands close here
    s.move(*meet(1.25, 0.2, 0.47, gap=0.08), T=0.4, line=True)
    return hands("rub_hands", "Two hands rubbed together, scheming: \"Excellent... my plan is working.\"", s, frac=FAST)


def hug():
    s = Seq()
    s.move(pose(j1=0.55, j2=0.2, j3=0.35, p=0.2, j6=0.3), T=0.8, hold=0.3)    # arms wide open
    s.move(*meet(0.95, -0.1, 0.42, x=0.42, gap=0.06), T=1.0)                  # wrap around
    s.move(*meet(0.95, -0.1, 0.42, x=0.42, gap=0.015), approach=TOUCH, hold=0.3)
    for dy in (0.025, -0.025, 0.0):                                            # rock side to side
        (pl, rl), (pr, rr) = meet(0.95, -0.1, 0.42, x=0.42, gap=0.015)
        s.move((pl + [0, dy, 0], rl), (pr + [0, dy, 0], rr), T=0.6, line=True, mirror=False)
    s.move(*meet(0.95, 0.1, 0.45, x=0.42, gap=0.08), T=0.5, line=True)
    return hands("hug", "Arms open wide, then wrap around in a big hug and rock side to side.", s)


def pull_rope():
    s = Seq()
    far, near = dict(x=0.43, z=0.40, el=-0.25), dict(x=0.33, z=0.47, el=0.1)
    s.grip("left", 0.0, 0.5).grip("right", 0.0, 0.5)                           # fists (closed fingers only widen the gap)
    s.move(*meet(1.2, far["el"], far["z"], x=far["x"], gap=0.05), T=0.9)
    s.move(*meet(1.2, far["el"], far["z"], x=far["x"], gap=0.02), approach=TOUCH)  # grab the rope
    for _ in range(3):
        s.move(*meet(1.2, near["el"], near["z"], x=near["x"], gap=0.02), T=0.7, line=True)   # heave!
        s.move(*meet(1.2, far["el"], far["z"], x=far["x"], gap=0.02), T=0.6, line=True)     # reach for more
    s.move(*meet(1.2, 0.1, 0.45, gap=0.08), T=0.5, line=True)
    s.grip("left", 1.0, 0.5).grip("right", 1.0, 0.5)
    return hands("pull_rope", "Two hands pull a heavy rope: reach, grab, heave back, three times.", s, mn=0.01)


def heart():
    s = Seq()
    s.move(pose(j2=0.15, j3=0.4, p=0.1, j1=0.1), T=0.8)                        # elbows up: the two lobes
    s.move(*meet(1.25, -0.45, 0.38, gap=0.05), T=0.8)
    s.move(*meet(1.25, -0.45, 0.38, gap=0.012), approach=TOUCH, hold=0.3)      # the tip at the bottom
    for _ in range(2):                                                          # it beats
        (pl, rl), (pr, rr) = meet(1.25, -0.45, 0.38, gap=0.012)
        s.move((pl + [0, 0, 0.02], rl), (pr + [0, 0, 0.02], rr), T=0.25, line=True, mirror=False)
        s.move((pl, rl), (pr, rr), T=0.35, line=True, mirror=False, hold=0.1)
    s.move(*meet(1.25, -0.2, 0.42, gap=0.08), T=0.5, line=True)
    return hands("heart", "Both arms make a big heart: elbows up for the lobes, the hands meet at the tip, it beats.", s)


def deltas(s):
    """Where both arms are now, as joint deltas from stage in the left-arm convention (for move(.., mirror=False))."""
    return s.q["left"] - show.stage_pose("left"), show.mirror(s.q["right"] - show.stage_pose("right"), "right")


def both_grip(s, g, T, after=0.0):
    return s.grip("left", g, T, after).grip("right", g, T, after)


def high_five():
    s = Seq()
    both_grip(s, 0.3, 0.4)
    s.move(pose(j1=0.1, j2=-0.04, j3=-0.12, p=-0.3), T=0.45, hold=0.1)          # anticipation: dip, hands curl
    both_grip(s, 1.0, 0.35)
    s.move(*meet(0.9, 0.7, 0.55, gap=0.2), T=0.6, hold=0.1, frac=FAST)          # rise, hands spring wide open
    s.move(*meet(1.15, 0.55, 0.53, gap=0.052), T=0.35)                          # swing in
    s.move(*meet(1.15, 0.55, 0.53, gap=0.01), approach=TOUCH, hold=0.15)        # SLAP: palm to palm
    s.move(*meet(1.0, 0.7, 0.56, gap=0.09), T=0.25, line=True)                 # bounce back
    dl, dr = deltas(s)
    up = pose(j1=0.12, p=0.25)
    s.move(dl + up, dr + up, T=0.35, mirror=False)                             # YES!
    for k, a in enumerate((0.15, -0.15, 0.15)):                                 # celebrate: wiggle, mouths flap
        both_grip(s, 0.4 if k % 2 == 0 else 1.0, 0.18)
        s.move(dl + up + pose(j6=a, j5=-a / 3), dr + up + pose(j6=a, j5=-a / 3), T=0.3, mirror=False)
    both_grip(s, 1.0, 0.18)
    s.move(dl + up, dr + up, T=0.3, mirror=False, hold=0.1)
    return hands("high_five", "High five: dip, hands rise and spring open, swing in and slap palm to palm in the "
                 "middle, bounce back, then celebrate with heads up and wiggling, flapping hands.", s, frac=FAST)


def kiss():
    """Built as a fist bump (round 3b); the owner saw a kiss (Nov 7): two heads meeting face to face, closed mouths
    touching. Kept as it was, documented as the kiss."""
    s = Seq()
    s.grip("left", 0.0, 0.4).grip("right", 0.0, 0.4)                           # make fists
    s.move(*meet(0.95, 0.25, 0.46, x=0.33, gap=0.25, grip=0.0), T=0.8, hold=0.25)  # wind-up: cock back and out
    s.move(*meet(1.3, 0.0, 0.42, x=0.37, gap=0.052, grip=0.0), T=0.35)          # punch in
    s.move(*meet(1.3, 0.0, 0.42, x=0.37, gap=0.01, grip=0.0), approach=TOUCH)  # the mouths meet
    s.hit("kiss").wait(0.15)                                                    # mwah: the contact frame
    both_grip(s, 1.0, 0.3, after=0.1)                                           # EXPLODE: fingers spring open...
    s.move(*meet(1.3, 0.0, 0.42, x=0.37, gap=0.08, grip=0.0), T=0.2, line=True)  # ...as the hands fly apart
    boom = pose(j1=0.4, j2=0.2, j3=0.3, p=0.45, j6=0.3)
    s.move(boom, T=0.4)
    both_grip(s, 0.6, 0.15)                                                     # sparks: the fingers flicker
    s.move(boom + pose(j6=-0.15, p=-0.08), T=0.3)
    both_grip(s, 1.0, 0.15)
    s.move(boom, T=0.3, hold=0.3)
    return hands("kiss", "A kiss: the two heads lean in and their closed mouths meet in the middle, then they spring "
                 "apart, mouths flapping open, giddy: love, romance, making up, a happy ending, \"and they kissed\".", s)


def law(U):
    """Seq.path warp from a sampled time law U (u every DT, ending at 1, e.g. approach_law); use T = len(U) * DT."""
    x, y = np.linspace(0, 1, len(U) + 1), np.concatenate([[0.0], U])
    return lambda u: float(np.interp(u, x, y))


def on_table(p, R, seed):
    """Left-arm joints with the grasp site near p and rotation R, its hand's lowest point at -show.BANG_DEPTH."""
    p = np.array(p, float)
    for _ in range(4):
        q = Seq()._solve("left", p, R, seed)
        low = SC.lows("left", [q])[0][0] + show.TABLE_Z["left"] - show.TABLE_TOP  # above the higher table: the
        p[2] += -show.BANG_DEPTH + 0.0005 - low  # mirrored right hand goes that deep; 0.5 mm spare: the keys are rounded
    return q


def drum_roll():
    """Two fists drum the table in turn, faster and faster, then both BANG together. Only the gripper and fingertips
    touch the table; the elbows stay up (the wrists cock the fists up 5-8 cm, the knock is the head pitching down)."""
    s = Seq()
    st = show.stage_pose("left")
    down = on_table((0.40, -0.06, 0.03), frame(aim(0.3, -1.0)), st + pose(j2=0.5, j3=0.3, p=-1.0)) - st
    up, hi = down + pose(p=0.32, j2=-0.03), down + pose(p=0.42, j2=-0.05)     # wind-ups: ~5.5 and ~7.5 cm
    both_grip(s, 0.0, 0.5)                                                      # fists
    s.move(up, T=1.0, hold=0.1, frac=FAST)
    f0, f1, K = 1.0, 1.3, 3                                                     # strokes/s, accelerating; K hits each
    T = (2 * K + 1) / (f0 + f1)

    def stroke(ph):                                                             # cosine stroke: 0 = up, pi = knock
        return down + (1 + np.cos(np.clip(ph, 0, 2 * np.pi * K))) / 2 * (up - down)

    def phase(u):
        t = u * T
        return 2 * np.pi * (f0 * t + (f1 - f0) * t * t / (2 * T))
    s.hit("drum_roll")
    s.path(lambda u: stroke(phase(u)), lambda u: stroke(phase(u) - np.pi), T=T, warp=None)  # the roll
    s.move(hi, T=0.45, hold=0.15)                                               # both fists up... wait for it
    U = approach_law(0.08, zone=1e-4, vc=0.26, vmax=0.26, acc=2.0, taper=1.5)
    s.path(lambda u: hi + u * (down - hi), T=len(U) * DT, warp=law(U))         # BANG!
    s.wait(0.15)
    s.move(pose(j1=0.25, j2=0.1, p=0.45), T=0.6, hold=0.1, frac=FAST)          # ta-da: heads up
    return ens("drum_roll", "A drum roll on the table: two fists knock in turn, faster and faster, wind up together "
               "and BANG at once, then heads up: ta-da!", s, props=False,
               table={"min": -show.BANG_DEPTH, "impact": 0.3}, frac=FAST)


# ---------------------------------------------------------------- toy duets (Nov 8): per-arm props ("props": {side: kind})
def car_frame(h):
    """Grasp site rotation holding the toy car wheels-down, its long axis (site y) along the horizontal h (as
    make_gestures.car_frame; not imported, that module imports this one)."""
    h = np.array([h[0], h[1], 0.0]) / np.hypot(h[0], h[1])
    z = np.array([0.0, 0.0, -1.0])
    return np.column_stack([np.cross(h, z), h, z])


CAR_Z = {s: show.TABLE_Z[s] + 0.028 - 0.002 for s in SIDES}  # grasp site height with the car's wheels 2 mm into
# each arm's own table (as drive); the shared frame's z is each arm's own


def car_chase(Y0=-0.02, X=0.36, SW=0.02, D=0.18, T=2.55):
    """Both arms hold a toy car (show.PROP_SHAPES["car"]) and drive them on the table across the middle, toward the
    right arm: the right's car leads, the left's chases behind on the same road (a gentle S swerve), creeping up on
    it until the leader speeds off. "Close" is as close as the arms allow: at table height the wrists stick out
    toward each other, so the cars' centres stay >= 26 cm apart (bumpers ~18 cm) to keep link3 15 cm from the other
    arm; the hands come ~12 cm apart (a declared pair, outside the slow zone). Both cars are gripped with their nose
    along site -y (toward the right arm when they touch down): IK only finds the site-y-toward-+y grip here."""
    s = Seq()
    road = lambda d, s="left": np.array([X + SW * np.sin(2 * np.pi * d / 0.30), Y0 - d, CAR_Z[s]])  # d: m down the road
    head = lambda d: car_frame((-SW * 2 * np.pi / 0.30 * np.cos(2 * np.pi * d / 0.30), 1.0))  # site y back along it
    gap = lambda u: 0.30 - 0.04 * np.sin(np.pi * min(u / 0.7, 1)) ** 2 + 0.03 * show.quintic((u - 0.6) / 0.4)
    up = np.array([0, 0, 0.05])                                                # centre spacing ^: closes in, escapes
    s.move((road(0) + up, head(0)), (road(gap(0), "right") + up, head(gap(0))), T=1.0, mirror=False, frac=FAST)
    s.move((road(0), head(0)), (road(gap(0), "right"), head(gap(0))), mirror=False,
           approach=dict(zone=0.05, vc=0.055, vmax=0.3, acc=1.0, taper=0.1))  # touch down gently (<= 6 cm/s)
    w = glide(0.3)

    def at(u):  # the chase: the follower's distance down the road, the leader gap(u) ahead
        d = D * w(u)
        return (road(d), head(d)), (road(d + gap(u), "right"), head(d + gap(u)))
    s.path(lambda u: at(u)[0], lambda u: at(u)[1], T=T, warp=None)
    (pl, rl), (pr, rr) = at(1.0)
    s.move((pl + 0.4 * up, rl), (pr + 0.4 * up, rr), T=0.25, line=True, mirror=False)  # lift off, and home
    s.home(T=1.0, frac=0.92)
    pair = [{"left": ["prop"] + HAND, "right": ["prop"] + HAND, "min": 0.08}]
    return s.duet("car_chase", "Both arms hold a toy car and drive them on the table across the middle: the right's "
                  "car leads, the left's chases right behind it on the same swerving road, creeping up on it, until "
                  "the leader speeds off: a car chase, a race, follow that car!", pair,
                  props={"left": "car", "right": "car"}, table={"min": -0.003, "impact": 0.06, "prop": True})


def ufo_frame(tilt, toward=-np.pi / 4):
    """Grasp-site pointing (roll free) for the UFO (show.PROP_SHAPES["ufo"], the saucer hanging 3 cm below the
    gripped dome): straight down tilted `tilt` rad toward azimuth `toward` (default: toward the audience and the
    right arm). Pointing straight down only works below ~16 cm site height (the wrist pitch limit)."""
    return unit([np.sin(tilt) * np.cos(toward), np.sin(tilt) * np.sin(toward), -np.cos(tilt)])


def ufo_abduct(H=(0.3256, -0.30, 0.35), TILT=0.7, ZIP=(0.3, -0.3, 0.0, 0.5)):
    """The left flies the UFO in over the middle and hovers it beside and above the right's head, belly (the beam)
    tilted toward it, pulsing and turning. The right cowers, looks up at it, trembles; then its head rises slowly
    toward the saucer, mouth falling open, as if being beamed up, and drops back as the UFO zips off. Why not right
    over its head: holding the saucer level up there is out of the left's reach (the wrist points straight down
    only below ~16 cm, and the far side of the stage is ~50 cm off), so it hovers ~17 cm inward of the head and ~5 cm
    above it, tilted 40 deg to aim the beam at it; the right's face comes within a few cm of the saucer (a declared pair)."""
    s = Seq()
    H = np.array(H, float) + [2 * MID_X, 0, 0]  # H's x is from the right base (its head is what the saucer hovers at)
    stL = show.stage_pose("left")
    qH = s._solve("left", H, ufo_frame(TILT), stL + np.array([0, 0.89, 0.9, -1.68, 0, 0]))
    dH = qH - stL
    cower = pose(j1=-0.45, j2=0.5, j3=-0.45, p=0.2, j5=0.5)                   # crouched, turned in, looking up
    beam = cower + pose(j2=0.08, j3=0.15, p=0.15)                             # the head lifted toward the saucer
    s.grip("right", 0.3, 0.3)                                                 # mouth shut, scared
    s.move(dH + pose(j1=0.25, j2=-0.1, j3=0.1, p=0.25), cower, T=1.0, mirror=False, frac=FAST)  # it comes in high
    s.move(dH, None, T=0.5, mirror=False)                                     # ...and drops into the hover
    w = glide(0.3)
    T = 2.1  # the hover (was 2.3; 2.1 keeps the duet under 8 s with the Round 3 offset)

    def ufo(u):  # pulsing (a 1.5 Hz bob) and slowly turning
        t = u * T + 2.3 - T  # the bob in step with the rise (RISE) as in the 2.3 s hover
        return dH + np.sin(np.pi * u) ** 2 * pose(j2=0.012 * np.sin(2 * np.pi * 1.5 * t),
                                                     j3=0.03 * np.sin(2 * np.pi * 1.5 * t),
                                                     j6=0.4 * np.sin(2 * np.pi * 0.4 * t))

    RISE, UP = T - 1.38, 1.035  # s: the rise into the beam starts here and takes this long (the 2.3 s hover's; a
    # faster rise closes on the saucer at > 5 cm/s), so the shorter hover only trembles for less

    def victim(u):  # trembles, then rises into the beam
        t = u * T
        shake = (1 - w(np.clip((t - RISE + 0.115) / 0.575, 0, 1))) * np.sin(np.pi * min(t / 0.46, 1) / 2) * \
            pose(p=0.03 * np.sin(2 * np.pi * 2.8 * t), j6=0.04 * np.sin(2 * np.pi * 2.5 * t + 1))
        return cower + w(np.clip((t - RISE) / UP, 0, 1)) * (beam - cower) + shake
    s.grip("right", 1.0, 0.8, after=RISE + 0.18)                              # the mouth falls open
    s.path(ufo, victim, T=T, warp=None)
    s.grip("right", 0.3, 0.4, after=0.2)                                      # (shut once the saucer is off)
    s.move(dH + pose(j1=ZIP[0], j2=ZIP[1], j3=ZIP[2], p=ZIP[3]), cower + pose(j3=-0.05, p=-0.3), T=0.4, mirror=False)
    # zip off; the right drops back, slumped
    s.home(T=1.0, frac=FAST)
    return s.duet("ufo_abduct", "The left flies the UFO in and hovers it over the right, pulsing and turning, its beam "
                  "tilted at it; the right cowers, looks up, trembles, then its head rises slowly toward the saucer, "
                  "mouth falling open, as if being beamed up, and drops back as the UFO zips off: abduction, beam me "
                  "up, aliens.",
                  [{"left": ["prop"], "right": HAND, "min": 0.025}, {"left": HAND, "right": HAND, "min": 0.03}],
                  props={"left": "ufo", "right": False})


# ---------------------------------------------------------------- mime (Nov 8, Marceau): crisp stops, invisible things
def mirror(GAP=0.065, F=0.8):
    """The mirror: an invisible glass on the midplane, both hands flat on it ~6.5 cm apart (outside the 5 cm slow
    zone, so every move may be crisp), the right the left's reflection: up, down, each stop sharp. Then the mistakes:
    the right goes UP when the left goes down; both freeze; the right snaps into place. The left tests it with a
    sudden jerk up: the right follows a beat late. The left glares; the right, innocent, mirrors the glare."""
    s = Seq()
    glass = lambda z: meet(1.2, 0.1, z, gap=GAP)
    s.move(*glass(0.46), T=0.9, hold=0.2, frac=FAST)                              # hands find the glass
    s.move(*glass(0.52), T=0.35, hold=0.15, frac=F)                               # up: toc
    s.move(glass(0.46)[0], glass(0.56)[1], T=0.35, mirror=False, hold=0.35, frac=F)  # the left goes down, the right
    s.move(None, glass(0.46)[1], T=0.35, mirror=False, hold=0.25, frac=F)         # UP; frozen; it snaps back
    s.move(glass(0.50)[0], None, T=0.25, mirror=False, frac=F)                     # the test: a sudden jerk up...
    s.move(None, glass(0.50)[1], T=0.25, mirror=False, hold=0.25, frac=F)          # ...the right a beat late. Caught.
    dl, dr = deltas(s)
    s.move(dl + pose(p=-0.15, j6=0.3), dr, T=0.35, mirror=False, hold=0.15, frac=F)   # the left glares
    s.move(None, dr + pose(p=-0.15, j6=0.3), T=0.35, mirror=False, hold=0.3, frac=F)  # the right: same glare
    s.home(T=0.9, frac=FAST)
    return s.duet("mirror", "Mime, the mirror: hands flat on an invisible glass between them, the right is the left's "
                  "reflection, up and down in crisp stops; then it slips, going up when the left goes down, both "
                  "freeze, it snaps back; the left tests it with a sudden jerk and the right is a beat late; the left "
                  "glares and the right innocently mirrors the glare.", [{"left": HAND, "right": HAND, "min": 0.03}],
                  props=False)


def rlook(target, p, azmax=1.2, elmax=0.3):
    """The right's grasp rotation at p pointing at `target`, clipped to what it can reach (in at most azmax, up at
    most elmax)."""
    d = unit(np.asarray(target, float) - p)
    az, el = np.clip(np.arctan2(d[1], d[0]), -1.0, azmax), np.clip(np.arcsin(d[2]), -0.6, elmax)
    return mirror_target(np.zeros(3), frame(aim(az, el)))[1]


def invisible_ball(TB=1.0, APEX=0.08, C=(0.33, -0.40, 0.50), DART=0.04):
    """Mime: the left throws an invisible ball in a high arc; the right's head follows it across, darts the last few
    cm to meet it and catches it in its mouth: a sharp stop and a snap. The ball's arc is a parabola from the left's
    mouth at release to the right's catch point C; the right's head points at the ball every frame (as far as it
    turns)."""
    s = Seq()
    st = show.stage_pose
    C = np.array(C, float)
    wind = pose(j1=0.25, j2=-0.05, j3=0.25, p=0.5)                                # cocked back and out
    throw = pose(j1=-0.3, j2=0.18, j3=0.3, p=0.25)                                # flung in and up
    B0, W0 = fk("left", st("left") + throw)[0], fk("left", st("left") + wind)[0]
    ball = lambda u: B0 + u * (C - B0) + np.array([0, 0, 4 * APEX * u * (1 - u)])
    Rc = rlook(ball(0.95), C)                                                      # the mouth meets the ball
    watch = C - DART * Rc[:, 2]                                                   # the head, before the dart
    qw = Seq()._solve("right", watch, Rc, st("right"))
    qW, qC = (show.mirror(q - st("right"), "right") for q in (qw, Seq()._solve("right", C, Rc, qw)))  # (deltas)
    elev = lambda u: np.arcsin(unit(ball(u) - watch)[2])                          # where the ball is, seen from there
    look = lambda u: qW + pose(p=elev(0.9 * u) - elev(0.9))                       # the head tilts to follow it
    s.grip("left", 0.3, 0.3)                                                      # the ball in its mouth
    s.move(wind, look(0.0), T=0.8, mirror=False, hold=0.25, frac=FAST)
    s.move(throw, None, T=0.35, mirror=False, frac=FAST)                          # THROW
    s.grip("left", 1.0, 0.1, after=-0.12)                                         # (released near the top)
    s.path(lambda u: None, look, T=TB, warp=show.quintic)                        # it watches the ball over...
    s.move(None, qC, T=0.15, mirror=False, frac=0.8)                              # ...darts at it...
    s.grip("right", 0.3, 0.08)                                                    # SNAP: caught
    s.wait(0.25)
    dl, dr = deltas(s)
    s.move(dl, dr + pose(p=0.35, j2=-0.03), T=0.4, mirror=False, hold=0.3, frac=FAST)  # head up, proud
    s.home(T=0.9, frac=FAST)
    return s.duet("invisible_ball", "Mime: the left throws an invisible ball in a high arc; the right's head follows it "
                  "across, darts to meet it and catches it in its mouth with a sharp stop and a snap, then lifts its "
                  "head, proud.", [], props=False)


def hand_through_wall(XW=0.40, Z=0.46):
    """Mime: an invisible wall across the front of the stage at x = XW. The left slaps its hand flat against it,
    crisp stops, at two spots, feeling for an edge. The right watches, then simply reaches straight through it.
    The left turns to stare, tries again: BONK, its head snaps back."""
    s = Seq()
    R = frame(aim(0.0, 0.0))                                                      # pointing at the audience
    spot = lambda y, z, x=XW: (np.array([x, y, z]), R)
    s.move(spot(0.0, Z, XW - 0.07), LOOK * 0.5, T=0.7, mirror=False, frac=FAST)
    s.move(spot(0.0, Z), None, T=0.2, mirror=False, hold=0.25, frac=0.8)          # TOC: the wall
    for y, z in ((0.03, Z + 0.04),):                                            # feel for an edge
        s.move(spot(y, z, XW - 0.02), None, T=0.2, mirror=False, frac=0.8)
        s.move(spot(y, z), None, T=0.15, mirror=False, hold=0.2, frac=0.8)       # toc
    s.move(None, mirror_target(*spot(0.0, Z, XW - 0.05)), T=0.6, mirror=False, frac=FAST)  # the right lines up...
    s.move(None, mirror_target(*spot(0.0, Z, XW + 0.07)), T=0.6, line=True, mirror=False, hold=0.2)  # ...through
    dl, _ = deltas(s)
    s.move(dl + pose(j1=-0.1, j5=0.4, p=0.1), None, T=0.4, mirror=False, hold=0.3, frac=FAST)  # the left stares
    s.move(spot(0.0, Z, XW - 0.04), None, T=0.4, mirror=False, frac=FAST)
    s.move(spot(0.0, Z), None, T=0.15, mirror=False, frac=0.8)                   # tries again: BONK
    dl, _ = deltas(s)
    s.move(dl + pose(p=0.4, j2=-0.03), None, T=0.25, mirror=False, hold=0.2, frac=FAST)   # the head snaps back
    s.home(T=0.9, frac=FAST)
    return s.duet("hand_through_wall", "Mime: the left slaps its hand flat on an invisible wall in front, crisp stops, "
                  "feeling for an edge; the right watches, then simply reaches straight through it; the left stares, "
                  "tries again, and BONK, its head snaps back.", [], props=False)


HANDS = [clap, rub_hands, hug, pull_rope, heart, high_five, kiss]
ENSEMBLES = [chomp, wings, this_big, shrug, rocket, waves, storm, gate_opening, explosion, row_boat, once_upon_a_time,
             the_end, cauldron, drum_roll, car_ride]
TOYS = [car_chase, ufo_abduct]
MIME = [mirror, invisible_ball, hand_through_wall]
LIBRARY = [cross_swords, cheers, poke, place_hat, quick_draw, tip_hats, copycat, elbow_nudge, straight_man, heckle] + ENSEMBLES + HANDS + TOYS + MIME


# Sounds on the contact frames (only where they clearly help; none on the hand-contact duets).
TOUCH_SOUND = {"cross_swords": "sword_clash"}


def touches(dc, du, near=0.01):
    """When the contact parts touch: each dip of the closest contact gap to within `near` of its minimum, at the
    first frame within 3 mm of that dip's own minimum."""
    tq, cd, _, _ = dc.frames(du)
    v = cd.min(1)
    on = np.concatenate([[False], v <= v.min() + near, [False]])
    edges = np.flatnonzero(np.diff(on.astype(int)))
    return [round(float(tq[a + int(np.argmax(v[a:b] <= v[a:b].min() + 0.003))]), 2) for a, b in zip(edges[::2], edges[1::2])]


def main(names):
    duets.DUET_DIR.mkdir(exist_ok=True)
    dc = duets.DuetChecker()
    for fn in LIBRARY:
        if names and fn.__name__ not in names:
            continue
        d = fn()
        if d["name"] in TOUCH_SOUND:
            at = touches(dc, duets.Duet(d))
            d["sound"] = {"name": TOUCH_SOUND[d["name"]], "at": at[0] if len(at) == 1 else at}
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
