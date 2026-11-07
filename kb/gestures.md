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
| `robot/duets.py` | Duet format, two-arm contact checker (`DuetChecker`), loader. `python duets.py` prints stats. |
| `robot/duets/*.json` | The duets. |
| `robot/make_duets.py` | Builds the duets from Cartesian targets (IK) and checks them. |
| `robot/preview_duets.py` | Renders `robot/previews/duet_<name>.gif` with a close-up inset and the live gap. |
| `robot/mouth.py` | The talking mouth overlay (`POST /talk`); `python mouth.py` self-checks its rates. |
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
- `POST /stance {"stance": "audience"|"each_other", "speed"}` turns both arms to that stance's pose; every later
  gesture, idle and `/stage` play relative to it (see Stances). Idle arms keep breathing in the new stance.
  `GET /gestures` lists each gesture's `stances`; `/state` has `stance` and `not_in_stance` (reasons).
- Errors: 409 (refused: unknown gesture, unsafe, busy) / 400 (bad body) with `{"ok": false, "error": "..."}`.
- Real mode: takes the station lock, refuses unless both arms are within 0.25 rad of rest, starts holding where the
  arms are. A tracking error > 0.35 rad (something blocking an arm) freezes both arms and sets `fault`.
- Ctrl-C / SIGTERM: smoothly back to rest (via stage if needed, auto-freeze off, a second Ctrl-C is ignored), then
  motors off. **Never `kill -9` it in real mode**: the arms would go limp wherever they are.

## Talking mouth (`robot/mouth.py`, `POST /talk {"arm","on"}`)
Each arm is a puppet head: the wrist camera's two lenses are its eyes, the gripper is its mouth, j4 nods, j5 turns,
j6 tilts. `/talk` flaps an arm's gripper like a talking mouth on top of whatever the arm plays or idles: irregular
syllables at 3–5.5 Hz with varied openings and short breaths between phrases, plus a small head bob (j4 lifts up
to 0.05 rad as the mouth opens, low-passed) and slow j6 tilts (±0.06 rad). The overlay fades in and out over
0.25 s. It stops on `"on": false`, `/freeze`, the start of a duet, shutdown, or after 20 s. The gripper never moves
faster than `show.GRIP_VMAX` = 3.5/s (the DM4310 at ~21 rad/s turns its 6.57 rad stroke in ~0.3 s; **untested on
the real gripper: tune it there**), so a 4 Hz syllable opens to ~45 %. The checker also caps gestures' grip
rates by it (`max_speed`). The overlay's head offsets are not in the swept volumes: ≤ 0.06 rad at the wrist moves
the gripper < 1 cm, inside the 4 cm table and 15 cm arm margins. The mouth opens the gripper: **a held prop drops**.
Preview: `preview.py --talk` → `robot/previews/talk.gif`.

## Stances (facing each other)
Gestures are deltas from a stance pose. `audience` = the stage pose (both arms face the audience, the default).
`each_other` = stage + `show.FACING_LEFT` = j1 −0.20, j5 +0.25 on the left arm (mirrored on the right: j1 +0.20,
j5 −0.25): base and head yaw ~26° toward the other arm, so a slash, roar or wave aims at the other character
(previews: `robot/previews/each_other/<name>.gif`). `load_library` checks every gesture in every stance (limits,
table, lean-back, and 15 cm from the other arm at the stance pose, rest and idle); a gesture that fails a stance is
refused only there. Today 16 of 17 play facing each other; `look_at_other` doesn't (its turn brings the prop 9.9 cm
from the other arm, and they already look at each other). `stage`/`rest` always fold via the audience pose. In
`each_other` 27 gesture pairs are refused when played on both arms at the same moment (409, as any clash): the
props point at each other, so two big moves at once come too close. Larger yaws were tried: j1 −0.35 or j5 +0.55
lose most gestures to the 15 cm gap. The agent uses `[face:each_other]` / `[face:audience]` tags
([storyteller.md](storyteller.md)). Bring-up adds a turn to `each_other` and back at 30 %.

## Duets (`robot/duets.py`)
Two-arm moves where the arms come close and touch, with synchronized keyframes for both arms (absolute joints,
solved by IK at build time). They start and end at the audience stage pose; the server blends in from wherever the
arms are and back to the current stance pose. Format: `{"name","about","contact":[{"left":[parts],"right":[parts],
"min":m}],"keys":[{"t","left":[6],"right":[6],"gl","gr"}]}`; parts are `prop`, `fingers`, `gripper`, `wrist`
(link4+link5). `DuetChecker` checks every 50 Hz frame with both arms at the same instant, with exact
geom-to-geom distances (`mj_geomDistance`, meshes as convex hulls):
- each arm passes the usual per-arm rules (limits, speed/accel, table clearance with the prop, lean-back);
- a declared contact pair stays ≥ its `min` (> 0, never interpenetrating in sim);
- every other pair of geoms stays ≥ 15 cm (the arm links, link2/link3, can never be contact parts);
- slow final approach: inside 5 cm a contact pair closes at ≤ 5 cm/s.
Deviation from the first brief: "contact" may involve the hand parts (fingers, gripper, wrist), not only the props,
each pair with its own minimum. Hand-to-hand contact (high five, handshake) turned out infeasible with 15 cm props
held: keeping link3 15 cm apart needs the hands angled in, and then the props cross. So duets touch prop to prop
(or prop to hand).

| Duet | s | Closest contact | Others | Fastest close (inside 5 cm) |
|---|---|---|---|---|
| `cross_swords`: en garde, wind up, swing; the blades lock in an X, shove left and right, spring apart | 8.7 | prop×prop 1.8 cm | ≥ 20 cm | 4.3 cm/s |
| `cheers`: raise, lift, swing in, clink the tips, glasses up high | 9.0 | prop×prop 1.5 cm | 16 cm | 4.4 cm/s |
| `poke`: the left sneaks up and pokes the right's head with its prop; the right flinches, shakes its head | 8.8 | prop×right hand 1.6 cm | 16.2 cm | 4.0 cm/s |
| `quick_draw`: face off, hands low, twitch, draw! The left fires, the right is hit and slumps | 7.7 | no touch | ≥ 20 cm | |
| `tip_hats`: turn to each other, the left tips its hat, the right answers deeper | 5.6 | no touch | ≥ 20 cm | |
| `copycat`: the right copies the left a beat late, gets caught, looks away innocent | 7.3 | no touch | ≥ 20 cm | |
| `car_ride`: the left steers (wheel on j6), both lean into the turns and bounce | 6.8 | no touch | ≥ 20 cm | |

"≥ 20 cm" is the distance cap of the check (15 cm + 5). `poke` closes the right gripper on its prop first (grip 0).

Server: `GET /duets`, `POST /duet {"name","speed"}` (speed ≤ 1). Refused (409) unless both arms are free (holding
or idling); while a duet plays everything except `/freeze` is refused. `/freeze` brakes both arms over 0.15 s on the
duet's own path and holds; the next request then first finishes the duet at half speed on its path (and gets a
409), since the arms may be touching. Shutdown (Ctrl-C) finishes a duet the same way before folding to rest.
Agent tag `[duet:name]` ([storyteller.md](storyteller.md)). Real-arm limits: about 1–2 cm of sag and calibration
error, so contacts at 1.5 cm may touch or miss; the slow approach keeps a touch gentle; fingers close on the 3 cm
prop at grip 0.31 (`G_ON_PROP`). Rebuild with `make_duets.py`, check with `duets.py`, render with
`preview_duets.py`.

## Readability review of the solo gestures (Nov 7; suggestions, not applied)
Goal: a viewer without sound names the action in a second. Big moves through j1–j3, not wrist flicks.
- Cut `gripper_clap`: only the fingers move, invisible at audience distance, and it drops a held prop.
- Cut `look_at_other`: the face-each-other stance does it better, and it is refused there.
- Redo `nod`: j4 alone (±0.17 rad), small and the joint that lags most on the real arms; bob j2/j3 with the head.
- Redo `shake_head`: j5 alone (±0.2 rad); add j1 ±0.15 so the whole arm says no.
- Redo `gallop`: 0.04–0.12 rad bounces read as jitter; a bigger j2/j3 bounce, prop forward like reins.
- Redo `sneak`: 4.6 s of slow j1 drift with a tiny bob; shorter, with big lifted tiptoe steps.
- Redo `die_dramatically`: 2.5 s of small shaking before the slump delays the payoff; a 0.5 s stagger, then a big slump.
- Keep `wave`, `bow`, `slash`, `thrust`, `block`, `celebrate`, `cower`, `roar`, `point_at_audience`, `idle_breathe`.

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
`preview.py --stance each_other [names]` → `robot/previews/each_other/<name>.gif`.

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
