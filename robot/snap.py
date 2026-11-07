"""Grab one frame from each station camera into ~/dial/snaps/<role>.jpg plus a side-by-side all.jpg.
Run: cd ~/physical-ai-hacks && .venv/bin/python ~/dial/snap.py   (fails while dreamcli/dhk-collect hold the cameras)
"""
import pathlib, sys, time
import cv2, numpy as np
from dream_hackathon import cameras, config

out = pathlib.Path.home() / "dial/snaps"; out.mkdir(parents=True, exist_ok=True)
station = config.load("configs/station.yaml")
cams = cameras.open_cameras(station.cameras, 640, 360)
try:
    time.sleep(2.0)  # ponytail: let auto-exposure settle; tune if frames come out dark
    frames = {}
    for role, cam in cams.items():
        f = cam.latest()
        frames[role] = f
        cv2.imwrite(str(out / f"{role}.jpg"), f if "--rgb" not in sys.argv else cv2.cvtColor(f, cv2.COLOR_RGB2BGR))
        print(role, f.shape, "mean", round(float(f.mean()), 1))
    strip = np.hstack([cv2.resize(f, (640, 360)) for f in frames.values()])
    cv2.imwrite(str(out / "all.jpg"), strip if "--rgb" not in sys.argv else cv2.cvtColor(strip, cv2.COLOR_RGB2BGR))
finally:
    for cam in cams.values():
        cam.close()
