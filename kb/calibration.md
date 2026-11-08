# Overhead camera ↔ arm calibration

Needed to turn a pixel in the `overhead` camera into a point the arm can reach (for pick-and-place). Story
gestures don't need it.

## Procedure (Nov 7, ~20 min with a human)
1. Put ~5 same-size coloured blocks on the table, spread in **2D** (not in a line: a line can't constrain the fit).
2. `snap.py` → `~/dial/snaps/overhead.jpg` (save a copy per round).
3. `teach.py <secs>` floats both arms (gravity compensation). A human puts each arm's gripper tip on the top centre
   of each block it can reach and holds still ~2 s. The script logs grasp-site FK at 20 Hz and records a
   "touch" whenever the tip is still (< 1 cm/s for > 1.5 s) and > 5 cm from rest → `~/dial/teach.npz`.
4. Match touches to blocks by hand in `robot/calib.py` (`PAIRS`), then run it: it refines each pixel to the
   colour blob's centre, fits a homography pixel → left-arm (x, y), and writes `~/dial/calib.json`
   (copy in `robot/calib.json`).
5. Move some blocks and repeat steps 2–4 to add points (we did 2 rounds).

## Results
- Homography error over 7 points: ~5 mm mean, 9.9 mm max.
- Right-arm frame = left-arm frame + (0.0056, 0.602). Measured from blocks both arms touched.
- Block top z: left arm 0.013 m, right arm 0.020 m (blocks are ~25 mm tall, so the table is ≈ −0.012 / −0.005).
- The right arm can't reach the leftmost blocks (far −y side); each arm covers its own half plus the middle.
- `pick.py --from 340,148 --to 300,190 --dry` landed within 7 mm of the hand-touched position.

## Round 3 (Nov 7, ~18:40, after the overhead camera was moved and re-aimed)
- The 11:30 calibration went stale when the camera moved: pixel → table was off by ~16 cm near the Lars dock.
  The baked Lars pick doesn't use the camera and kept working; only camera-based checks and picks were wrong.
- 5 blocks, both arms touched each (`teach_cal3_round*.npz`, `snaps/overhead_cal3.jpg`). Homography residuals
  0.2–3.7 mm. Right offset (−0.0056, 0.6018), the same as before within 1 cm: the arms haven't moved.
- Matching touches to blocks: fit a rigid 2D shape (rotation, scale, shift) for every assignment and keep the best
  (37 mm vs 114 mm next). **Flip the image v axis first**: pixels are left-handed, so without the flip no
  assignment fits. A 4-point homography always fits exactly, so it can't pick the assignment.
- teach.py only counts a touch held still for > 1.5 s; quick taps are lost. Ask for 3 s holds on every block.
- The old file is kept on the station as `~/dial/calib_before_cal3.json`.
- The offset's x moved 1.1 cm (+0.0056 → −0.0056); the per-block offsets in `calib.py` spread ±1.5 cm, so it's within
  the noise of the touches. The Lars dock is pinned in the right arm's frame (`DOCK_RIGHT`, where the real pick
  worked), so this didn't move the arm's target; only its left-frame/pixel position follows ([props.md](props.md)).
- After a recalibration: re-grab `robot/lars/facedown_dock_cal3.jpg` with Lars at the dock (the selftest's dock
  check runs on it).
- Block tops (Round 3): left 0.0185, right 0.019 → table z **left −0.0065, right −0.006** (25 mm blocks). Both arms'
  table came out ~6 mm higher than the 11:30 fit's −0.012 (the left one too), so moves that touch the table went
  ~6 mm deeper until they were re-solved (below).

## Who reads calib.json
- `show.py` (the safety checker, the sim scene, make_gestures/make_duets): `RIGHT_OFFSET` from `right_offset`, and
  `TABLE_Z[side]` = `block_top_z[side]` − `BLOCK_H` (25 mm) per arm, each arm's parts checked against its own table.
  `TABLE_TOP` (the higher one) is what mirrored keys that touch the table are solved against: the same keys go
  deeper on that arm. Missing or unreadable file: a loud stderr warning and the Round 3 values.
- `lars.py`, `pick.py`: the homography, offset and per-arm block tops (they read it themselves).
- After a recalibration: regenerate (`make_gestures.py`, `make_duets.py`) and run `check_gestures.py`. Table moves
  given in joint space (bang_table's `fist`, die_dramatically's `dead`, ufo_crash's drop) are solved against the
  table of the time: if the checker rejects them, re-solve those numbers (keep each the same height above the table);
  don't loosen the limits. Duets that place one arm relative to the other in the shared frame (cross_swords' clash,
  ufo_abduct's hover point) follow the offset.

## Redo it when
The cameras or the arms move, or picks start missing by more than ~1 cm. Pixel coordinates refer to 640×360 frames.
