"""Renders every gesture to previews/<name>.gif from the audience side (both arms, table; props only in a _props stance), for review.

  preview.py [name ...]        default: every gesture in gestures/
  preview.py --stance each_other [name ...]    the same facing each other, to previews/each_other/<name>.gif
  (each_other_props / audience_props: holding props)
  preview.py --talk                            previews/talk.gif: the talking mouth (POST /talk)
  preview.py --sound name ...                  previews/sound/<name>.mp4 at 25 fps, its default sound mixed in on its
                                               hits (the gesture's "sound"), to judge the sync (needs ffmpeg)
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


def render(sc, renderer, g, ok, why, stance="audience", fps=FPS, hits=()):
    frames = []
    sides = (g.side,) if g.side else show.SIDES if g.arms == "both" else ("left",)
    traj = {s: g.samples(s, stance=stance) for s in sides}
    n = len(next(iter(traj.values()))[0])
    for s in show.SIDES:  # a toy gesture (car, ufo) shows its toy in the playing hand; else sticks in a _props stance
        sc.show_prop(s, (s in sides and g.prop) if g.prop else show.has_props(stance))
    for i in list(range(0, n, int(show.HZ / fps))) + [n - 1] * (fps // 2):  # hold the last pose for ~0.5 s
        for s in show.SIDES:
            if s in traj:
                Q, G = traj[s]
                sc.set(s, Q[i], 1.0 if G is None else G[i])
            else:
                sc.set(s, show.stage_pose(s, stance), 1.0)
        mujoco.mj_forward(sc.m, sc.d)
        renderer.update_scene(sc.d, camera="audience")
        im = Image.fromarray(renderer.render())
        dr = ImageDraw.Draw(im)
        dr.text((6, 4), f"{g.name}  {g.duration:.1f} s  ({'both arms' if g.arms == 'both' else 'one arm'})"
                + ("" if stance == "audience" else f"  facing {stance}"),
                fill=(255, 255, 255) if ok else (255, 80, 80))
        if not ok:
            dr.text((6, 18), ("REJECTED: " + why)[:70], fill=(255, 80, 80))
        dr.text((6, H - 14), "right arm", fill=(200, 200, 200)); dr.text((W - 56, H - 14), "left arm", fill=(200, 200, 200))
        dr.text((W - 70, 4), f"{i / show.HZ:4.1f} s", fill=(200, 200, 200))
        if any(0 <= i / show.HZ - a < 0.2 for a in hits):
            dr.text((W // 2 - 20, 20), "HIT", fill=(255, 220, 60))
        frames.append(im.quantize(48, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE))
    return frames


def main(names, stance="audience"):
    ck = show.Checker()
    ok, bad, _ = show.load_library(ck, verbose=False)
    out = OUT if stance == "audience" else OUT / stance
    sc = show.Scene()
    sc.m.vis.global_.offwidth, sc.m.vis.global_.offheight = W, H
    renderer = mujoco.Renderer(sc.m, H, W)
    out.mkdir(parents=True, exist_ok=True)
    for f in sorted(show.GESTURE_DIR.glob("*.json")):
        g = show.load(f)
        if names and g.name not in names or stance != "audience" and g.name in show.FOLD:
            continue
        good = g.name in ok and stance in ok[g.name].stances
        why = bad.get(g.name) or ck.stance_rejected.get((g.name, stance), "")
        frames = render(sc, renderer, g, good, why, stance)
        path = out / f"{g.name}.gif"
        frames[0].save(path, save_all=True, append_images=frames[1:], duration=int(1000 / FPS), loop=0, optimize=True)
        print(f"{path}  {len(frames)} frames  {path.stat().st_size // 1024} KB")


def sound(names, fps=25):
    """previews/sound/<name>.mp4: the gesture with its default sound mixed in at each hit, frame-exact."""
    ok, _, _ = show.load_library(show.Checker(), verbose=False)
    sc = show.Scene()
    sc.m.vis.global_.offwidth, sc.m.vis.global_.offheight = W, H
    renderer = mujoco.Renderer(sc.m, H, W)
    out = OUT / "sound"
    for name in names:
        g = ok[name]
        ats = g.sound["at"] if isinstance(g.sound["at"], list) else [g.sound["at"]]
        frames = render(sc, renderer, g, True, "", fps=fps, hits=ats)
        mux(frames, fps, g.sound["name"], ats, out / f"{name}.mp4")


def mux(frames, fps, name, ats, path):
    """frames + the sfx `name` mixed in at each hit time in ats (s), started its lead early as the agent does ->
    an mp4 at path (preview_duets.py --sound uses it too)."""
    import subprocess, tempfile
    sfx = show.DIR.parent / "agent" / "sounds" / "sfx"
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as d:
        for k, f in enumerate(frames):
            f.convert("RGB").save(f"{d}/{k:04d}.png")
        ins = sum((["-i", str(next(sfx.glob(f"*/{name}.ogg")))] for _ in ats), [])
        lead = float(subprocess.run(["uv", "run", "python", "-c", f"import gestures; print(gestures.sfx_lead({name!r}))"],
                                    cwd=sfx.parent.parent, check=True, capture_output=True, text=True).stdout)
        mix = "".join(f"[{k + 1}:a]adelay={max(0, round((a - lead) * 1000))}:all=1[a{k}];" for k, a in enumerate(ats))
        mix += "".join(f"[a{k}]" for k in range(len(ats))) + f"amix=inputs={len(ats)}:normalize=0[aout]"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(fps), "-i", f"{d}/%04d.png", *ins,
                        "-filter_complex", mix, "-map", "0:v", "-map", "[aout]", "-t", f"{len(frames) / fps:.2f}",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)], check=True)
    print(f"{path}  hits {ats}  lead {lead}  {path.stat().st_size // 1024} KB")


def talk(stance="each_other"):
    """previews/talk.gif: idling, the left arm talks, then the right answers; close-up of the talking head."""
    import mouth
    sc = show.Scene()
    sc.m.vis.global_.offwidth, sc.m.vis.global_.offheight = W, H
    big, small = mujoco.Renderer(sc.m, H, W), mujoco.Renderer(sc.m, 100, 150)
    for s in show.SIDES:  # no props: the mouth is free
        sc.m.geom_rgba[sc.m.geom(("l_" if s == "left" else "r_") + "prop").id] = 0
    g = show.load(show.GESTURE_DIR / "idle_breathe.json")
    traj = {s: g.samples(s, stance=stance)[0] for s in show.SIDES}
    mo = {s: mouth.Mouth(seed=i) for i, s in enumerate(show.SIDES)}
    script = [(0.5, None), (2.5, "left"), (0.6, None), (2.5, "right"), (0.8, None)]
    frames, i = [], 0
    for secs, who in script:
        for s in show.SIDES:
            mo[s].on = s == who
        for k in range(int(secs * show.HZ)):
            for s in show.SIDES:
                gr, dq = mo[s].step(0.05)
                sc.set(s, traj[s][i % len(traj[s])] + dq, gr)
            i += 1
            if k % int(show.HZ / FPS):
                continue
            mujoco.mj_forward(sc.m, sc.d)
            big.update_scene(sc.d, camera="audience")
            im = Image.fromarray(big.render())
            if who:
                cam = mujoco.MjvCamera()
                cam.lookat[:] = sc.d.site_xpos[sc.site[who]]; cam.distance, cam.azimuth, cam.elevation = 0.3, 180, -10
                small.update_scene(sc.d, camera=cam)
                im.paste(Image.fromarray(small.render()), (W - 154, H - 118))
            dr = ImageDraw.Draw(im)
            dr.text((6, 4), f"/talk  {who + ' arm talking' if who else 'idle'}", fill=(255, 255, 255))
            dr.text((6, H - 14), "right arm", fill=(200, 200, 200)); dr.text((W - 210, H - 14), "left arm", fill=(200, 200, 200))
            frames.append(im.quantize(64, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE))
    path = OUT / "talk.gif"
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=int(1000 / FPS), loop=0, optimize=True)
    print(path, len(frames), "frames")


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["--talk"]:
        talk()
    elif a[:1] == ["--sound"]:
        sound(a[1:])
    elif a[:1] == ["--stance"]:
        main(a[2:], a[1])
    else:
        main(a)
