"""Duets: choreographed two-arm moves where the arms come close and touch, with a per-duet contact allowance.

A duet file (duets/<name>.json) holds synchronized keyframes for BOTH arms as absolute joints (solved by IK at build
time in make_duets.py), played with the same monotone cubic at 50 Hz as gestures:
  {"name", "about", "contact": [{"left": [parts], "right": [parts], "min": m}, ...],
   "prop": {"t": s, "to": "right"} (optional: the left hands its prop to the right at t),
   "keys": [{"t": s, "left": [6 rad], "right": [6 rad], "gl": 0..1, "gr": 0..1 (grips optional)}, ...]}
Every duet starts and ends with both arms at the stage pose. Parts: "prop" (the 15 cm stick, on whichever arm holds
it), "fingers" (both finger links), "gripper" (the gripper body), "wrist" (link4 and link5, the wrist links behind the gripper). The arm links (link2,
link3) can never be contact parts: they always keep show.GAP (15 cm).

Safety (DuetChecker.check, on top of show.Checker's per-arm rules: limits, speed/accel, table clearance with the prop,
lean-back COM): at every 50 Hz frame, with both arms at the same instant,
  - a declared contact pair (a left part x a right part) stays >= its "min" (> 0: never interpenetrating in sim);
  - every other geom pair of the two arms stays >= show.GAP (15 cm);
  - inside APPROACH_ZONE, a contact pair closes at <= APPROACH_V (slow final approach: the real arms sag and lag).
Distances are exact geom-to-geom (MuJoCo's mj_geomDistance; meshes as their convex hulls, so never optimistic).
Without "gl"/"gr" a duet keeps the arm's grip; it is checked with the fingers fully open (widest). Fingers holding
the prop stop on it (G_ON_PROP); duets that touch with the fingers set the grip (0 = closed on the prop).
"""
import json, pathlib, sys
import mujoco, numpy as np
import show

DUET_DIR = show.DIR / "duets"
SIDES, HZ = show.SIDES, show.HZ
APPROACH_ZONE = 0.05  # m: inside this surface distance a contact pair...
APPROACH_V = 0.05     # m/s ...closes at most this fast (at speed 1; duets never play faster than 1)
PARTS = ("prop", "fingers", "gripper", "wrist")  # the "hand"; link2/link3 (shoulder, upper arm) can never be contact parts
PRE = {"left": "l_", "right": "r_"}
G_ON_PROP = 0.3125    # grip where the fingers (9.6 cm full opening) close on the 3 cm prop: they stop there


def finger_grip(g, held):
    """Grip the sim fingers show: the commanded one, but never closer than the held prop allows."""
    g = 1.0 if g is None else float(g)
    return max(g, G_ON_PROP) if held else g


class Duet:
    def __init__(self, d):
        self.d = d
        self.name, self.about = d["name"], d.get("about", "")
        keys = d["keys"]
        self.t = np.array([k["t"] for k in keys], float)
        self.q = {s: np.array([k[s] for k in keys], float) for s in SIDES}
        self.g = {}
        for s in SIDES:
            gk = [(k["t"], k["g" + s[0]]) for k in keys if "g" + s[0] in k]
            self.g[s] = (np.array([a for a, _ in gk]), np.array([[b] for _, b in gk])) if gk else None
        self.contact = [(tuple(c["left"]), tuple(c["right"]), float(c["min"])) for c in d.get("contact", [])]
        self.prop = d.get("prop")
        assert all(self.q[s].shape[1] == 6 for s in SIDES) and np.all(np.diff(self.t) > 0) and self.t[0] == 0, \
            f"{self.name}: bad keys"
        assert all(set(a) | set(b) <= set(PARTS) for a, b, _ in self.contact), f"{self.name}: unknown contact part"
        self.duration = float(self.t[-1])
        self.max_speed = None  # set by DuetChecker.check (<= 1)
        self.stats = {}

    def times(self, speed=1.0, hz=HZ):
        n = max(2, int(round(self.duration / speed * hz)) + 1)
        return np.linspace(0, self.duration, n)

    def samples(self, speed=1.0, hz=HZ):
        """{side: (joints (N,6), grips (N,) or None)}: both arms on the same clock."""
        tq = self.times(speed, hz)
        out = {}
        for s in SIDES:
            G = None if self.g[s] is None else np.clip(show.pchip(*self.g[s], tq)[:, 0], 0, 1)
            out[s] = (show.pchip(self.t, self.q[s], tq), G)
        return out

    def holds_prop(self, side, tq):
        """Which frames the side holds a prop (both do, except across a handover)."""
        if not self.prop:
            return np.ones(len(tq), bool)
        after = np.asarray(tq) >= self.prop["t"]
        return after if side == self.prop["to"] else ~after


def load(path):
    return Duet(json.loads(pathlib.Path(path).read_text()))


class DuetChecker:
    """Frame-by-frame two-arm check of duets. Shares a show.Checker (scene, per-arm rules)."""

    def __init__(self, ck=None):
        self.ck = ck or show.Checker()
        sc = self.ck.scene
        m = sc.m
        self.part = {}  # side -> {geom id: part name or None}
        for s in SIDES:
            p, parts = PRE[s], {}
            for g, _, _ in sc.geoms[s]:
                body = m.body(m.geom_bodyid[g]).name
                parts[g] = ("prop" if m.geom(g).name == p + "prop" else "fingers" if body in (p + "tip_left", p + "tip_right")
                            else "gripper" if body == p + "gripper" else "wrist" if body in (p + "link4", p + "link5") else None)
            self.part[s] = parts
        self._ft = np.zeros(6)

    def frames(self, duet, speed=1.0):
        """Per frame: (contact distance per allowance (N, K), closest other pair (N,), its geom pair per frame)."""
        sc, m, d = self.ck.scene, self.ck.scene.m, self.ck.scene.d
        tq = duet.times(speed)
        smp = duet.samples(speed)
        held = {s: duet.holds_prop(s, tq) for s in SIDES}
        K = len(duet.contact)
        cd, od, op = np.full((len(tq), K), np.inf), np.full(len(tq), np.inf), [None] * len(tq)
        L, R = list(self.part["left"]), list(self.part["right"])
        cap = show.GAP + 0.05
        for i in range(len(tq)):
            for s in SIDES:
                Q, G = smp[s]
                sc.set(s, Q[i], finger_grip(None if G is None else G[i], held[s][i]))
            mujoco.mj_kinematics(m, d)
            for a in L:
                pa = self.part["left"][a]
                if pa == "prop" and not held["left"][i]:
                    continue
                for b in R:
                    pb = self.part["right"][b]
                    if pb == "prop" and not held["right"][i]:
                        continue
                    dist = mujoco.mj_geomDistance(m, d, a, b, cap, self._ft)
                    hit = [k for k, (cl, cr, _) in enumerate(duet.contact) if pa in cl and pb in cr]
                    for k in hit:
                        cd[i, k] = min(cd[i, k], dist)
                    if not hit and dist < od[i]:
                        od[i], op[i] = dist, (a, b)
        return tq, cd, od, op

    def name(self, g):
        m = self.ck.scene.m
        return m.geom(g).name or m.body(m.geom_bodyid[g]).name

    def check(self, duet):
        """Problems (empty = ok). Sets duet.max_speed and duet.stats."""
        probs = []
        smp = duet.samples()
        for s in SIDES:
            Q, G = smp[s]
            if np.abs(Q[0] - show.stage_pose(s)).max() > 1e-3 or np.abs(Q[-1] - show.stage_pose(s)).max() > 1e-3:
                probs.append(f"{s}: must start and end at the stage pose")
            p, _ = self.ck.check_samples(s, Q)
            probs += [f"{s}: {x}" for x in p]
            if G is not None and (G.min() < -1e-6 or G.max() > 1 + 1e-6):
                probs.append(f"{s}: gripper outside 0..1")
        tq, cd, od, op = self.frames(duet)
        if od.min() < show.GAP:
            i = int(np.argmin(od)); a, b = op[i]
            probs.append(f"{self.name(a)} and {self.name(b)} come {od[i]*100:.1f} cm apart at {tq[i]:.2f} s "
                         f"(not a declared contact: needs {show.GAP*100:.0f} cm)")
        closing_worst = 0.0
        for k, (cl, cr, mn) in enumerate(duet.contact):
            c = cd[:, k]
            if mn <= 0:
                probs.append(f"contact {cl}x{cr}: min must be > 0")
            if c.min() < mn:
                i = int(np.argmin(c))
                probs.append(f"contact {'+'.join(cl)} x {'+'.join(cr)} reaches {c[i]*100:.1f} cm at {tq[i]:.2f} s, "
                             f"declared min {mn*100:.1f} cm")
            v = -np.diff(c) * HZ  # closing speed, m/s
            near = np.minimum(c[1:], c[:-1]) < APPROACH_ZONE
            if near.any():
                vn = np.where(near & np.isfinite(v), v, 0)
                closing_worst = max(closing_worst, float(vn.max()))
                if vn.max() > APPROACH_V + 1e-4:
                    i = int(np.argmax(vn))
                    probs.append(f"contact {'+'.join(cl)} x {'+'.join(cr)} closes at {vn[i]*100:.1f} cm/s at {tq[i]:.2f} s "
                                 f"within {APPROACH_ZONE*100:.0f} cm (max {APPROACH_V*100:.0f} cm/s)")
        v, a = show.peak_rates(smp["left"][0])
        v2, a2 = show.peak_rates(smp["right"][0])
        v, a = np.maximum(v, v2), np.maximum(a, a2)
        fac = float(min(1.0, show.VMAX / max(v.max(), 1e-6), np.sqrt(np.min(show.AMAX / np.maximum(a, 1e-6)))))
        duet.max_speed = fac
        duet.stats = {"closest_contact_cm": {f"{'+'.join(cl)} x {'+'.join(cr)}": round(float(cd[:, k].min()) * 100, 1)
                                             for k, (cl, cr, _) in enumerate(duet.contact)},
                      "closest_other_cm": round(float(od.min()) * 100, 1),
                      "fastest_close_cm_s": round(closing_worst * 100, 1)}
        return probs


def load_duets(dc=None, folder=DUET_DIR, verbose=True):
    """Loads and checks every duets/*.json. Returns (ok {name: Duet}, rejected {name: reason})."""
    dc = dc or DuetChecker()
    ok, bad = {}, {}
    for f in sorted(pathlib.Path(folder).glob("*.json")):
        try:
            du = load(f)
            probs = dc.check(du)
        except Exception as e:
            bad[f.stem] = f"cannot load: {e}"; continue
        if probs:
            bad[du.name] = "; ".join(probs)
        else:
            ok[du.name] = du
    if verbose:
        for n, why in bad.items():
            print(f"REJECTED duet {n}: {why}")
    return ok, bad


if __name__ == "__main__":
    ok, bad = load_duets()
    for du in ok.values():
        print(f"{du.name:16s} {du.duration:4.1f} s  max_speed {du.max_speed:.2f}  {du.stats}")
    sys.exit(1 if bad else 0)
