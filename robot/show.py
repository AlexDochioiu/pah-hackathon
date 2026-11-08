"""Shared gesture code: the two-arm MuJoCo scene, the gesture format, trajectory sampling and the safety checker.

Pure kinematics, no hardware: stage.py, make_gestures.py, preview.py, record.py and check_gestures.py import it.
Shared frame = the left arm's base frame (x toward the audience, y left, z up); the right base is at -RIGHT_OFFSET.
A gesture is joint-space keyframes for the LEFT arm, relative to its stage pose; the right arm plays the mirror image.
"""
import json, pathlib
import mujoco, numpy as np

DIR = pathlib.Path(__file__).resolve().parent
GESTURE_DIR = DIR / "gestures"
SIDES = ("left", "right")
HZ = 50

RIGHT_OFFSET = np.array([0.0056, 0.602, 0.0])  # right-arm frame = left-arm frame + this (calib.json)
TABLE_Z = -0.012                               # table surface, arm frame
PROP_LEN, PROP_R = 0.15, 0.015                 # a prop sticks out of the fingers along the grasp axis
MIRROR = np.array([-1, 1, 1, 1, -1, -1])       # y -> -y: yaw (j1, j5) and roll (j6) flip, pitch joints stay

# Joint limits: arm spec intersected with i2rt's model (j3 model max 3.14). j2/j3 rest on their 0 stop at home.
LO = np.array([-2.62, 0.0, 0.0, -1.57, -1.57, -2.09])
HI = np.array([3.05, 3.65, 3.14, 1.57, 1.57, 2.09])
MARGIN = 0.08                                  # rad kept from every limit, except the j2/j3 fold stops below
SAFE_LO, SAFE_HI = LO + MARGIN, HI - MARGIN
SAFE_LO[1:3] = -0.02
VMAX = 1.5                                     # rad/s, any joint
AMAX = np.array([8.0, 8.0, 8.0, 15.0, 15.0, 15.0])  # rad/s^2; the wrist joints carry little mass
GRIP_VMAX = 3.5  # gripper (0..1) per s: the DM4310 (~21 rad/s) turns the 6.57 rad stroke in ~0.3 s; untested
CLEAR = 0.04                                   # m every moving link, grasp site and prop tip stay above the table
GAP = 0.15                                     # m the two arms (links + props) stay apart
VOX = 0.01                                     # swept-volume voxel; the gap check subtracts its error (VOX*sqrt(3))
# Don't lean back (Nov 7): the bases sit near the table's back edge and the table is light, so moving the arms' mass
# behind the bases tips it over backward. Each arm's centre of mass (link1 and below, gripper; base frame) must stay
# ahead of the stage pose's COM minus a margin, in every gesture, idle and blend. Folds (the stage/rest gestures and
# the server's blends and stage/rest moves, all <= 75 % of the joint limits) may lean back as far as rest or the pose
# they start from, no further: rest is folded low and the table holds it.
LEAN_BACK_CHECK = True  # switch: False turns the rule off everywhere (checker, stage server, arm.py)
COM_BACK_MARGIN = 0.02  # ponytail: calibration knob, m the COM may go behind the stage pose's COM (first guess)
FOLD = ("stage", "rest")

# Stage pose: lift.py's raised pose (gripper ~0.48 m up, facing the audience) with the "head" (gripper) level.
# Not turned inward: with 15 cm props the arms have only ~0.22 m of inward room each before the 15 cm gap.
STAGE_LEFT = np.array([0.0, 0.8, 1.2, -0.3, 0.0, 0.0])
# Stances: the pose gestures play relative to. "audience" = STAGE_LEFT (both face the audience); "each_other" = base
# and head yawed toward the other arm, so two characters can slash / wave / roar AT each other (left j1 < 0, j5 > 0;
# the right arm mirrors). Every gesture is checked in each stance; one that fails a stance is refused only there.
# Props (Nov 7, evening): empty hands are the default (the gripper is the mouth), so the plain stances are checked
# WITHOUT the prop sticks and "each_other" turns the heads deep enough for eye contact (the wrist cameras, the eyes,
# point at each other's head). While an arm holds a prop (stage /props) the "_props" variants apply: checked with
# both props, and a shallow facing turn, since the props would cross.
FACING_LEFT = np.array([-0.12, 0.04, 0.0, 0.04, 1.05, 0.0])      # delta from STAGE_LEFT: head ~66 deg toward the other
FACING_PROPS_LEFT = np.array([-0.2, 0.0, 0.0, 0.0, 0.25, 0.0])  # with props: ~25 deg
PROPS = "_props"
STANCES = {"audience": STAGE_LEFT, "each_other": STAGE_LEFT + FACING_LEFT,
           "audience" + PROPS: STAGE_LEFT, "each_other" + PROPS: STAGE_LEFT + FACING_PROPS_LEFT}
FACINGS = ("audience", "each_other")  # what /stance takes; the props mode picks the variant


def with_props(stance, props):
    """The stance variant for a props mode: ("each_other", True) -> "each_other_props"."""
    return stance.removesuffix(PROPS) + (PROPS if props else "")


def has_props(stance):
    return stance.endswith(PROPS)

try:
    import yaml
    _home = yaml.safe_load((pathlib.Path.home() / ".dreamlabs/station.yaml").read_text())
    HOME = {"left": np.asarray(_home["follower_l"]["home"]), "right": np.asarray(_home["follower_r"]["home"])}
except Exception:  # off the station: the values dreamcli wrote on Nov 7
    HOME = {"left": np.array([0.0677, 0.0048, 0.0071, -0.0715, 0.0006, 0.0761]),
            "right": np.array([0.0326, 0.0017, 0.0109, -0.0784, -0.0151, -0.0261])}


def mirror(dq, side):
    return np.asarray(dq) * (MIRROR if side == "right" else 1)


def stage_pose(side, stance="audience"):
    return mirror(STANCES[stance], side)


def key(name, side, stance="audience"):
    """Swept-volume cache key of a gesture on one arm in one stance."""
    return (name, side) if stance == "audience" else (name, side, stance)


# ---------------------------------------------------------------- scene

def scene_spec():
    """MjSpec: both arms (prefix l_ / r_) with a prop stick in each gripper (Scene.props says if it counts), a table,
    a light and an audience camera."""
    from i2rt.robots.utils import ArmType, GripperType, combine_arm_and_gripper_xml
    xml = combine_arm_and_gripper_xml(ArmType.YAM, GripperType.from_string_name("linear_4310"))
    s = mujoco.MjSpec()
    s.visual.global_.offwidth, s.visual.global_.offheight = 640, 480
    w = s.worldbody
    w.add_geom(type=mujoco.mjtGeom.mjGEOM_BOX, pos=[0.15, -0.301, TABLE_Z - 0.01], size=[0.45, 0.65, 0.01],
               rgba=[0.55, 0.42, 0.3, 1], contype=0, conaffinity=0)
    w.add_light(pos=[1.2, -0.3, 1.5], dir=[-0.6, 0, -1], diffuse=[0.8, 0.8, 0.8])
    w.add_light(pos=[-0.5, -0.3, 1.5], dir=[0.3, 0, -1], diffuse=[0.3, 0.3, 0.3])
    w.add_geom(type=mujoco.mjtGeom.mjGEOM_BOX, pos=[-0.45, -0.301, 0.4], size=[0.01, 1.2, 0.8],
               rgba=[0.75, 0.78, 0.82, 1], contype=0, conaffinity=0)  # backdrop, for the previews
    cam, look = np.array([1.05, 0.35, 0.72]), np.array([0.12, -0.301, 0.3])  # audience side, a bit to the left arm's side
    f = (look - cam) / np.linalg.norm(look - cam)
    right = np.cross(f, [0, 0, 1]); right /= np.linalg.norm(right)
    w.add_camera(name="audience", pos=cam, xyaxes=[*right, *np.cross(right, f)], fovy=50)
    for side, pre, pos in (("left", "l_", [0, 0, 0]), ("right", "r_", list(-RIGHT_OFFSET))):
        arm = mujoco.MjSpec.from_file(xml)
        site = arm.site("grasp_site")
        am = arm.compile()  # the site's pose in its body frame is only final after compiling
        sid = am.site("grasp_site").id
        p0 = am.site_pos[sid].copy()
        rot = np.zeros(9); mujoco.mju_quat2Mat(rot, am.site_quat[sid])
        p1 = p0 + rot.reshape(3, 3) @ [0, 0, PROP_LEN]
        site.parent.add_geom(name="prop", type=mujoco.mjtGeom.mjGEOM_CAPSULE, fromto=[*p0, *p1], size=[PROP_R, 0, 0],
                             rgba=[0.95, 0.75, 0.1, 1] if side == "left" else [0.2, 0.6, 0.95, 1],
                             contype=0, conaffinity=0)
        s.attach(arm, prefix=pre, frame=w.add_frame(pos=pos))
    return s


class Scene:
    """Compiled two-arm scene: set poses, get point clouds of the moving parts, render frames."""

    def __init__(self):
        self.m = scene_spec().compile()
        self.d = mujoco.MjData(self.m)
        m = self.m
        self.qadr = {side: np.array([m.jnt_qposadr[m.joint(f"{p}joint{i}").id] for i in range(1, 9)])
                     for side, p in (("left", "l_"), ("right", "r_"))}
        self.finger_max = float(m.jnt_range[m.joint("l_joint7").id][1])
        self.geoms, self.site = {}, {}
        self.props = {s: False for s in SIDES}  # does the prop stick count (clouds, sweeps, clearance)?
        self.prop_geom = {s: m.geom(p + "prop").id for s, p in (("left", "l_"), ("right", "r_"))}
        for side, p in (("left", "l_"), ("right", "r_")):
            self.site[side] = m.site(f"{p}grasp_site").id
            gs = []
            for g in range(m.ngeom):
                body = m.body(m.geom_bodyid[g]).name
                if not body.startswith(p) or body in (p + "base", p + "link1"):  # base and link1 never move off the table
                    continue
                if m.geom_type[g] == mujoco.mjtGeom.mjGEOM_MESH:
                    mid = m.geom_dataid[g]
                    v = m.mesh_vert[m.mesh_vertadr[mid]: m.mesh_vertadr[mid] + m.mesh_vertnum[mid]]
                    _, keep = np.unique(np.round(v / 0.012), axis=0, return_index=True)  # ~1 cm sample of the hull
                    gs.append((g, v[keep].astype(float), 0.0))
                else:  # the prop capsule: points along its axis, each with the capsule radius
                    gs.append((g, np.outer(np.linspace(-1, 1, 16), [0, 0, m.geom_size[g][1]]), float(m.geom_size[g][0])))
            self.geoms[side] = gs

    def set(self, side, q, grip=1.0):
        a = self.qadr[side]
        self.d.qpos[a[:6]] = q
        self.d.qpos[a[6:]] = np.clip(grip, 0, 1) * self.finger_max

    def forward(self):
        mujoco.mj_kinematics(self.m, self.d)

    def cloud(self, side):
        """(points (N,3), radii (N,)) of the side's moving parts for the current pose, shared frame."""
        pts, rad = [], []
        for g, v, r in self.geoms[side]:
            if g == self.prop_geom[side] and not self.props[side]:
                continue
            pts.append(self.d.geom_xpos[g] + v @ self.d.geom_xmat[g].reshape(3, 3).T)
            rad.append(np.full(len(v), r))
        return np.concatenate(pts), np.concatenate(rad)

    def sweep(self, side, Q):
        """Swept volume of joint samples Q (N,6): (voxel points, radii, lowest point above the table in m)."""
        pts, rad, low = [], [], np.inf
        if not len(Q):
            return np.zeros((0, 3)), np.zeros(0), np.inf
        for q in Q:
            self.set(side, q); self.forward()
            p, r = self.cloud(side)
            low = min(low, float(np.min(p[:, 2] - r)) - TABLE_Z)
            pts.append(p); rad.append(r)
        p, r = np.concatenate(pts), np.concatenate(rad)
        out_p, out_r = [], []
        for rv in np.unique(r):
            k = np.unique(np.round(p[r == rv] / VOX), axis=0) * VOX
            out_p.append(k); out_r.append(np.full(len(k), rv))
        return np.concatenate(out_p), np.concatenate(out_r), low


def join(*sweeps):
    """Union of swept volumes."""
    sweeps = [s for s in sweeps if s is not None and len(s[0])]
    if not sweeps:
        return np.zeros((0, 3)), np.zeros(0), np.inf
    return np.concatenate([s[0] for s in sweeps]), np.concatenate([s[1] for s in sweeps]), min(s[2] for s in sweeps)


def gap(a, b):
    """Smallest surface distance between two swept volumes (points, radii, ...), minus voxel error."""
    (pa, ra), (pb, rb) = a[:2], b[:2]
    if not len(pa) or not len(pb):
        return np.inf
    reach = GAP + 2 * PROP_R + 2 * VOX  # points further apart than this in y alone cannot be too close
    ka = (pa[:, 1] < pb[:, 1].max() + reach) & (pa[:, 1] > pb[:, 1].min() - reach)
    kb = (pb[:, 1] < pa[:, 1].max() + reach) & (pb[:, 1] > pa[:, 1].min() - reach)
    pa, ra, pb, rb = pa[ka], ra[ka], pb[kb], rb[kb]
    if not len(pa) or not len(pb):
        return np.inf
    best = np.inf
    for i in range(0, len(pa), 2000):
        d = np.linalg.norm(pa[i:i + 2000, None] - pb[None], axis=2) - rb[None]
        best = min(best, float(np.min(d - ra[i:i + 2000, None])))
    return best - VOX * 3 ** 0.5


# ---------------------------------------------------------------- gestures

def quintic(x):
    x = np.clip(x, 0, 1)
    return x * x * x * (10 + x * (-15 + 6 * x))


def move_time(a, b, want=0.0, frac=0.75):
    """Seconds for a quintic move a -> b that peaks at `frac` of the joint speed and acceleration limits."""
    d = np.abs(np.asarray(b) - np.asarray(a))
    return max(want, 1.875 * d.max() / (frac * VMAX), float(np.sqrt(5.78 * d / (frac * AMAX)).max()))


def blend(q0, q1, speed=1.0, g0=None, g1=None, hz=HZ):
    """Quintic joint-space move q0 -> q1 at hz, excluding q0 (empty if already there); grips too if given.
    speed scales it down (0.3 = 30 %); above 1 it stays at 1, already 75 % of the limits."""
    T = move_time(q0, q1, 0.3 if np.abs(np.asarray(q1) - q0).max() > 1e-3 else 0.0) / float(np.clip(speed, 1e-3, 1.0))
    if g0 is not None and g1 is not None:
        T = max(T, abs(g1 - g0) * 0.6)  # ponytail: ~0.6 s for a full gripper stroke, like lift.py's clap
    n = int(np.ceil(T * hz))
    s = quintic(np.arange(1, n + 1) / n)[:, None] if n else np.zeros((0, 1))
    Q = q0 + s * (np.asarray(q1) - q0)
    G = None if g0 is None else g0 + s[:, 0] * ((g0 if g1 is None else g1) - g0)
    return Q, G

def pchip(t, y, tq):
    """Monotone cubic (Fritsch-Carlson) through keys (t (K,), y (K,D)) at times tq: smooth, never overshoots a key."""
    t, y, tq = np.asarray(t, float), np.asarray(y, float), np.clip(np.asarray(tq, float), t[0], t[-1])
    if len(t) == 1:
        return np.repeat(y[:1], len(tq), 0)
    h = np.diff(t)[:, None]; s = np.diff(y, axis=0) / h
    m = np.zeros_like(y)
    m[0], m[-1] = s[0], s[-1]
    if len(t) > 2:
        w1, w2 = 2 * h[1:] + h[:-1], h[1:] + 2 * h[:-1]
        same = s[:-1] * s[1:] > 0
        with np.errstate(divide="ignore", invalid="ignore"):
            m[1:-1] = np.where(same, (w1 + w2) / (w1 / s[:-1] + w2 / s[1:]), 0)
    m[0] = np.where(np.sign(m[0]) != np.sign(s[0]), 0, m[0]); m[-1] = np.where(np.sign(m[-1]) != np.sign(s[-1]), 0, m[-1])
    i = np.clip(np.searchsorted(t, tq, side="right") - 1, 0, len(t) - 2)
    hh = h[i, 0][:, None]; u = ((tq - t[i]) / hh[:, 0])[:, None]
    h00, h10, h01, h11 = 2 * u**3 - 3 * u**2 + 1, u**3 - 2 * u**2 + u, -2 * u**3 + 3 * u**2, u**3 - u**2
    return h00 * y[i] + h10 * hh * m[i] + h01 * y[i + 1] + h11 * hh * m[i + 1]


class Gesture:
    """A gesture file: {"name", "about", "arms": "one"|"both", "hold": bool, "loop": bool,
    "keys": [{"t": s, "q": [6 rad, left arm, relative to stage], "g": 0..1 (optional, absolute gripper)}]}."""

    def __init__(self, d):
        self.d = d
        self.name, self.about = d["name"], d.get("about", "")
        self.arms, self.hold, self.loop = d.get("arms", "one"), bool(d.get("hold")), bool(d.get("loop"))
        keys = d["keys"]
        self.t = np.array([k["t"] for k in keys], float)
        self.dq = np.array([k["q"] for k in keys], float)
        gk = [(k["t"], k["g"]) for k in keys if "g" in k]
        self.gt, self.gv = (np.array([a for a, _ in gk]), np.array([[b] for _, b in gk])) if gk else (None, None)
        assert self.dq.shape[1] == 6 and np.all(np.diff(self.t) > 0) and self.t[0] == 0, f"{self.name}: bad keys"
        self.duration = float(self.t[-1])
        self.max_speed = None  # set by check()
        self.stances = {"audience"}  # stances it passed the checker in (load_library)

    def samples(self, side, speed=1.0, hz=HZ, stance="audience"):
        """(joints (N,6), grips (N,) or None) at hz for the given speed, absolute for that side."""
        n = max(2, int(round(self.duration / speed * hz)) + 1)
        tq = np.linspace(0, self.duration, n)
        Q = stage_pose(side, stance) + mirror(pchip(self.t, self.dq, tq), side)
        G = None if self.gt is None else pchip(self.gt, self.gv, tq)[:, 0]
        return Q, G


def load(path):
    return Gesture(json.loads(pathlib.Path(path).read_text()))


def motion_problems(Q, hz=HZ):
    """Limit, speed and acceleration problems of a joint trajectory sampled at hz (list of strings)."""
    out = []
    lo, hi = Q.min(0), Q.max(0)
    for j in range(6):
        if lo[j] < SAFE_LO[j] - 1e-6 or hi[j] > SAFE_HI[j] + 1e-6:
            out.append(f"j{j+1} reaches [{lo[j]:.2f}, {hi[j]:.2f}], allowed [{SAFE_LO[j]:.2f}, {SAFE_HI[j]:.2f}]")
    if len(Q) > 2:
        v = np.abs(np.diff(Q, axis=0)).max(0) * hz
        a = np.abs(np.diff(Q, 2, axis=0)).max(0) * hz * hz
        for j in range(6):
            if v[j] > VMAX + 1e-6:
                out.append(f"j{j+1} speed {v[j]:.2f} rad/s > {VMAX}")
            if a[j] > AMAX[j] + 1e-6:
                out.append(f"j{j+1} acceleration {a[j]:.1f} rad/s^2 > {AMAX[j]}")
    return out


def peak_rates(Q, hz=HZ):
    v = np.abs(np.diff(Q, axis=0)).max(0) * hz
    a = np.abs(np.diff(Q, 2, axis=0)).max(0) * hz * hz if len(Q) > 2 else np.zeros(6)
    return v, a


class Checker:
    """Checks gestures and plans in simulation. Holds the swept volume of every checked gesture per side."""

    def __init__(self):
        self.scene = Scene()
        self.swept = {}  # (name, side) -> sweep tuple
        m = self.scene.m
        self._link1 = {s: m.body(p + "link1").id for s, p in (("left", "l_"), ("right", "r_"))}
        self._base = {s: m.body(p + "base").id for s, p in (("left", "l_"), ("right", "r_"))}
        self.stage_x = {s: float(self.com(s, [stage_pose(s)])[0, 0]) for s in SIDES}
        self.rest_x = {s: float(self.com(s, [HOME[s]])[0, 0]) for s in SIDES}

    def com(self, side, Q):
        """(N, 3) centre of mass of the arm's moving links (link1 and below, gripper) per sample, in its base frame."""
        sc, out = self.scene, []
        for q in Q:
            sc.set(side, q); mujoco.mj_kinematics(sc.m, sc.d); mujoco.mj_comPos(sc.m, sc.d)
            out.append(sc.d.subtree_com[self._link1[side]] - sc.d.xpos[self._base[side]])
        return np.array(out)

    def lean_problems(self, side, Q, fold=False):
        """The don't-lean-back rule (LEAN_BACK_CHECK, COM_BACK_MARGIN): problems as strings, empty = ok.
        fold=True: a fold or blend, which may also lean back as far as rest or its first sample."""
        if not LEAN_BACK_CHECK or not len(Q):
            return []
        x = self.com(side, Q)[:, 0]
        base = min(self.stage_x[side], self.rest_x[side], float(x[0])) if fold else self.stage_x[side]
        if x.min() < base - COM_BACK_MARGIN - 1e-6:
            return [f"leans back (tips the table): centre of mass at x = {x.min()*100:.1f} cm, "
                    f"limit {(base - COM_BACK_MARGIN)*100:.1f} cm"]
        return []

    def check_samples(self, side, Q, fold=False):
        """Problems of one arm's absolute joint samples (limits, speed, accel, table clearance, lean-back); returns
        (problems, sweep). fold: see lean_problems()."""
        probs = motion_problems(Q) + self.lean_problems(side, Q, fold=fold)
        sw = self.scene.sweep(side, Q)
        if sw[2] < CLEAR:
            probs.append(f"lowest point{' (prop included)' if any(self.scene.props.values()) else ''} is {sw[2]*100:.1f} cm above the table, needs {CLEAR*100:.0f} cm")
        return probs, sw

    def set_props(self, on):
        """Whether both arms' prop sticks count in what follows (sweeps, clearance, gaps)."""
        self.scene.props = {s: bool(on) for s in SIDES}

    def check(self, g, stance="audience"):
        """Problems of a gesture on either arm at speed 1 (empty = ok), in the stance's props mode (it stays set).
        Sets g.max_speed and caches swept volumes."""
        self.set_props(has_props(stance))
        probs = []
        for side in SIDES:
            Q, G = g.samples(side, stance=stance)
            p, sw = self.check_samples(side, Q, fold=g.name in FOLD)
            probs += [f"{side}: {x}" for x in p]
            self.swept[key(g.name, side, stance)] = sw
        if G is not None and (G.min() < -1e-6 or G.max() > 1 + 1e-6):
            probs.append("gripper outside 0..1")
        Q, G = g.samples("left")
        v, a = peak_rates(Q)
        gv = np.abs(np.diff(G)).max() * HZ if G is not None and len(G) > 1 else 0.0
        g.max_speed = float(min(1.5, VMAX / max(v.max(), 1e-6), np.sqrt(np.min(AMAX / np.maximum(a, 1e-6))),
                                GRIP_VMAX / max(gv, 1e-6)))
        return probs

    def pose_sweep(self, side, q):
        return self.scene.sweep(side, np.asarray(q)[None])


def load_library(checker=None, folder=GESTURE_DIR, verbose=True):
    """Loads and checks every gestures/*.json. Returns (ok {name: Gesture}, rejected {name: reason}, unsafe pairs)."""
    ck = checker or Checker()
    ok, bad = {}, {}
    for f in sorted(pathlib.Path(folder).glob("*.json")):
        try:
            g = load(f)
            probs = ck.check(g)
        except Exception as e:
            bad[f.stem] = f"cannot load: {e}"; continue
        if probs:
            bad[g.name] = "; ".join(probs)
        else:
            ok[g.name] = g
    # Alone on one arm, a gesture must clear the other arm at stage, at rest and idling (or the gesture is rejected).
    others = {side: [ck.pose_sweep(side, stage_pose(side)), ck.pose_sweep(side, HOME[side])] for side in SIDES}
    for side in SIDES:
        if ("idle_breathe", side) in ck.swept:
            others[side].append(ck.swept[("idle_breathe", side)])
    for name in list(ok):
        for side, other in (("left", "right"), ("right", "left")):
            d = min(gap(ck.swept[(name, side)], o) for o in others[other])
            if d < GAP:
                bad[name] = f"on the {side} arm comes {d*100:.1f} cm from the resting/idle {other} arm (< {GAP*100:.0f} cm)"
                ok.pop(name); break
    # Both arms at once: worst case over any timing = swept volume vs swept volume. Unsafe pairs are refused online.
    unsafe = set()
    for a in ok:
        for b in ok:
            if gap(ck.swept[(a, "left")], ck.swept[(b, "right")]) < GAP:
                unsafe.add((a, b))
    # The same for the other stances (folds only with props): a gesture failing there is only refused in that stance.
    ck.stance_rejected, ck.stance_unsafe = {}, {}
    for st in STANCES:
        if st == "audience":
            continue
        good = []
        for name, g in ok.items():
            if name in FOLD and st != "audience" + PROPS:
                continue
            probs = ck.check(g, st)
            if probs:
                ck.stance_rejected[(name, st)] = "; ".join(probs)
            else:
                good.append(name)
        ck.set_props(has_props(st))
        others = {side: [ck.pose_sweep(side, stage_pose(side, st)), ck.pose_sweep(side, HOME[side])] for side in SIDES}
        for side in SIDES:
            if "idle_breathe" in good:
                others[side].append(ck.swept[key("idle_breathe", side, st)])
        for name in list(good):
            for side, other in (("left", "right"), ("right", "left")):
                d = min(gap(ck.swept[key(name, side, st)], o) for o in others[other])
                if d < GAP:
                    ck.stance_rejected[(name, st)] = (f"on the {side} arm comes {d*100:.1f} cm from the {st} or idle "
                                                      f"{other} arm (< {GAP*100:.0f} cm)")
                    good.remove(name); break
        for name in good:
            ok[name].stances.add(st)
        ck.stance_unsafe[st] = {(a, b) for a in good for b in good
                                if gap(ck.swept[key(a, "left", st)], ck.swept[key(b, "right", st)]) < GAP}
    ck.set_props(False)  # empty hands: the default
    if verbose:
        for n, why in bad.items():
            print(f"REJECTED {n}: {why}")
        for (n, st), why in ck.stance_rejected.items():
            print(f"not in stance {st}: {n}: {why}")
    return ok, bad, unsafe
