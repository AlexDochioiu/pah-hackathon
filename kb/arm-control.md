# Driving the arms

Our scripts command joint targets directly through the kit ([station.md](station.md)). Dream-1 never drives the
arms itself: its chunks go through the stage's `POST /trajectory` and our checker ([dream-1.md](dream-1.md)).

## The pattern (see `robot/lift.py`, `robot/home.py`)
1. `lock = station_lock.acquire("name")`, then `a = arms.open_arms(station, energize=True)`.
2. Read the current pose and **refuse to start unless the arm is within 0.25 rad of rest** (a known starting pose
   is what makes the rest of the plan safe).
3. Move with smooth interpolation (smoothstep), sending `a.command(...)` at 50 Hz. Keep speed ≤ ~0.6 rad/s for
   anything near the table; `home.py` uses 0.3 rad/s.
4. Always finish back at rest, then `a.close()` and `lock.close()` in a `finally`. `close()` switches the motors
   off and the arm goes limp, so ending anywhere but rest drops it.
5. When commanding one arm, keep sending the other arm its current pose.

## High-level wrapper (`robot/arm.py`)
A teammate's class that owns both arms, the station lock and the IK: `from arm import Arms`, then
`with Arms.open() as arms: arms.stage().move_to("left", [0.3, -0.1, 0.2]).play("nod", side="left").rest()`.
`play()` uses only gestures that pass `show.Checker` (capped at their `max_speed`), and every move is checked
against the lean-back rule before it starts. Joints 4–6 run at a low gain (kp=10) and sag ~1.5° under gravity, so
"arrived" means within 0.05 rad.

## Planning in simulation first (`robot/pick.py`)
- Solve every waypoint with the kit's IK (`teleop_ik`) before any motor moves; reject waypoints with position
  error > 6 mm or rotation error > 0.08 rad.
- The motors interpolate in **joint space**, so a straight line between two safe poses can still dip. Check FK at
  ~25 samples along every segment and abort if the gripper goes below the table floor.
- Start and end through the raised pose (rest + `[0, 0.8, 1.2, 0, 0, 0]`).
- `--dry` prints the plan and exits; use it first, always.
- `show.Checker` takes the right arm's offset and each arm's table height from `robot/calib.json`
  ([calibration.md](calibration.md#who-reads-calibjson)); every arm is checked against its own table.
- Stage server, arm against arm (`stage.py` `_commit`): a new plan must clear what the other arm is committed to by
  15 cm: its whole plan while it runs or loops (idle breathing, stir), only the pose it holds once a one-shot plan
  (a gesture, `/stance`, `/stage`) has finished. Whatever starts next on that arm (idle, a queued gesture) is checked
  when it starts.

## POST /trajectory (Dream-1's chunks)
`{"arm", "t": [s], "q": [[6 rad]], "g": [0..1] (optional), "speed": 0.1..1, "name", "carry": "lars" (optional)}`:
an external joint trajectory on one arm. `robot/dream_run.py` sends Dream-1's chunks here.
- It starts from the arm's current command, held 0.2 s at both ends, and is retimed so no joint passes
  0.3 rad/s × speed (`TRAJ_VMAX`, the table cap), 2 rad/s², and the gripper 1.0 per s (a full close over ~1 s), easing
  in and out over the first and last 8 keys.
- Checked like a pick (`show.Checker.check_side` with the table rule: limits, 4 cm clearance, 0.3 rad/s and 3 mm
  within 5 cm of the table), lean-back no further than rest or its start, 15 cm from the other arm's committed
  motion. A refusal is a 409 with the reason; nothing moves.
- Two allowances for Dream-1 (owner, Nov 7): the fingertips alone may brush the table down to −3 mm
  (`TRAJ_TIP_FLOOR`; its real Lars grasps reached 2.3 and −0.2 mm in our model), and joint values up to 0.05 rad past
  a safe limit are clamped onto it (`TRAJ_CLAMP`; its jitter at the j2/j3 fold stops). The stage log prints each
  trajectory's lowest fingertip and other-part clearance; the reply has `clamped`, `tip_mm` and `tip_end_mm`.
- The arm must be free (done or idling). Like a pick it runs to its end; `/freeze` brakes it on its path and it is
  never resumed. Answers `{"ok", "name", "duration_s", "peak_rad_s", "slowed", "props", "clamped", "tip_mm",
  "tip_end_mm"}`; `GET /state` shows it as
  the arm's `playing`.
- Props: with `carry`, a trajectory that closes the gripper from ≥ 0.6 to ≤ 0.35 at its end takes the prop, as
  `pick_lars` does: from then on gestures keep the grip, `/talk` and opening `/grip` are refused, and later
  trajectories keep the grip (their `g` is ignored) unless they end ≥ 0.6, which puts it down (so does
  `place_lars`). Any other prop in hand (`/props`, a toy) refuses the trajectory.

## YAM joint geometry (from FK on i2rt's model)
- At zero the joint axes are j1 +z (base yaw), j2 +y, j3 −y, j4 −y (pitches), j5 −z (wrist yaw), j6 +x (roll).
- j1 > 0 turns the arm toward +y. For the left arm the other arm is at −y, so it faces the other arm with j1 < 0;
  the right arm with j1 > 0. Mirroring a motion between the arms = multiply joints by `[−1, 1, 1, 1, −1, −1]`.
- j2 + tips the upper arm forward (gripper forward and down); j3 + raises the forearm (gripper up and back).
  Gripper pitch relative to a pose = −Δj2 + Δj3 + Δj4, so `Δj4 = Δj2 − Δj3` keeps the gripper level.
- j6 rolls about the gripper's pointing axis: it spins a held prop about itself, it doesn't re-aim it.
- `grasp_site`'s z axis points out of the fingers. At rest (zeros) it is ~0.17 m up, pointing at the audience; at
  `lift.py`'s raised pose ~0.55 m up. Base and link1 never leave the table; link2 (shoulder) is the lowest moving
  part at ~9.5 cm above the table.
- i2rt's model limits j3 to [0, 3.14] (not 3.67). `get_yam_robot(..., sim=True)` returns a `SimRobot` before
  any CAN access, so `arms.open_arms(station, energize=True, sim=True)` is safe to run while others use the arms.

## Safety
- A human stands at the e-stop for every real-arm run. Tell them before each run starts. The one exception is
  `robot/lars.py --unattended` ([AGENTS.md](../AGENTS.md)).
- Don't run anything on the arms while someone else is using the station.
- Never move the arms' mass behind the bases: the table tips (seen Nov 7). `show.Checker` enforces it (each arm's
  centre of mass stays ≥ stage − 2 cm in x; [gestures.md](gestures.md#safety-checker-showchecker-showload_library)).
- Tracking lag: fast wrist waves lag up to ~0.19 rad behind the command; slow moves track within ~0.03 rad.

## E-stop recovery
1. Fold the arms by hand to rest.
2. Release the e-stop **and** do the station power reset (releasing alone doesn't bring the motors back).
3. Reset both CAN links: `sudo ip link set can_follower_X down`, then
   `sudo ip link set can_follower_X up type can bitrate 1000000` (after an e-stop they sit in ERROR-PASSIVE).
4. Run `home.py` (or `home.py left|right` for one arm).

- **Motors that stop answering at start** ("loss communication" on one motor id, then "motor chain is not
  running"): i2rt's control thread exits but `read()` keeps returning the last pose, so a script looks healthy while
  the arm ignores it. `stage.py` checks `dead_motors()` at start and every tick and stops. Usual cause: e-stop
  pressed or arm power off; release it, do the power reset, reset the CAN links (above).
- **Heavy CPU work with the real arms open drops motors.** The stage server's ~30 s library check, run after
  `open_arms`, starved i2rt's motor thread: a different motor "lost communication" on each start. `stage.py --real`
  now checks the library first and opens the arms after. Do the same in any script: compute first, then open.

## Harmless noise
- "DM Error in control loop: file descriptor -1" and "motor failed with info" at shutdown: an i2rt race when
  closing. Ignore.
- Motors blinking red after a script ends: they're switched off (idle). Normal.
