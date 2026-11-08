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
  "tip_end_mm", "check_ms"}` (`check_ms`: the check's CPU time, see [Motor dropouts](#motor-dropouts-nov-7));
  `GET /state` shows it as
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
  The same thing hit dreamcli mid-run on Nov 7 night: see [Motor dropouts](#motor-dropouts-nov-7).

## Motor dropouts (Nov 7)
Two `dreamcli robot run`s lost motors mid-run on both buses at once, then i2rt re-enabled them and the arms jumped:
the hand-over (`~/drun4.log`, 21:57:02) and the hat onto the other gripper (`~/drun5.log`, 22:01:23). The pen pick
(`drun3`), the two Lars picks (`drun1`, `drun2`) and `dream.check` ran to their end; their only motor errors are
the known shutdown race. Read from copies of the station logs and i2rt/dreamcli source; no CAN or motor registers
were read.

**Cause (high confidence): the dreamcli process stalled for ~0.7 s and sent the motors nothing.** It is not power and
not the CAN bus:
- "loss communication" is not a host timeout. It is status 0xD, which the **motor itself** reports in its reply
  (i2rt `motor_drivers/utils.py` `MotorErrorCode`, read from the reply's first byte in `dm_driver.parse_recv_message`).
  The motor's watchdog fired: no command reached it for its CAN timeout. That's register 9 in 50 µs steps; i2rt's
  `motor_config_tool/set_timeout.py` writes 8000 = 400 ms. Our motors evidently have it on, though dreamcli's
  `robots/yam.py` assumes it's 0 on a YAM. Every reply arrived: no "Failed to receive" or "CAN Error … Retrying"
  lines, so the bus and adapters worked. A sag would show as 0x9 (undervoltage), and a reset motor comes back
  0x0 (disabled), not 0xD.
- Both buses faulted within 72 ms (drun4) and 200 ms (drun5). Each bus has its own adapter and its own i2rt
  thread, so only something process-wide hits both. The camera encode threads "fell a capture behind" in the same
  millisecond.
- The process was frozen, not busy: right after the loss, consecutive log lines from one i2rt function (its
  WARNING, then its ERROR) came 70–250 ms apart, while the load average was ~0.7 of 16 cores. One thread held the
  Python GIL and the rest waited.
- drun5's trace (`last-run.csv`, the copy at 22:02) puts it at ~22:01:23.1. The 20 Hz tick due then took 2 s,
  ~0.7 s before i2rt saw the 0xD. That's when chunk 9's answer was due, and with the `eef` head the dream client
  solves the chunk's IK on its refill thread, inside the same process. All four other tick overruns that night
  (160–200 ms: drun1, drun3, drun5, dream.check) came 40–100 ms before a chunk installed, i.e. the client's
  per-chunk work routinely freezes the process ~150 ms. The fatal two were the same freeze running past 400 ms.
  dreamcli's own `robots/run.py` notes that its IK compile "starved the arm thread past a Metal gripper's 500 ms
  CAN timeout (fault 0xD)". Not verified: which call inside `dream` stalls (IK solve, a recompile, GC). The
  `dream` package wasn't copied.
- Same signature as the afternoon's stage start (`~/dial/stage_story3..6.out`): CPU work in the arm process, both
  buses, a different motor id each time (tonight L2+R7, L3+R1, then L1+R1 during recovery). The motor that reports
  is just the next one in i2rt's loop when the thread wakes up, not the most loaded joint.
- Long reach and the grip force cap are bystanders: the cap only backs the gripper command off, and drun5's arm
  was at a moderate pose (left j2 1.33, j3 1.1). TX dropped 10 on `can_follower_l` counts since 14:30 (e-stops,
  recovery bursts). Temperatures at start were 30–31 °C. i2rt reads no voltage (`voltage=-1`).

**Why the arms jumped.** dreamcli's i2rt (`dm_driver._try_recover_motors`) catches the 0xD and "recovers" every
motor on the chain, healthy ones included: `clean_error` disables each motor ("motor 1 error: disabled"), then
`motor_on` re-enables it, one motor at a time over ~1 s. Meanwhile the arm hangs limp and falls; drun5's left arm
went from j2 1.33 to 2.11 and j4 −0.61 to −1.49. Each motor then gets the **stale** pre-fault target at full kp
and snaps back: j2 2.11 → 1.35 at up to ~4 rad/s.

**Red LEDs** mean the motor's driver has power but isn't driving: disabled or faulted. That's what a 0xD fault,
or i2rt's `clean_error`, leaves. Off would mean no power. They confirm the power stayed up.

**Our show path's exposure.** Dream-1's client (and its IK) runs in `dream_preview.py`, a separate process, so its
stalls can't starve the stage's motor threads. That's the design; keep it. The stage process still does CPU work
next to i2rt's threads: a `/trajectory` check takes 100–370 ms on the laptop (the station is slower; the stage log
now prints `checked in N ms` and the reply has `check_ms`), and a `/play` runs a clearance check. The stage sets
`sys.setswitchinterval(0.001)` with `--real`, so the motor threads get the GIL back within 1 ms instead of 5 after
each CAN call. The kit's i2rt (the stage's, in `~/physical-ai-hacks/.venv`, not copied tonight) behaved
differently in the afternoon logs: its motor thread exited on the first 0xD, with no recovery and no jump. The
stage then sees `dead_motors()` and stops; that arm goes limp where it is. Confirm with
`grep -n _try_recover_motors ~/physical-ai-hacks/.venv/lib/python3.13/site-packages/i2rt/motor_drivers/dm_driver.py`:
no match means no auto-recovery.

**Mitigations, ranked.**
1. Done by design: in the show, Dream-1 runs only through `dream_preview.py` → `/trajectory`, never through
   `dreamcli robot run`. Its IK lives in another process.
2. Done: `--real` lowers the GIL switch interval; `/trajectory` logs its check time. On the first real run, a check
   over ~300 ms on the station is the warning sign. Then move the check off the arm process.
3. For any further `dreamcli robot run`: try `--action-space joint` (no client IK; the model's own joints) or
   `ik_solve: tick` in the config, and watch for `tick overrun` lines. They're the precursor. Any overrun near
   400 ms means stop.
4. dreamcli's i2rt recovery is unsafe as written (stale target, healthy motors disabled). Report it to Dream Labs /
   i2rt. A fix would re-enable at the measured pose with kp ramped from 0, or not auto-recover at all. Don't patch
   the station's site-packages without the owner.
5. Not recommended: raising or disabling the motors' CAN timeout (register 9). It would ride out the stalls, but
   then a hung or crashed program leaves an arm stiff on its last target instead of going limp. Owner's call, a
   CAN register write with the arms supported.
6. Not supported by the evidence: a reach limit or a lower grip force cap. Neither causes the stall; a reach limit
   would only shorten the fall.

**What the owner can check physically** (cheap, though the evidence points away from it): both CAN cables seated
at the adapters and the arms, and termination on each bus. If the arms share a PSU, its rating against two arms
at peak (both shoulders loaded plus a grip). Whether any motor LED ever went **off**, not red, during a run: off
would reopen the power hypothesis.

## Harmless noise
- "DM Error in control loop: file descriptor -1" and "motor failed with info" at shutdown: an i2rt race when
  closing. Ignore.
- Motors blinking red after a script ends: they're switched off (idle). Normal.
