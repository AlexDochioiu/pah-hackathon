# Props: Lars the frog

Lars is a green plush frog (~9 cm) and the show's host: the AI host of "Stage Hands" is named Lars, and the doll is
Lars's physical body. The right arm picks Lars up from the dock, where Lars lies face-down (on the belly), holds
Lars head up facing the audience while it plays its gestures, and puts Lars back before the end. Code: `robot/lars.py`; gestures `robot/gestures/pick_lars.json` and
`place_lars.json`; preview `robot/previews/lars_baked.gif`.

## Detection (`lars.detect`, overhead camera, 640x360)
- Green HSV blob + shape: outline, body axis (PCA). Head end from three cues, each a vote:
  - eyes: white pixels near one end (face up only);
  - crown: Lars's yellow crown (hue 14–27, 8–220 px) in one cluster past one end, seen either way up. On the Nov 7
    frames it sits at 0.76–0.90 of the half-length, with every crown pixel at the same end;
  - legs: face down, the two legs show split apart (two runs of green across the body) near the feet end.
  `confident` = eyes or crown decided and no cue against them. Legs alone are never confident. The old width/mass
  shape cue only shows in the text: face down it points at the feet (splayed legs, and the crown isn't green), so
  on the real face-down frames it was wrong every time.
- A yellow block is not taken for Lars. Without a hint the largest doll-like green blob wins; in today's frames
  another blob near px (532, 233) does, so give `--hint`. The dock check always passes one.
- `lars.py --selftest` covers:
  - the face-up frame, two real face-down frames (`facedown_dock_overhead.jpg`, `facedown_overhead.jpg`, Nov 7
    evening, the camera before its 18:45 move) and rotated/mirrored variants of each (head direction within 25°,
    confident);
  - the dock check on `facedown_dock_cal3.jpg` (the current camera and `calib.json`, Lars face-down where the real
    place_lars left him, ~25° off `DOCK_AXIS` and 2 cm off the dock): passes as is and turned 15° toward the dock;
    refuses turned 15° away (~40°), at 60/120/90° and 180° (a flipped Lars), and with the crown painted out (also
    turned 180°);
  - the block;
  - a live dry plan both ways up.
  Output `robot/lars/lars_variants.jpg`.
- Pixels → arm frame with `calib.json` (per-arm table z: left −0.0065, right −0.006), refitted at 18:45 after the
  camera moved ([calibration.md](calibration.md)). Recalibrate → re-grab `facedown_dock_cal3.jpg` with Lars at the
  dock. The baked pick doesn't use the camera.
- The wrist_l camera fails to open on the station ("Couldn't resolve requests"); nothing here uses it.

## The dock (where Lars waits)
- A fixed table position in the right arm's frame (the arm that picks), not a camera frame: `DOCK_RIGHT`
  (0.4416, 0.2673) m, head along `DOCK_AXIS` (−17° from +x), length 12.0 cm, 4.2 cm wide at the grip line. It was
  measured from `robot/lars/lars_overhead.jpg` (Lars face-up) through the 11:30 `calib.json`; the real pick on
  Nov 7 evening took Lars from there and put Lars back there, so that spot is the dock. A recalibration doesn't
  move it. `DOCK_XY` (the left-frame dock, for the dock check and the gesture's `dock.xy_left`) is
  `DOCK_RIGHT − right_offset` through the current `calib.json`: (0.4473, −0.3345) with the 18:45 fit, px (331, 86).
  The 18:45 fit moved `right_offset` by 1.1 cm in x (the per-block offsets in `calib.py` spread ±1.5 cm), so
  pinning the left-frame point would have moved the right arm's grip 1.1 cm.
- Lars lies there **face-down**, head toward the audience end (top-right in the overhead image). Right arm, grip
  at (0.453, 0.264) m in the right arm's frame, grasp site 1.6 cm above the table, 40% of Lars's length from the
  head tip, approach leaning 20° along the body.
- Lars goes back the same way each time (place_lars does it). Moving the dock: change `DOCK_*`, rebake. A stand:
  `--dock-z <m>` (the dock surface height above the table).
- Face-down model (estimates, not measured): body axis 2.1 cm up (resting on the belly bump and eyes, vs 1.8 face
  up), back top `BACK_TOP` 4.0 cm. That is the model's 3.9 cm rounded up, and it only sets the hover height
  (`DOLL_TOP` 3.5 cm on the back). `GRIP_Z` stays 1.6 cm both ways up: on the real arms (Nov 7 evening) the face-up
  bake held a face-down Lars fine at `GRIP_Z` / `GRIP_CLOSE`.

## Baking (`lars.py --bake [--speed S] [--dock-z H] [--face-up]`)
Plans once offline from `DOCK_*` (no camera) and writes both gestures; restart the stage server to load them.
Default: face-down. `--face-up` gives the old bake (Lars on the back, held in profile): the same holding pose,
with the retime fix below.
- **pick_lars**: stage pose → above Lars → down (the gripper opens during the reach) → close → lift straight up
  8.5 cm → the holding pose. Ends holding (`hold`).
- **place_lars**: holding pose → above the dock → down → open → up → stage pose.
- Path: via points with rounded corners, even arclength, then forward/backward speed passes with per-joint speed
  and acceleration limits; every 50 Hz sample is checked by lars.py (table, the other arm at rest and at the audience
  stage pose, lean-back) and by `show.Checker.check_side`.
- Speeds: ≤ 0.3 rad/s per joint wherever any part (Lars included) is within 5 cm of the table (show's TABLE_* rule);
  elsewhere `V_FRAC` = 0.9 of show's VMAX and AMAX.
- **`--speed`** (0.25–1.0, default 0.3) divides every key time: speed ×s, acceleration ×s². Committed bake: 0.3.

| speed | pick_lars (face-down, committed) | place_lars | face-up pick / place |
|---|---|---|---|
| 1.0 | 4.7 s | 4.8 s | 4.9 / 4.9 s |
| 0.75 | 6.2 s | 6.4 s | ~6.6 / 6.6 s |
| 0.5 | 9.4 s | 9.6 s | ~9.8 / 9.8 s |
| 0.3 | 15.6 s | 16.1 s | ~16.4 / 16.4 s |

Baked with the 18:45 `calib.json` (grip 1.0 cm above its right-arm table z of −0.006). Face-up at speeds below 1.0
is 1.0's time / speed (not baked).

- What binds at 1.0: joint travel at the speed limit plus ~1.5 s inside the 0.3 rad/s zone. `V_FRAC` 1.0 fails the
  checker. `retime` caps the path speed at corners (|d²q| ṡ² ≤ amax per joint) and the acceleration back-off checks
  the keys as written (rounded to 1e-4 rad): without both the 18:45 calib's bake broke j3's 8 rad/s² by 0.3.
  Face-down is shorter because the holding pose sits next to the stage pose.
- **The holding pose (face-down dock):** Lars's head straight up (0° off vertical), Lars's face straight at the
  audience (0°), the fingers pointing at the audience with Lars just past them; joints [0.0, 0.75, 0.89, 0.21, 0.0,
  0.0] (the stage pose is [0, 0.8, 0.7, 0.2, 0, 0]). Lars lies face-down and the gripper grips from above, so the
  face looks out past the fingertips once lifted. The search tries faces in `FACES_BELLY` order (0, ±10, ±20…) and 0
  passed.
- Face-up (`--face-up`): head up, the face toward the left arm (a profile from the audience); joints [−0.55, 1.22,
  1.01, 0.82, 0.93, −0.51].

## Gesture fields (see [gestures.md](gestures.md))
`side` (absolute joints, that arm only), `near: 0.05` (the TABLE_* rule instead of `CLEAR`: the arm's own parts stay
3 mm above the table, the carried prop may touch), `carry: {"name": "lars", "from", "to", "capsule", "r": 0.025}`
(Lars as a capsule in the grasp-site frame while held; loading the gesture adds `lars` to `show.PROP_SHAPES`),
`dock` (`xy_left` and `head_xy_left`: table positions in the left arm's frame; `lying`: belly or back), `speed`
(the bake's).

## Stage server
- `/play {"arm": "right", "gesture": "pick_lars"}`: right arm only, empty hands, facing the audience. In each_other
  (since the lowered stage pose of round 3b) they come 12.2 cm from the left arm, in each_other_props 5.4 cm: the
  stage refuses them with "would come 13 cm from the left arm (stance); needs 15 cm". The same for place_lars, which
  needs Lars held. **Also refused right after `/stance audience` from each_other** (the left arm's finished stance
  move still counts its whole sweep, through the each_other pose) until the left arm moves again (any gesture or
  `/stage`).
- After the pick the right arm is in props mode holding `lars`: unlike the toys (car, ufo), every gesture plays,
  re-checked with Lars's shape (/play ~140 ms). `/grip`, `/talk`, `/props` and duets are refused for that arm.
  The place ends it.
- A pick/place runs to its end: nothing else starts on that arm meanwhile. Left-arm slash, thrust, pounce,
  look_at_other, look_around, drive, ufo_fly and ufo_crash are unsafe pairs with them: refused at the same time.
- `/freeze` mid-pick/place brakes on the gesture's own path; the next request finishes it at half speed first (like
  a frozen duet) and is refused with "finishing it". Shutdown finishes it, puts Lars back, then rests.
- `--dock-check`: before pick_lars, one overhead frame (~1.5 s); refused (409) unless Lars lies within 3 cm and 30°
  of the baked dock. Off by default (the agent's HTTP timeout is 2 s). `lars.dock_problem` maps the dock's table
  position (and its head tip) into the camera through the current `calib.json` and looks for Lars there. The
  comparison goes through that same homography, so it follows a recalibration; it needs a calibration that
  matches the camera (with the 18:45 fit the dock is at px (331, 86)). It refuses when it can't tell the head end (no crown or eyes, or the cues disagree). A Lars turned 180° is
  refused by the 30° rule.

## Agent
`[pick:lars]` and `[place:lars]` go to `/play` on the arm the stage lists for them (`side` in /gestures). Prompt
(`{{GUESTS}}`): Lars the frog doll, which the right arm can pick up mid-story and must put back before the end, facing the
audience first, that arm's mouth full meanwhile, at most one visit per story; the pick's length comes from /gestures.

## Sim results (stage.Stage on i2rt's MuJoCo sim, in-process)
Face-down bake, 18:45 `calib.json`, speeds 0.3, 0.5, 0.75 and 1.0: pick and place play, max tracking error
0.004–0.027 rad, no fault; the refusals above hold (each_other, and right after `/stance audience` from each_other); nod/wave/celebrate play while holding Lars; the left arm waves meanwhile; freeze mid-pick and mid-place, then
finish.

## Before the first real run
- Speed 0.3 first (the committed bake); rebake at 0.5 → 0.75 → 1.0 only after several clean picks.
- The face-up bake picked and placed a face-down Lars on the real arms (Nov 7 evening) at `GRIP_Z` 1.6 cm and
  `GRIP_CLOSE` 0. Not yet on the real arms: the face-down bake (approach, the holding pose, Lars's face at the
  audience), `BACK_TOP` (an estimate, hover height only), the grip 1 mm lower (the 18:45 table z). If Lars slips
  or tilts: `GRIP_Z`, `GRIP_FROM_HEAD`, `HEAD_UP_ROLL`.
- The real place left Lars ~25° off `DOCK_AXIS` and ~2 cm off the dock (`facedown_dock_cal3.jpg`): inside the
  dock check's 30° / 3 cm, with little margin. Straighten him by hand before a pick if the check is on.
- show's checker uses one table height (−0.012) for both arms; the 18:45 fit puts the right arm's at −0.006, so its
  clearances read 6 mm optimistic. lars.py's own bake checks use `calib.json`'s.
- Live planning (`lars.py --dry`, `--go`) stays as a fallback for Lars anywhere on the table.
