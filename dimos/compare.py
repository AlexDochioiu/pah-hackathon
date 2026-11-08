"""Side-by-side check of the twin against our own sim: freeze a SIM stage mid-gesture, then render the same measured
pose in our MuJoCo scene (show.py's "audience" camera) and screenshot the twin's Viser page from the same camera.

  ./check_model.sh compare [gesture@seconds | duet:name@seconds ...]   (the twin must be running on :8095)

Writes dimos/shots/<name>.png: MuJoCo left, dimos Viser right. It POSTs /play, /duet, /freeze and /rest, so it only
talks to a sim stage (default :8783) and refuses port 8770. Needs no dimos: the robot env plus Playwright, which
drives the installed Google Chrome.
"""
import io, json, os, pathlib, sys, time, urllib.error, urllib.parse, urllib.request

os.environ.setdefault("MUJOCO_GL", "cgl" if sys.platform == "darwin" else "egl")
import mujoco, numpy as np  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "robot"))
import show  # noqa: E402

STAGE = os.environ.get("TWIN_SIM_STAGE", "http://127.0.0.1:8783")
VISER = os.environ.get("TWIN_VISER", "http://127.0.0.1:8095")
OUT = pathlib.Path(__file__).resolve().parent / "shots"
W, H, PANEL, HEADER = 640, 480, 355, 47  # the 3D canvas we keep; Viser's side panel and header, px
DEFAULT = ["rest@0", "wave@2.2", "celebrate@2.0", "bow@1.6", "bite@1.6", "duet:hug@3.0"]

assert urllib.parse.urlparse(STAGE).port != 8770, "compare.py POSTs: never point it at the real arms"


def call(path, body=None):
    req = urllib.request.Request(STAGE + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def rest():
    """Back to rest; after a frozen duet the stage first finishes it (409), so retry."""
    for _ in range(10):
        try:
            return call("/rest", {"arm": "both"})
        except urllib.error.HTTPError:
            time.sleep(1.5)


def pose(spec):
    """Play the gesture or duet from rest, freeze it the given seconds into the motion, return the stage's /state."""
    what, at = spec.rsplit("@", 1)
    rest(); time.sleep(3.5)
    if what != "rest":
        r = call("/duet", {"name": what[5:]}) if what.startswith("duet:") else call("/play", {"arm": "both", "gesture": what})
        time.sleep(float(r.get("starts_in", 0)) + float(at))
        call("/freeze", {"arm": "both"})
    time.sleep(0.8)  # the kin stand-in's measured pose settles onto the command
    return call("/state")


def mujoco_image(sc, renderer, st):
    for s in show.SIDES:
        sc.set(s, np.array(st["arms"][s]["measured"]), st["arms"][s]["grip"])
        sc.show_prop(s, False)
    sc.forward()
    mujoco.mj_camlight(sc.m, sc.d)  # Scene.forward() is mj_kinematics only: it doesn't place the cameras
    renderer.update_scene(sc.d, camera="audience")
    return Image.fromarray(renderer.render())


def viser_image(page):
    """A fresh load of the twin's page (it opens on the audience camera), the 3D canvas only."""
    page.goto(VISER)
    page.add_style_tag(content=".mantine-Notifications-root { display: none !important; }")  # "software WebGL" toast
    page.wait_for_timeout(4000)  # meshes load and the first joint state arrives
    return Image.open(io.BytesIO(page.screenshot())).convert("RGB").crop((0, HEADER, W, HEADER + H))


def main(specs):
    from playwright.sync_api import sync_playwright
    OUT.mkdir(exist_ok=True)
    sc = show.Scene()
    renderer = mujoco.Renderer(sc.m, H, W)
    with sync_playwright() as p:  # the installed Chrome, software WebGL (headless has no GPU canvas on a Mac)
        browser = p.chromium.launch(channel="chrome", args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        page = browser.new_page(viewport={"width": W + PANEL, "height": H + HEADER})
        for spec in specs:
            st = pose(spec)
            img = Image.new("RGB", (2 * W, H), "white")
            img.paste(mujoco_image(sc, renderer, st), (0, 0))
            img.paste(viser_image(page), (W, 0))
            d = ImageDraw.Draw(img)
            grips = ", ".join(f"{s} grip {st['arms'][s]['grip']:.2f}" for s in show.SIDES)
            d.text((8, 8), f"our MuJoCo (robot/show.py): {spec}", fill="black")
            d.text((W + 8, 8), f"dimos twin (Viser): {grips}", fill="white")
            out = OUT / f"{spec.split('@')[0].replace(':', '_')}.png"
            img.save(out)
            print(out, {s: st["arms"][s]["measured"] for s in show.SIDES}, flush=True)
        browser.close()
    rest()


if __name__ == "__main__":
    main(sys.argv[1:] or DEFAULT)
