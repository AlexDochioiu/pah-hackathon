# Notes / progress log

## 2026-11-07 (Sat)
- Idea brainstorm: Dial-a-Robot (public phone line → LiveKit voice agent → robot, asks callers for help when stuck); Snack Thief as idle gag.
- Station explored; arms + cameras verified via direct i2rt control (`wiggle.py`, `lift.py`).
- Dream-1 base model (`dreamcli robot run`, zero-shot) on "pick up the yellow block": 4 runs, no successful pick.
  - Gripper closed on air / grabbed wrong (red) block; long "what + how" prompt made it worse; one run hit the table → e-stop.
  - Takeaways: short prompts only, clean table, half speed (`max_ee_speed_m_s: 0.3`); fine-tune needs 50+ demos and 7–8 h.
- **Decision: bypass Dream-1, control the arms directly** (overhead camera → pixel↔arm calibration → IK pick/place → block castle).
- Next: calibration with `teach.py` (5 same-size blocks, touch top centres with each arm), then `pick.py`.
- Calibration (2 rounds of hand-guided touches): overhead pixel → arm frame homography, ~5 mm mean / 10 mm max error.
  Arm bases are 0.602 m apart, parallel (right = left + (0.006, 0.602)). Block top z: left 0.013, right 0.020.
- `pick.py` written: colour blob → centre + rotation → top-down IK plan (raise → above → down → grip → lift → place),
  every waypoint solved in sim, path checked against the table floor, refuses unless arm at rest. Dry run OK.
- After a script ends the motors switch off and blink red (normal idle).
