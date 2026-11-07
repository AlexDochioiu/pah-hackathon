"""Renders every gesture to previews/<name>.gif from the audience side (both arms, table, props), for review.

  preview.py [name ...]        default: every gesture in gestures/
Off-screen MuJoCo: on the station run with MUJOCO_GL=egl (or osmesa); on a Mac MUJOCO_GL=cgl. The left arm is on the
RIGHT of the image (seen from the audience). Gestures meant for one arm play on the left arm, the right arm stands at
the stage pose; "both" gestures play mirrored on both arms; stage starts from rest.
"""
import os, sys
os.environ.setdefault("MUJOCO_GL", "cgl" if sys.platform == "darwin" else "egl")
import mujoco, numpy as np
from PIL import Image, ImageDraw
import show

W, H, FPS = 360, 240, 10
OUT = show.DIR / "previews"


def render(sc, renderer, g, ok, why):
    frames = []
    sides = show.SIDES if g.arms == "both" else ("left",)
    traj = {s: g.samples(s) for s in sides}
    n = len(next(iter(traj.values()))[0])
    for i in list(range(0, n, int(show.HZ / FPS))) + [n - 1] * 5:  # hold the last pose for ~0.5 s
        for s in show.SIDES:
            if s in traj:
                Q, G = traj[s]
                sc.set(s, Q[i], 1.0 if G is None else G[i])
            else:
                sc.set(s, show.stage_pose(s), 1.0)
        mujoco.mj_forward(sc.m, sc.d)
        renderer.update_scene(sc.d, camera="audience")
        im = Image.fromarray(renderer.render())
        dr = ImageDraw.Draw(im)
        dr.text((6, 4), f"{g.name}  {g.duration:.1f} s  ({'both arms' if g.arms == 'both' else 'one arm'})",
                fill=(255, 255, 255) if ok else (255, 80, 80))
        if not ok:
            dr.text((6, 18), ("REJECTED: " + why)[:70], fill=(255, 80, 80))
        dr.text((6, H - 14), "right arm", fill=(200, 200, 200)); dr.text((W - 56, H - 14), "left arm", fill=(200, 200, 200))
        dr.text((W - 70, 4), f"{i / show.HZ:4.1f} s", fill=(200, 200, 200))
        frames.append(im.quantize(48, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE))
    return frames


def main(names):
    ok, bad, _ = show.load_library(verbose=False)
    sc = show.Scene()
    sc.m.vis.global_.offwidth, sc.m.vis.global_.offheight = W, H
    renderer = mujoco.Renderer(sc.m, H, W)
    OUT.mkdir(exist_ok=True)
    for f in sorted(show.GESTURE_DIR.glob("*.json")):
        g = show.load(f)
        if names and g.name not in names:
            continue
        frames = render(sc, renderer, g, g.name in ok, bad.get(g.name, ""))
        path = OUT / f"{g.name}.gif"
        frames[0].save(path, save_all=True, append_images=frames[1:], duration=int(1000 / FPS), loop=0, optimize=True)
        print(f"{path}  {len(frames)} frames  {path.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main(sys.argv[1:])
