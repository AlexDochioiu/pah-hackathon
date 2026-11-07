# Gestures and stage server

The arms act as puppets: named joint-space motions ("gestures") played by one long-running stage server that the
storyteller agent ([storyteller.md](storyteller.md)) calls over HTTP. Code in `robot/`; everything below runs in
simulation unless `--real` is given. Status (Nov 7): built and sim-tested end to end (kinematic sim and i2rt's MuJoCo
sim on the station), then run on the real arms (results at the end).

## Files
| File | What |
|---|---|
| `robot/show.py` | Shared code: two-arm MuJoCo scene, gesture format + interpolation, safety checker. |
| `robot/gestures/*.json` | The library (one file per gesture). |
| `robot/make_gestures.py` | Regenerates the 19 built-in gestures procedurally (keeps recorded ones). |
| `robot/stage.py` | Stage server (HTTP API) and `--bringup` test mode. |
| `robot/bringup.sh` | One-command bring-up: `home.py` then `stage.py --bringup`. |
| `robot/record.py` | Record a new gesture by moving a floating arm by hand. |
| `robot/preview.py` | Renders `robot/previews/<name>.gif` from the audience side. |
| `robot/check_gestures.py` | Assert-based self-check (library, checker, HTTP API in sim). |

## Frames and poses
- Shared frame = the left arm's base frame: x toward the audience, y to the left arm's left, z up. The right base
  is at y = −0.602 (right frame = left frame + (0.0056, 0.602, 0)). Table at z = −0.012.
- **Stage pose** (left arm) = `[0, 0.8, 1.2, −0.3, 0, 0]`: `lift.py`'s raised pose with the gripper ("head") level,
  facing the audience, gripper ~0.48 m up. Not turned inward: with 15 cm props each arm has only ~0.22 m of inward
  room before the 15 cm arm-to-arm gap.
- **Rest** = each arm's `home` in `~/.dreamlabs/station.yaml` (≈ zeros).

## Gesture format
```json
{"name": "slash", "about": "...", "arms": "one|both", "hold": false, "loop": false,
 "keys": [{"t": 0.0, "q": [6 rad], "g": 0.0}, ...]}
```
- `q` = joint deltas for the **left** arm relative to its stage pose; the right arm plays the mirror image
  (multiply by `[−1, 1, 1, 1, −1, −1]`: base yaw j1, wrist yaw j5 and wrist roll j6 flip).
- Toward the other arm = j1 < 0 or j5 > 0 (left-arm convention).
- Keys are interpolated with a monotone cubic (no overshoot) and played at 50 Hz; `speed` scales time.
- `g` (gripper, 0 closed … 1 open) is optional; without it the gripper keeps its current value (so held props stay
  held). Only `gripper_clap` uses it.
- `hold`: stay at the end pose instead of returning to idle (`die_dramatically`, `stage`, `rest`).
  `loop`: repeat (`idle_breathe`). `arms`: the intended use, `both` = mirrored on both arms.
- `recorded`: set by `record.py`; `make_gestures.py` keeps such files when it regenerates.

## Safety checker (`show.Checker`, `show.load_library`)
Every gesture is checked on **both** arms at load (the server and bring-up refuse to start without `idle_breathe`):
- Joint limits with 0.08 rad margin: j1 [−2.62, 3.05], j2 [0, 3.65], j3 [0, 3.14] (the model's j3 max is 3.14,
  not 3.67), j4 [−1.57, 1.57], j5 [−1.57, 1.57], j6 [−2.09, 2.09]. j2/j3 may sit on their 0 stop (rest).
- Joint speed ≤ 1.5 rad/s; acceleration ≤ 8 rad/s² (j1–j3) / 15 rad/s² (j4–j6), sampled at 50 Hz at speed 1.
  Each gesture gets a `max_speed` factor (≤ 1.5) the server clamps requests to.
- Table: every moving link (link2 and below, fingers), and a 15 cm × 1.5 cm-radius prop capsule sticking out of the
  fingers along the grasp axis, stays ≥ 4 cm above the table. Geometry = the hull vertices of i2rt's meshes,
  sampled every ~1 cm.
- Other arm: alone on one arm, a gesture must stay ≥ 15 cm from the other arm at stage, at rest and idling, or it
  is rejected. Two gestures at once: the swept volumes (all frames, any timing) of left × right are compared for
  every pair; pairs closer than 15 cm are listed as `unsafe_pairs` (currently none; closest is `look_at_other` on
  both arms, 19 cm). The gap check subtracts the 1 cm voxel error.
- **Don't lean back** (Nov 7: the arms leaning back tipped the light table): each arm's centre of mass (link1 and
  below plus gripper, i2rt link masses, 4.1 kg; arm base frame) must stay at x ≥ stage COM − `COM_BACK_MARGIN`
  (2 cm) in every gesture, idle and blend. Stage COM x = +1.3 cm, so the limit is −0.7 cm. Folds (the `stage`/`rest`
  gestures, the server's blends and `/stage`/`/rest` moves, `arm.py` moves; all slow, ≤ 75 % of the joint limits)
  may lean back as far as rest (−2.5 cm) or their start pose, minus the margin, no further. `show.LEAN_BACK_CHECK =
  False` switches the rule off everywhere. At stage the COM moves ~+24 cm/rad with j2, −10 with j3, −1 with j4, so
  "head up" pairs j2+ with j3+ (~1:2.4) instead of j2−.
- Not enforced, measured: accelerating the arm's mass forward pushes the table back too (fast forward moves).
  Acceleration-shifted COM x − (z + 0.75 m table) · aₓ / g, at speed 1 (cm): roar −10.4, thrust −7.8, slash −6.2,
  point_at_audience −6.1, gallop −5.0, bow −2.7; everything else ≥ −1.5. If the table still rocks, slow those.
- Online (stage server): every plan (blend from wherever the arm is + gesture, or a pose move) is re-checked before it
  starts, and its swept volume must clear the other arm's **committed** motion by 15 cm; otherwise HTTP 409.
- Assumption: props point out of the fingers along the grasp axis. A prop held crosswise is not modelled.

## Library (19 gestures, all pass; durations at speed 1)
| Gesture | s | Notes |
|---|---|---|
| `stage` | 3.0 | rest → stage (library copy; the server's `/stage` plans from wherever the arm is) |
| `rest` | 3.0 | stage → rest (`/rest` likewise plans from the current pose, via stage if needed) |
| `idle_breathe` | 4.0 | loops while idle |
| `wave` | 4.7 | head up, side-to-side wave ×2 |
| `bow` | 2.9 | lean toward audience, head down |
| `nod` | 2.5 | yes ×2 |
| `shake_head` | 2.9 | no ×2 |
| `slash` | 3.9 | wind up high/outside (upward, not back), chop down-across to the front |
| `thrust` | 3.0 | raise the prop, stab forward level |
| `block` | 4.5 | prop raised upright in front, hold |
| `celebrate` | 4.8 | prop up high, wiggle |
| `cower` | 3.8 | shrink down, head turned away from the other arm and down, tremble |
| `die_dramatically` | 3.9 | stagger, slump forward and sideways (away from the other arm), droop; holds: send `/stage` or `/rest` after |
| `gallop` | 3.0 | bounce |
| `roar` | 3.7 | rise up tall, lunge at audience, head shake |
| `sneak` | 4.6 | crouch, creep across, head bob |
| `look_at_other` | 2.8 | turn head toward the other arm |
| `point_at_audience` | 2.8 | reach forward, point |
| `gripper_clap` | 2.4 | opens the gripper: **drops a held prop** |

Lean-back rework (Nov 7, pose-only; same requested move times, stretched where the joint deltas grew): minimum COM x per arm before → after, cm (limit −0.7;
mirrored both-arm gestures double the moment): celebrate −10.3 → −0.4 (both arms), roar −7.8 → 1.0, slash −6.6 →
−0.6, wave −6.5 → 0.5, block −5.3 → −0.1, thrust −4.9 → 0.6, look_at_other −4.3 → 0.0 (both arms),
die_dramatically −2.9 → 1.2, cower −2.1 → 0.5, sneak −1.1 → 1.0. Unchanged and within the rule: bow, nod,
shake_head, point_at_audience, gripper_clap 1.3, gallop 0.8, idle_breathe 0.2; stage/rest −2.7 (folds).
Previews of the changed gestures re-rendered locally (Mac, `MUJOCO_GL=cgl`).

Speed limits make gestures slower than a cartoon: a 1.5 rad/s limit allows e.g. a ±0.25 rad wave at 0.6 Hz.
Shakes in `make_gestures.py` are capped automatically to ~60 % of the limits.

## Stage server (`robot/stage.py`)
```
cd ~/physical-ai-hacks && .venv/bin/python ~/dial/stage.py            # i2rt sim (default)
cd ~/physical-ai-hacks && .venv/bin/python ~/dial/stage.py --real     # real arms (someone at the e-stop)
python robot/stage.py --backend kin                                   # anywhere: kinematic stand-in
```
- Listens on `127.0.0.1:8770` (`--port`). JSON in/out; `arm` = `left|right|both` (default both).
- `GET /gestures` → list of `{name, duration_s, arms, about, hold, loop, max_speed, uses_gripper}` (the agent
  reads `name` and `duration_s`).
- `GET /state` → per arm `playing`, `pose` (last plan), `progress`, `queue`, `after` (idle|hold), `grip`, `joints`,
  `measured`, `tracking_error`, `last_error`; plus `backend`, `fault`, `rejected`, `unsafe_pairs`.
- `POST /play {"arm","gesture","speed":1,"mode":"now"|"queue"}`: `now` (default) = newest wins, blends from the
  current command; `queue` = after the current gesture (starts at once if idle/holding).
- `POST /freeze` brakes to a stop in 0.15 s and holds; clears queues and idle mode (interrupting the narrator).
- `POST /idle` blends to stage and loops `idle_breathe`; gestures then return to idle unless `hold`.
- `POST /stage {"speed"}`, `POST /rest {"speed"}` plan a smooth move from wherever the arm is (speed ≤ 1).
- `POST /grip {"arm","value"}` 0 closed … 1 open; refused while the arm plays a gesture.
- Errors: 409 (refused: unknown gesture, unsafe, busy) / 400 (bad body) with `{"ok": false, "error": "..."}`.
- Real mode: takes the station lock, refuses unless both arms are within 0.25 rad of rest, starts holding where the
  arms are. A tracking error > 0.35 rad (something blocking an arm) freezes both arms and sets `fault`.
- Ctrl-C / SIGTERM: smoothly back to rest (via stage if needed, auto-freeze off, a second Ctrl-C is ignored), then
  motors off. **Never `kill -9` it in real mode**: the arms would go limp wherever they are.

## Bring-up (first real run)
`bash ~/dial/bringup.sh --real` on the station, props removed, someone at the e-stop. It runs `home.py`, then
`stage.py --bringup --real`: settle at rest, stage at 30 % speed, every gesture once at 50 % on one arm at a time (the
other holds stage; `die_dramatically` and `idle_breathe` are followed by a move back to stage), rest at 30 %. It
prints the worst tracking error per step and stops (back to rest) above 0.2 rad (`--track-max`). Log:
`~/dial/bringup-<time>.log`. Without `--real` it runs in i2rt's sim (~4.5 min); sim tracking error is ~0.01 rad.

## Recording new gestures (`robot/record.py`, untested on hardware)
`record.py NAME --arm left [--secs 8] [--hold] [--both] [--about TEXT]`: floats both arms (gravity compensation,
never commanded), records the chosen arm at 50 Hz while a human moves it from near the stage pose, trims stillness,
smooths, slows it down until it meets the speed/accel limits, appends a return to stage (unless `--hold`), saves
`gestures/NAME.json` if it passes the checker, else `gestures/rejected/NAME.json` with the reason. Raw joints go to
`recordings/NAME.npz`; `--from recordings/NAME.npz` re-processes without arms. Fold both arms to rest before it
exits (it waits, and asks before going limp). Fast hand motion gets slowed a lot (a 1.5 Hz wave ×3).

## Previews
`MUJOCO_GL=egl .venv/bin/python ~/dial/preview.py [names]` on the station (or `MUJOCO_GL=cgl` on a Mac) →
`robot/previews/<name>.gif`, 360×240, 10 fps, ~250 KB, 3/4 view from the audience side (the left arm is on the
right of the image, gold prop; right arm blue prop). Rejected gestures render with the reason in red.

## Gotchas
- After `make_gestures.py` or any gesture edit: run `check_gestures.py`, then `preview.py`, and look at the GIFs.
- Sim needs i2rt's model: on the station the kit's `.venv`; elsewhere put i2rt (and its deps tyro, pydantic,
  crcmod, python-can, mujoco 3.17) on `PYTHONPATH`. `--backend kin` still needs i2rt for the model, not the kit.
- The scene is rebuilt from i2rt's `combine_arm_and_gripper_xml` each start (~0.2 s); checking the whole library
  takes ~3 s.

## Real-arm results (Nov 7, ~13:00)
- `bringup.sh --real` (each gesture at 50 %, one arm at a time): **passed**, 41 steps, worst tracking 0.124 rad.
- `speedtest.py 1.0` (both arms at once, full speed): **auto-freeze in `cower`**, left arm 0.36 rad behind; block
  and celebrate already reached ~0.20 rad. The server froze, folded to rest and switched off as designed; all
  motors answered afterwards.
- `speedtest.py 0.75` (stopped by hand after shake_head, no fault): worst 0.174 rad (`die_dramatically`). Per joint,
  the **wrist pitch j4 lags most** (0.08–0.17 rad); shoulder/elbow stay ≤ 0.12.
- So: ~75 % is the practical ceiling as tuned. To go faster, slow only the j4 parts of fast gestures (or soften
  their shakes) rather than everything.
- After a stage/bring-up run both CAN links may show ERROR-PASSIVE/WARNING from the shutdown race; a down/up reset
  brings them back to ERROR-ACTIVE and every motor answers.
- A Dream "station agent" (`station_frames.py`, left by `dreamcli`) can hold `~/.dreamlabs/arms.lock` and the
  cameras; stop it with `kill -INT` (not -9) once its owner is done.

## Reworked (no lean-back) gestures on the real arms (Nov 7, evening)
`speedtest.py 0.5 block,celebrate,cower,die_dramatically,look_at_other,roar,slash,sneak,thrust,wave`, both arms
at once: **all passed**, no fault. Worst tracking 0.134 rad (`block`), j4 again lags most (≤ 0.13). Freeze, idle
and rest all worked. Shutdown printed the usual harmless `file descriptor -1` trace.
