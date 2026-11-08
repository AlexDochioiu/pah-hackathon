# Props: Lars the frog

Lars is a green plush frog (~9 cm) and the show's host: the AI host of "Stage Hands" is named Lars, and the doll is
Lars's physical body. The right arm picks Lars up from the dock, holds Lars head up while it plays its gestures,
and puts Lars back before the end. Code: `robot/lars.py`; gestures `robot/gestures/pick_lars.json` and
`place_lars.json`; preview `robot/previews/lars_baked.gif`.

## Detection (`lars.detect`, overhead camera, 640x360)
- Green HSV blob + shape: outline, body axis (PCA), head end from the eyes (white pixels near one end) and the
  outline width; `confident` only when the cues agree. A yellow block is not taken for Lars.
- `lars.py --selftest`: the real frame plus rotated/mirrored synthetic variants (head direction within 25°), the
  block, then a live dry plan. Output `robot/lars/lars_variants.jpg`, `lars_annotated.jpg`.
- Pixels → arm frame with `calib.json` (per-arm table z: left −0.012, right −0.005).
- The wrist_l camera fails to open on the station ("Couldn't resolve requests"); nothing here uses it.

## The dock (where Lars waits)
- Reference frame `robot/lars/lars_overhead.jpg`: Lars at px (377, 125), lying on Lars's back, head −17° in the
  image. Right arm, grip at (0.453, 0.264) m in the right arm's frame, 1.6 cm above the table, 40% of Lars's length
  from the head tip, approach leaning 20° along the body.
- Lars goes back the same way each time (place_lars does it). Moving the dock: snap a new `lars_overhead.jpg` with
  Lars on it, set `DOCK_HINT`, rebake. A stand: `--dock-z <m>` (the dock surface height above the table).

## Baking (`lars.py --bake [--speed S] [--dock-z H]`)
Plans once offline and writes both gestures; restart the stage server to load them.
- **pick_lars**: stage pose → above Lars → down (the gripper opens during the reach) → close → lift straight up
  8.5 cm → the holding pose. Ends holding (`hold`).
- **place_lars**: holding pose → above the dock → down → open → up → stage pose.
- Path: via points with rounded corners, even arclength, then forward/backward speed passes with per-joint speed
  and acceleration limits; every 50 Hz sample is checked by lars.py (table, the other arm at rest and at the audience
  stage pose, lean-back) and by `show.Checker.check_side`.
- Speeds: ≤ 0.3 rad/s per joint wherever any part (Lars included) is within 5 cm of the table (show's TABLE_* rule);
  elsewhere `V_FRAC` = 0.9 of show's VMAX and AMAX.
- **`--speed`** (0.25–1.0, default 0.3) divides every key time: speed ×s, acceleration ×s². Committed bake: 0.3.

| speed | pick_lars | place_lars |
|---|---|---|
| 1.0 | 5.0 s (reach 1.44 + 0.56 slow, grip 0.40, lift 0.94 slow + 1.70) | 5.2 s |
| 0.75 | 6.7 s | 6.9 s |
| 0.5 | 10.0 s | 10.4 s |
| 0.3 | 16.8 s | 17.3 s |

- What binds at 1.0: joint travel at the speed limit (j4 turns 2.15 rad from lifted to holding, j2 1.61 rad on the
  reach) plus ~1.5 s inside the 0.3 rad/s zone. `V_FRAC` 1.0 fails the checker; the library's 0.75 gives 5.5 s.
- The holding pose: Lars's head straight up (0° off vertical), Lars's face toward the left arm, a profile from the
  audience; joints [−0.55, 1.22, 1.02, 0.81, 0.93, −0.51]. Lars lies face up and the gripper grips from above, so the
  face looks back at the gripper; facing the audience would need a face-down dock (untested).

## Gesture fields (see [gestures.md](gestures.md))
`side` (absolute joints, that arm only), `near: 0.05` (the TABLE_* rule instead of `CLEAR`: the arm's own parts stay
3 mm above the table, the carried prop may touch), `carry: {"name": "lars", "from", "to", "capsule", "r": 0.025}`
(Lars as a capsule in the grasp-site frame while held; loading the gesture adds `lars` to `show.PROP_SHAPES`),
`dock` (where Lars was baked), `speed` (the bake's).

## Stage server
- `/play {"arm": "right", "gesture": "pick_lars"}`: right arm only, empty hands, facing the audience. In each_other
  (since the lowered stage pose of round 3b) they come 12.2 cm from the left arm, in each_other_props 5.4 cm: refused
  with "face the audience first". The same for place_lars, which needs Lars held.
- After the pick the right arm is in props mode holding `lars`: unlike the toys (car, ufo), every gesture plays,
  re-checked with Lars's shape (/play ~140 ms). `/grip`, `/talk`, `/props` and duets are refused for that arm.
  The place ends it.
- A pick/place runs to its end: nothing else starts on that arm meanwhile. Left-arm slash, thrust, pounce,
  look_at_other, look_around, drive, ufo_fly and ufo_crash are unsafe pairs with them: refused at the same time.
- `/freeze` mid-pick/place brakes on the gesture's own path; the next request finishes it at half speed first (like
  a frozen duet) and is refused with "finishing it". Shutdown finishes it, puts Lars back, then rests.
- `--dock-check`: before pick_lars, one overhead frame (~1.5 s); refused (409) unless Lars lies within 3 cm and 30°
  of the baked dock. Off by default (the agent's HTTP timeout is 2 s).

## Agent
`[pick:lars]` and `[place:lars]` go to `/play` on the arm the stage lists for them (`side` in /gestures). Prompt
(`{{GUESTS}}`): Lars the frog doll, which the right arm can pick up mid-story and must put back before the end, facing the
audience first, that arm's mouth full meanwhile, at most one visit per story; the pick's length comes from /gestures.

## Sim results (stage.Stage on i2rt's MuJoCo sim, in-process)
Speeds 0.3, 0.5, 0.75 and 1.0: pick and place play, max tracking error 0.008–0.027 rad, no fault; the refusals above
hold; nod/wave/celebrate play while holding Lars; the left arm waves meanwhile; freeze mid-pick and mid-place, then
finish.

## Before the first real run
- Speed 0.3 first (the committed bake); rebake at 0.5 → 0.75 → 1.0 only after several clean picks.
- Untested on the real plush: grip depth `GRIP_Z` (1.6 cm), `GRIP_CLOSE` 0 (squeezes the plush), `GRIP_FROM_HEAD`
  0.40, `LARS_R`. If Lars slips or tilts: `GRIP_Z`, `GRIP_FROM_HEAD`, `HEAD_UP_ROLL`.
- show's checker uses one table height (−0.012) for both arms; the right arm's is −0.005, so its clearances read
  7 mm optimistic. lars.py's own bake checks use −0.005.
- Live planning (`lars.py --dry`, `--go`) stays as a fallback for Lars anywhere on the table.
