# PAH hackathon — robot station code

MakerMods Physical AI Hacks, Founders Inc SF, Nov 7–8 2026. Creative track. **Submissions due Sun Nov 8, 15:00.**

## Station
- Box `nuck-9` (Ubuntu 26.04, no GPU), reached over SSH as `pah@<station ip>` (alias `nuck9`). Login from organisers; never commit it.
- 2× i2rt **YAM** 6-DOF arms on CAN (`can_follower_l`, `can_follower_r`), linear grippers.
- 3× RealSense D405: `overhead` (top), `wrist_l`, `wrist_r`. **Don't move the cameras.**
- Organiser kit: `~/physical-ai-hacks` (github.com/koustubh1012/physical-ai-hacks). Our scripts run with its venv from `~/dial`.

## Scripts (`robot/`, synced to `~/dial` with `./sync.sh`)
Run each as `cd ~/physical-ai-hacks && .venv/bin/python ~/dial/<script>.py`. All take the station lock (one arm program at a time).

| Script | What |
|---|---|
| `home.py [left\|right]` | Slowly drive arms to rest pose, switch off. Use after any e-stop. |
| `wiggle.py` | Smallest motion test (grippers + wrist roll). Refuses unless at rest. |
| `lift.py` | Lift ~38 cm, clap, sway, wave, lower. Refuses unless at rest. |
| `snap.py` | One frame from each camera → `~/dial/snaps/`. |
| `teach.py [secs]` | Float arms; log gripper-tip touches for camera↔arm calibration → `~/dial/teach.npz`. |
| `calib.py` | Fit overhead pixel → arm frame from the touches → `~/dial/calib.json` (copy in repo). |
| `pick.py --from U,V --to U,V [--level N] [--dry]` | Direct-control pick & place of the block at an overhead pixel. Plans + checks in sim first. |

## E-stop recovery
1. Fold arms by hand. 2. Release e-stop **and** do the power reset. 3. Reset CAN (`sudo ip link set can_follower_X down/up type can bitrate 1000000`). 4. `home.py`.

## Log
See [NOTES.md](NOTES.md).
