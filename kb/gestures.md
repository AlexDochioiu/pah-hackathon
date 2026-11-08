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
- **Stage pose** (left arm) = `[0, 0.8, 0.7, 0.2, 0, 0]` (Nov 7 evening, lowered so the action plays closer to the
  table): gripper ("head") level, facing the audience, ~0.37 m up. The lean-back rule stays anchored at `lift.py`'s
  raised pose `[0, 0.8, 1.2, −0.3, 0, 0]` (`LEAN_REF_LEFT`, ~0.48 m up), where the table was measured not to tip. Not turned inward: with 15 cm props each arm has only ~0.22 m of inward
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
- `g` (gripper, 0 closed … 1 open) is optional; without it the gripper keeps its current value. Most round-3
  gestures act with the mouth (`mouth.py` shut 0.05, wide 0.8); while an arm holds a prop the server keeps its grip.
- `table` (optional): table contact, see the checker.
- `hold`: stay at the end pose instead of returning to idle (`die_dramatically`, `stage`, `rest`).
  `loop`: repeat (`idle_breathe`). `arms`: the intended use, `both` = mirrored on both arms.
- `recorded`: set by `record.py`; `make_gestures.py` keeps such files when it regenerates.
- Pick/place gestures (`pick_lars`, `place_lars`, made by `lars.py --bake`): `side` = keys are absolute joints for
  that arm only (same in every facing); `near` = the near-table rule instead of `CLEAR`; `carry: {"name", "from",
  "to", "capsule", "r"}` = the carried prop as a capsule in the grasp-site frame, held between those times.
  See [props.md](props.md).

## Safety checker (`show.Checker`, `show.load_library`)
Every gesture is checked on **both** arms at load (the server and bring-up refuse to start without `idle_breathe`):
- Joint limits with 0.08 rad margin: j1 [−2.62, 3.05], j2 [0, 3.65], j3 [0, 3.14] (the model's j3 max is 3.14,
  not 3.67), j4 [−1.57, 1.57], j5 [−1.57, 1.57], j6 [−2.09, 2.09]. j2/j3 may sit on their 0 stop (rest).
- Joint speed ≤ 1.5 rad/s; acceleration ≤ 8 rad/s² (j1–j3) / 15 rad/s² (j4–j6), sampled at 50 Hz at speed 1.
  Each gesture gets a `max_speed` factor (≤ 1.5) the server clamps requests to.
- Table: every moving link (link2 and below, fingers), and a 15 cm × 1.5 cm-radius prop capsule sticking out of the
  fingers along the grasp axis, stays ≥ 4 cm above the table. Geometry = the hull vertices of i2rt's meshes,
  sampled every ~1 cm.
- **Table contact** (Nov 7, round 3): a gesture (or duet) may declare `"table": {"min": m, "impact": m/s}`. Then
  only the hand (the `gripper` and `tip_left/right` bodies) may go down to `min` above the table (negative = into
  it: `bang_table` aims `show.BANG_DEPTH` = 4 mm in, a calibration knob), coming down at ≤ `impact` once within
  5 cm of `min`; everything else keeps 4 cm. Empty hands only (refused in the `_props` stances). `max_speed` is
  capped at 1 for them. A blend out of a table gesture inherits its allowance (a dead arm can get up). The real arms
  sag 1–2 cm and are position-controlled: aiming 4 mm into the table is a small, held error. Sim only so far;
  nobody tests them on the real arms while the station is unattended. `die_dramatically` (min 5 mm, 5 cm/s) and
  `revive` lie a hair above the table; `bang_table` (min −4 mm, 30 cm/s) knocks it. Facing each other (head yawed
  ~60°) the same deltas stay ~4–8 cm above the table: a knock in the air, a slump that hovers.
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
- Props: `show.PROP_SHAPES` models each held prop as points with a radius in the grasp-site frame: `stick` (15 cm
  along the grasp axis), `car` (a 1:64 toy car gripped across the roof: 7.5 cm along the finger slots, wheels 2.8 cm
  below the site) and `ufo` (a 10 cm saucer hanging 3 cm below the gripped dome). A gesture with `"prop": kind` needs
  that toy (`POST /props {"arm","on","kind"}`); an arm holding a toy plays only its own gestures (and the
  folds), and duets are refused. `"table": {..., "prop": true}` lets the held toy touch the table too (drive,
  ufo_land). Sim only, like every table-contact move.

## Library (62 gestures, all pass; durations at speed 1; the `about` texts the storyteller sees)
| Gesture | s | About |
|---|---|---|
| `bang_table` | 6.9 | Make a fist and bang it on the table, twice, snarl, and once more, hard: anger, impatience, demanding, a judge's verdict. **Table contact.** |
| `beat_chest` | 6.9 | Rise tall and pound, left-right-left-right, mouth roaring open: beat the chest like a gorilla, show off, I am the strongest! |
| `bite` | 3.2 | Rear back with the mouth wide, then lunge and snap it shut, twice, and shake the catch: bite, snap the jaws, chomp, grab with the teeth. |
| `block` | 4.5 | Raise the prop upright in front like a guard, hold. |
| `bow` | 2.9 | Lean toward the audience, head down, hold, straighten. |
| `celebrate` | 4.8 | Thrust the prop up high and wiggle it in triumph. |
| `chef_kiss` | 3.4 | The chef's kiss: pinched fingertips peck forward, then spring open and fly up: perfect!, delicious!, magnificent, a masterpiece. |
| `cower` | 3.8 | Shrink down, turn the head away from the other arm and down, trembling. |
| `cry` | 4.4 | Hang the head and sob, shoulders shaking, the mouth quivering: cry, weep, sob, sad, heartbroken. |
| `die_dramatically` | 5.8 | Gasp and shudder, then go limp: drops like a puppet with its strings cut, a small bounce, and lies dead with the head on the table, mouth slack (play revive or stage after). **Table contact.** |
| `dodge` | 2.7 | Weave fast to one side, then the other, then back: dodge, swerve, weave, escape a blow. |
| `double_take` | 4.4 | Glance at something, look away, then snap back, head jerking up and tilted, jaw dropped: a double take, spot, notice, wait, what?! |
| `drive` | 15.0 | Holding a toy car, put it down on the table and drive it: vroom along over two bumps, turn toward the audience, brake, lift it off: drive, race, park, a road trip. **Table contact.** **Needs the car.** |
| `duck` | 3.3 | Duck down fast, stay low, then peek up carefully: duck, hide, take cover, an arrow flies over. |
| `eat` | 4.5 | Bend down and munch, chewing with the head bobbing, then look up satisfied: eat, munch, chew, gobble, taste. |
| `eye_roll` | 4.8 | The head rolls up and around and away, then sags: an eye roll, ugh, so boring, whatever, really?, roll eyes. |
| `facepalm` | 4.4 | The head drops into the hand and turns away, shaking slowly: facepalm, groan, a terrible pun, oh no, embarrassed, I can't believe it. |
| `flick` | 2.7 | A dismissive flick: the hand flips up and away, fingers springing open, head turned aside: bah!, whatever, go away, never mind. |
| `gallop` | 3.0 | Bounce up and down like riding a horse. |
| `grab` | 2.5 | Reach out with the mouth open, snatch, and pull it back: grab, take, snatch, steal, catch. |
| `gripper_clap` | 2.4 | Snap the gripper open and shut three times (drops a held prop!). |
| `hop` | 4.7 | Bounce up and down three times like hopping: hop, jump, skip, a bunny, a frog. |
| `idle_breathe` | 4.0 | Slow breathing sway; loops while idle. |
| `italian` | 5.4 | The pinched-fingers hand: fingertips together pointing up, pumping up and down with a head tilt: "what do you want?!", arguing, insisting, explaining, complaining. |
| `italian_exasperated` | 7.2 | Both hands pinched and pumping big, then thrown open to the sky and dropped: "mamma mia!", exasperated, fed up, giving up. |
| `knee_slap` | 5.9 | Laugh, head thrown back, and slap the knee, three times: knee slap, that's hilarious, the joke lands, roaring with laughter. |
| `laugh` | 4.2 | Throw the head back and laugh, ha-ha-ha, bouncing, then double over: laugh, giggle, find it funny, chuckle. |
| `laugh_hard` | 6.0 | Crack up: head back, bouncing, the mouth flapping with laughter, then doubled over, still laughing: laugh hard, crack up, roar with laughter, can't stop laughing, LOL. |
| `lift_heavy` | 4.6 | Bend down, grip something with the mouth and heave it up, slowly, trembling with the strain: lift, carry something heavy, pick up, struggle. |
| `look_around` | 3.8 | Scan slowly to one side, then the other, searching: look around, search, where is it?, keep watch. |
| `look_at_other` | 2.8 | Turn the head toward the other arm, tilt, hold. |
| `mic_drop` | 6.1 | Hold the mic up high, let it drop (the mouth springs open), look down at it, then a smug head tilt: mic drop, nailed it, the punchline, boom. |
| `nod` | 2.5 | Nod twice (yes). |
| `point_at_audience` | 2.8 | Reach forward and point the prop at the audience. |
| `pounce` | 3.5 | Crouch low, wiggle, then leap forward with the mouth open and snap it shut on the prey: pounce, leap out, lunge, attack, catch. |
| `pull` | 3.2 | Bite onto something ahead and tug it back, hard, twice: pull, drag, tug, heave a rope. |
| `push` | 3.4 | Lower the head and shove forward, twice, straining: push, shove, ram, butt, move a boulder. |
| `rest` | 3.0 | Fold back down to rest. |
| `revive` | 5.5 | Back from the dead: a twitch, another, the head lifts groggily, shakes it off and stands ready again (after die_dramatically). **Table contact.** |
| `roar` | 3.6 | Rise up tall, lunge at the audience and shake the head. |
| `shake_head` | 2.9 | Shake the head side to side (no). |
| `shoot` | 5.0 | Aim the gripper like a gun barrel and fire twice, each shot kicking the head up in recoil, then blow the smoke away: shoot, fire, blast, zap, a laser. |
| `sigh` | 4.4 | Breathe in, rising, then let it all out and sag, head drooping: sigh, tired, bored, sad, oh well. |
| `slash` | 3.9 | Wind up high and outside, chop down and across toward the front. |
| `slow_turn` | 6.0 | Freeze, then turn the head slowly to the audience and stare, deadpan, saying nothing: after a bad joke, deadpan, awkward silence, really?, the slow turn. |
| `sneak` | 4.6 | Crouch low and creep across, head bobbing. |
| `sneeze` | 3.5 | Ah... ah... head tilting back twice, mouth open, then ACHOO, a sharp snap forward: sneeze, cough, an allergy, a cold. |
| `snore` | 6.2 | Nod off, head drooping to one side, and snore, the mouth opening on every slow breath: sleep, snore, doze, yawn, bored to sleep. |
| `stage` | 3.0 | Rise from rest into the stage pose. |
| `startle` | 3.0 | Jump up and back in fright, mouth wide, trembling, then calm down: a scare, startle, jump back, gasp, eek! |
| `stir_frantic` | 8.2 | Stir a pot fast and frantically, head bobbing: whisking, cooking in a hurry, mixing a potion before it boils over. |
| `stir_lazy` | 10.5 | Hold a spoon down in a pot and stir slow, lazy circles, then look up content: cooking, brewing a potion, mixing, waiting. |
| `stomp` | 4.6 | Lift high and stamp down hard, twice: stomp, march, land heavily, stamp in anger, a giant's step. |
| `ta_da` | 4.1 | Gather in, then swing up and out to the audience with a flourish, mouth wide: ta-da, presenting, voila, here it is, a big reveal, a magic trick. |
| `throw` | 2.9 | Wind back and up, then fling forward, letting go at the top: throw, toss, hurl, fling, cast a spell. |
| `thrust` | 3.0 | Raise the prop, then stab straight forward. |
| `tremble_excited` | 3.1 | Pop up and quiver all over with excitement, mouth chattering: so excited!, can't wait, oh oh oh!, yes yes yes. |
| `ufo_crash` | 11.5 | The UFO loses control: it wobbles harder and harder, spirals down and crashes, tilted (stopping well above the table): crash, shot down, spin out, out of fuel. **Needs the ufo.** |
| `ufo_fly` | 13.6 | Holding the UFO, fly it in a wobbly figure-eight, rocking and spinning: fly, zoom, an alien ship, invade, cruise through space. **Needs the ufo.** |
| `ufo_hover` | 8.0 | The UFO stops in mid-air and hovers, pulsing up and down and slowly turning: beam up, abduct, scan, hover, the aliens watch. **Needs the ufo.** |
| `ufo_land` | 13.0 | The UFO comes down slowly, wobbling, lands on the table, sits a moment, then takes off again: land, arrive, touch down, visit Earth. **Table contact.** **Needs the ufo.** |
| `wave` | 4.7 | Raise the head and wave it side to side, three times. |

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

## Stances (facing each other) and the props mode
Gestures are deltas from a stance pose. `audience` = the stage pose (both arms face the audience, the default).
Empty hands are the default (Nov 7 evening: the gripper is the mouth, so props are now explicit), and the plain
stances are checked WITHOUT the prop sticks. `each_other` = stage + `show.FACING_LEFT` = j1 −0.12, j2 +0.04,
j4 +0.04, j5 +1.05 on the left arm (mirrored on the right): the heads turn ~67° toward each other, the wrist cameras
(the eyes, along the grasp axis) point 24° off the other's head toward the audience, a 3/4 stage cheat. Most of the
turn is the head (j5), not the base (j1): turning the base brings link3 inside 15 cm. j2/j4 +0.04 lean it forward
(the head yaw alone moves the COM back past the lean-back limit for celebrate/look_at_other). All 17 non-fold
gestures play there (previews: `robot/previews/each_other/<name>.gif`, props hidden). j5 +1.25 (~77°, eyes ~14°
off) would only lose look_at_other (its j5 passes the limit); kept at 1.05 to leave head room for look-around moves.

`POST /props {"arm","on"}` says an arm holds a prop. While any arm does, the `_props` stance variants apply:
everything is checked with both props, `each_other_props` is the old shallow turn (`FACING_PROPS_LEFT`: j1 −0.20,
j5 +0.25, ~26°; the props would cross deeper), and the arms turn there at once (checked with empty hands: the prop
goes in after the call). A holding arm refuses `/talk`, its gestures keep its grip (never drop the prop), and duets
with `"props": false` (hand contact) are refused. `/props` off turns back deep. `load_library` checks every gesture
in all four stance variants (limits, table, lean-back, and 15 cm from the other arm at the stance pose, rest and
idle); a gesture failing one is refused only there (`look_at_other` in `each_other_props`). `stage`/`rest` fold via
`audience` or `audience_props`. Both arms at once: 18 pairs are refused in `each_other` and 27 in
`each_other_props` (409, as any clash). The agent uses `[face:...]` and `[props:left|right|both|none]` tags
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

| Duet | s | hands |
|---|---|---|
| `cheers`: Raise the glasses, lift, swing in and clink the tips together in the middle, then hold them up high. | 8.7 | sticks |
| `copycat`: The right copies every move of the left a beat late; the left whips round and catches it; the right looks away, innocent. | 7.3 | sticks |
| `cross_swords`: En garde, wind up and clash: the swords lock in an X in the middle, shove each other left and right, then spring apart. | 8.4 | sticks |
| `elbow_nudge`: "Get it? Get it?": the left leans in beside the right and nudges it with the side of its head, three quick pokes, mouth flapping; the right stays deadpan; the left grins wide. | 7.1 | empty hands |
| `heckle`: The left points at the right and laughs at it, head back, bouncing, mouth flapping; the right looks over, then slowly droops, head hanging. | 8.1 | empty hands |
| `place_hat`: The left raises its prop like a hat and lays it gently on the right's head; the right ducks to take it, then straightens up proudly. | 8.8 | sticks |
| `poke`: The left arm sneaks up and pokes the right one on the side of its head with its prop; the right flinches away and shakes its head. | 8.1 | sticks |
| `quick_draw`: Western standoff: guns low at the hips, fingers twitch... DRAW! The left is faster: BANG, its gun kicks up; the right, hit mid-draw, jerks back, drops its gun and slumps; the left raises its smoking barrel. | 8.9 | sticks |
| `straight_man`: The left does a silly bit, bouncing and wiggling with its mouth flapping; the right slowly turns to stare at it, deadpan; the left notices and freezes; the right turns back to the audience and the left droops. | 9.6 | empty hands |
| `tip_hats`: Turn to each other; the left tips its hat, the right answers with a deeper tip. | 5.6 | sticks |

"≥ 20 cm" is the distance cap of the check (15 cm + 5). `poke` closes the right gripper on its prop first (grip 0).

Server: `GET /duets`, `POST /duet {"name","speed"}` (speed ≤ 1). Refused (409) unless both arms are free (holding
or idling); while a duet plays everything except `/freeze` is refused. `/freeze` brakes both arms over 0.15 s on the
duet's own path and holds; the next request then first finishes the duet at half speed on its path (and gets a
409), since the arms may be touching. Shutdown (Ctrl-C) finishes a duet the same way before folding to rest.
Agent tag `[duet:name]` ([storyteller.md](storyteller.md)). Real-arm limits: about 1–2 cm of sag and calibration
error, so contacts at 1.5 cm may touch or miss; the slow approach keeps a touch gentle; fingers close on the 3 cm
prop at grip 0.31 (`G_ON_PROP`). Rebuild with `make_duets.py`, check with `duets.py`, render with
`preview_duets.py`.

### Ensembles (both arms as ONE thing, action or mood)

Duets with `"kind": "ensemble"` in their JSON: same format, checker and `/duet` playback, listed with their kind by
`GET /duets`. No casting lock: the storyteller keeps two characters and drops into an ensemble at a big or
illustrative beat (the storm, the gate, "THIS big!"), even mid-scene; the stage blends in from the current stance
(audience or each_other) and back to it. Built in `make_duets.py` as joint deltas (`ens()`), mirrored unless both
arms are given; none touch (all ≥ 20 cm). Previews: `robot/previews/ensemble_<name>.gif`.

| Ensemble | s | hands |
|---|---|---|
| `car_ride`: Both hands on one big steering wheel: turn left, turn right, leaning into the turns, bounce over two bumps, then let go: we're here. | 8.6 | sticks |
| `cauldron`: Two witches at one cauldron: closed grippers angle down into the pot like spoons; one stirs round and round while the other's head follows the spoon, they swap, then both cackle. | 10.4 | empty hands |
| `chomp`: The two arms are one giant pair of jaws in the middle, the left hand the upper jaw and the right the lower: they open wide, fingers bared like teeth, and SNAP shut, three times, then shake the prey. | 9.3 | empty hands |
| `clap`: Both arms are two hands clapping softly together in the middle, three times. | 8.2 | empty hands |
| `drum_roll`: A drum roll on the table: two fists knock in turn, faster and faster, wind up together and BANG at once, then heads up: ta-da! | 9.0 | empty hands |
| `explosion`: Both fists huddle into one bomb in the middle, the fuse fizzes and trembles, a held breath, then BOOM: both fling up and out, heads thrown back, hands bursting open, and the debris flutters down. | 8.6 | empty hands |
| `fist_bump`: Fist bump: make fists, cock back, punch in until the knuckles meet in the middle, then EXPLODE: the fists spring open as the hands fly apart, fingers flickering. | 7.2 | empty hands |
| `gate_opening`: A castle gate: the arms swing shut until the sticks meet in the middle, it rattles, then the doors creak open on their hinges, stop and start, and swing wide. | 9.9 | sticks |
| `heart`: Both arms make a big heart: elbows up for the lobes, the hands meet at the tip, it beats. | 8.1 | empty hands |
| `high_five`: High five: dip, hands rise and spring open, swing in and slap palm to palm in the middle, bounce back, then celebrate with heads up and wiggling, flapping hands. | 8.1 | empty hands |
| `hug`: Arms open wide, then wrap around in a big hug and rock side to side. | 7.6 | empty hands |
| `once_upon_a_time`: The narrator's storybook: the hands press together in the middle like a closed book, it opens to the audience, a beat to read it, then a slow, grand sweep up and out. | 8.1 | empty hands |
| `pull_rope`: Two hands pull a heavy rope: reach, grab, heave back, three times. | 13.7 | empty hands |
| `rocket`: Crouch for the countdown, rumble, then blast up high together: a rocket lifting off. | 5.9 | sticks |
| `row_boat`: Both arms row a boat with the sticks as oars, off-sync: dip in, pull back, lift out and return with the wrist rolled flat, three strokes each. | 8.2 | sticks |
| `rub_hands`: Two hands rubbed together, scheming: "Excellent... my plan is working." | 10.0 | empty hands |
| `shrug`: "Who knows?": a little dip, then both pop up with the hands thrown open, palms up and out, heads tilted, an "eh" bob, and they drop: oh well. | 4.7 | sticks |
| `storm`: Both arms bend and whip together like trees in a howling storm. | 4.9 | sticks |
| `the_end`: Arms wide for "THE END!", then a deep bow together. | 4.0 | sticks |
| `this_big`: Arms thrown wide apart: "it was THIS big!" | 3.5 | sticks |
| `waves`: The two arms rise and fall in turn like rolling ocean waves. | 3.7 | sticks |
| `wings`: Both arms are a great pair of wings: spread wide, flap three times, glide. | 7.1 | sticks |

Lean-back rule of thumb for writing them: the arm's centre of mass moves about `24·j2 − 11·j3 − 1.7·p` cm (deltas
in rad, p = head pitch) and must stay above −2 cm, so raising the elbow or the head needs j2 forward with it.
Hand-contact ensembles (`"props": false`: checked without props, refused while one is held). `meet()` in
`make_duets.py` bisects the gripper targets until the hands are a given gap apart (pointing in at az ~1.2 rad, the
arms stay ≥ 17.8 cm apart); the final close is a creep at ≤ 4.4 cm/s (`TOUCH`), so a clap is a soft pat.

(All hand-contact ensembles are in the ensemble table above, marked "empty hands".)

A sliding rub (hands passing each other) failed the closing-speed rule (the gap between staggered hands changes
fast), hence the wrist rolls.

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
