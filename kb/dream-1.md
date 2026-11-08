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
| Driver | `robot/dream_run.py`, laptop, stdlib only | Runs the preview over ssh, picks the arm that moves most, POSTs each chunk, fetches the next one 1.5 s before the current one ends (a round trip is ~1 s), stops on a still chunk, a refusal, a tracking error over 0.2 rad, a fault, a Dream error or the timeout; then goes back along the played chunks, reversed, and on a failure plays `pick_lars` + `place_lars`. JSON lines out: `say` (Lars's lines), `info`, `done` |
| Agent hook | `[dream:pick_lars]` (`agent/gestures.py` `Stage.dream`, voiced by `storyteller.py`'s `stage.on_dream`) | Runs `dream_run.py` against the stage URL, speaks each `say` line in the host's voice. Extra flags in `DREAM_ARGS` (e.g. `--speed 0.5 --chunks 2`). Put the literal tag in a show act line: `show.py`'s `book_knows` drops `dream:` options from config chains |

## Results on real frames (Nov 7, ~21:13, no motion)
Lars at the table centre, a plush dog by the right arm, both arms at rest, task "pick up the green frog".
- **5 billed `get_action` calls** (one more was `--fake`, unbilled), all in `~/dream-ghost/calls.jsonl`. No price per
  call is published; there's no balance API (ask the organisers).
- **Latency:** joint head 3.04 s and 2.70 s for a cold first call (server inference 0.30 s, gateway 2.8 s), then
  1.27 s and 0.94 s. Eef head 14.8 s for its first chunk (3.9 s round trip, the rest the client's IK). Opening the
  cameras and filling the window: 8–14 s.
- **What it returned:** 48 rows × 7 per arm. Both heads, and three chained joint-head chunks (each seamed on the last
  one's end): **Dream-1 stays at rest.** Every joint stays within 0.02–0.06 rad of rest (the arm that moves most:
  0.11 rad in total), grippers unchanged (right ~0.96, left ~0.31). It does not reach toward Lars.
- **Checker on the kin stage** (`dream_run.py --replay robot/lars/dream_chunks.json --stage http://127.0.0.1:8785`):
  - the saved chunks are refused on both arms: "j2 reaches [-0.03, 0.01], allowed [-0.02, 3.57]; j3 reaches
    [-0.02, 0.03], allowed [-0.02, 3.06]". Rest sits against j2's and j3's lower stops, and Dream-1's jitter goes
    1–2 cmrad past them;
  - the other single chunks pass on some arms, as a 4–12 s shuffle of a few centimetres (slowed 1.5–4.5×).
- So `dream_run.py` now treats a chunk that moves less than 0.15 rad (gripper < 0.3) as Dream-1 idling: "Dream-1 is
  daydreaming today. Our own move, then!", and our `pick_lars` plays. With today's behaviour that is what the show
  will do.
- Possible reasons, not yet tested: the base model with no fine-tune on this scene; the `dream.check` INPUTS step
  never confirmed (it warns on every call); Lars is small and far from both arms. The morning's dreamcli runs (eef
  head, blocks in front of one arm) did reach.

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
Needs a person at the e-stop, and the stage server on this commit (`/trajectory` and `carry` are new: `./sync.sh`
and a stage restart, ~20 s of checks). `~/dream-ghost/dream_preview.py` is already on the station.
1. Lars at his dock, right arm at rest and empty-handed (`GET /state`: no fault, `props` both false).
2. Offline first, on the laptop kin stage: `cd robot && PYTHONPATH=/tmp/kit/site:. /tmp/kit/.venv/bin/python stage.py
   --backend kin --port 8785`, then `python3 robot/dream_run.py --replay robot/lars/dream_chunks.json --stage
   http://127.0.0.1:8785`.
3. Real, slower than the cap the first time (`--speed 0.5` = 0.15 rad/s), two chunks:
   `python3 robot/dream_run.py --speed 0.5 --chunks 2` (the stage through the 8770 tunnel; Dream on nuck9).
   Watch the `say` and `done` lines. It stops itself on a still or refused chunk, a 0.2 rad tracking error or a fault;
   `/freeze` (the page's STOP) brakes a chunk for good.
4. In the show: `[dream:pick_lars]` in an act line, `DREAM_ARGS="--speed 0.5 --chunks 2"` for the agent.

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
