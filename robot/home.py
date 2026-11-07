"""Slowly drive both YAM arms from wherever they are to dreamcli's home (rest) pose, then switch off there.
Run: cd ~/physical-ai-hacks && .venv/bin/python ~/dial/home.py
"""
import pathlib, sys, time
import numpy as np, yaml
from dream_hackathon import arms, config, station_lock

MAX_RAD_S = 0.3  # ponytail: slow on purpose; raise if homing feels sluggish
station = config.load("configs/station.yaml")
d = yaml.safe_load((pathlib.Path.home() / ".dreamlabs/station.yaml").read_text())
HOME = {"left": np.asarray(d["follower_l"]["home"]), "right": np.asarray(d["follower_r"]["home"])}

lock = station_lock.acquire("dial-home")
SIDES = [x for x in sys.argv[1:] if x in ("left", "right")] or ["left", "right"]
if SIDES == ["left", "right"]:
    a = arms.open_arms(station, energize=True)
else:  # ponytail: one arm through YamArms internals, for when the other arm is down
    from i2rt.robots.get_robot import get_yam_robot
    from i2rt.robots.utils import GripperType
    from dream_hackathon.arms import yam
    a = yam.YamArms.__new__(yam.YamArms); a.energized = True
    a._robots = {s: get_yam_robot(channel=station.arms[s].port, gripper_type=GripperType.from_string_name(station.yam.gripper), zero_gravity_mode=False) for s in SIDES}
try:
    js, gs = a.read()
    print("from", {s: np.round(v, 2).tolist() for s, v in js.items()}, "grips", {s: round(g, 2) for s, g in gs.items()})
    secs = max(5.0, max(float(np.max(np.abs(HOME[s] - js[s]))) for s in js) / MAX_RAD_S)
    print(f"homing over {secs:.1f} s")
    hz = 50
    for k in range(1, int(secs * hz) + 1):
        t = k / (secs * hz); t = t * t * (3 - 2 * t)
        a.command({s: js[s] + t * (HOME[s] - js[s]) for s in js}, {s: gs[s] + t * (1.0 - gs[s]) for s in gs})
        time.sleep(1 / hz)
    time.sleep(1.0)
    jr, _ = a.read()
    print("off home", {s: round(float(np.max(np.abs(jr[s] - HOME[s]))), 3) for s in jr})
finally:
    a.close()
    lock.close()
