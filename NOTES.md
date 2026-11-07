# Notes / progress log

## 2026-11-07 (Sat)
- Idea brainstorm: Dial-a-Robot (public phone line → LiveKit voice agent → robot, asks callers for help when stuck); Snack Thief as idle gag.
- Station explored; arms + cameras verified via direct i2rt control (`wiggle.py`, `lift.py`).
- Dream-1 base model (`dreamcli robot run`, zero-shot) on "pick up the yellow block": 4 runs, no successful pick.
  - Gripper closed on air / grabbed wrong (red) block; long "what + how" prompt made it worse; one run hit the table → e-stop.
  - Takeaways: short prompts only, clean table, half speed (`max_ee_speed_m_s: 0.3`); fine-tune needs 50+ demos and 7–8 h.
- **Decision: bypass Dream-1, control the arms directly** (overhead camera → pixel↔arm calibration → IK pick/place → block castle).
- Next: calibration with `teach.py` (5 same-size blocks, touch top centres with each arm), then `pick.py`.
