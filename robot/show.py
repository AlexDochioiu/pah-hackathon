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

BLOCK_H = 0.025  # m, the calibration blocks' height (calib.json block_top_z is their top, touched by each arm)
CALIB = DIR / "calib.json"  # the overhead calibration (calib.py), committed copy


def _calib():
    """(RIGHT_OFFSET, TABLE_Z) from calib.json; the Round 3 values (Nov 7 ~18:40) if it can't be read, loudly."""
    try:
        cal = json.loads(CALIB.read_text())
        off = np.array([float(cal["right_offset"][0]), float(cal["right_offset"][1]), 0.0])
        return off, {s: float(cal["block_top_z"][s]) - BLOCK_H for s in SIDES}
    except Exception as e:
        import sys
        print(f"show.py: WARNING: cannot read {CALIB} ({e!r}); using the Round 3 fallback right offset (-0.0056, "
              f"0.6018) and table z left -0.0065 / right -0.006. The checker's table clearance and arm gap may be wrong.",
              file=sys.stderr, flush=True)
        return np.array([-0.0056, 0.6018, 0.0]), {"left": -0.0065, "right": -0.006}


# right-arm frame = left-arm frame + RIGHT_OFFSET (bases at the same height); TABLE_Z[side] = the table surface in that
# arm's frame. The shared frame is the left arm's and offsets z by 0, so the right arm's parts are checked against
# TABLE_Z["right"] there too.
RIGHT_OFFSET, TABLE_Z = _calib()
# Mirrored keys (a gesture's left-arm keys, a duet's mirrored Cartesian targets) play on both arms: those that touch
# the table are solved against the higher of the two tables, where the same keys go deeper.
TABLE_TOP = max(TABLE_Z.values())
PROP_LEN, PROP_R = 0.15, 0.015                 # a prop sticks out of the fingers along the grasp axis
# What an arm can hold (Nov 7, round 3b), as checker points (site frame: z out of the fingers, x the finger-closing
# axis) with a radius each. "stick" (True) = any handed-in prop, a foam sword; "car" and "ufo" only play their own
# gestures (Gesture.prop). ponytail: measured from the props' listed sizes, not on the real grasp.
_disc = [(x, y) for x in np.arange(-0.04, 0.041, 0.01) for y in np.arange(-0.04, 0.041, 0.01) if x * x + y * y <= 0.0017]
PROP_SHAPES = {
    "stick": ([(0, 0, z) for z in np.linspace(0, PROP_LEN, 16)], PROP_R),
    "car": ([(0, y, 0.013) for y in np.linspace(-0.0235, 0.0235, 6)], 0.015),  # Majorette 1:64, 7.5 x 2.8 x 3 cm,
    # gripped across the roof: its long side along y, the wheels ~2.8 cm out of the fingers
    "ufo": ([(x, y, 0.03) for x, y in _disc] + [(0, 0, 0.012)], 0.012),  # 10 cm saucer hanging from its gripped dome
}
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
# Table contact (Nov 7 evening), only for gestures that declare it ("table": {"min": m, "impact": m/s}) and only with
# empty hands (or "prop": true: a prop gesture whose prop touches, a toy car driving): the gripper and fingertips
# (TABLE_PARTS, plus that prop) may go down to `min` above the table (negative = into it, a knock), descending at <= `impact` m/s once within TABLE_ZONE of `min`; everything else (links, wrist, a prop) keeps
# CLEAR. The real arms sag 1-2 cm and are position-controlled, so a shallow target into the table stays a small error.
TABLE_PARTS = ("gripper", "tip_left", "tip_right")
TABLE_ZONE = 0.05
BANG_DEPTH = 0.004  # ponytail: calibration knob, m below the table surface a knock aims for (tune on the real arms)
GAP = 0.15                                     # m the two arms (links + props) stay apart
VOX = 0.01                                     # swept-volume voxel; the gap check subtracts its error (VOX*sqrt(3))
# Don't lean back (Nov 7): the bases sit near the table's back edge and the table is light, so moving the arms' mass
# behind the bases tips it over backward. Each arm's centre of mass (link1 and below, gripper; base frame) must stay
# ahead of the raised reference pose's COM (LEAN_REF_LEFT) minus a margin, in every gesture, idle and blend. Folds (the stage/rest gestures and
# the server's blends and stage/rest moves, all <= 75 % of the joint limits) may lean back as far as rest or the pose
# they start from, no further: rest is folded low and the table holds it.
LEAN_BACK_CHECK = True  # switch: False turns the rule off everywhere (checker, stage server, arm.py)
COM_BACK_MARGIN = 0.02  # ponytail: calibration knob, m the COM may go behind the reference pose's COM (first guess)
FOLD = ("stage", "rest")
# Pick/place gestures (Nov 7: lars.py --bake's pick_lars / place_lars): one arm's absolute joints ("side") that go down
# to the table to pick up or put down a carried prop ("carry"). Instead of CLEAR they get this rule: wherever any part
# (the prop included) is within the gesture's "near" (>= TABLE_NEAR) of the table, every joint moves at most TABLE_VMAX
# and the arm's own parts stay TABLE_FLOOR above it (the carried prop may touch: it is what gets set down).
TABLE_NEAR, TABLE_VMAX, TABLE_FLOOR = 0.05, 0.3, 0.003

# Stage pose (Nov 7 evening, lowered): the gripper ~0.37 m up and 4 cm further forward than lift.py's raised pose
# (LEAN_REF_LEFT, ~0.48 m up), head level, facing the audience, so the action plays closer to the tabletop.
# Not turned inward: with 15 cm props the arms have only ~0.22 m of inward room each before the 15 cm gap.
# The lean-back rule stays anchored at the raised pose, where the table was measured not to tip: lower and forward
# moves the COM forward, so the lowered stage has ~3 cm more room before the limit.
LEAN_REF_LEFT = np.array([0.0, 0.8, 1.2, -0.3, 0.0, 0.0])
STAGE_LEFT = LEAN_REF_LEFT + np.array([0.0, 0.0, -0.5, 0.5, 0.0, 0.0])
# Stances: the pose gestures play relative to. "audience" = STAGE_LEFT (both face the audience); "each_other" = base
# and head yawed toward the other arm, so two characters can slash / wave / roar AT each other (left j1 < 0, j5 > 0;
# the right arm mirrors). Every gesture is checked in each stance; one that fails a stance is refused only there.
# Props (Nov 7, evening): empty hands are the default (the gripper is the mouth), so the plain stances are checked
# WITHOUT the prop sticks and "each_other" turns the heads deep enough for eye contact (the wrist cameras, the eyes,
# point at each other's head). While an arm holds a prop (stage /props) the "_props" variants apply: checked with
# both props, and a shallow facing turn, since the props would cross.
FACING_LEFT = np.array([-0.08, 0.04, 0.0, 0.04, 1.05, 0.0])      # delta from STAGE_LEFT: head ~66 deg toward the other
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
    mid = -RIGHT_OFFSET[1] / 2  # each arm's half of the table at its own TABLE_Z (looks only: the checker uses TABLE_Z)
    for y0, y1, side in ((mid, 0.349, "left"), (-0.951, mid, "right")):
        w.add_geom(type=mujoco.mjtGeom.mjGEOM_BOX, pos=[0.15, (y0 + y1) / 2, TABLE_Z[side] - 0.01],
                   size=[0.45, (y1 - y0) / 2, 0.01], rgba=[0.55, 0.42, 0.3, 1], contype=0, conaffinity=0)
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
        at = lambda z: list(p0 + rot.reshape(3, 3) @ [0, 0, z])
        q = list(am.site_quat[sid])  # looks only (hidden until shown); the checker uses PROP_SHAPES
        site.parent.add_geom(name="prop_car", type=mujoco.mjtGeom.mjGEOM_BOX, pos=at(0.013), quat=q,
                             size=[0.014, 0.0375, 0.015], rgba=[0.85, 0.1, 0.1, 0], contype=0, conaffinity=0)
        site.parent.add_geom(name="prop_ufo", type=mujoco.mjtGeom.mjGEOM_CYLINDER, pos=at(0.03), quat=q,
                             size=[0.05, 0.01, 0], rgba=[0.7, 0.75, 0.8, 0], contype=0, conaffinity=0)
        site.parent.add_geom(name="prop_ufo_dome", type=mujoco.mjtGeom.mjGEOM_SPHERE, pos=at(0.012), quat=q,
                             size=[0.022, 0, 0], rgba=[0.5, 0.9, 0.6, 0], contype=0, conaffinity=0)
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
        self.geoms, self.site, self.hand = {}, {}, {}
        self.props = {s: False for s in SIDES}  # what the arm holds for the checker: False or a PROP_SHAPES kind
        self.prop_geom = {s: m.geom(p + "prop").id for s, p in (("left", "l_"), ("right", "r_"))}
        self.prop_look = {s: {"stick": [m.geom(p + "prop").id], "car": [m.geom(p + "prop_car").id],
                              "ufo": [m.geom(p + "prop_ufo").id, m.geom(p + "prop_ufo_dome").id]}
                          for s, p in (("left", "l_"), ("right", "r_"))}
        for side, p in (("left", "l_"), ("right", "r_")):
            self.site[side] = m.site(f"{p}grasp_site").id
            gs = []
            for g in range(m.ngeom):
                body = m.body(m.geom_bodyid[g]).name
                if not body.startswith(p) or body in (p + "base", p + "link1") or m.geom(g).name.startswith(p + "prop"):
                    continue  # base and link1 never move off the table; props: PROP_SHAPES
                if m.geom_type[g] == mujoco.mjtGeom.mjGEOM_MESH:
                    mid = m.geom_dataid[g]
                    v = m.mesh_vert[m.mesh_vertadr[mid]: m.mesh_vertadr[mid] + m.mesh_vertnum[mid]]
                    _, keep = np.unique(np.round(v / 0.012), axis=0, return_index=True)  # ~1 cm sample of the hull
                    gs.append((g, v[keep].astype(float), 0.0))
                else:  # the prop capsule: points along its axis, each with the capsule radius
                    gs.append((g, np.outer(np.linspace(-1, 1, 16), [0, 0, m.geom_size[g][1]]), float(m.geom_size[g][0])))
            self.geoms[side] = gs
            self.hand[side] = {g for g, _, _ in gs if m.body(m.geom_bodyid[g]).name[len(p):] in TABLE_PARTS}

    def set(self, side, q, grip=1.0):
        a = self.qadr[side]
        self.d.qpos[a[:6]] = q
        self.d.qpos[a[6:]] = np.clip(grip, 0, 1) * self.finger_max

    def forward(self):
        mujoco.mj_kinematics(self.m, self.d)

    def show_prop(self, side, kind):
        """Rendering: show only the given prop (False/None = none) in the side's gripper."""
        for k, ids in self.prop_look[side].items():
            for g in ids:
                self.m.geom_rgba[g][3] = 1.0 if k == ("stick" if kind is True else kind) else 0.0

    def prop_cloud(self, side):
        """(points, radii) of the held prop for the current pose (empty when the hand is empty)."""
        kind = self.props[side]
        if not kind:
            return np.zeros((0, 3)), np.zeros(0)
        v, r = PROP_SHAPES["stick" if kind is True else kind]
        sid = self.site[side]
        return self.d.site_xpos[sid] + np.asarray(v) @ self.d.site_xmat[sid].reshape(3, 3).T, np.full(len(v), r)

    def cloud(self, side):
        """(points (N,3), radii (N,)) of the side's moving parts and held prop for the current pose, shared frame."""
        pts, rad = [], []
        for g, v, r in self.geoms[side]:
            pts.append(self.d.geom_xpos[g] + v @ self.d.geom_xmat[g].reshape(3, 3).T)
            rad.append(np.full(len(v), r))
        pp, pr = self.prop_cloud(side)
        return np.concatenate(pts + [pp]), np.concatenate(rad + [pr])

    def lows(self, side, Q, prop_hand=False):
        """Per sample: (lowest point of the hand (TABLE_PARTS, plus the held prop if prop_hand), lowest point of
        everything else), above the table."""
        hand, other = [], []
        for q in Q:
            self.set(side, q); self.forward()
            h = o = np.inf
            for g, v, r in self.geoms[side]:
                z = float(np.min(self.d.geom_xpos[g][2] + v @ self.d.geom_xmat[g].reshape(3, 3)[2])) - r - TABLE_Z[side]
                if g in self.hand[side]:
                    h = min(h, z)
                else:
                    o = min(o, z)
            pp, pr = self.prop_cloud(side)
            if len(pp):
                z = float(np.min(pp[:, 2] - pr)) - TABLE_Z[side]
                h, o = (min(h, z), o) if prop_hand else (h, min(o, z))
            hand.append(h); other.append(o)
        return np.array(hand), np.array(other)

    def sweep(self, side, Q):
        """Swept volume of joint samples Q (N,6): (voxel points, radii, lowest point above the table in m)."""
        pts, rad, low = [], [], np.inf
        if not len(Q):
            return np.zeros((0, 3)), np.zeros(0), np.inf
        for q in Q:
            self.set(side, q); self.forward()
            p, r = self.cloud(side)
            low = min(low, float(np.min(p[:, 2] - r)) - TABLE_Z[side])
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
    "keys": [{"t": s, "q": [6 rad, left arm, relative to stage], "g": 0..1 (optional, absolute gripper)}],
    "stance_keys": {stance: keys} (optional: the same move re-solved for that stance, e.g. so a table move still
    reaches the table with the head turned; "each_other" also serves "each_other_props"),
    "sound": {"name": an agent/sounds/sfx name or None, "at": s or [s]} (optional: the impact keyframes; the agent
    plays the sound, or an explicit [sfx] in the same sentence, on them)}."""

    def __init__(self, d):
        self.d = d
        self.name, self.about = d["name"], d.get("about", "")
        self.arms, self.hold, self.loop = d.get("arms", "one"), bool(d.get("hold")), bool(d.get("loop"))
        self.var = {st: self._keys(k) for st, k in d.get("stance_keys", {}).items()}
        self.t, self.dq, self.gt, self.gv = self._keys(d["keys"])
        self.duration = float(self.t[-1])
        self.max_speed = None  # set by check()
        self.table = d.get("table")  # {"min": m, "impact": m/s, "prop": bool}: the hand may touch the table (TABLE_PARTS)
        self.prop = d.get("prop")  # a PROP_SHAPES kind the gesture needs in the hand ("car", "ufo"), else None
        # pick/place (lars.py --bake): "side" = the one arm that plays it, keys are its ABSOLUTE joints (the same in every
        # stance); "near" = m, the TABLE_* rule; "carry" = {"name", "from": s, "to": s, "capsule": [6, site frame], "r"}:
        # it holds that prop from..to (a pick when "from" > 0, a place when "from" is 0). The prop joins PROP_SHAPES and,
        # once picked, plays any gesture (checked with it), unlike the toys.
        self.side, self.near, self.carry = d.get("side"), d.get("near"), d.get("carry")
        assert self.side in (None, *SIDES) and (self.near is None or self.side) and not (self.side and (self.table or self.prop)), \
            f"{self.name}: bad side/near"
        if self.carry:
            c = np.asarray(self.carry["capsule"], float)
            PROP_SHAPES[self.carry["name"]] = ([tuple(c[:3] + f * (c[3:] - c[:3])) for f in np.linspace(0, 1, 12)],
                                               float(self.carry["r"]))
        self.sound = d.get("sound")  # {"name": sfx or None, "at": s or [s]}: its hits, from t = 0 at speed 1
        self.stances = {"audience"}  # stances it passed the checker in (load_library)

    def _keys(self, keys):
        t = np.array([k["t"] for k in keys], float)
        dq = np.array([k["q"] for k in keys], float)
        gk = [(k["t"], k["g"]) for k in keys if "g" in k]
        gt, gv = (np.array([a for a, _ in gk]), np.array([[b] for _, b in gk])) if gk else (None, None)
        assert dq.shape[1] == 6 and np.all(np.diff(t) > 0) and t[0] == 0, f"{self.name}: bad keys"
        return t, dq, gt, gv

    def keys_for(self, stance):
        """(t, dq, gt, gv) played in that stance: its own re-solved keys, else the plain stance's, else the default."""
        return self.var.get(stance) or self.var.get(stance.removesuffix("_props")) or (self.t, self.dq, self.gt, self.gv)

    def samples(self, side, speed=1.0, hz=HZ, stance="audience"):
        """(joints (N,6), grips (N,) or None) at hz for the given speed, absolute for that side."""
        t, dq, gt, gv = self.keys_for(stance)
        n = max(2, int(round(t[-1] / speed * hz)) + 1)
        tq = np.linspace(0, t[-1], n)
        if self.side:  # absolute for self.side; the other arm gets the mirror image (previews only, never played)
            Q = pchip(t, dq, tq) * (1 if side == self.side else MIRROR)
        else:
            Q = stage_pose(side, stance) + mirror(pchip(t, dq, tq), side)
        G = None if gt is None else pchip(gt, gv, tq)[:, 0]
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
        self.stage_x = {s: float(self.com(s, [mirror(LEAN_REF_LEFT, s)])[0, 0]) for s in SIDES}  # the lean reference
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

    def check_samples(self, side, Q, fold=False, table=None):
        """Problems of one arm's absolute joint samples (limits, speed, accel, table clearance, lean-back); returns
        (problems, sweep). fold: see lean_problems(). table: a gesture's table-contact allowance (TABLE_PARTS)."""
        probs = motion_problems(Q) + self.lean_problems(side, Q, fold=fold)
        sw = self.scene.sweep(side, Q)
        if table:
            hand, other = self.scene.lows(side, Q, prop_hand=bool(table.get("prop")))
            if other.min() < CLEAR:
                probs.append(f"lowest point off the hand{' (prop included)' if any(self.scene.props.values()) else ''}"
                             f" is {other.min()*100:.1f} cm above the table, needs {CLEAR*100:.0f} cm")
            if hand.min() < table["min"] - 1e-6:
                probs.append(f"the hand goes {hand.min()*100:.1f} cm above the table, allowed {table['min']*100:.1f} cm")
            down = -np.diff(hand) * HZ
            near = np.minimum(hand[1:], hand[:-1]) < table["min"] + TABLE_ZONE
            if len(down) and np.any(near) and down[near].max() > table["impact"] + 1e-4:
                probs.append(f"the hand comes down at {down[near].max()*100:.0f} cm/s near the table, "
                             f"allowed {table['impact']*100:.0f} cm/s")
        elif sw[2] < CLEAR:
            probs.append(f"lowest point{' (prop included)' if any(self.scene.props.values()) else ''} is {sw[2]*100:.1f} cm above the table, needs {CLEAR*100:.0f} cm")
        return probs, sw

    def set_props(self, on):
        """What both arms hold in what follows (sweeps, clearance, gaps): False, True (a stick), a PROP_SHAPES kind,
        or {side: one of those}."""
        self.scene.props = dict(on) if isinstance(on, dict) else {s: on or False for s in SIDES}

    def check_side(self, g):
        """A one-arm absolute gesture (g.side), at speed 1, with its own hand: empty, then its carried prop from..to.
        With g.near, the TABLE_* rule instead of CLEAR. Caches the swept volume under key(name, side)."""
        side, sc = g.side, self.scene
        Q, G = g.samples(side)
        t = np.linspace(0, g.duration, len(Q))
        c = g.carry
        inside = (t >= c["from"] - 1e-9) & (t <= c["to"] + 1e-9) if c else np.zeros(len(Q), bool)
        probs = motion_problems(Q) + self.lean_problems(side, Q)
        if G is not None and (G.min() < -1e-6 or G.max() > 1 + 1e-6):
            probs.append("gripper outside 0..1")
        near = max(TABLE_NEAR, float(g.near)) if g.near else None
        v = np.abs(np.diff(Q, axis=0)).max(1) * HZ
        v = np.maximum(np.r_[0, v], np.r_[v, 0])  # a sample's speed: into or out of it
        before, sweeps = dict(sc.props), []
        try:
            for held in (False, True):
                idx = np.nonzero(inside == held)[0]
                if not len(idx):
                    continue
                sc.props[side] = c["name"] if held else False
                sweeps.append(sc.sweep(side, Q[idx]))
                for i in idx:
                    sc.set(side, Q[i]); sc.forward()
                    arm_low = min(float(np.min(sc.d.geom_xpos[k][2] + pts @ sc.d.geom_xmat[k].reshape(3, 3)[2] - r))
                                  for k, pts, r in sc.geoms[side]) - TABLE_Z[side]
                    pp, pr = sc.prop_cloud(side)
                    low = min(arm_low, float(np.min(pp[:, 2] - pr)) - TABLE_Z[side] if len(pp) else np.inf)
                    if near is None or low >= near:
                        if low < CLEAR:
                            probs.append(f"at {t[i]:.2f} s the lowest point is {low*100:.1f} cm above the table, needs {CLEAR*100:.0f} cm")
                            break
                    elif v[i] > TABLE_VMAX + 1e-6:
                        probs.append(f"at {t[i]:.2f} s: {v[i]:.2f} rad/s within {near*100:.0f} cm of the table (max {TABLE_VMAX})")
                        break
                    elif arm_low < TABLE_FLOOR:
                        where = f"{abs(arm_low)*1000:.1f} mm {'into' if arm_low < 0 else 'above'} the table"
                        probs.append(f"at {t[i]:.2f} s the fingertips went below the allowed "
                                     f"{-TABLE_FLOOR*1000:.0f} mm brush ({where})" if TABLE_FLOOR < 0 else
                                     f"at {t[i]:.2f} s the arm is {where} (needs {TABLE_FLOOR*1000:.0f} mm)")
                        break
        finally:
            sc.props = before
        sw = join(*sweeps)
        self.swept[key(g.name, side)] = sw
        v1, a1 = peak_rates(Q)
        g.max_speed = float(min(1.0 if g.near else 1.5, VMAX / max(v1.max(), 1e-6), np.sqrt(np.min(AMAX / np.maximum(a1, 1e-6)))))
        g.stances = set(STANCES)
        return [f"{side}: {x}" for x in probs]

    def check(self, g, stance="audience"):
        """Problems of a gesture on either arm at speed 1 (empty = ok), in the stance's props mode (it stays set).
        Sets g.max_speed and caches swept volumes."""
        if g.side:
            return self.check_side(g)
        self.set_props(g.prop or has_props(stance))
        probs = []
        for side in SIDES:
            Q, G = g.samples(side, stance=stance)
            if g.table and has_props(stance) and not g.prop:
                probs.append("touches the table: empty hands only")
            p, sw = self.check_samples(side, Q, fold=g.name in FOLD, table=g.table)
            probs += [f"{side}: {x}" for x in p]
            self.swept[key(g.name, side, stance)] = sw
        if G is not None and (G.min() < -1e-6 or G.max() > 1 + 1e-6):
            probs.append("gripper outside 0..1")
        Q, G = g.samples("left")
        v, a = peak_rates(Q)
        gv = np.abs(np.diff(G)).max() * HZ if G is not None and len(G) > 1 else 0.0
        g.max_speed = float(min(1.0 if g.table else 1.5, VMAX / max(v.max(), 1e-6), np.sqrt(np.min(AMAX / np.maximum(a, 1e-6))),
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
    pairs = (("left", "right"), ("right", "left"))
    for name in list(ok):
        for side, other in pairs:
            if ok[name].side not in (None, side):
                continue
            d = min(gap(ck.swept[(name, side)], o) for o in others[other])
            if d < GAP:
                bad[name] = f"on the {side} arm comes {d*100:.1f} cm from the resting/idle {other} arm (< {GAP*100:.0f} cm)"
                ok.pop(name); break
    # Both arms at once: worst case over any timing = swept volume vs swept volume. Unsafe pairs are refused online.
    unsafe = set()
    for a in ok:
        for b in ok:
            if (ok[a].side != "right" and ok[b].side != "left"
                    and gap(ck.swept[(a, "left")], ck.swept[(b, "right")]) < GAP):
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
            if g.side:  # the same absolute path in every stance: only the other arm's poses differ (below)
                good.append(name); continue
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
            for side, other in pairs:
                if ok[name].side not in (None, side):
                    continue
                d = min(gap(ck.swept[key(name, side, "audience" if ok[name].side else st)], o) for o in others[other])
                if d < GAP:
                    ck.stance_rejected[(name, st)] = (f"on the {side} arm comes {d*100:.1f} cm from the {st} or idle "
                                                      f"{other} arm (< {GAP*100:.0f} cm)")
                    good.remove(name); break
        for name in good:
            ok[name].stances.add(st)
        plain = [n for n in good if not ok[n].side]
        ck.stance_unsafe[st] = {(a, b) for a in plain for b in plain
                                if gap(ck.swept[key(a, "left", st)], ck.swept[key(b, "right", st)]) < GAP}
    ck.set_props(False)  # empty hands: the default
    if verbose:
        for n, why in bad.items():
            print(f"REJECTED {n}: {why}")
        for (n, st), why in ck.stance_rejected.items():
            print(f"not in stance {st}: {n}: {why}")
    return ok, bad, unsafe
