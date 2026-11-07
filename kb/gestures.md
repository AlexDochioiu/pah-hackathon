# Gestures and stage server

In progress (Nov 7). The plan:
- A library of named arm motions (wave, bow, slash, block, cower, celebrate, die_dramatically…), stored as
  joint-space keyframes relative to a raised "stage" pose, mirrored per arm.
- Every gesture passes a simulation check before it can play: joint and speed limits, every link and the prop tip
  ≥ 4 cm above the table, and the two arms ≥ 15 cm apart (props add ~15 cm beyond the gripper).
- `robot/stage.py`: one process owns both arms and serves a local HTTP API on `127.0.0.1:8770`
  (`GET /gestures`, `POST /play {arm, gesture, speed}`, `/freeze`, `/idle`, `/stage`, `/rest`, `GET /state`).
  `--sim` by default; real arms need `--real`.
- A recorder to capture new gestures by moving a floating arm by hand, and a one-command bring-up test.

Whoever finishes this replaces this page with how it actually works.
