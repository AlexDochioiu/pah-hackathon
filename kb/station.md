# Robot station

## Access
- Linux box `nuck-9` (Ubuntu 26.04, 16 cores, 10 GB RAM, **no GPU**) on the venue network. SSH as user `pah`;
  ask the team for the IP and login (never commit them). A handy `~/.ssh/config` entry:
  `Host nuck9` with `HostName <ip>`, `User pah`, `ControlMaster auto`, `ControlPersist 24h`, `ServerAliveInterval 30`.
- Our scripts live in `~/dial` on the box (synced from `robot/` with `./sync.sh`) and run with the organiser kit's
  Python: `cd ~/physical-ai-hacks && .venv/bin/python ~/dial/<script>.py`.
- `sudo` needs the login password, except `ip link set can_* up`, which is passwordless.

## Hardware
- 2× **i2rt YAM** 6-DOF arms with `linear_4310` grippers, on CAN buses `can_follower_l` and `can_follower_r`
  (1 Mbit/s). There is a physical **e-stop**.
- 3× Intel RealSense D405: `overhead` (top-down view of the table), `wrist_l`, `wrist_r`. Don't move them.

## Software
- Organiser kit `~/physical-ai-hacks` (github.com/koustubh1012/physical-ai-hacks), Python 3.13 `.venv` with
  i2rt, mujoco, mink, numpy, opencv. Useful modules:
  - `dream_hackathon.config.load("configs/station.yaml")` → station config.
  - `dream_hackathon.station_lock.acquire(holder)` → the one-program-at-a-time lock (`.close()` to release).
  - `dream_hackathon.arms.open_arms(station, energize=True|False, sim=False)` → `YamArms` with
    `.read()` → `({side: 6 joints}, {side: gripper})`, `.command(joints, grippers)`, `.close()`.
    `energize=False` floats the arms in gravity compensation (move them by hand); `sim=True` uses i2rt's MuJoCo sim.
  - `dream_hackathon.arms.yam.teleop_ik(station)[side]` → `ArmIK` with `.fk(q)` (4×4 pose of `grasp_site`) and
    `.step(target4x4, q, dt)` (mink differential IK).
  - `dream_hackathon.cameras.open_cameras(station.cameras, 640, 360)`, `.latest()` returns BGR frames.
- Dream Labs CLI in `~/dream-env` (`dreamcli` 1.0.37). Its config `~/.dreamlabs/station.yaml` holds each arm's
  **home (rest) pose**. See [dream-1.md](dream-1.md).
- `uv` at `~/.local/bin/uv`.

## Arm facts
- Rest pose (folded) ≈ all zeros. Exact values in `~/.dreamlabs/station.yaml`:
  left `[0.0677, 0.0048, 0.0071, -0.0715, 0.0006, 0.0761]`, right `[0.0326, 0.0017, 0.0109, -0.0784, -0.0151, -0.0261]`.
- Joint limits (rad): j1 [-2.62, 3.05], j2 [0, 3.65], j3 [0, 3.67], j4 [-1.57, 1.57], j5 [-1.57, 1.57], j6 [-2.09, 2.09].
  Positive j2/j3 lift the arm. Rest + `[0, 0.8, 1.2, 0, 0, 0]` raises the gripper from z ≈ 0.16 m to ≈ 0.54 m
  (tested on the real arms).
- Gripper command: 0 = closed, 1 = open (max opening 2 × 47.5 mm). Fingers close along the `grasp_site` y-axis;
  its z-axis is the approach direction and points +x at rest.

## Frames
- Each arm reports poses in its own base frame. Both arms face +x (across the table), bases parallel.
- **right-arm frame = left-arm frame + (0.0056, 0.602, 0)** m. So the right arm sits at +y of the left one.
- Heights (arm-frame z): 25 mm block tops at 0.013 (left) / 0.020 (right), so the table is at ≈ −0.012 / −0.005.
  See [calibration.md](calibration.md).
