# Dream-1 (Dream Labs): asked, checked by us, then played

Dream-1 is a language-conditioned robot policy. It reads the three cameras and both arms' joints and answers an
"action chunk": 48 rows of joints and gripper per arm. In the show it never drives the arms itself:

**ask Dream-1 (preview, nothing moves) → our `show.Checker` on the stage → play slowly if it passes → otherwise our
own `pick_lars`**, with Lars narrating the "imagining" beat ("Let me ask Dream-1… it says… let's see").

The path is the dreamlabs client used directly (dreamcli's `robots` helpers on the station), not `dreamcli robot run`
and not Dimensional (dropped Nov 7). dreamcli needs the stage server stopped (it opens the arms over CAN and takes
`~/.dreamlabs/arms.lock`, even with `--dry-run`), and the show can't stop the stage mid-show.

## The show path (built Nov 7)
| Piece | Where | What it does |
|---|---|---|
| Preview | `robot/dream_preview.py`, on the station in `~/dream-ghost/` (not `~/dial`), run with `~/dream-env/bin/python` | Opens the three cameras through dreamcli's readers (`~/.dreamlabs/station.yaml`, face blur on) and the client (`_record` off), feeds the observation window at 5 Hz from the cameras and `GET /state` (measured joints). One `get_action` per "next": 48 rows per arm, played at 20 Hz (dreamcli's pace). Never opens an arm, never POSTs. Each call goes to `~/dream-ghost/calls.jsonl`; frames and chunks to `~/dream-ghost/runs/HHMMSS/`. A camera frame older than 1 s skips the tick; 8 s without a full set gives up. Warming errors are retried as the server says (capped). The left wrist camera is on a USB 2 link: it opens at 15 fps |
| Stage endpoint | `POST /trajectory` in `robot/stage.py` ([arm-control.md](arm-control.md#post-trajectory-dream-1s-chunks)) | Retimes to ≤ 0.3 rad/s on every joint (ease in and out), gripper ≤ 1.0 per s (a full close over about 1 s, slow and deliberate), checks with `show.Checker` like a pick, refuses with the reason. `carry: "lars"` makes a closing chunk hold Lars like `pick_lars` |
| Driver | `robot/dream_run.py`, laptop, stdlib only | Runs the preview over ssh, skips still chunks, picks the arm that moves most, POSTs each chunk with `carry` lars, fetches the next one 1.5 s before the current one ends (a round trip is ~1 s). Done when the stage holds Lars and a chunk ends with the fingertips 40 mm up: Lars stays held, the arm is the stage's again. On a refusal, a tracking error over 0.2 rad, a fault, a Dream error or `--pick-s`/`--chunks`, it goes back along the played chunks, reversed, and plays `pick_lars` + `place_lars` (`--hold`: `pick_lars` only, so it ends held either way). A **miss** (below) plays the "ah, damn it" first. `--patter S`: a filler line after S s of silence. JSON lines out: `say` (Lars's lines), `oops`, `info`, `done` |
| Agent hook | `[dream:pick_lars]` (`agent/gestures.py` `Stage.dream`, voiced by `storyteller.py`'s `stage.on_dream`) | Runs `dream_run.py` against the stage URL, speaks each `say` line in the host's voice. Extra flags in `DREAM_ARGS` (e.g. `--speed 0.5 --chunks 2`). Put the literal tag in a show act line: `show.py`'s `book_knows` drops `dream:` options from config chains. `Stage.on_dream_oops` gets a miss: live mode cues the sad trombone and asks the LLM for one line about it; show mode says a `DREAM_OOPS` quip. A show cut ends `dream_run.py` (`stop_dream`) |
| In the show | `agent/show.md` `DREAM_PICK = off` (on: act 2's pick line is `[dream:pick_lars]`, it wins over `LARS_PICK`), `DREAM_ARGS`, `DREAM_OOPS` | `show.py` cuts the act after the tag: the runner says the part before, waits while the stage is dreaming and its lines are spoken (patter fills the silences), then says the rest (`{LARS_PUT_DOWN}`). The estimate adds 90 s per tag: act 2 runs well over budget with it on |

**A miss** (owner, Nov 7 night): Dream-1 messing up, not our checker saying no. Detected after each played chunk
from `GET /state`: the stage holds Lars but the measured gripper is under `EMPTY_GRIP` 0.05 (closed on nothing;
on Lars the real fingers stop at 0.09–0.3), the hold was lost before the lift, or the chunks or `--pick-s` ran out
(`not_lifted` if held, else `timeout`). Then: no more chunks, an `oops` line (kind, `facepalm`, `sad_trombone`, a
default quip), the arm's `facepalm` through the normal checked `/play` (right away with an empty hand, else after
the way back), "Our own move, then!" and our `pick_lars`. Laptop checks: kin stage with a crafted empty-grip replay
(oops → way back → facepalm → fallback, ended held, 63 s with patter 5) and an opening closing chunk (timeout →
facepalm at once → fallback); `agent/tests/test_dream_run.py` (stub stage) and the show test in `test_show.py`.
`EMPTY_GRIP` needs a check on the real arms.

## Act 2's wand: Dream-1 picks, our IK hands over (`--handover`, branch `dream-labs-pick`, Nov 8, untested)
In `agent/show.md` only as a comment: `# DREAM_ARGS = --handover --cam-fallback --no-fallback --speed 0.5 --pick-s 90
--patter 8`. The active line stays `--cam --raw ...` (the live demo, owner). New flags go on the commented line only.
Untested on the real arms and on the kin stage: nothing in it has run yet.
- **Why split it.** Dream-1 has picked the green pen (drun3, and the checked 0.5-speed path at 11:39), but never a
  handover: dreamcli's hand-over run (drun4) dropped motors, the morning's zero-shot picks missed 4 of 4, and our
  path plays one arm's chunks at a time, with the stage's 15 cm arm gap that a handover breaks (raw would drop every
  arm-to-arm check). cam_pick's `handover()` is the move the owner saw run clean. So Dream-1 does the pick and the
  lift, and our plan does the rest.
- **Before Dream-1:** `snap.py` and `robot/dream_hand.py locate` on the station: the pen's two ends on the table. No
  pen seen: Dream-1 still tries; there's just no handover.
- **Dream-1's part:** the joint head, task `pick up the green pen and hold it up` (`agent/gestures.py` `DREAM_TASKS`),
  every chunk through `/trajectory` and `show.Checker` (not raw), retimed to 0.15 rad/s, no `carry`: the stage never
  thinks an arm holds the pen. A close is Dream-1's gripper going to `CLOSED_G` or below after it opened; a grip is
  the measured gripper stopping `GRIP_BLOCK` (0.015) short of the command (a 1 cm pen stops it near 0.1 of the 95 mm
  stroke). Misses: measured under `EMPTY_GRIP` 0.07 (closed on nothing; after a grip: dropped), open again after a
  grip (dropped), lifted with no grip (`empty_grip`). A lift is the fingertips 30 mm above their lowest since the
  close (`RISE_MM`). The joints at the close are kept as the grasp.
- **Our part:** `dream_hand.py plan` from the arm's joints now. The pen lay flat along its two ends and turns with the
  claw from the close, so its axis in the claw's frame is the grasp rotation's transpose times that direction; the
  longer end is the one farther from the grasp point. Then: rise to `LIFT`, present (the longer end up), four wrist
  rolls, and `handover()` with `HAND_UP` cut to the longer end minus 1.5 cm (at least 4 cm). Our pieces play raw, as
  `--cam`'s: they're floor-checked, and arm-to-arm checked here and inside `handover()`.
- **No handover** when the grasp point is over 2.5 cm from the pen in the snap (it was pushed, or that was something
  else), the pen is more than ~20° off the claw's x (`AXIS_COS`), the longer end is too short, or there's no pose at the
  handover spot. Then the same arm rolls it, comes back to where Dream-1 left it, and goes back along Dream-1's chunks
  (checked: the reversed close opens the gripper where it picked). No plan at all: straight to that way back (no raw
  wand wiggle: the arm can be only 3 cm above the table).
- **A miss** (`empty_grip`, `dropped`, `not_lifted`, `timeout`), a refusal by our checker (`refused`, new) or a
  Dream error (`timeout` mid-run, `no_plan` before the first chunk) all give the oops line, then the way back and the
  facepalm. With the pen still in the claw, the way back comes first (it puts the pen down), then the facepalm.
- **Patter:** after the lift it stops saying the moves are Dream-1's (`OURS_PATTER`, then `HAND_PATTER` during a
  handover).
- **`--cam-fallback` (Plan B):** after a miss, refusal or Dream error whose way back finished (the pen on the table,
  or never lifted), the oops joke and the facepalm play first, then "Plan B: our own code, same pen." and the `--cam`
  run itself (`--cam --raw`, no prop, fresh snap, cam_pick's pick, present, roll, handover, place). It waits up to
  20 s for both arms to be free; a fault, a busy arm or a prop held means no Plan B (the done line says why). The way
  back stopping halfway ("I'm stuck halfway") also means no Plan B. Plan B's own lines don't credit Dream-1
  (`CAM_PATTER`, "The left arm, then."). If Plan B misses too: its own oops (`FALLBACK_OOPS`, refusals and errors
  included), its way back, a second facepalm, done. Its done line is the run's last.
- **Act length, estimated from cam_pick's segment times at `--speed 0.5`, not measured:** Plan B adds about 60-90 s
  (snap and IK planning 10-20 s, pick ~9 s, present and rolls ~11 s, handover and place ~31 s, piece overhead
  ~8 s), plus the facepalm (~4 s) before it. A Dream-1 attempt that runs out its `--pick-s 90` and then needs Plan B
  makes act 2 about 3-3.5 minutes; a lower `--pick-s` (e.g. 60) trims the worst case, but Dream-1 needs ~15 s just
  to open its cameras.
- Knobs to check on the real arms: `EMPTY_GRIP` and `GRIP_BLOCK` against a real close on the pen (about 1 cm
  thick) and on air, `GRASP_TOL`, `AXIS_COS`.
- **Review (Nov 8, code read only, nothing run).** Fixed: the grasp row was computed on the live `--cam --raw` path
  too (now `--handover` only: that path runs main's code exactly); the raw wand wiggle could run 3 cm above the table
  when `dream_hand.py` had no plan; with `--cam-fallback`, a refusal or a Dream error now gets its joke before Plan B.
  Checked and fine: the frames (shared = arm frame − `OFFSET` for the right arm, as `handover()`), the handover spot
  (`mid_y`), the long-end sign at present (`ysign = −long_sign`, matching `handover()`'s own `long_sign`), ssh
  failures and timeouts (`station_json` gives `None`, so no pen means no handover and no plan means the way back),
  `None` grasp or pen. Left as is: the stage dropping off the network mid-run crashes `dream_run.py` as it does on
  main (the arm stops where its last piece ended; no prop is marked held); the show's `DREAM_OOPS` quip is random, so
  Plan B's miss can repeat the first quip.

## Real picks with dreamcli (Nov 7, ~21:45, the owner at the e-stop)
After the cameras were reseated (all three on USB 3.2), `dream.check` passed (a borderline 1.0° lag on the left
hand). Two `dreamcli robot run` at half speed (`~/dream-test.yaml`: station_slow + `action_timeout_s: 5.0`):
- "pick up the green frog" (`~/drun1.log`): picked Lars, let go, picked him again.
- "pick up the green frog and hold it up" (`~/drun2.log`, its `last-run.csv` saved as `robot/lars/dream_pick.json`,
  the executed rows of chunks 1–16): gripped and raised him. The right arm did two reach → close → lift cycles of
  ~10 s (j2 up to 1.73, j3 up to 1.01, j4 down to −0.83; the gripper closes to 0.09–0.3 on him); Dream-1 idles at
  rest for the first ~5 s, and opens again after each lift.

**Through our checker** (`dream_run.py --replay robot/lars/dream_pick.json --stage http://127.0.0.1:8785`, kin):
- Both grasps brush the table with the fingertips: 2.3 mm (first) and −0.2 mm (second) in our model, against the
  3 mm table floor. The owner's call: `/trajectory` lets the **fingertips** go to −3 mm (`TRAJ_TIP_FLOOR`); every
  other part keeps 3 mm, and the 0.3 rad/s table speed, gap, clash and lean checks are unchanged. The stage logs
  each chunk's lowest fingertip and other-part clearance.
- With that, both real grasps pass: idle chunk skipped, reach (7.8 s, slowed 2.9×), grasp, close (Lars held:
  `carry`), lift to 96 mm (first cycle) or 146 mm (second) → done, ~15 s of motion at speed 1. Afterwards a gesture
  plays with the grip kept, `/talk` is refused, `place_lars` puts him back at the dock.
- Joints a little past a safe limit (≤ 0.05 rad, Dream-1's jitter at the j2/j3 fold stops) are clamped onto it.

## Earlier: preview on real frames before the reseat (Nov 7, ~21:13, no motion)
5 billed preview calls (`~/dream-ghost/calls.jsonl`). Joint head 3.04 s / 2.70 s cold (server inference 0.30 s,
gateway 2.8 s), then 1.27 s and 0.94 s; eef head 14.8 s for its first chunk (3.9 s round trip, the rest the client's
IK). Opening the cameras and filling the window: 8–14 s. Both heads and three chained chunks stayed at rest
(≤ 0.06 rad per joint): the left wrist camera was on USB 2 then and `dream.check` hadn't run. `dream_run.py`
therefore skips still chunks (< 0.15 rad and gripper < 0.3) and keeps asking, up to `--chunks`/`--pick-s`.

## Dream-1 real test (dreamcli, with the owner at the e-stop)
The cheapest real test of Dream-1 on its own loop. **The stage server must be stopped for all of it** (dreamcli
opens the arms over CAN and takes the arms lock, even for `--dry-run`); restart it afterwards.
```bash
ssh nuck9; source ~/dream-env/bin/activate
cp ~/dial/station_slow.yaml ~/dream-test.yaml   # half speed (max_ee_speed_m_s 0.3); then edit the copy:
#   - add the line   action_timeout_s: 5.0     (chunks over 3 s were thrown away this morning; a round trip is ~1-3 s)
#   - in the left camera's line set   fps: 15  (its USB 2 link can't do 1280x720 at 30), or re-plug it into USB 3
# 0. Clear the table; the prop ~0.40 m straight in front of ONE arm (morning reaches went straight ahead).
# 1. Loop and inputs check: no model calls, nothing billed. The arms trace 8 cm circles for ~15 s; the owner types
#    "the workspace is clear" and answers the INPUTS page (cameras on their own keys, upright, natural colours):
#    from the laptop  ssh -L 8643:localhost:8643 nuck9  and open http://localhost:8643. Only the owner answers it.
python -m dream.check -- dreamcli robot run --config ~/dream-test.yaml --task "pick up the green frog"
# 2. Dry run, 30 s: arms opened but not driven
timeout -s INT 30 dreamcli robot run --config ~/dream-test.yaml --task "pick up the green frog" --dry-run
dreamcli robot report        # where it would have gone; send the trace for a reach check against the prop
# 3. Real, e-stop in hand, 60 s cap (homes first; homes and goes limp at the end)
timeout -s INT 60 dreamcli robot run --config ~/dream-test.yaml --task "pick up the green frog"
dreamcli robot report
```
A dreamcli run calls the model about once a second: a 60 s run is ~60 billed calls.

## Real-arm steps for the show path (preview → checker → play)
Needs a person at the e-stop, and the stage server on this code (`/trajectory`, `carry`, the fingertip floor are
new: `./sync.sh` and a stage restart, ~20 s of checks). `~/dream-ghost/dream_preview.py` is already on the station.
1. Lars anywhere in reach of the right arm, both arms at rest and empty-handed (`GET /state`: no fault, `props`
   both false). Nothing else on the stage (no story running: the arms must be free).
2. Offline first, on the laptop kin stage: `cd robot && PYTHONPATH=/tmp/kit/site:. /tmp/kit/.venv/bin/python stage.py
   --backend kin --port 8785`, then `python3 robot/dream_run.py --replay robot/lars/dream_pick.json --stage
   http://127.0.0.1:8785` (ends `"done", "ok": true, "held": true`).
3. Real, half the cap the first time (0.15 rad/s), from the repo root on the laptop with the 8770 tunnel up:
   `python3 robot/dream_run.py --speed 0.5 --pick-s 90`
   (defaults: task "pick up the green frog and hold it up", eef head, `carry` lars, ≤ 30 Dream-1 calls). Watch the
   `say`/`info`/`done` lines; the stage log prints each chunk's lowest fingertip. It stops itself on a refused chunk,
   a 0.2 rad tracking error or a fault, then goes back along its chunks and plays our `pick_lars`; `/freeze` (the
   page's STOP) brakes a chunk for good. Success: `"done", "ok": true, "held": true`: the right arm holds Lars where
   Dream-1 lifted him, and gestures play with him in hand.
4. Put him back: `curl -s -X POST http://127.0.0.1:8770/play -d '{"arm": "right", "gesture": "place_lars"}'`.
5. In the show: `DREAM_PICK = on` in `agent/show.md` (its `DREAM_ARGS` already has `--speed 0.5 --hold --pick-s 90
   --patter 6`), then rehearse act 2 with someone at the e-stop.

## Dreamcli basics
`dreamcli robot run --task "<instruction>" [--dry-run] [--config file] [--view]` homes the arms first, runs until
Ctrl-C (`timeout -s INT <secs>` works), then homes and switches off. `dreamcli robot report` summarises the last run.
It needs the team `DREAM_API_KEY` in `~/.dreamlabs/credentials` (never print, copy or commit it; `credentials.bak`
is another team's key: never touch it).

## The morning runs (Nov 7)
- 4 zero-shot runs of "pick up the yellow block": no successful pick. The gripper closed on air, grabbed the wrong
  (red) block, and once drove into the table, so the e-stop was pressed.
- A long, detailed "what + how" prompt made it worse than a short one.
- Logs: ~1 s round trips, chunks over 3 s discarded (`action_timeout_s` 3.0), stalls up to 131 ticks, RealSense
  frame drops on all three cameras, the gripper capped at 0.05 per tick (a full close ~1 s).
- Calibration needs nothing from us: no extrinsics; joint zeros, home, camera roles and the gripper (Dream's tool point
  134.7 mm, i2rt's fingertips 144.7 mm) all match the station.

## Fine-tuning on our own scripted demos (Lars pick)
Teleop isn't required; only the dataset format matters. Fine-tuning needs 50+ demos, 7–8 h of training plus queue
time, and costs ~$200. Sources: `~/physical-ai-hacks/docs/{data_collection,fine_tuning,running_your_model}.md`,
`tools/finetune.py`, `src/dream_hackathon/{dataset,collect,layouts}.py`.
- **Format:** LeRobot v2.1 in `~/hackathon_data/<name>/`, 30 fps. `observation.state` and `action` are float32[14]
  (measured and commanded joints): `right_joint_0..5, right_gripper, left_joint_0..5, left_gripper`, absolute
  radians, gripper 0 closed to 1 open. All three cameras are required (640x360 H.264). Episodes are at least 1.6 s;
  aim for 50+ of 10–30 s. Don't mix in `~/hackathon_data/myteam-cubes` (organiser test set).
- **Recording from our own loop:** `dataset.DatasetWriter(path, 30, "yam", names, video_keys, 640, 360)`, then
  `ep = w.start_episode(task)`, `ep.add_frame(layouts.to_vector("yam", q, g), layouts.to_vector("yam", cmd_q, cmd_g),
  {video_key: cams[role].latest()})` every 1/30 s in real time, then `ep.save()` or `ep.discard()`. Start each
  episode from dreamcli's home pose (`dreamcli robot run` homes first), keep the idle arm at home, and don't record
  the reset. The stage server holds `~/.dreamlabs/arms.lock`; stop it first.
- **Train:** `~/dream-env/bin/python tools/finetune.py ~/hackathon_data/lars --arm yam --check` (local, sends
  nothing), then without `--check` plus `--name lars --suffix lars --lr-schedule cosine` in tmux. It prints
  `dream-1-ft:<uid8>-lars`; resume with `--dataset-id`/`--job-id`. Knobs not exposed by the script
  (`dream.DreamClient().finetune(...)`): `steps` 500–2000 (default 1000; fewer is cheaper and faster),
  `base_share` (0.5 default), `learning_rate`. ~$200 per job from prepaid credits; there's no balance API, so ask
  the organisers. A queued job can't be cancelled; a failed one costs nothing.
- **Run:** `dreamcli robot run --checkpoint dream-1-ft:<uid8>-lars --task "<same task text>" --action-space joint
  --dry-run`, then without `--dry-run`. The first call can take up to 1 h to start the model.
