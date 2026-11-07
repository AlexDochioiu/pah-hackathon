"""Renders every duet to previews/duet_<name>.gif: the audience view (same style as preview.py) plus a close-up
inset of the middle, where the arms meet, and the live gap between the declared contact parts.

  preview_duets.py [name ...]        default: every duet in duets/
Off-screen MuJoCo: MUJOCO_GL=cgl on a Mac (the default there), egl on the station.
"""
import os, sys
os.environ.setdefault("MUJOCO_GL", "cgl" if sys.platform == "darwin" else "egl")
import mujoco, numpy as np
from PIL import Image, ImageDraw
import show, duets

W, H, FPS = 360, 240, 10
IW, IH = 150, 100  # inset
OUT = show.DIR / "previews"


def main(names):
    dc = duets.DuetChecker()
    ok, bad = duets.load_duets(dc, verbose=False)
    sc = dc.ck.scene
    m, d = sc.m, sc.d
    m.vis.global_.offwidth, m.vis.global_.offheight = W, H
    big, small = mujoco.Renderer(m, H, W), mujoco.Renderer(m, IH, IW)
    props = {s: m.geom(duets.PRE[s] + "prop").id for s in show.SIDES}
    rgba = {s: m.geom_rgba[g].copy() for s, g in props.items()}
    OUT.mkdir(exist_ok=True)
    for f in sorted(duets.DUET_DIR.glob("*.json")):
        du = duets.load(f)
        if names and du.name not in names:
            continue
        good = du.name in ok
        tq, cd, od, _ = dc.frames(du)
        smp = du.samples()
        held = {s: du.holds_prop(s, tq) for s in show.SIDES}
        # close-up camera: centred between the grippers at the closest contact moment
        k = int(np.argmin(cd.min(1))) if cd.size else len(tq) // 2
        for s in show.SIDES:
            sc.set(s, smp[s][0][k]);
        mujoco.mj_kinematics(m, d)
        look = (d.site_xpos[sc.site["left"]] + d.site_xpos[sc.site["right"]]) / 2
        cam = mujoco.MjvCamera(); cam.lookat[:] = look; cam.distance = 0.42; cam.azimuth = 180; cam.elevation = -12
        frames = []
        step = int(show.HZ / FPS)
        for i in list(range(0, len(tq), step)) + [len(tq) - 1] * 5:
            for s in show.SIDES:
                Q, G = smp[s]
                sc.set(s, Q[i], duets.finger_grip(None if G is None else G[i], held[s][i]))
                m.geom_rgba[props[s]] = rgba[s] if held[s][i] else [0, 0, 0, 0]
            mujoco.mj_forward(m, d)
            big.update_scene(d, camera="audience")
            im = Image.fromarray(big.render())
            small.update_scene(d, camera=cam)
            inset = Image.fromarray(small.render())
            dr = ImageDraw.Draw(im)
            if du.contact:  # no-touch duets: no close-up
                im.paste(inset, (W - IW - 4, H - IH - 18))
                dr.rectangle([W - IW - 5, H - IH - 19, W - 4, H - 18], outline=(255, 255, 255))
                dr.text((W - IW, H - 16), "close-up", fill=(200, 200, 200))
            col = (255, 255, 255) if good else (255, 80, 80)
            dr.text((6, 4), f"duet: {du.name}  {du.duration:.1f} s", fill=col)
            if not good:
                dr.text((6, 18), ("REJECTED: " + bad.get(du.name, ""))[:64], fill=(255, 80, 80))
            if cd.size:
                gapcm = float(cd[i].min()) * 100
                if np.isfinite(gapcm) and gapcm < 15:
                    dr.text((6, 32), f"gap {gapcm:4.1f} cm", fill=(255, 230, 120) if gapcm < 5 else (220, 220, 220))
            dr.text((6, H - 14), "right arm", fill=(200, 200, 200)); dr.text((W - IW - 60, H - 14), "left arm", fill=(200, 200, 200))
            dr.text((W - 44, 4), f"{tq[i]:4.1f} s", fill=(200, 200, 200))
            frames.append(im.quantize(64, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE))
        for s in show.SIDES:
            m.geom_rgba[props[s]] = rgba[s]
        path = OUT / f"duet_{du.name}.gif"
        frames[0].save(path, save_all=True, append_images=frames[1:], duration=int(1000 / FPS), loop=0, optimize=True)
        print(f"{path}  {len(frames)} frames  {path.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main(sys.argv[1:])
