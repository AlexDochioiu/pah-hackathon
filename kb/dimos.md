# dimos digital twin

**Parked (Nov 7 ~20:20): the owner saw no value in it.** The code stays in `dimos/` for reference; nothing in the show runs it. `/state` has since gained `grip_measured` (the gripper as read back, mouth flaps included); the twin still shows `grip`.

A Dimensional (dimos) digital twin of our two arms, for the Creative track (a Dimensional engineer judges).
dimos's own Dual OpenYAM stack (ControlCoordinator + ManipulationModule + its Viser 3D view) runs on the laptop.
Its two arms mirror ours live, and it can show a "ghost" trajectory (Dream-1's next move) over them. Code, run
commands and checks: [dimos/README.md](../dimos/README.md).

## How it connects
```
stage.py (:8770 real via tunnel, or :8783 kin) ── GET /state, 20 Hz ──► StageConnection (dimos ConnectionModule)
   joint_state ──► ControlCoordinator (100 Hz) ──► TwinManipulationModule ──► Viser :8095 (30 Hz)
   stage_raw ──────────────────────────────────────► (fingers, latency probe)        ▲
Dream-1 side ── POST :8796/ghost ──────────────────────────────────────────────────── ghost = dimos preview robot
--mcp: StageSkills (play, duet, freeze, rest, gestures, duets) ── POST ──► the kin stage :8783 only; McpServer :9990
```
- `StageConnection` stands where dimos's CAN connection (`DualOpenYamConnection`) or its `--simulation` mock
  would. It publishes `arm/{left,right}_joint1..6` = `/state` `measured`, and `arm/{side}_gripper` = `/state`
  `grip`. It drops every command: the coordinator always sends some, and Plan/Execute in the Viser panel send
  more. **The twin never moves the arms.** It sends only `GET /state` to the stage.
- `StageSkills` POSTs, so it talks only to `--skills-stage` (default `http://127.0.0.1:8783`) and refuses port 8770.
- dimos runs from `/tmp/dimos-src` at `ab978ec5a0` (main, Nov 7). It is not vendored: `dimos/run.sh` clones it
  there if it's missing. Its models come from git-lfs (`dual_openyam_abc_box_v2`, `yam_description`).

## Model: matched to robot/show.py
dimos's stock Dual OpenYAM URDF differs from ours in two places. `twin.py` patches its URDF at start:
| | dimos stock | ours (the twin) |
|---|---|---|
| Base spacing | 0.62 m apart in y, same x (Amazon ABC scene) | right base at −`right_offset` from `robot/calib.json` = (+5.6 mm, −601.8 mm) from the left, read at start |
| Fingers | fixed closed (the two jaw openings are made-up 0..1 joints with no visuals) | slide again (i2rt `yam.urdf`): finger = −grip × 46.95 mm, joint8 mimics joint7 |

`dimos/check_model.sh` poses our MuJoCo scene and the twin URDF with the same joints (200 random poses, both arms,
random grippers). It compares each part's mesh bounding box in world:
- base, link1–5, gripper: ≤ 0.04 mm, sizes equal. Joint zeros, signs, axes and base placement are identical
  (both come from i2rt's YAM model).
- fingertips: ≤ 2.8 mm. The finger meshes and their origins differ slightly between i2rt's MuJoCo
  `linear_4310` gripper and the URDF. The stroke is 47.5 mm (MuJoCo) vs 46.95 mm (URDF), and MuJoCo's closed
  position sits 0.55 mm further in.

Visual check: `dimos/check_model.sh compare` freezes the kin stage mid-gesture, renders our MuJoCo audience camera
and screenshots Viser from the same camera (`dimos/shots/`: rest, wave, celebrate, bow, bite, duet_hug). The poses
match. Viser's view looks a little wider than MuJoCo's at the same camera; it's only a view setting.

## Gotchas
- `/state` has no measured gripper, only `grip` (the commanded value). The twin shows that, so `/talk`'s mouth
  flaps (an overlay the stage adds after `grip`) don't show in the twin.
- No table in the dimos scene: its grid sits at the bases' height, and our table is ~1.2 cm below.
- dimos's ManipulationModule pushes Viser at a fixed 10 Hz. `TwinManipulationModule` restarts that thread at 30 Hz.
- Stop the twin with Ctrl-C (SIGINT). SIGTERM leaves dimos's forkserver workers holding :8095.
- `show.Scene.forward()` is `mj_kinematics` only: it doesn't place cameras. Call `mujoco.mj_camlight` before
  rendering a named camera, or the frame is black.
- Headless Chrome on the Mac draws Viser's WebGL canvas only with SwiftShader
  (`--use-angle=swiftshader --enable-unsafe-swiftshader`).

## Latency (Nov 7 evening)
- dimos pipeline, stage reading → Viser push, while the arms move: p50 38 ms, p95 62 ms (n ≈ 450, the twin's own
  probe, logged every 10 s as "Twin latency").
- `GET /state` through the ssh tunnel: p50 23 ms (30 lone requests); p50 32 ms, p95 97 ms while polling at 20 Hz.
  On the kin stage locally: < 1.5 ms.
- Plus the 20 Hz poll phase (avg 25 ms) and the stage's own 50 Hz loop (≤ 20 ms). Real arm motion → twin is
  about 0.1 s typical and ~0.2 s at p95.

## Ghost
A joint trajectory replayed, looping, on dimos's translucent preview robot (its Plan/Preview ghost) over the live
mirror. Used for "ghost, then the real arms run Dream-1's move if our checker passes it". The twin only displays
it: nothing is sent to the stage.

`POST http://127.0.0.1:8796/ghost` (only on the laptop running the twin; `--ghost-port`, 0 = off)
```json
{
  "side": "both",
  "t": [0.0, 0.05, 0.1],
  "q": {
    "left":  [[j1, j2, j3, j4, j5, j6, grip], ...],
    "right": [[j1, j2, j3, j4, j5, j6, grip], ...]
  },
  "loop": true,
  "label": "dream-1 chunk 12"
}
```
- `side`: `"left"`, `"right"` or `"both"` (default). `q` must hold every side named; an arm the ghost doesn't carry
  follows the live arm.
- `t`: seconds, non-decreasing, 1–20000 entries. It is shifted to start at 0. A single entry = a still pose.
- `q.<side>`: one row per `t`. Absolute joints in rad, in the same convention as `/state` `measured` (each arm's
  own joints, the right arm not mirrored), then the gripper 0 closed .. 1 open. The gripper is optional (6 numbers:
  it follows the live gripper).
- Playback is linear interpolation at 30 Hz. With `loop` (default true) it holds the last frame 0.5 s, then starts
  over, until cleared or replaced. With `"loop": false` it stays on the last frame.
- Replies `200 {"ok": true, "active": true, "side", "frames", "duration_s", "loop", "label"}`, or
  `400 {"ok": false, "error": "..."}` naming the bad field.

`POST /ghost/clear` → `{"ok": true}` hides it. `GET /ghost` → the status above, or `{"active": false}`.
dimos's own Preview button animates the same ghost robot. While a ghost loops, the twin shows it again within 1 s
after a Preview ends.
