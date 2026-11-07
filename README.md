# Robot story theatre (PAH hackathon)

Two robot arms act out a story that an AI narrator improvises live by voice; interrupt anytime to change it.
Built at MakerMods Physical AI Hacks (Founders Inc SF, Nov 7–8 2026) with LiveKit and two i2rt YAM arms.

- **Start here:** [kb/README.md](kb/README.md), the knowledge base (project, station, arm control, calibration,
  decisions, log). AI agents: read [AGENTS.md](AGENTS.md).
- `robot/` station scripts · `agent/` LiveKit storyteller · `site/` launch site · `social/` posts.

## Station scripts (`robot/`, synced to `~/dial` with `./sync.sh`)
Run each as `cd ~/physical-ai-hacks && .venv/bin/python ~/dial/<script>.py`. Scripts that open the real arms take
the station lock. A human must be at the e-stop for anything that moves the arms ([kb/arm-control.md](kb/arm-control.md)).

| Script | What |
|---|---|
| `home.py [left\|right]` | Slowly drive arms to rest pose, switch off. Use after any e-stop. |
| `wiggle.py` | Smallest motion test (grippers + wrist roll). Refuses unless at rest. |
| `lift.py` | Lift ~38 cm, clap, sway, wave, lower. Refuses unless at rest. |
| `snap.py` | One frame from each camera → `~/dial/snaps/`. |
| `teach.py [secs]` | Float arms; log gripper-tip touches for camera↔arm calibration → `~/dial/teach.npz`. |
| `calib.py` | Fit overhead pixel → arm frame from the touches → `~/dial/calib.json` (copy in repo). |
| `pick.py --from U,V --to U,V [--level N] [--dry] [--probe S]` | Direct-control pick & place of the block at an overhead pixel. Plans + checks in sim first. |
| `stage.py [--real] [--port 8770]` | Stage server: owns both arms, plays safety-checked gestures, HTTP API for the storyteller. Sim unless `--real`. See [kb/gestures.md](kb/gestures.md). |
| `bash ~/dial/bringup.sh [--real]` | First real run: `home.py`, stage at 30 %, every gesture once at 50 % per arm, rest; logs tracking error, stops on error. |
| `record.py NAME --arm left` | Float the arms, record a hand-guided gesture, save it if it passes the safety checker. |
| `check_gestures.py` | Self-check, sim only: every gesture passes the checker, HTTP API works. |
| `make_gestures.py` · `preview.py` | Regenerate the built-in gesture library · render `previews/*.gif` (sim only, `MUJOCO_GL=egl`). |

## Storyteller agent (`agent/`)
LiveKit voice agent that narrates and cues the arms. Setup and run: [agent/README.md](agent/README.md); how it
works: [kb/storyteller.md](kb/storyteller.md). Quick try without arms: `cd agent && python3 fake_stage.py` and,
in another terminal, `uv run storyteller.py console`.
