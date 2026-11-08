"""Does the twin's URDF match our MuJoCo model? Poses both with the same joints and compares every part's mesh, in world.

For random poses (both arms, joints within show.SAFE_LO..SAFE_HI, random grippers) it compares the world-space bounding
box (centre and size) of each part's mesh: base, link1-5, gripper and both fingertips. Any difference in base
placement, joint zero, joint sign or gripper scaling shows up as a centre error. Prints the worst error per part (mm).

  ./check_model.sh            (needs i2rt on PYTHONPATH for show.py's MuJoCo scene, like stage.py)
"""
import pathlib, sys

import numpy as np
import trimesh, yourdfpy

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "robot"))
import show  # noqa: E402
import twin  # noqa: E402
from dimos.robot.manipulators.dual_openyam.model import DUAL_OPENYAM_PACKAGE  # noqa: E402

PARTS = ("base", "link1", "link2", "link3", "link4", "link5", "gripper", "tip_left", "tip_right")
SHIFT = np.array([*twin.BASES["left"], 0.0])  # our shared frame (left base) -> dimos's root (between the bases)


def urdf_model():
    pkg = pathlib.Path(str(DUAL_OPENYAM_PACKAGE))
    xml = twin.twin_urdf().replace("package://dual_openyam_abc_box/", f"{pkg}/")
    path = pathlib.Path("/tmp/twin_check.urdf")
    path.write_text(xml)
    return yourdfpy.URDF.load(str(path), load_meshes=False, build_scene_graph=True)


def main(n=200, seed=0):
    rng = np.random.default_rng(seed)
    u, sc = urdf_model(), show.Scene()
    m, d = sc.m, sc.d
    meshes = {}
    err = {p: 0.0 for p in PARTS}
    size_err = {p: 0.0 for p in PARTS}
    for k in range(n):
        q = {s: rng.uniform(show.SAFE_LO, show.SAFE_HI) for s in show.SIDES}
        g = {s: float(rng.uniform(0, 1)) for s in show.SIDES}
        if k == 0:
            q = {s: np.zeros(6) for s in show.SIDES}
        for s in show.SIDES:
            sc.set(s, q[s], g[s])
        sc.forward()
        cfg = {f"{s}_joint{i + 1}": float(q[s][i]) for s in show.SIDES for i in range(6)}
        # the twin's fingers (dimos makes joint8 a mimic of joint7; the raw URDF here has both free)
        cfg.update({k.removeprefix("arm/"): v for k, v in twin.finger_values(g).items()})
        u.update_cfg(cfg)
        for s, pre in (("left", "l_"), ("right", "r_")):
            for part in PARTS:
                # MuJoCo: the body's mesh geoms, vertices into world
                body = m.body(pre + part).id
                mj = [d.geom_xpos[gi] + m.mesh_vert[m.mesh_vertadr[m.geom_dataid[gi]]:
                                                     m.mesh_vertadr[m.geom_dataid[gi]] + m.mesh_vertnum[m.geom_dataid[gi]]]
                      @ d.geom_xmat[gi].reshape(3, 3).T
                      for gi in range(m.ngeom) if m.geom_bodyid[gi] == body and m.geom_type[gi] == 7]
                mj = np.concatenate(mj) + SHIFT
                # URDF: the link's visual meshes, vertices into world
                link = u.link_map[f"{s}_{part}"]
                ur = []
                for vis in link.visuals:
                    fn = vis.geometry.mesh.filename
                    if fn not in meshes:
                        meshes[fn] = trimesh.load(fn, force="mesh").vertices
                    T = u.get_transform(link.name) @ (vis.origin if vis.origin is not None else np.eye(4))
                    ur.append(meshes[fn] @ T[:3, :3].T + T[:3, 3])
                ur = np.concatenate(ur)
                c_mj, c_ur = (mj.min(0) + mj.max(0)) / 2, (ur.min(0) + ur.max(0)) / 2
                err[part] = max(err[part], float(np.linalg.norm(c_mj - c_ur)))
                size_err[part] = max(size_err[part], float(np.max(np.abs((mj.max(0) - mj.min(0)) - (ur.max(0) - ur.min(0))))))
    print(f"{n} random poses (both arms, random grippers), worst mesh bounding-box error per part, mm:")
    for p in PARTS:
        print(f"  {p:10s} centre {1000 * err[p]:6.2f}   size {1000 * size_err[p]:6.2f}")
    arm = max(err[p] for p in PARTS[:7])
    print("arm MATCH" if arm < 0.001 else "arm MISMATCH", f"(base..gripper worst {1000 * arm:.2f} mm);",
          f"fingertips worst {1000 * max(err['tip_left'], err['tip_right']):.2f} mm")


if __name__ == "__main__":
    main()
