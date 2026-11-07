# Decisions

Newest first.

- **2026-11-07 — Story theatre.** The arms act out an AI-improvised, interruptible story with props. Gestures are
  easier to make reliable than manipulation, and a performance is more shareable than a pick-and-place. The
  pick-and-place work ([calibration.md](calibration.md), `robot/pick.py`) stays as a possible story beat
  ("the knight hands over the treasure").
- **2026-11-07 — Bypass Dream-1, drive the arms directly.** Zero-shot Dream-1 never completed a pick in 4 tries and
  hit the table once; fine-tuning would take 7–8 h. Direct joint control plus IK in simulation is predictable.
  Details: [dream-1.md](dream-1.md).
- **2026-11-07 — Dial-a-Robot (dropped).** A public phone line where callers voice-control the arms through a
  LiveKit agent and Dream-1. It depended on Dream-1 being reliable.
