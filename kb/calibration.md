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

## Redo it when
The cameras or the arms move, or picks start missing by more than ~1 cm. Pixel coordinates refer to 640×360 frames.
