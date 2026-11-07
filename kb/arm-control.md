# Driving the arms

We don't use Dream-1 ([dream-1.md](dream-1.md)); our scripts command joint targets directly through the kit
([station.md](station.md)).

## The pattern (see `robot/lift.py`, `robot/home.py`)
1. `lock = station_lock.acquire("name")`, then `a = arms.open_arms(station, energize=True)`.
2. Read the current pose and **refuse to start unless the arm is within 0.25 rad of rest** (a known starting pose
   is what makes the rest of the plan safe).
3. Move with smooth interpolation (smoothstep), sending `a.command(...)` at 50 Hz. Keep speed ≤ ~0.6 rad/s for
   anything near the table; `home.py` uses 0.3 rad/s.
4. Always finish back at rest, then `a.close()` and `lock.close()` in a `finally`. `close()` switches the motors
   off and the arm goes limp, so ending anywhere but rest drops it.
5. When commanding one arm, keep sending the other arm its current pose.

## Planning in simulation first (`robot/pick.py`)
- Solve every waypoint with the kit's IK (`teleop_ik`) before any motor moves; reject waypoints with position
  error > 6 mm or rotation error > 0.08 rad.
- The motors interpolate in **joint space**, so a straight line between two safe poses can still dip. Check FK at
  ~25 samples along every segment and abort if the gripper goes below the table floor.
- Start and end through the raised pose (rest + `[0, 0.8, 1.2, 0, 0, 0]`).
- `--dry` prints the plan and exits; use it first, always.

## Safety
- A human stands at the e-stop for every real-arm run. Tell them before each run starts.
- Don't run anything on the arms while someone else is using the station.
- Tracking lag: fast wrist waves lag up to ~0.19 rad behind the command; slow moves track within ~0.03 rad.

## E-stop recovery
1. Fold the arms by hand to rest.
2. Release the e-stop **and** do the station power reset (releasing alone doesn't bring the motors back).
3. Reset both CAN links: `sudo ip link set can_follower_X down`, then
   `sudo ip link set can_follower_X up type can bitrate 1000000` (after an e-stop they sit in ERROR-PASSIVE).
4. Run `home.py` (or `home.py left|right` for one arm).

## Harmless noise
- "DM Error in control loop: file descriptor -1" and "motor failed with info" at shutdown: an i2rt race when
  closing. Ignore.
- Motors blinking red after a script ends: they're switched off (idle). Normal.
