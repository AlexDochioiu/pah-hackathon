# Robot story theatre (PAH hackathon)

Two robot arms act out a story that an AI narrator improvises live by voice; interrupt anytime to change it.
Built at MakerMods Physical AI Hacks (Founders Inc SF, Nov 7–8 2026) with LiveKit and two i2rt YAM arms.

- **Start here:** [kb/README.md](kb/README.md), the knowledge base (project, station, arm control, calibration,
  decisions, log). AI agents: read [AGENTS.md](AGENTS.md).
- `robot/` station scripts · `agent/` LiveKit storyteller · `site/` launch site · `social/` posts.

## Station scripts (`robot/`, synced to `~/dial` with `./sync.sh`)
Run each as `cd ~/physical-ai-hacks && .venv/bin/python ~/dial/<script>.py`. All take the station lock.
A human must be at the e-stop for anything that moves the arms ([kb/arm-control.md](kb/arm-control.md)).

| Script | What |
|---|---|
| `home.py [left\|right]` | Slowly drive arms to rest pose, switch off. Use after any e-stop. |
| `wiggle.py` | Smallest motion test (grippers + wrist roll). Refuses unless at rest. |
| `lift.py` | Lift ~38 cm, clap, sway, wave, lower. Refuses unless at rest. |
| `snap.py` | One frame from each camera → `~/dial/snaps/`. |
| `teach.py [secs]` | Float arms; log gripper-tip touches for camera↔arm calibration → `~/dial/teach.npz`. |
| `calib.py` | Fit overhead pixel → arm frame from the touches → `~/dial/calib.json` (copy in repo). |
| `pick.py --from U,V --to U,V [--level N] [--dry] [--probe S]` | Direct-control pick & place of the block at an overhead pixel. Plans + checks in sim first. |
